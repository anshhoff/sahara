"""The four stopping invariants, exercised directly and through the loop
(docs/04 §5, docs/05 §4)."""
from __future__ import annotations

from app import cases, clock, config, db, executor, invariants, policy, webhooks
from tests.conftest import failure_payload


def _open_case(**kw):
    return cases.create(subscription_id=kw.get("sub", "SYNTH-sub-i"),
                        customer_id="SYNTH-cust-i",
                        amount_at_risk_paise=49900,
                        opted_out=kw.get("opted_out", False),
                        synthetic=True)


def test_i3_opt_out_beats_everything(fresh_db):
    case = _open_case(opted_out=True)
    cases.set_category(case["id"], "insufficient_funds")
    v = invariants.check(case["id"], phase="pre_decision")
    assert v.is_stop and v.invariant == "I3" and v.status == "stopped_opt_out"


def test_i4_unknown_never_guesses(fresh_db):
    case = _open_case()
    cases.set_category(case["id"], "unknown")
    v = invariants.check(case["id"], phase="pre_decision")
    assert v.is_stop and v.invariant == "I4" and v.status == "stopped_unknown"


def test_i1_caps_attempts_even_when_the_policy_table_would_continue(fresh_db):
    """I1 is the backstop: with the shipped policy table attempt 3 is always terminal,
    so this status is unreachable through the table — which is exactly why the cap is
    enforced in a module the table cannot influence."""
    case = _open_case()
    cases.set_category(case["id"], "insufficient_funds")
    for _ in range(config.MAX_ATTEMPTS):
        cases.record_execution(case["id"], "RETRY_LATER")
    v = invariants.check(case["id"], phase="pre_decision")
    assert v.is_stop and v.invariant == "I1" and v.status == "stopped_max_attempts"


def test_i2_defers_a_contact_inside_the_cooldown(fresh_db, sim_clock):
    case = _open_case()
    cases.set_category(case["id"], "card_expired")
    cases.record_execution(case["id"], "SEND_UPDATE_LINK")   # sets last_contact_at = now
    sim_clock.advance(hours=2)
    v = invariants.check(case["id"], "SEND_UPDATE_LINK", phase="pre_execution")
    assert v.is_defer and v.invariant == "I2"
    assert v.until == clock.plus_hours(clock.parse_iso(cases.get(case["id"])["last_contact_at"]),
                                       config.COOLDOWN_HOURS)


def test_i2_does_not_apply_to_a_silent_retry(fresh_db, sim_clock):
    """RETRY_LATER re-charges an existing mandate; it puts no message in front of a
    person, so the contact cooldown does not gate it."""
    case = _open_case()
    cases.set_category(case["id"], "insufficient_funds")
    cases.record_execution(case["id"], "SEND_UPDATE_LINK")
    sim_clock.advance(hours=1)
    assert invariants.check(case["id"], "RETRY_LATER", phase="pre_execution").ok


def test_i2_stops_when_the_next_contact_would_fall_outside_the_episode(fresh_db, sim_clock):
    case = _open_case()
    cases.set_category(case["id"], "card_expired")
    sim_clock.advance(days=config.EPISODE_WINDOW_DAYS - 0.5)
    cases.record_execution(case["id"], "SEND_UPDATE_LINK")
    v = invariants.check(case["id"], "SEND_UPDATE_LINK", phase="pre_execution")
    assert v.is_stop and v.invariant == "I2" and v.status == "stopped_cooldown_expired"


def test_ordering_consent_beats_unknown_beats_attempts(fresh_db):
    case = _open_case(opted_out=True)
    cases.set_category(case["id"], "unknown")
    for _ in range(config.MAX_ATTEMPTS):
        cases.record_execution(case["id"], "RETRY_LATER")
    assert invariants.check(case["id"], phase="pre_decision").invariant == "I3"

    cases.set_opted_out(case["id"], False)
    assert invariants.check(case["id"], phase="pre_decision").invariant == "I4"

    cases.set_category(case["id"], "insufficient_funds")
    assert invariants.check(case["id"], phase="pre_decision").invariant == "I1"


def test_gate_reloads_state_and_never_trusts_the_caller(fresh_db):
    case = _open_case()
    cases.set_category(case["id"], "insufficient_funds")
    stale = dict(case)                       # a snapshot taken before the opt-out
    cases.set_opted_out(case["id"], True)
    v = invariants.check(stale, phase="pre_execution")
    assert v.is_stop and v.invariant == "I3"


# ------------------------------------------------------- through the full loop
def test_opt_out_stops_before_any_contact(fresh_db):
    webhooks.intake(failure_payload(opted_out=True), source="synthetic")
    case = cases.find_latest_by_subscription("SYNTH-sub-t")
    assert case["status"] == "stopped_opt_out"
    assert case["attempt_count"] == 0
    assert db.scalar("SELECT COUNT(*) FROM execution_record WHERE case_id = ?", (case["id"],), 0) == 0


def test_mid_flight_opt_out_is_caught_at_the_pre_execution_gate(fresh_db, sim_clock):
    webhooks.intake(failure_payload(), source="synthetic")   # insufficient_funds -> RETRY_LATER +24h
    case = cases.find_latest_by_subscription("SYNTH-sub-t")
    assert case["status"] == "open"
    assert db.query_one("SELECT 1 FROM intervention_decision WHERE case_id = ? AND status = 'scheduled'",
                        (case["id"],)) is not None

    cases.set_opted_out(case["id"], True)                    # arrives after the decision
    sim_clock.advance(hours=25)
    executor.tick()

    case = cases.get(case["id"])
    assert case["status"] == "stopped_opt_out"
    assert case["attempt_count"] == 0
    decision = db.query_one("SELECT * FROM intervention_decision WHERE case_id = ?", (case["id"],))
    assert decision["status"] == "blocked_by_invariant"


def test_unknown_stops_with_zero_interventions(fresh_db):
    webhooks.intake(failure_payload(error=None), source="synthetic")
    case = cases.find_latest_by_subscription("SYNTH-sub-t")
    assert case["status"] == "stopped_unknown"
    assert case["attempt_count"] == 0
    assert db.scalar("SELECT COUNT(*) FROM intervention_decision WHERE case_id = ?",
                     (case["id"],), 0) == 0


def test_attempts_never_exceed_the_cap_across_a_long_lived_case(fresh_db, sim_clock):
    payload = failure_payload()
    webhooks.intake(payload, source="synthetic")
    case = cases.find_latest_by_subscription("SYNTH-sub-t")
    for i in range(12):                       # keep failing, forever
        sim_clock.advance(hours=12)
        executor.tick()
        if cases.get(case["id"])["status"] != "open":
            break
        p = failure_payload(event_id=f"SYNTH-evt-t-loop-{i}", pay_id=f"SYNTH-pay-t-{i}")
        webhooks.intake(p, source="synthetic")
    assert cases.get(case["id"])["attempt_count"] <= config.MAX_ATTEMPTS

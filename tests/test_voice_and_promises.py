"""Voice as escalation rung 3, and the promise tracker behind it.

Two claims are under test here, and the second is the interesting one.

1. Voice is an ACTION, not a channel — so it inherits I2, I5, I6 and I7 and the
   annoyance pricing with zero new guardrail code. If that inheritance is not real,
   this design has no argument.
2. The inbound schema has NO AMOUNT FIELD and can never acquire one. A compromised
   model, or a caller who talks their way into one, cannot make this system state a
   wrong rupee figure — because there is nowhere for the figure to travel.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import (cases, clock, config, db, economics, executor, inbound,  # noqa: E402
                 invariants, metrics, policy)
from tests.conftest import failure_payload  # noqa: E402


# ------------------------------------------------------------ 3.1 registration
def test_voice_is_registered_as_an_action():
    assert "VOICE_CALL" in config.ACTIONS
    assert "VOICE_CALL" in config.CONTACT_ACTIONS
    assert config.ACTION_COST_PAISE["VOICE_CALL"] == 2500


def test_voice_is_priced_between_a_link_and_a_person():
    """The entire economic argument for the rung, as an assertion."""
    assert (config.ACTION_COST_PAISE["SEND_UPDATE_LINK"]
            < config.ACTION_COST_PAISE["VOICE_CALL"]
            < config.ACTION_COST_PAISE["STOP_HANDOFF"])


def test_voice_inherits_every_contact_guardrail_with_no_new_code():
    """The acceptance criterion for registering voice as an action rather than as a
    channel. Each gate keys off CONTACT_ACTIONS, so membership IS the inheritance —
    and the SQL those gates run keys off the same set, derived rather than retyped."""
    assert "VOICE_CALL" in config.CONTACT_ACTIONS_SQL
    for action in config.CONTACT_ACTIONS:
        assert f"'{action}'" in config.CONTACT_ACTIONS_SQL


def test_voice_carries_annoyance_pricing():
    assert economics.annoyance_cost_paise("VOICE_CALL", 3, 49900) > 0
    assert economics.annoyance_cost_paise("RETRY_LATER", 3, 49900) == 0


def test_voice_is_priced_on_the_same_hazard_curve_as_every_other_contact():
    """A per-channel churn multiplier for voice was drafted and removed: the sign of
    the difference is genuinely unclear, and an unsupported assumption that decides
    which interventions fire is worse than no assumption."""
    for attempt in (1, 2, 3):
        assert (economics.annoyance_cost_paise("VOICE_CALL", attempt, 49900)
                == economics.annoyance_cost_paise("SEND_UPDATE_LINK", attempt, 49900))


# ------------------------------------------------------------ 3.2 substitution
def test_voice_substitutes_at_attempt_three_and_adds_no_fourth_rung():
    assert config.MAX_ATTEMPTS == 3
    for category in ("card_expired", "issuer_declined", "authentication_failed"):
        assert config.POLICY[(category, 3)][0] == "VOICE_CALL"
    assert policy.is_total()


def test_stop_handoff_never_consumed_an_attempt_which_is_why_the_rung_was_free():
    assert "STOP_HANDOFF" in economics.NON_INTERVENTION_ACTIONS
    assert "STOP_HANDOFF" not in config.CONTACT_ACTIONS


# ------------------------------------------------------------ 3.3/3.4 templates
def test_every_registered_voice_template_passes_the_copy_validator(fresh_db):
    """The same gate every other outbound string goes through. No exceptions for voice,
    and none for Hindi."""
    case = cases.create(subscription_id="s", customer_id="c", amount_at_risk_paise=49900)
    for (category, language), text in config.VOICE_TEMPLATES.items():
        result = executor.validate_copy(text, case)
        assert result["ok"], f"({category}, {language}): {result['problems']}"


def test_the_voice_template_table_is_total_over_categories_and_languages():
    for category in config.CATEGORIES:
        for language in config.VOICE_LANGUAGES:
            assert (category, language) in config.VOICE_TEMPLATES


def test_an_unregistered_language_cannot_be_spoken(fresh_db):
    case = cases.create(subscription_id="s", customer_id="c", amount_at_risk_paise=49900)
    db.update("recovery_case", case["id"], {"current_category": "card_expired"})
    with pytest.raises(ValueError, match="not registered"):
        executor.voice_script(cases.get(case["id"]), "fr")


def test_a_devanagari_digit_is_rejected_like_an_ascii_one(fresh_db):
    """Python's \\d matches ०-९, so a Hindi draft cannot smuggle in an amount either.
    Without this the digit rule would have been an English-only rule."""
    case = cases.create(subscription_id="s", customer_id="c", amount_at_risk_paise=49900)
    bad = "[SYNTHETIC DEMO] आपका भुगतान ४९९ रुपये बाकी है: {LINK}"
    result = executor.validate_copy(bad, case)
    assert result["ok"] is False
    assert any("literal numbers" in p for p in result["problems"])


# ------------------------------------------------------------------- 3.5 I8
def test_i8_is_not_applicable_when_nothing_would_transmit(fresh_db):
    case = cases.create(subscription_id="s", customer_id="c", amount_at_risk_paise=49900,
                        synthetic=True)
    db.update("recovery_case", case["id"], {"current_category": "card_expired"})
    verdict = invariants.check(cases.get(case["id"]), "VOICE_CALL", phase="pre_execution")
    assert verdict.details["I8"] == invariants.NOT_APPLICABLE
    assert verdict.ok or verdict.is_defer


def test_a_synthetic_customer_can_never_be_dialled(fresh_db, monkeypatch):
    """The structural half of I8. Not a flag anyone has to remember: a synthetic
    customer has no phone number anywhere in this system, so there is nothing that
    could match an allowlist however the allowlist is configured."""
    monkeypatch.setattr(config, "VOICE_REAL_SEND_ENABLED", True)
    monkeypatch.setattr(config, "VERIFIED_RECIPIENTS", frozenset({"+919999999999"}))
    case = cases.create(subscription_id="s", customer_id="c", amount_at_risk_paise=49900,
                        synthetic=True)
    assert invariants.verified_recipient(cases.get(case["id"])) is None
    # Still not applicable, because a synthetic case never reaches the real-send path.
    assert invariants.would_really_transmit(cases.get(case["id"]), "VOICE_CALL") is False


def test_i8_refuses_a_real_transmission_to_an_unlisted_number(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "VOICE_REAL_SEND_ENABLED", True)
    monkeypatch.setattr(config, "VERIFIED_RECIPIENTS", frozenset({"+919999999999"}))
    case = cases.create(subscription_id="s", customer_id="c", amount_at_risk_paise=49900,
                        synthetic=False)
    db.update("recovery_case", case["id"], {"current_category": "card_expired"})
    verdict = invariants.check(cases.get(case["id"]), "VOICE_CALL", phase="pre_execution")
    assert verdict.is_stop
    assert verdict.invariant == "I8"
    assert verdict.status == "stopped_unverified_recipient"


def test_i8_fails_closed_on_an_empty_allowlist(fresh_db, monkeypatch):
    """The one invariant that stops on ABSENCE rather than on a violation. With nothing
    on the list, nothing can be dialled — which is the safe default and the reason the
    allowlist is empty out of the box."""
    monkeypatch.setattr(config, "VOICE_REAL_SEND_ENABLED", True)
    monkeypatch.setattr(config, "VERIFIED_RECIPIENTS", frozenset())
    case = cases.create(subscription_id="s", customer_id="c", amount_at_risk_paise=49900,
                        synthetic=False)
    db.update("recovery_case", case["id"], {"current_category": "card_expired"})
    assert invariants.check(cases.get(case["id"]), "VOICE_CALL").invariant == "I8"


# ------------------------------------------------- 3.6 the inbound schema
def test_the_inbound_schema_has_no_amount_field():
    """The single assertion this whole design rests on."""
    fields = {f.lower() for f in inbound.schema_fields()}
    assert fields == {"intent", "promised_date", "confidence", "rationale"}
    for forbidden in ("amount", "amount_paise", "rupees", "sum", "total", "price"):
        assert forbidden not in fields


def test_the_schema_refuses_an_amount_bearing_field_outright():
    """Rejected, not ignored. Silently dropping the field would be almost as safe;
    failing loudly turns a boundary violation into a visible event."""
    with pytest.raises(Exception):
        inbound.InboundReading.model_validate(
            {"intent": "will_pay_on_date", "promised_date": "2026-03-06", "amount": 200})


def test_the_import_time_guard_refuses_a_money_shaped_field(monkeypatch):
    monkeypatch.setattr(inbound, "schema_fields", lambda: ("intent", "amount_paise"))
    with pytest.raises(AssertionError, match="no amount in an inbound reading"):
        inbound.assert_no_money_field()


def test_an_intent_outside_the_closed_enum_is_refused():
    with pytest.raises(Exception):
        inbound.InboundReading.model_validate({"intent": "will_pay_probably"})


def test_a_date_on_an_undated_intent_is_dropped(sim_clock):
    out = inbound.normalise({"intent": "cannot_pay", "promised_date": "2026-03-06"})
    assert out["promised_date"] is None


def test_a_dated_intent_without_a_resolvable_date_falls_back_to_unclear(sim_clock):
    """An intent that promises a day while naming none is not a promise anyone can
    track, and recording it would put a row in the table that could only ever break."""
    out = inbound.normalise({"intent": "will_pay_on_date", "promised_date": "sometime"})
    assert out["intent"] == "unclear"


@pytest.mark.parametrize("said,expected", [
    ("kal", "2026-03-03"),
    ("parso", "2026-03-04"),
    ("friday", "2026-03-06"),
    ("I will pay by 2026-03-10", "2026-03-10"),
])
def test_hinglish_relative_days_resolve_against_the_simulated_clock(sim_clock, said, expected):
    assert inbound.resolve_date(said) == expected


def test_a_date_in_the_past_is_refused_rather_than_corrected(sim_clock):
    """Silently moving a promise to a date the customer did not name is exactly the
    invention this module exists to prevent."""
    assert inbound.resolve_date("2026-02-01") is None


def test_a_date_beyond_the_episode_window_is_refused(sim_clock):
    assert inbound.resolve_date("2026-09-01") is None


def test_the_rule_baseline_reads_the_headline_hinglish_promise(sim_clock):
    out = inbound.read_by_rules("haan bhai, salary aane ke baad Friday ko kar dunga")
    assert out["intent"] == "will_pay_on_date"
    assert out["promised_date"] == "2026-03-06"


# ------------------------------------------------------ 3.7 the promise tracker
def _voice_case(fresh_db, category="issuer_declined", amount=49900):
    case = cases.create(subscription_id="sv", customer_id="cv", amount_at_risk_paise=amount,
                        synthetic=True)
    db.update("recovery_case", case["id"], {"current_category": category})
    return cases.get(case["id"])


def test_a_named_date_becomes_a_tracked_promise(fresh_db, sim_clock):
    case = _voice_case(fresh_db)
    reading = inbound.read_by_rules("Friday ko kar dunga")
    reading["source"] = "rule"
    promise = cases.record_promise(case["id"], reading)
    assert promise["promised_date"] == "2026-03-06"
    assert promise["status"] == "open"
    # The deadline is the END of the named day, in IST. Marking someone broken at
    # midnight UTC is 05:30 on that morning in Delhi.
    assert promise["due_at"].startswith("2026-03-06T18:29")


def test_an_undated_reading_records_no_promise(fresh_db, sim_clock):
    case = _voice_case(fresh_db)
    assert cases.record_promise(case["id"], inbound.read_by_rules("paise nahi hain")) is None


def test_only_one_open_promise_per_case(fresh_db, sim_clock):
    case = _voice_case(fresh_db)
    assert cases.record_promise(case["id"], inbound.read_by_rules("Friday ko kar dunga")) is not None
    assert cases.record_promise(case["id"], inbound.read_by_rules("kal kar dunga")) is None


def test_recovering_keeps_the_promise(fresh_db, sim_clock):
    case = _voice_case(fresh_db)
    cases.record_promise(case["id"], inbound.read_by_rules("Friday ko kar dunga"))
    cases.transition(case["id"], "recovered", summary="paid", stage="outcome")
    assert metrics.promises()["promises_kept"] == 1


def test_closing_any_other_way_breaks_it(fresh_db, sim_clock):
    case = _voice_case(fresh_db)
    cases.record_promise(case["id"], inbound.read_by_rules("Friday ko kar dunga"))
    cases.transition(case["id"], "stopped_handoff", summary="lapsed")
    assert metrics.promises()["promises_broken"] == 1


def test_a_case_can_never_close_leaving_a_promise_open(fresh_db, sim_clock):
    """Resolution lives inside transition() rather than at each call site, so no future
    stop path can forget it — which would inflate promises_kept by never counting the
    failures."""
    case = _voice_case(fresh_db)
    cases.record_promise(case["id"], inbound.read_by_rules("Friday ko kar dunga"))
    cases.transition(case["id"], "stopped_unknown", summary="whatever")
    assert cases.open_promise(case["id"]) is None


def test_the_sweep_breaks_a_promise_whose_day_has_passed(fresh_db, sim_clock):
    case = _voice_case(fresh_db)
    cases.record_promise(case["id"], inbound.read_by_rules("kal kar dunga"))
    assert executor._sweep_tracked_promises() == 0        # the day is not over yet

    sim_clock.advance(hours=48)
    assert executor._sweep_tracked_promises() == 1
    assert cases.get(case["id"])["status"] == "stopped_handoff"
    assert metrics.promises()["promises_broken"] == 1


def test_a_tracked_promise_overrides_the_fixed_grace_window(fresh_db, sim_clock):
    """Someone who says "next Tuesday" has until Tuesday. Applying a 72-hour clock on
    top of the date they named would hand them off while the promise is still good."""
    case = _voice_case(fresh_db)
    db.insert("intervention_decision", {
        "id": "dec_V", "case_id": case["id"], "attempt_number": 3,
        "category": "issuer_declined", "action": "VOICE_CALL",
        "policy_row_ref": "issuer_declined/3", "invariant_check": "{}",
        "decided_at": clock.now_iso(), "scheduled_for": clock.now_iso(),
        "status": "executed", "synthetic": 1})
    db.insert("execution_record", {
        "id": "exe_V", "decision_id": "dec_V", "case_id": case["id"], "action": "VOICE_CALL",
        "mode": "simulated", "status": "success", "result_payload": "{}", "cost_paise": 2500,
        "executed_at": clock.now_iso(), "synthetic": 1})
    cases.record_promise(case["id"], inbound.read_by_rules("I will pay by 2026-03-12"),
                         execution_id="exe_V")

    sim_clock.advance(hours=96)                     # past the 72h window, before the date
    assert executor._close_lapsed_promises() == 0
    assert cases.get(case["id"])["status"] == "open"


def test_promise_counts_are_traceable_to_case_ids(fresh_db, sim_clock):
    case = _voice_case(fresh_db)
    cases.record_promise(case["id"], inbound.read_by_rules("Friday ko kar dunga"))
    cases.transition(case["id"], "recovered", summary="paid", stage="outcome")
    traced = metrics.trace("promises_kept")
    assert traced["value"] == 1 and traced["case_ids"] == [case["id"]]


# ------------------------------------------------ the executor's voice handler
def test_a_voice_call_is_recorded_distinctly_from_a_link_send(fresh_db, sim_clock):
    case = _voice_case(fresh_db)
    result = executor.place_voice_call(case)
    assert result["simulated_channel"] == "voice"
    assert result["mode"] == "simulated"
    assert result["result_payload"]["language"] == config.VOICE_DEFAULT_LANGUAGE
    assert "never dialled" in result["result_payload"]["transmission"]


def test_a_call_with_no_transcript_reads_as_no_answer(fresh_db, sim_clock):
    executor.set_transcript_provider(None)
    result = executor.place_voice_call(_voice_case(fresh_db))
    assert result["result_payload"]["inbound_reading"]["intent"] == "no_answer"


def test_a_broken_transcript_provider_does_not_fail_the_call(fresh_db, sim_clock):
    executor.set_transcript_provider(lambda case: 1 / 0)
    try:
        result = executor.place_voice_call(_voice_case(fresh_db))
        assert result["status"] == "success"
        assert result["result_payload"]["inbound_reading"]["intent"] == "no_answer"
    finally:
        executor.set_transcript_provider(None)


def test_an_opt_out_heard_on_a_call_suppresses_the_person(fresh_db, sim_clock):
    """Said on a call, honoured everywhere: suppression is per PERSON (I6), so it
    reaches this customer's other subscriptions too."""
    case = _voice_case(fresh_db)
    executor.set_transcript_provider(lambda c: "do not call me again, remove my number")
    try:
        db.insert("intervention_decision", {
            "id": "dec_O", "case_id": case["id"], "attempt_number": 3,
            "category": "issuer_declined", "action": "VOICE_CALL",
            "policy_row_ref": "issuer_declined/3", "invariant_check": "{}",
            "decided_at": clock.now_iso(), "scheduled_for": clock.now_iso(),
            "status": "scheduled", "synthetic": 1})
        executor.execute_decision(db.row_to_dict(db.query_one(
            "SELECT * FROM intervention_decision WHERE id = 'dec_O'")))
        assert invariants.is_suppressed("cv") is not None
    finally:
        executor.set_transcript_provider(None)


# ------------------------------------------- the counterfactual the gate uses
def test_a_handoff_is_not_credited_with_recovering_anything():
    """It must not inherit P_RECOVER_DEFAULT. Doing so credited the human queue with an
    8% chance of the money arriving by itself, which made it the best-value option on
    the board and refused every intervention it was meant to be compared against."""
    case = {"current_category": "card_expired", "amount_at_risk_paise": 49900}
    ev = economics.evaluate(case, "VOICE_CALL", 3)
    assert ev["alternative_action"] == "STOP_HANDOFF"
    assert ev["alternative_ev_paise"] == -config.ACTION_COST_PAISE["STOP_HANDOFF"]


def test_early_attempts_are_still_priced_against_doing_nothing():
    case = {"current_category": "card_expired", "amount_at_risk_paise": 49900}
    ev = economics.evaluate(case, "SEND_UPDATE_LINK", 1)
    assert ev["alternative_action"] is None
    assert ev["alternative_ev_paise"] == 0


def test_the_gate_prefers_a_person_when_the_call_is_worth_less_than_one():
    """A big subscription is expensive to lose, so the annoyance term grows with the
    ticket and the arithmetic sends the large cases to a human. That is the gate
    working, not the gate failing."""
    cheap = economics.evaluate(
        {"current_category": "card_expired", "amount_at_risk_paise": 19900}, "VOICE_CALL", 3)
    assert cheap["verdict"] == "proceed"
    assert cheap["ev_paise"] > cheap["alternative_ev_paise"]

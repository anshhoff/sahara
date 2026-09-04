"""Dispatch fencing — look before you leap, and verify after you write (docs/04 §6.4).

Every invariant re-reads the CASE, which is our record of the world and exactly as
stale as the last webhook that happened to arrive. These fences re-read the world.

The distinction matters because without them this system can dun someone who settled
an hour ago and produce a perfect audit trail proving it did so correctly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import audit, cases, config, db, executor, fencing, policy, webhooks  # noqa: E402
from tests.conftest import failure_payload, recovery_payload  # noqa: E402


# --------------------------------------------------------------------- helpers
def _open_case(sub_id="SYNTH-sub-f", cust_id="SYNTH-cust-f", event_id="SYNTH-evt-f-1"):
    """A real case through the real front door: intake -> diagnose -> decide."""
    webhooks.intake(failure_payload(event_id=event_id, sub_id=sub_id, cust_id=cust_id,
                                    error=("BAD_REQUEST_ERROR", "card_expired",
                                           "The card used for this payment has expired", "customer")),
                    source="synthetic")
    return cases.find_latest_by_subscription(sub_id)


def _settle_out_of_band(case, event_id="SYNTH-evt-f-paid"):
    """Put a settlement in the immutable ledger that the case row does not reflect.

    This is not a contrivance; it is the state a crash inside `intake()` leaves behind.
    Look at the order there: `_insert_event` writes the failure_event row FIRST — it is
    the claim token, and the UNIQUE event id is what makes ingestion idempotent — and
    `outcome_recovered` transitions the case SECOND. Anything that stops the process
    between those two lines leaves exactly this: the money is recorded as arrived and
    the case is still open, waiting to be dunned.

    The same shape is what a lost update or a concurrent transition produces. The point
    of the pre-dispatch fence is that it cross-checks two independent representations —
    the case row and the event ledger — instead of trusting the one that can go stale.
    """
    from app import clock

    db.insert("failure_event", {
        "id": db.new_id("evt"),
        "case_id": None,                  # never linked: the crash was before that write
        "source": "synthetic",
        "razorpay_event_id": event_id,
        "event_type": "subscription.charged",
        "subscription_id": case["subscription_id"],
        "payment_id": "SYNTH-pay-settled",
        "customer_id": case["customer_id"],
        "amount_paise": int(case["amount_at_risk_paise"]),
        "currency": "INR",
        "occurred_at": clock.now_iso(),
        "received_at": clock.now_iso(),
        "raw_payload": json.dumps({"event": "subscription.charged"}),
        "synthetic": 1,
    })
    assert cases.get(case["id"])["status"] == "open"


# ------------------------------------------------------- 2.1 look before you leap
def test_the_ledger_refetch_sees_a_settlement_the_case_row_does_not(fresh_db):
    case = _open_case()
    assert fencing.refetch(case).verdict == fencing.CLEAR

    _settle_out_of_band(case)
    verdict = fencing.refetch(cases.get(case["id"]))
    assert verdict.verdict == fencing.SETTLED
    assert verdict.source == "local_ledger"
    assert "subscription.charged" in verdict.reason


def test_a_settlement_from_before_the_case_opened_does_not_fence_it(fresh_db, sim_clock):
    """An old recovery on the same subscription is a previous episode, not this one."""
    webhooks.intake(recovery_payload(event_id="SYNTH-evt-old", sub_id="SYNTH-sub-f"),
                    source="synthetic")
    sim_clock.advance(hours=5)
    case = _open_case()
    assert fencing.refetch(case).verdict == fencing.CLEAR


def test_a_mid_flight_settlement_stops_the_dispatch(fresh_db):
    """The end-to-end claim: the customer pays between the decision and its execution,
    and no contact goes out."""
    case = _open_case()
    decision = db.row_to_dict(db.query_one(
        "SELECT * FROM intervention_decision WHERE case_id = ? ORDER BY rowid DESC LIMIT 1",
        (case["id"],)))
    # card_expired attempt 1 is SEND_UPDATE_LINK at delay 0, so policy.decide already
    # executed it. Drive a fresh, still-scheduled decision instead.
    db.execute("DELETE FROM execution_record WHERE case_id = ?", (case["id"],))
    db.update("intervention_decision", decision["id"], {"status": "scheduled"})
    db.update("recovery_case", case["id"], {"attempt_count": 0, "last_contact_at": None})

    _settle_out_of_band(case)

    n_before = int(db.scalar("SELECT COUNT(*) FROM execution_record", (), 0))
    assert executor.execute_decision(db.row_to_dict(db.query_one(
        "SELECT * FROM intervention_decision WHERE id = ?", (decision["id"],)))) is None

    assert int(db.scalar("SELECT COUNT(*) FROM execution_record", (), 0)) == n_before
    assert cases.get(case["id"])["status"] == "stopped_already_settled"
    assert db.scalar("SELECT status FROM intervention_decision WHERE id = ?",
                     (decision["id"],)) == "blocked_by_fence"


def test_a_fenced_dispatch_consumes_neither_an_attempt_nor_the_claim(fresh_db):
    """The fence sits before reserve_attempt on purpose: nothing forbade this message,
    there was simply nothing left to collect, so it must not spend one of three."""
    case = _open_case()
    decision = db.row_to_dict(db.query_one(
        "SELECT * FROM intervention_decision WHERE case_id = ? ORDER BY rowid DESC LIMIT 1",
        (case["id"],)))
    db.execute("DELETE FROM execution_record WHERE case_id = ?", (case["id"],))
    db.update("intervention_decision", decision["id"], {"status": "scheduled"})
    db.update("recovery_case", case["id"], {"attempt_count": 0, "last_contact_at": None})
    _settle_out_of_band(case)

    executor.execute_decision(db.row_to_dict(db.query_one(
        "SELECT * FROM intervention_decision WHERE id = ?", (decision["id"],))))
    assert int(cases.get(case["id"])["attempt_count"]) == 0


def test_a_retry_later_is_not_fenced(fresh_db):
    """RETRY_LATER contacts nobody. A silent re-charge of an already-settled mandate is
    a no-op at the gateway, not a message to someone who owes nothing — so the fence
    that exists to stop unwanted CONTACT does not apply to it."""
    case = _open_case(sub_id="SYNTH-sub-r", cust_id="SYNTH-cust-r", event_id="SYNTH-evt-r-1")
    db.update("recovery_case", case["id"], {"current_category": "insufficient_funds"})
    assert "RETRY_LATER" not in config.CONTACT_ACTIONS


# ------------------------------------------------------------- degrade, never raise
def test_a_broken_refetch_degrades_to_unverified_and_does_not_block(fresh_db, monkeypatch):
    """A fence that can crash the pipeline is strictly worse than no fence."""
    case = _open_case()

    def boom(_case):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(fencing, "_ledger_settlement", boom)
    result = fencing.refetch(case)
    assert result.verdict == fencing.UNVERIFIED
    assert result.blocks is False
    assert "rate limited" in result.reason


def test_unverified_is_recorded_rather_than_absent(fresh_db, monkeypatch):
    """"We did not check" must never be indistinguishable from "we checked and it was
    fine". The verdict is a row, not a missing row."""
    case = _open_case()
    monkeypatch.setattr(fencing, "_ledger_settlement",
                        lambda _c: (_ for _ in ()).throw(RuntimeError("down")))
    fencing.guard_dispatch(case, "SEND_UPDATE_LINK")
    assert fencing.fence_stats()["by_phase"]["pre_dispatch"]["unverified"] == 1


# -------------------------------------------------- 2.2 verify after write
def test_a_post_write_settlement_always_appends_a_compensation_entry(fresh_db):
    case = _open_case()
    _settle_out_of_band(case)

    result = fencing.verify_after_write(cases.get(case["id"]), "SEND_UPDATE_LINK", None)
    assert result.verdict == fencing.SETTLED

    entries = [e for e in audit.trail(case["id"]) if e["stage"] == "compensate"]
    assert len(entries) == 1
    assert "Compensation" in entries[0]["summary"]


def test_the_compensation_entry_is_appended_even_when_cancellation_fails(fresh_db, monkeypatch):
    """The attempt is the evidence. A compensation log that only records successes is a
    compensation log that flatters itself, and the failed one is what a reader needs."""
    case = _open_case()
    _settle_out_of_band(case)
    monkeypatch.setattr(fencing, "_cancel_link",
                        lambda ref: {"attempted": True, "ok": False, "reason": "429 rate limited"})

    fencing.verify_after_write(cases.get(case["id"]), "SEND_UPDATE_LINK", "plink_XYZ")
    entry = [e for e in audit.trail(case["id"]) if e["stage"] == "compensate"][0]
    assert "FAILED" in entry["summary"]
    assert entry["detail"]["cancellation"]["ok"] is False
    assert entry["detail"]["razorpay_ref"] == "plink_XYZ"


def test_no_compensation_entry_when_the_world_did_not_move(fresh_db):
    case = _open_case()
    assert fencing.verify_after_write(case, "SEND_UPDATE_LINK", None).verdict == fencing.CLEAR
    assert [e for e in audit.trail(case["id"]) if e["stage"] == "compensate"] == []


def test_a_compensation_entry_is_inside_the_hash_chain(fresh_db):
    case = _open_case()
    _settle_out_of_band(case)
    fencing.verify_after_write(cases.get(case["id"]), "SEND_UPDATE_LINK", None)
    chain = audit.verify()
    assert chain["status"] == "intact" and chain["n_unchained"] == 0


# ------------------------------------------------ 2.3 the stale-inference guard
def test_irrelevant_churn_leaves_the_fingerprint_stable(fresh_db, sim_clock):
    """The whole value of this guard is that it is quiet. A fingerprint that trips on
    notes, timestamps or customer metadata gets switched off within a week."""
    case = _open_case()
    before = fencing.decision_fingerprint(cases.get(case["id"]))

    sim_clock.advance(hours=3)
    cases.touch(case["id"], last_contact_at=None)          # updated_at moves
    db.execute("UPDATE recovery_case SET updated_at = ? WHERE id = ?",
               ("2099-01-01T00:00:00Z", case["id"]))
    db.execute("UPDATE failure_event SET raw_payload = ? WHERE case_id = ?",
               (json.dumps({"notes": {"anything": "at all"}}), case["id"]))

    assert fencing.decision_fingerprint(cases.get(case["id"])) == before


@pytest.mark.parametrize("field,value", [
    ("status", "recovered"),
    ("attempt_count", 2),
    ("current_category", "insufficient_funds"),
    ("customer_opted_out", 1),
    ("amount_at_risk_paise", 999900),
])
def test_every_decision_relevant_field_moves_the_fingerprint(fresh_db, field, value):
    case = _open_case()
    before = fencing.decision_fingerprint(cases.get(case["id"]))
    db.execute(f"UPDATE recovery_case SET {field} = ? WHERE id = ?", (value, case["id"]))
    assert fencing.decision_fingerprint(cases.get(case["id"])) != before


def test_a_new_payment_link_moves_the_fingerprint(fresh_db):
    """Link presence is decision-relevant: a case that already has a live link is not
    the case the recommendation was computed for."""
    case = _open_case()
    before = fencing.decision_fingerprint(cases.get(case["id"]))
    db.execute("UPDATE execution_record SET razorpay_ref = 'plink_LIVE' WHERE case_id = ?",
               (case["id"],))
    assert fencing.decision_fingerprint(cases.get(case["id"])) != before


def test_a_changed_fingerprint_is_reported_as_blocking(fresh_db):
    case = _open_case()
    stale = fencing.decision_fingerprint(cases.get(case["id"]))
    db.execute("UPDATE recovery_case SET attempt_count = 3 WHERE id = ?", (case["id"],))
    result = fencing.inference_unchanged(cases.get(case["id"]), stale)
    assert result.verdict == fencing.CHANGED
    assert result.blocks is True


# ---------------------------------------------- 2.4 the metric and its denominator
def test_the_claim_carries_its_denominator(fresh_db):
    case = _open_case()
    # _open_case already dispatched one contact through the real pipeline, and that
    # dispatch was fenced like any other — which is the point. Count from there.
    base = fencing.fence_stats()["n_dispatches_fenced"]
    assert base >= 1, "the real pipeline did not fence its own dispatch"

    fencing.guard_dispatch(case, "SEND_UPDATE_LINK")
    fencing.guard_dispatch(case, "SEND_UPDATE_LINK")
    stats = fencing.fence_stats()
    assert stats["n_dispatches_fenced"] == base + 2
    assert stats["outreach_to_settled"] == 0
    assert stats["claim"] == (f"outreach to already-settled customers: 0 of "
                              f"{base + 2} dispatches fenced")


def test_the_numerator_is_computed_rather_than_asserted(fresh_db, sim_clock):
    """A breach — a contact executed on a case a fence had already called settled —
    must be counted, not assumed away. If this metric can never be non-zero it is
    decoration, so the test drives it non-zero on purpose."""
    case = _open_case()
    _settle_out_of_band(case)
    fencing.guard_dispatch(case, "SEND_UPDATE_LINK")
    assert fencing.fence_stats()["outreach_to_settled"] == 0

    sim_clock.advance(hours=1)
    from app import clock
    db.insert("intervention_decision", {
        "id": "dec_BREACH", "case_id": case["id"], "attempt_number": 1,
        "category": "card_expired", "action": "SEND_UPDATE_LINK",
        "policy_row_ref": "card_expired/1", "invariant_check": "{}",
        "decided_at": clock.now_iso(), "scheduled_for": clock.now_iso(),
        "status": "executed", "synthetic": 1,
    })
    db.insert("execution_record", {
        "id": db.new_id("exe"), "decision_id": "dec_BREACH", "case_id": case["id"],
        "action": "SEND_UPDATE_LINK", "mode": "simulated", "status": "success",
        "result_payload": "{}", "cost_paise": 1200,
        "executed_at": clock.now_iso(), "synthetic": 1,
    })
    stats = fencing.fence_stats()
    assert stats["outreach_to_settled"] == 1
    assert stats["outreach_to_settled_case_ids"] == [case["id"]]


def test_fence_stats_is_served_on_the_summary(fresh_db):
    from app import metrics
    assert "fencing" in metrics.summary()

"""Detect: signature handling, dedupe, malformed payloads, and outcome attribution
(docs/04 §2, §7)."""
from __future__ import annotations

import json

from app import cases, db, webhooks
from tests.conftest import failure_payload, recovery_payload


def test_duplicate_event_id_is_absorbed(fresh_db):
    payload = failure_payload(event_id="SYNTH-evt-dup")
    first = webhooks.intake(payload, source="synthetic")
    second = webhooks.intake(payload, source="synthetic")
    assert first["status"] == "processed"
    assert second["status"] == "duplicate"
    assert db.scalar("SELECT COUNT(*) FROM failure_event", (), 0) == 1
    assert db.scalar("SELECT COUNT(*) FROM recovery_case", (), 0) == 1


def test_malformed_payload_without_an_error_object_does_not_crash(fresh_db):
    result = webhooks.intake(failure_payload(error=None), source="synthetic")
    assert result["status"] == "processed"
    case = cases.get(result["case_id"])
    assert case["current_category"] == "unknown"
    assert case["status"] == "stopped_unknown"


def test_payload_with_no_event_id_is_rejected_without_writing_anything(fresh_db):
    payload = failure_payload()
    payload.pop("id")
    assert webhooks.intake(payload, source="synthetic")["status"] == "rejected"
    assert db.scalar("SELECT COUNT(*) FROM failure_event", (), 0) == 0


def test_unrelated_event_types_are_ignored(fresh_db):
    payload = failure_payload(event_id="SYNTH-evt-other")
    payload["event"] = "payment.captured"
    assert webhooks.intake(payload, source="synthetic")["status"] == "ignored"
    assert db.scalar("SELECT COUNT(*) FROM recovery_case", (), 0) == 0


def test_a_second_failure_joins_the_same_open_case(fresh_db):
    webhooks.intake(failure_payload(event_id="SYNTH-evt-a"), source="synthetic")
    webhooks.intake(failure_payload(event_id="SYNTH-evt-b", pay_id="SYNTH-pay-b"), source="synthetic")
    assert db.scalar("SELECT COUNT(*) FROM recovery_case", (), 0) == 1
    assert db.scalar("SELECT COUNT(*) FROM failure_event", (), 0) == 2


def test_recovery_signal_closes_the_case_and_is_traceable(fresh_db):
    webhooks.intake(failure_payload(), source="synthetic")
    case = cases.find_latest_by_subscription("SYNTH-sub-t")
    webhooks.intake(recovery_payload(case_id=case["id"]), source="synthetic")

    case = cases.get(case["id"])
    assert case["status"] == "recovered"
    assert case["closed_at"] is not None
    last = db.query_one("SELECT * FROM audit_log WHERE case_id = ? ORDER BY seq DESC LIMIT 1",
                        (case["id"],))
    assert last["stage"] == "outcome"
    assert json.loads(last["detail"])["payment_id"] == "SYNTH-pay-ok"


def test_sending_a_link_recovers_nothing_on_its_own(fresh_db):
    """A case counts as recovered only on a recovery signal (docs/07 §2)."""
    webhooks.intake(failure_payload(
        error=("BAD_REQUEST_ERROR", "card_expired", "The card has expired", "customer")),
        source="synthetic")
    case = cases.find_latest_by_subscription("SYNTH-sub-t")
    assert db.scalar("SELECT COUNT(*) FROM execution_record WHERE case_id = ?", (case["id"],), 0) == 1
    assert case["status"] == "open"


def test_signature_verification_rejects_a_tampered_body():
    secret = "a-long-random-webhook-secret"
    body = json.dumps({"event": "payment.failed"}).encode()
    import hashlib
    import hmac
    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert webhooks.verify_signature(body, good, secret) is True
    assert webhooks.verify_signature(body + b" ", good, secret) is False
    assert webhooks.verify_signature(body, "deadbeef", secret) is False
    assert webhooks.verify_signature(body, None, secret) is False
    assert webhooks.verify_signature(body, good, "") is False

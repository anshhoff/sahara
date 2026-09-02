"""Detect — webhook receipt, signature verification, dedupe and case routing
(docs/04 §2).

`intake()` is the single ingestion path. A real Razorpay delivery and a synthetic
payload differ only in `source`; everything from diagnosis down is byte-identical
code. That is what makes the batch numbers claims about the real pipeline rather
than about a parallel test harness.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from app import audit, cases, clock, config, db, diagnosis, policy

log = logging.getLogger(__name__)

# Serialises the claim region of intake() — the dedupe check, the event insert and the
# open-case lookup — against itself. In live mode the webhook path and the tick loop
# genuinely run concurrently (main.py dispatches both through asyncio.to_thread), and
# Razorpay's at-least-once delivery means the two racing bodies are routinely the same
# event. Diagnosis and the policy decision are deliberately outside it: those can be
# slow (a model call) and are safe to run in parallel once the case exists.
#
# The lock covers one process. `failure_event.razorpay_event_id UNIQUE` covers the rest:
# a second process losing the race gets an IntegrityError, which is caught below and
# answered as a duplicate — a 200, so Razorpay stops redelivering, rather than a 500,
# which would make it try again.
_claim_lock = threading.RLock()


# ------------------------------------------------------------------- signature
def verify_signature(raw_body: bytes, signature: Optional[str], secret: Optional[str] = None) -> bool:
    """HMAC-SHA256 over the RAW body, via the official SDK helper (docs/02 §6.4).

    The raw bytes matter: re-serialising the parsed JSON changes whitespace and key
    order and the signature will not match.
    """
    secret = secret if secret is not None else config.RAZORPAY_WEBHOOK_SECRET
    if not secret or not signature:
        return False
    try:
        import razorpay

        # Utility.verify_webhook_signature is an instance method in the current SDK
        # (it takes no client), and it raises rather than returning False on mismatch.
        razorpay.Utility().verify_webhook_signature(raw_body.decode("utf-8"), signature, secret)
        return True
    except Exception as exc:
        log.warning("webhook signature rejected: %s", exc)
        return False


# ------------------------------------------------------------------ extraction
def _entity(payload: dict[str, Any], name: str) -> dict[str, Any]:
    node = (payload.get("payload") or {}).get(name) or {}
    ent = node.get("entity") if isinstance(node, dict) else None
    return ent if isinstance(ent, dict) else {}


def _epoch_to_iso(value: Any) -> Optional[str]:
    try:
        return clock.to_iso(datetime.fromtimestamp(int(value), tz=timezone.utc))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def extract_event_id(payload: dict[str, Any], headers: Optional[dict[str, str]] = None) -> Optional[str]:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    return payload.get("id") or headers.get("x-razorpay-event-id")


def extract(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull the fields the pipeline needs out of a Razorpay event body, defensively —
    a malformed payload must produce a diagnosable event, not an exception
    (edge case SYNTH-E-07)."""
    payment = _entity(payload, "payment")
    subscription = _entity(payload, "subscription")
    link = _entity(payload, "payment_link")
    notes = payment.get("notes") or link.get("notes") or subscription.get("notes") or {}
    if not isinstance(notes, dict):
        notes = {}

    error = payment.get("error") if isinstance(payment.get("error"), dict) else {}

    amount = (
        payment.get("amount")
        if payment.get("amount") is not None
        else link.get("amount")
        if link.get("amount") is not None
        else subscription.get("amount")
    )

    return {
        "event_type": payload.get("event"),
        "subscription_id": (
            payment.get("subscription_id")
            or subscription.get("id")
            or notes.get("subscription_id")
            or link.get("reference_id")
            or "unknown_subscription"
        ),
        "payment_id": payment.get("id"),
        "customer_id": (
            payment.get("customer_id")
            or subscription.get("customer_id")
            or notes.get("customer_id")
            or "unknown_customer"
        ),
        "amount_paise": int(amount) if amount is not None else 0,
        "currency": payment.get("currency") or link.get("currency") or "INR",
        "error_code": payment.get("error_code") or error.get("code"),
        "error_description": payment.get("error_description") or error.get("description"),
        "error_source": payment.get("error_source") or error.get("source"),
        "error_step": payment.get("error_step") or error.get("step"),
        "error_reason": payment.get("error_reason") or error.get("reason"),
        "occurred_at": _epoch_to_iso(payload.get("created_at"))
        or _epoch_to_iso(payment.get("created_at"))
        or clock.now_iso(),
        "case_id_hint": notes.get("case_id"),
        "opted_out": bool(payload.get("customer_opted_out")),
        "synthetic": bool(payload.get("synthetic")),
        # Arm assignment travels with the event, so it is decided once — by whoever
        # generated the batch — and never re-rolled inside the pipeline.
        "holdout": bool(payload.get("holdout")),
    }


def customer_opted_out(customer_id: str) -> bool:
    """Consent is per customer, not per case: once someone opts out, every future
    episode for them starts opted out."""
    row = db.query_one(
        "SELECT 1 FROM recovery_case WHERE customer_id = ? AND customer_opted_out = 1 LIMIT 1",
        (customer_id,),
    )
    return row is not None


# ---------------------------------------------------------------------- intake
def intake(payload: dict[str, Any], source: str = "webhook",
           headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """The one ingestion path. Idempotent on the event id."""
    event_id = extract_event_id(payload, headers)
    if not event_id:
        return {"status": "rejected", "reason": "no event id in payload or headers"}

    if db.query_one("SELECT 1 FROM failure_event WHERE razorpay_event_id = ?", (event_id,)):
        # Razorpay retries delivery on any non-2xx and can duplicate on its own.
        return {"status": "duplicate", "event_id": event_id}

    f = extract(payload)
    event_type = f["event_type"]
    if event_type not in config.FAILURE_EVENT_TYPES + config.RECOVERY_EVENT_TYPES:
        return {"status": "ignored", "event_id": event_id, "event_type": event_type}

    synthetic = f["synthetic"] or source == "synthetic"

    # ------------------------------------------------- recovery signals
    if event_type in config.RECOVERY_EVENT_TYPES:
        with _claim_lock:
            case = None
            if f["case_id_hint"]:
                case = cases.get(f["case_id_hint"])
            if case is None:
                case = cases.find_open_by_subscription(f["subscription_id"])
            try:
                _insert_event(event_id, case, f, source, synthetic, payload, event_type)
            except sqlite3.IntegrityError:
                return {"status": "duplicate", "event_id": event_id}
            if case is None or case["status"] != "open":
                return {"status": "no_open_case", "event_id": event_id, "event_type": event_type}
            return outcome_recovered(case, f, event_id)

    # ------------------------------------------------- failure signals
    # The event row is written first, with no case attached, because it is the claim
    # token: the UNIQUE event id makes the insert itself the dedupe. Opening the case
    # first would mean a lost race leaves an empty case behind — a second attempt
    # budget against a customer who only ever failed once.
    with _claim_lock:
        try:
            event = _insert_event(event_id, None, f, source, synthetic, payload, event_type)
        except sqlite3.IntegrityError:
            return {"status": "duplicate", "event_id": event_id}

        case = cases.find_open_by_subscription(f["subscription_id"])
        opened = False
        if case is None:
            case = cases.create(
                subscription_id=f["subscription_id"],
                customer_id=f["customer_id"],
                amount_at_risk_paise=f["amount_paise"],
                currency=f["currency"],
                opted_out=f["opted_out"] or customer_opted_out(f["customer_id"]),
                synthetic=synthetic,
                holdout=f["holdout"],
            )
            opened = True
        elif f["opted_out"] and not int(case["customer_opted_out"]):
            cases.set_opted_out(case["id"], True)
            case = cases.get(case["id"])

        # The one write a failure_event ever receives: case_id set once, from NULL,
        # inside the same claim. Every other column stays immutable after insert.
        db.update("failure_event", event["id"], {"case_id": case["id"]})
        event["case_id"] = case["id"]

    audit.audit(
        case["id"], "detect", "razorpay" if source == "webhook" else "system",
        (f"{event_type} received, Rs {cases.rupees(case['amount_at_risk_paise'])} at risk"
         + (" (new case opened)" if opened else " (existing open case)")),
        {
            "event_id": event["id"],
            "razorpay_event_id": event_id,
            "event_type": event_type,
            "source": source,
            "payment_id": f["payment_id"],
            "error_code": f["error_code"],
            "error_reason": f["error_reason"],
            "error_description": f["error_description"],
            "amount_paise": f["amount_paise"],
            "new_case": opened,
        },
    )

    # subscription.halted is not terminal on its own: it means Razorpay's own retries
    # are exhausted, which is precisely when this agent matters. Same path as any failure.
    diagnosis.diagnose(case, event)
    policy.decide(cases.get(case["id"]))

    return {
        "status": "processed",
        "event_id": event_id,
        "case_id": case["id"],
        "case": cases.get(case["id"]),
    }


def _insert_event(event_id: str, case: Optional[dict[str, Any]], f: dict[str, Any], source: str,
                  synthetic: bool, payload: dict[str, Any], event_type: str) -> dict[str, Any]:
    row_id = db.new_id("evt")
    db.insert(
        "failure_event",
        {
            "id": row_id,
            "case_id": case["id"] if case else None,
            "source": source,
            "razorpay_event_id": event_id,
            "event_type": event_type,
            "subscription_id": f["subscription_id"],
            "payment_id": f["payment_id"],
            "customer_id": f["customer_id"],
            "amount_paise": f["amount_paise"],
            "currency": f["currency"],
            "error_code": f["error_code"],
            "error_description": f["error_description"],
            "error_source": f["error_source"],
            "error_step": f["error_step"],
            "error_reason": f["error_reason"],
            "occurred_at": f["occurred_at"],
            "received_at": clock.now_iso(),
            "raw_payload": json.dumps(payload, sort_keys=True, default=str),
            "synthetic": 1 if synthetic else 0,
        },
    )
    return db.row_to_dict(db.query_one("SELECT * FROM failure_event WHERE id = ?", (row_id,)))


def outcome_recovered(case: dict[str, Any], f: dict[str, Any], event_id: str) -> dict[str, Any]:
    """A case counts as recovered only on a real recovery signal. Sending a link
    recovers nothing (docs/07 §2)."""
    cases.transition(
        case["id"], "recovered",
        summary=(f"Recovered: {f['event_type']} confirms Rs {cases.rupees(case['amount_at_risk_paise'])} "
                 f"collected after {case['attempt_count']} attempt(s)"),
        detail={
            "razorpay_event_id": event_id,
            "event_type": f["event_type"],
            "payment_id": f["payment_id"],
            "amount_paise": case["amount_at_risk_paise"],
            "attempts_used": case["attempt_count"],
        },
        stage="outcome",
    )
    # Any decision still waiting to run is moot once the money has arrived.
    for row in db.query(
        "SELECT id FROM intervention_decision WHERE case_id = ? AND status = 'scheduled'", (case["id"],)
    ):
        db.update("intervention_decision", row["id"], {"status": "superseded"})
    return {"status": "recovered", "case_id": case["id"], "event_id": event_id}

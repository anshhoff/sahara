"""Shared fixtures. Every test runs against a fresh temp database on a simulated
clock, so tests never touch recovery.db and never depend on wall time."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ.setdefault("LLM_PROVIDER", "none")  # tests never call a model by default
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import clock, db  # noqa: E402

T0 = datetime(2026, 3, 2, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def sim_clock():
    c = clock.SimulatedClock(T0)
    clock.set_clock(c)
    yield c
    clock.set_clock(clock.Clock())


@pytest.fixture()
def fresh_db(tmp_path, sim_clock):
    path = str(tmp_path / "test.db")
    db.reset(path)
    yield path
    db.close()


def failure_payload(*, event_id="SYNTH-evt-t-1", sub_id="SYNTH-sub-t", cust_id="SYNTH-cust-t",
                    pay_id="SYNTH-pay-t", amount=49900, error=("BAD_REQUEST_ERROR", "payment_failed",
                                                               "Your card has insufficient funds", "bank"),
                    event_type="payment.failed", opted_out=False, occurred=None):
    when = occurred or T0
    entity = {
        "id": pay_id, "entity": "payment", "amount": amount, "currency": "INR",
        "status": "failed", "method": "card", "customer_id": cust_id,
        "created_at": int(when.timestamp()),
        "notes": {"subscription_id": sub_id, "customer_id": cust_id},
    }
    if error is not None:
        code, reason, description, source = error
        entity.update({"error_code": code, "error_reason": reason,
                       "error_description": description, "error_source": source,
                       "error_step": "payment_authorization"})
    return {
        "entity": "event", "event": event_type, "contains": ["payment", "subscription"],
        "payload": {
            "payment": {"entity": entity},
            "subscription": {"entity": {"id": sub_id, "entity": "subscription",
                                        "customer_id": cust_id, "status": "active"}},
        },
        "created_at": int(when.timestamp()), "id": event_id,
        "synthetic": True, "customer_opted_out": opted_out,
    }


def recovery_payload(*, event_id="SYNTH-evt-t-ok", sub_id="SYNTH-sub-t", cust_id="SYNTH-cust-t",
                     amount=49900, event_type="subscription.charged", case_id=None):
    now = clock.now()
    notes = {"subscription_id": sub_id, "customer_id": cust_id}
    if case_id:
        notes["case_id"] = case_id
    return {
        "entity": "event", "event": event_type, "contains": ["payment"],
        "payload": {"payment": {"entity": {
            "id": "SYNTH-pay-ok", "entity": "payment", "amount": amount, "currency": "INR",
            "status": "captured", "customer_id": cust_id, "created_at": int(now.timestamp()),
            "notes": notes}}},
        "created_at": int(now.timestamp()), "id": event_id, "synthetic": True,
    }

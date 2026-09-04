"""Live demo — a real one-time Razorpay Order + Checkout, used to produce a
genuinely Razorpay-dispatched `payment.failed` webhook without needing Subscriptions'
e-mandate registration (which errors internally in this account's test mode:
SERVER_ERROR at step card_mandate_process — see pay_TXWdYXtMuatTy2).

Every order gets a unique `subscription_id` note so it opens its own case rather
than colliding with previous demo runs; Razorpay copies an order's notes onto the
payment it produces, which is how `webhooks.extract()` recovers it (app/webhooks.py
falls back to `notes.subscription_id` / `notes.customer_id` for exactly this reason).

Pay the resulting order with UPI ID `failure@razorpay` to get a real, signed
`payment.failed` delivery; pay whatever Payment Link the executor then creates with
`success@razorpay` to trigger `payment_link.paid` and watch the case recover.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from app import cases, clock, config, db, executor, webhooks

router = APIRouter(prefix="/api/demo", tags=["live-demo"])

DEMO_AMOUNT_PAISE = 49900  # Rs 499, matching the synthetic batch's typical ticket size


def _resolve_case_id(demo_id: Optional[str], case_id: Optional[str]) -> Optional[str]:
    """The pipeline page is opened two ways: from a real checkout order (which only
    knows its own demo_id note, not yet which case it opened) and from a direct
    category injection (which already has the case_id `/api/control/inject` handed
    back). Either is enough to find the case to watch."""
    if case_id:
        return case_id
    if demo_id:
        row = db.query_one(
            "SELECT id FROM recovery_case WHERE subscription_id = ? ORDER BY created_at DESC LIMIT 1",
            (demo_id,),
        )
        return row["id"] if row else None
    return None


@router.get("/config")
def demo_config() -> dict[str, Any]:
    return {
        "configured": executor.razorpay_configured(),
        "key_id": config.RAZORPAY_KEY_ID or None,
        "amount_paise": DEMO_AMOUNT_PAISE,
    }


@router.post("/order")
def create_demo_order() -> dict[str, Any]:
    """A real Razorpay Order for a one-time payment. No subscription, no mandate
    registration — the part of this account's test mode that is actually broken."""
    if not executor.razorpay_configured():
        raise HTTPException(status_code=400, detail="RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set")

    demo_id = f"demo_{uuid.uuid4().hex[:12]}"
    order = executor.razorpay_client().order.create(
        {
            "amount": DEMO_AMOUNT_PAISE,
            "currency": "INR",
            "notes": {
                "synthetic": "false",
                "case_source": "live-demo",
                "subscription_id": demo_id,
                "customer_id": demo_id,
            },
        }
    )
    return {
        "order_id": order["id"],
        "amount": order["amount"],
        "currency": order["currency"],
        "key_id": config.RAZORPAY_KEY_ID,
        "demo_id": demo_id,
    }


@router.get("/case")
def demo_case(demo_id: Optional[str] = None, case_id: Optional[str] = None) -> dict[str, Any]:
    """Poll target for the pipeline page: resolves either a demo_id (a real checkout
    order's subscription_id note) or a case_id (handed back directly by
    /api/control/inject) to whatever case it points at, and returns the same rich
    payload as GET /api/cases/{id} so the page can render the pipeline live."""
    resolved = _resolve_case_id(demo_id, case_id)
    if resolved is None:
        return {"case": None}

    from app.api import get_case  # deferred: api.py has no reason to import live_demo

    return get_case(resolved)


@router.post("/force-tick")
def demo_force_tick(demo_id: Optional[str] = None, case_id: Optional[str] = None) -> dict[str, Any]:
    """Demo-only clock skip: a real `tick()` only executes decisions already past
    their real scheduled_for, and RETRY_LATER is genuinely scheduled hours out — the
    same wall-clock discipline the live loop runs under (see main.py's tick loop).

    Waiting out real hours mid-demo isn't practical, so this does exactly what
    manually editing scheduled_for and re-running tick would do: it pulls this one
    case's still-scheduled decision to now, then calls the same executor.tick() the
    background loop calls. Nothing about execute_decision's own logic — invariant
    re-checks, ExecutionRecord writing, attempt reservation — is bypassed."""
    resolved = _resolve_case_id(demo_id, case_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="no case found for that demo_id/case_id")

    scheduled = db.query(
        "SELECT id FROM intervention_decision WHERE case_id = ? AND status = 'scheduled'",
        (resolved,),
    )
    if not scheduled:
        return {"pulled_forward": 0, "tick": executor.tick()}

    now_iso = clock.to_iso(clock.now())
    for r in scheduled:
        db.update("intervention_decision", r["id"], {"scheduled_for": now_iso})

    return {"pulled_forward": len(scheduled), "tick": executor.tick()}


def _recovery_payload(case_id: str, amount_paise: int) -> dict[str, Any]:
    """A `payment_link.paid` body shaped like Razorpay's real one, addressed at a
    specific case via `notes.case_id` — the same field `webhooks.extract()` reads as
    `case_id_hint` for a recovery signal. Marked synthetic for the same reason
    `app.control._demo_payload` is: it can never be counted as live recovery."""
    now = int(clock.now().timestamp())
    event_id = f"evt_DEMO{uuid.uuid4().hex[:8].upper()}"
    return {
        "entity": "event", "event": "payment_link.paid", "contains": ["payment_link", "payment"],
        "payload": {
            "payment_link": {"entity": {
                "id": f"plink_DEMO{event_id[-8:]}", "entity": "payment_link",
                "amount": amount_paise, "currency": "INR", "status": "paid",
                "notes": {"case_id": case_id, "synthetic": "true"},
            }},
            "payment": {"entity": {
                "id": f"pay_DEMO{event_id[-8:]}", "entity": "payment",
                "amount": amount_paise, "currency": "INR", "status": "captured",
            }},
        },
        "created_at": now, "id": event_id, "synthetic": True,
    }


@router.post("/simulate")
def demo_simulate(
    outcome: str, demo_id: Optional[str] = None, case_id: Optional[str] = None
) -> dict[str, Any]:
    """Stands in for the next real-world signal a live Sahara would eventually get on
    its own: whether a silent retry actually cleared, or whether the customer paid (or
    ignored) an update link. Enters through the identical `webhooks.intake()` every
    real delivery uses — `source="synthetic"` is the only difference — exactly like
    `/api/control/inject`, just addressed at an existing case instead of opening a
    fresh one.
    """
    if outcome not in ("recovered", "failed_again"):
        raise HTTPException(status_code=400, detail="outcome must be 'recovered' or 'failed_again'")

    resolved = _resolve_case_id(demo_id, case_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="no case found for that demo_id/case_id")

    case = cases.get(resolved)
    if case is None:
        raise HTTPException(status_code=404, detail=f"unknown case {resolved}")
    if case["status"] != "open":
        raise HTTPException(status_code=409, detail=f"case is {case['status']}, not open")

    if outcome == "recovered":
        payload = _recovery_payload(resolved, int(case["amount_at_risk_paise"]))
    else:
        from app.control import _DEMO_ERRORS, _demo_payload  # deferred: avoid a control<->live_demo import cycle

        category = case["current_category"] or "unknown"
        payload = _demo_payload(
            event_id=f"evt_DEMO{uuid.uuid4().hex[:8].upper()}",
            sub_id=case["subscription_id"],
            cust_id=case["customer_id"],
            amount_paise=int(case["amount_at_risk_paise"]),
            error=_DEMO_ERRORS[category],
        )

    result = webhooks.intake(payload, source="synthetic")

    from app.api import get_case  # deferred: api.py has no reason to import live_demo

    return {"outcome": outcome, "intake_status": result["status"], **get_case(resolved)}

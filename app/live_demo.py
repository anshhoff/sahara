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

import threading
import time
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


# ------------------------------------------------------------------- scripted run
"""The one-click run.

Everything above this line is a manual control: create an order, pull a decision
forward, tell the case what happened next. Demoing with them means eight-odd
interactions across two windows, each one a chance to fumble in front of a panel, and
it reliably overruns five minutes.

`POST /api/demo/run` performs that same sequence on a background thread and narrates
it. It adds no new path into the pipeline: every failure still enters through
`webhooks.intake()`, every execution still goes through `executor.tick()`, and every
gate is still evaluated by its own unmodified code. The two things the script is
allowed to do are the two a human operator was doing by hand — press the button, and
skip the wait.

Skipping the wait is the part worth stating precisely. The waits here are real and
they are the product: RETRY_LATER is scheduled 12-72h out by the policy table, and
invariant I2 holds 24h between two customer contacts. The script does not shorten
them and does not edit the records that encode them — moving `last_contact_at` to
make I2 pass would be forging the evidence that I2 works. It moves the *clock*
(`clock.OffsetClock`), so each gate is evaluated exactly as it would be tomorrow,
against code that never learns it is in a demo.

One consequence, stated rather than hidden: the clock is process-global and
`tick()` is not per-case, so any other case with a decision due inside the skipped
window executes too. That is what would have happened had the operator waited.
"""

_run_lock = threading.Lock()
_run: Optional["DemoRun"] = None

# Wall-clock seconds between beats. The pipeline page polls at 1s, so anything under
# that renders as a jump-cut; much over it and the whole run stops fitting in a demo
# slot. Four beats per attempt at ~2.5s is roughly 90 seconds for three attempts.
BEAT_SECONDS = 2.5


class DemoRun:
    """One scripted run, and the narration a viewer reads while it happens."""

    def __init__(self, case_id: str, category: str, final_outcome: str):
        self.id = f"run_{uuid.uuid4().hex[:12]}"
        self.case_id = case_id
        self.category = category
        self.final_outcome = final_outcome
        self.status = "running"          # running | finished | error
        self.error: Optional[str] = None
        self.started_at = time.time()
        self.beats: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def beat(self, label: str, detail: str = "", *, skipped_hours: float = 0.0) -> None:
        with self._lock:
            self.beats.append({
                "n": len(self.beats),
                "label": label,
                "detail": detail,
                "elapsed_seconds": round(time.time() - self.started_at, 1),
                "skipped_hours": round(skipped_hours, 1),
                "demo_now": clock.now_iso(),
            })

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "run_id": self.id,
                "case_id": self.case_id,
                "category": self.category,
                "status": self.status,
                "error": self.error,
                "elapsed_seconds": round(time.time() - self.started_at, 1),
                "beats": list(self.beats),
            }


def _skip_to_next_gate(run: DemoRun) -> float:
    """Move the demo clock to the first moment the next step could legitimately run.

    Three things can hold a decision back and all three are consulted, because
    clearing one and tripping another is how a scripted demo stalls on stage:

      * the decision's own `scheduled_for` — RETRY_LATER's 12-72h
      * I2's contact cooldown, measured from the case's `last_contact_at`
      * I5's quiet hours, which defer a contact to 09:00 IST rather than by a duration

    Returns the hours skipped, so the narration can say how much time it stood in for.
    """
    clk = clock.get_clock()
    if not isinstance(clk, clock.OffsetClock):   # nothing to move; a real tick will wait
        return 0.0

    before = clk.now()
    target = before

    row = db.query_one(
        "SELECT scheduled_for FROM intervention_decision "
        "WHERE case_id = ? AND status = 'scheduled' ORDER BY scheduled_for DESC LIMIT 1",
        (run.case_id,),
    )
    if row and row["scheduled_for"]:
        target = max(target, clock.parse_iso(row["scheduled_for"]))

    case = cases.get(run.case_id)
    if case and case.get("last_contact_at"):
        target = max(target, clock.plus_hours(
            clock.parse_iso(case["last_contact_at"]), config.COOLDOWN_HOURS))

    # A minute past the boundary, not exactly on it: `due_decisions` compares
    # `scheduled_for <= now` on second precision and landing on the same second is a
    # coin flip nobody should have to debug mid-demo.
    if target > before:
        clk.skip(seconds=(target - before).total_seconds() + 60)

    if config.QUIET_HOURS_ENABLED and clock.in_quiet_hours(clk.now()):
        wake = clock.next_ist_hour(clk.now(), config.QUIET_HOURS_END_IST)
        clk.skip(seconds=(wake - clk.now()).total_seconds() + 60)

    return (clk.now() - before).total_seconds() / 3600.0


def _drive(run: DemoRun, pace: float) -> None:
    """Walk one case from its opening failure to whatever ends it."""
    previous = clock.get_clock()
    clock.set_clock(clock.OffsetClock())
    try:
        case = cases.get(run.case_id)
        run.beat(
            "failure detected",
            f"case {run.case_id} opened, diagnosed as "
            f"{(case or {}).get('current_category') or 'pending'}",
        )

        # One iteration per rung of the ladder. The bound is MAX_ATTEMPTS + 1 because
        # the final pass is the one where the policy has nothing left to offer and I1
        # stops the case — the step most worth showing, and the easiest to loop past.
        for rung in range(1, config.MAX_ATTEMPTS + 2):
            case = cases.get(run.case_id)
            if case is None or case["status"] != "open":
                break

            time.sleep(pace)
            skipped = _skip_to_next_gate(run)
            if skipped >= 0.5:
                run.beat("clock skipped", f"stood in for {skipped:.0f}h of real waiting",
                         skipped_hours=skipped)

            # Contact rungs carry delay 0 and are executed inline at decide time, so a
            # tick that reports zero executions is the normal case for them, not a
            # stall. The decision's own status is the thing worth narrating.
            executor.tick()
            row = db.query_one(
                "SELECT action, status, attempt_number FROM intervention_decision "
                "WHERE case_id = ? ORDER BY attempt_number DESC, decided_at DESC LIMIT 1",
                (run.case_id,),
            )
            decision = db.row_to_dict(row) if row is not None else {}
            run.beat(
                f"attempt {decision.get('attempt_number') or rung}",
                f"{decision.get('action') or 'no decision'} — "
                f"{decision.get('status') or 'none'}",
            )

            case = cases.get(run.case_id)
            if case is None or case["status"] != "open":
                break

            time.sleep(pace)
            # The next real-world signal. In a live deployment this is Razorpay
            # telling us whether the retry cleared or the customer paid the link;
            # here it is the one thing the script has to stand in for, and it enters
            # through the same `intake()` either way.
            last = rung >= config.MAX_ATTEMPTS
            outcome = "recovered" if (last and run.final_outcome == "recovered") else "failed_again"
            demo_simulate(outcome, case_id=run.case_id)
            run.beat(
                "customer signal",
                "payment cleared" if outcome == "recovered" else "failed again",
            )

        case = cases.get(run.case_id)
        run.beat("case closed", f"final status: {(case or {}).get('status') or 'unknown'}")
        run.status = "finished"
    except Exception as exc:  # noqa: BLE001 — a failed run must be readable, not silent
        run.status = "error"
        run.error = f"{type(exc).__name__}: {exc}"
        run.beat("run failed", run.error)
    finally:
        clock.set_clock(previous)


@router.post("/run")
def demo_run(
    category: str = "issuer_declined",
    amount_rupees: float = 499.0,
    final_outcome: str = "failed_again",
    pace_seconds: float = BEAT_SECONDS,
) -> dict[str, Any]:
    """Start the scripted run and hand back the case immediately, so the caller can
    open the pipeline view on it and watch the rest arrive through the normal poll.

    `issuer_declined` is the default because its policy row is the only one that uses
    three different rungs — RETRY_LATER, then SEND_UPDATE_LINK, then VOICE_CALL — so a
    single run shows the whole ladder rather than the same action three times.
    """
    global _run
    if not config.CONTROL_ENABLED:
        raise HTTPException(status_code=404, detail="control API disabled")
    if category not in config.CATEGORIES:
        raise HTTPException(status_code=400,
                            detail=f"category must be one of {list(config.CATEGORIES)}")
    if final_outcome not in ("recovered", "failed_again"):
        raise HTTPException(status_code=400,
                            detail="final_outcome must be 'recovered' or 'failed_again'")

    with _run_lock:
        if _run is not None and _run.status == "running":
            raise HTTPException(status_code=409,
                                detail=f"a run is already in progress ({_run.id})")

        from app.control import InjectRequest, inject  # deferred: avoids an import cycle

        opened = inject(InjectRequest(category=category, amount_rupees=amount_rupees))
        case_id = opened.get("case_id")
        if not case_id:
            raise HTTPException(status_code=500,
                                detail=f"intake returned '{opened.get('status')}' and opened no case")

        _run = DemoRun(case_id, category, final_outcome)
        # Daemon: a run holds no resource worth blocking shutdown for, and the clock it
        # swaps is restored in its own `finally` on the way out.
        threading.Thread(target=_drive, args=(_run, max(0.0, pace_seconds)),
                         daemon=True, name="demo-run").start()
        return {**_run.snapshot(), "watch": f"/pipeline?case_id={case_id}"}


@router.get("/run")
def demo_run_status() -> dict[str, Any]:
    """The narration track for the run in progress, or the last one that finished."""
    return _run.snapshot() if _run is not None else {"status": "idle", "beats": []}


@router.get("/order-info")
def demo_order_info(order_id: str) -> dict[str, Any]:
    """What the /pay page needs to open Checkout on an Order the executor created.

    Read-only and by id: the page is handed a link, not a key, and this is the one
    call that turns that link into a payable Checkout without the front end ever
    holding a secret.
    """
    if not executor.razorpay_configured():
        raise HTTPException(status_code=400, detail="Razorpay not configured")
    try:
        order = executor.razorpay_client().order.fetch(order_id)
    except Exception as exc:  # noqa: BLE001 — the reason is what the page must show
        raise HTTPException(status_code=404, detail=f"{type(exc).__name__}: {exc}")
    return {
        "order_id": order.get("id"),
        "amount": order.get("amount"),
        "currency": order.get("currency"),
        "status": order.get("status"),
        "key_id": config.RAZORPAY_KEY_ID,
        "case_id": (order.get("notes") or {}).get("case_id"),
    }

"""RecoveryCase — the aggregate root (docs/03 §3) and its status state machine.

This module is the one place a case's status changes. `transition()` rejects every
edge not drawn in docs/03 §3 and writes the closing audit entry itself, so a closed
case can never be reopened or silently re-labelled.

(Module note: docs/01 §3 lists eight app modules; this ninth one exists only to keep
`transition()` out of both db.py and audit.py without an import cycle.)
"""
from __future__ import annotations

from typing import Any, Optional

from app import audit, clock, config, db

# open is the only non-terminal status; every terminal status is reachable from it
# and from nowhere else.
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset(config.TERMINAL_STATUSES),
    **{s: frozenset() for s in config.TERMINAL_STATUSES},
}

# Which audit stage records the arrival at a terminal status.
_CLOSING_STAGE = {"recovered": "outcome"}


def create(
    *,
    subscription_id: str,
    customer_id: str,
    amount_at_risk_paise: int,
    currency: str = "INR",
    opted_out: bool = False,
    synthetic: bool = False,
    holdout: bool = False,
) -> dict[str, Any]:
    now = clock.now_iso()
    case_id = db.new_id("case")
    db.insert(
        "recovery_case",
        {
            "id": case_id,
            "subscription_id": subscription_id,
            "customer_id": customer_id,
            "customer_opted_out": 1 if opted_out else 0,
            "amount_at_risk_paise": int(amount_at_risk_paise),
            "currency": currency,
            "status": "open",
            "attempt_count": 0,
            "last_contact_at": None,
            "current_category": None,
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
            "synthetic": 1 if synthetic else 0,
            "is_holdout": 1 if holdout else 0,
        },
    )
    return get(case_id)


def get(case_id: str) -> Optional[dict[str, Any]]:
    """Always reload from the database — no gate ever trusts in-memory case state."""
    return db.row_to_dict(db.query_one("SELECT * FROM recovery_case WHERE id = ?", (case_id,)))


def find_open_by_subscription(subscription_id: str) -> Optional[dict[str, Any]]:
    return db.row_to_dict(
        db.query_one(
            "SELECT * FROM recovery_case WHERE subscription_id = ? AND status = 'open'"
            " ORDER BY created_at DESC LIMIT 1",
            (subscription_id,),
        )
    )


def find_latest_by_subscription(subscription_id: str) -> Optional[dict[str, Any]]:
    return db.row_to_dict(
        db.query_one(
            "SELECT * FROM recovery_case WHERE subscription_id = ? ORDER BY created_at DESC LIMIT 1",
            (subscription_id,),
        )
    )


def touch(case_id: str, **fields: Any) -> dict[str, Any]:
    """Update non-status fields. Status changes must go through transition()."""
    if "status" in fields:
        raise ValueError("status changes must go through transition()")
    fields["updated_at"] = clock.now_iso()
    db.update("recovery_case", case_id, fields)
    return get(case_id)


def set_category(case_id: str, category: str) -> dict[str, Any]:
    if category not in config.CATEGORIES:
        raise ValueError(f"category {category!r} outside the fixed enum")
    return touch(case_id, current_category=category)


def set_opted_out(case_id: str, opted_out: bool = True) -> dict[str, Any]:
    return touch(case_id, customer_opted_out=1 if opted_out else 0)


# The increment is computed by SQLite, never in Python. A read-modify-write here
# would let two concurrent callers both read the same attempt_count and both write
# count+1, losing an attempt and taking I1 with it. last_contact_at is written in the
# same statement, guarded by a flag, so a contact action can never bump the counter
# without also arming the I2 cooldown.
_SET_ATTEMPT = (
    " SET attempt_count = attempt_count + 1,"
    "     updated_at = ?,"
    "     last_contact_at = CASE WHEN ? THEN ? ELSE last_contact_at END"
)
_RESERVE_ATTEMPT_SQL = (
    "UPDATE recovery_case" + _SET_ATTEMPT
    + " WHERE id = ? AND status = 'open' AND attempt_count < ?"
)
_RECORD_ATTEMPT_SQL = "UPDATE recovery_case" + _SET_ATTEMPT + " WHERE id = ?"


def _attempt_params(action: str) -> list[Any]:
    now = clock.now_iso()
    return [now, 1 if action in config.CONTACT_ACTIONS else 0, now]


def reserve_attempt(case_id: str, action: str) -> bool:
    """Atomically claim one attempt slot, or refuse. True iff this caller now owns it.

    This is I1's real enforcement point. `invariants.check` reads attempt_count and
    then returns, so between that read and the execution another thread can execute
    and increment — in live mode the webhook path and the tick loop genuinely run
    concurrently (main.py dispatches both through asyncio.to_thread). A gate that only
    reads cannot close that window; one conditional UPDATE can, because SQLite applies
    it atomically.

    Losing the race means rowcount == 0 and no attempt is consumed, so the caller must
    not execute. The slot is claimed *before* the intervention runs rather than after,
    which is also the honest ordering: a failed execution has still spent an attempt,
    and that is exactly what the previous post-hoc increment recorded.
    """
    cur = db.execute(_RESERVE_ATTEMPT_SQL, _attempt_params(action) + [case_id, config.MAX_ATTEMPTS])
    return cur.rowcount == 1


def record_execution(case_id: str, action: str) -> dict[str, Any]:
    """Unconditionally bump attempt_count (and last_contact_at for contact actions).

    Drives a case's counters directly in tests and fixtures. The executor uses
    reserve_attempt() instead, because it must be refused at the cap rather than
    pushed past it.
    """
    db.execute(_RECORD_ATTEMPT_SQL, _attempt_params(action) + [case_id])
    return get(case_id)


def transition(case_id: str, new_status: str, *, summary: str, detail: dict[str, Any] | None = None,
               actor: str = "system", stage: Optional[str] = None) -> dict[str, Any]:
    case = get(case_id)
    if case is None:
        raise ValueError(f"unknown case {case_id}")
    current = case["status"]
    if new_status == current:
        return case
    if new_status not in LEGAL_TRANSITIONS.get(current, frozenset()):
        raise ValueError(f"illegal transition {current} -> {new_status} for {case_id}")

    now = clock.now_iso()
    db.update("recovery_case", case_id, {"status": new_status, "closed_at": now, "updated_at": now})

    stage = stage or _CLOSING_STAGE.get(new_status, "stop")
    payload = dict(detail or {})
    payload.setdefault("from_status", current)
    payload.setdefault("to_status", new_status)
    audit.audit(case_id, stage, actor, summary, payload)
    return get(case_id)


def rupees(paise: int | float | None) -> float:
    return round((paise or 0) / 100, 2)

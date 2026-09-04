"""RecoveryCase — the aggregate root (docs/03 §3) and its status state machine.

This module is the one place a case's status changes. `transition()` rejects every
edge not drawn in docs/03 §3 and writes the closing audit entry itself, so a closed
case can never be reopened or silently re-labelled.

(Module note: docs/01 §3 lists eight app modules; this ninth one exists only to keep
`transition()` out of both db.py and audit.py without an import cycle.)
"""
from __future__ import annotations

import json
from datetime import timedelta
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


# ------------------------------------------------------------------ suppression
# The per-case opt-out flag above is what invariant I3 reads. This table is what I6
# reads, and the difference is scope: an opt-out belongs to a case, a suppression
# belongs to a person. Someone who asks to be left alone about one failing
# subscription has not asked to be left alone about only that one subscription.
#
# Writes live here rather than in invariants.py so that module keeps its independence
# claim — it reads this table and imports nothing new to do it.
def suppress_customer(customer_id: str, reason: str = "opt_out", *, source: str = "system",
                      note: Optional[str] = None) -> dict[str, Any]:
    """Add a customer to the suppression list, idempotently.

    `INSERT OR IGNORE`, not upsert: the FIRST reason a customer was suppressed is the
    one that matters, and a later, weaker reason must never overwrite a complaint.
    """
    if reason not in ("opt_out", "complaint", "manual"):
        raise ValueError(f"unknown suppression reason {reason!r}")
    db.execute(
        "INSERT OR IGNORE INTO suppression (customer_id, reason, source, note, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (customer_id, reason, source, note, clock.now_iso()),
    )
    return db.row_to_dict(
        db.query_one("SELECT * FROM suppression WHERE customer_id = ?", (customer_id,)))


def suppressed_customers() -> list[dict[str, Any]]:
    return db.rows_to_dicts(db.query("SELECT * FROM suppression ORDER BY created_at DESC"))


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


# ------------------------------------------------------------------- promises
# `send_promise_offer` sets a 72-hour window and calls the result a promise. It is not
# one: nothing the customer said is recorded anywhere, so there is no promise to keep
# or to break — only an offer that expired. What follows is the other thing. A date the
# customer ACTUALLY NAMED, scheduled to, swept on lapse, and resolved as kept or broken.
#
# The reading that produces it (app/inbound.py) carries an intent and a date and NO
# AMOUNT, and neither does the table. That is deliberate and it is structural: a
# compromised model cannot make this system state a wrong rupee figure because there is
# nowhere for the figure to travel.


def promise_deadline(promised_date: str) -> str:
    """The end of the day the customer named, in IST.

    Not the start: someone who says "Friday" has until Friday is over, and marking them
    broken at midnight UTC — 05:30 on Friday morning in Delhi — would be a bug that
    only ever punished the customer.
    """
    day = clock.parse_iso(promised_date + "T00:00:00+05:30")
    return clock.to_iso(clock.plus_hours(day, 24) - timedelta(seconds=1))


def record_promise(case_id: str, reading: dict[str, Any], *,
                   execution_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Persist a dated promise from an inbound reading. Returns None if there is none.

    One open promise per case, ever. A second reading on the same case supersedes
    nothing — it is simply not recorded, because a customer who names two dates has
    given us one promise and one revision, and treating the revision as a fresh promise
    would let a case accumulate an unbounded number of things to break.
    """
    if reading.get("intent") != "will_pay_on_date" or not reading.get("promised_date"):
        return None
    if db.query_one("SELECT 1 FROM promise WHERE case_id = ? AND status = 'open'", (case_id,)):
        return None

    case = get(case_id)
    if case is None:
        return None
    promise_id = db.new_id("prom")
    due_at = promise_deadline(reading["promised_date"])
    db.insert("promise", {
        "id": promise_id,
        "case_id": case_id,
        "execution_id": execution_id,
        "intent": reading["intent"],
        "promised_date": reading["promised_date"],
        "source": reading.get("source") or "rule",
        "reading_confidence": float(reading.get("confidence") or 0.0),
        "reading_raw": json.dumps(reading, sort_keys=True, default=str),
        "status": "open",
        "created_at": clock.now_iso(),
        "due_at": due_at,
        "resolved_at": None,
        "synthetic": case["synthetic"],
    })
    audit.audit(
        case_id, "execute", "system",
        f"Promise recorded: the customer said they would pay on {reading['promised_date']}",
        {
            "promise_id": promise_id,
            "promised_date": reading["promised_date"],
            "due_at": due_at,
            "reading_source": reading.get("source"),
            "reading_confidence": reading.get("confidence"),
            "rationale": reading.get("rationale"),
            "execution_id": execution_id,
            "note": ("the date is what the customer named; the amount is not from them and "
                     "never can be — the inbound schema has no field for one"),
        },
    )
    return db.row_to_dict(db.query_one("SELECT * FROM promise WHERE id = ?", (promise_id,)))


def open_promise(case_id: str) -> Optional[dict[str, Any]]:
    return db.row_to_dict(db.query_one(
        "SELECT * FROM promise WHERE case_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 1",
        (case_id,)))


def resolve_promises(case_id: str, status: str) -> int:
    """Close every open promise on a case as kept or broken. Returns how many moved."""
    if status not in ("kept", "broken"):
        raise ValueError(f"a promise resolves as kept or broken, not {status!r}")
    rows = db.query("SELECT id FROM promise WHERE case_id = ? AND status = 'open'", (case_id,))
    for row in rows:
        db.update("promise", row["id"], {"status": status, "resolved_at": clock.now_iso()})
    return len(rows)


def due_promises(include_synthetic: bool = True) -> list[dict[str, Any]]:
    """Open promises whose named day is over."""
    return db.rows_to_dicts(db.query(
        "SELECT * FROM promise WHERE status = 'open' AND due_at <= ?"
        + ("" if include_synthetic else " AND synthetic = 0")
        + " ORDER BY due_at ASC",
        (clock.now_iso(),)))


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

    # A promise resolves with the case it was made about. Recovering keeps it; any
    # other terminal state breaks it. Doing this inside transition() rather than at each
    # call site is what makes it impossible to close a case and leave a promise dangling
    # open forever, which would quietly inflate promises_kept by never counting the
    # failures.
    if db.query_one("SELECT 1 FROM promise WHERE case_id = ? AND status = 'open'", (case_id,)):
        resolve_promises(case_id, "kept" if new_status == "recovered" else "broken")

    stage = stage or _CLOSING_STAGE.get(new_status, "stop")
    payload = dict(detail or {})
    payload.setdefault("from_status", current)
    payload.setdefault("to_status", new_status)
    audit.audit(case_id, stage, actor, summary, payload)
    return get(case_id)


def rupees(paise: int | float | None) -> float:
    return round((paise or 0) / 100, 2)

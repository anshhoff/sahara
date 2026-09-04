"""Look before you leap — the fences around a dispatch (docs/04 §6.4).

Every gate in `invariants.py` answers *"are we allowed to do this?"*. This module
answers a different and equally load-bearing question: **"is this still true?"**

Between the moment a decision is made and the moment it acts, the world moves. The
customer pays. The issuer stops declining. Razorpay's own retry succeeds. Nothing in
the invariants notices, because none of them re-reads the world — they re-read the
*case*, which is our record of the world and is exactly as stale as the last webhook
we happened to receive. Without a fence, this system can dun someone who settled an
hour ago, and it would have a perfect audit trail proving it did so correctly.

Three fences, at three moments:

  1. `guard_dispatch()`     — before any contact leaves. Halted means "do not send".
  2. `verify_after_write()` — after a payment link exists. Too late to not create it;
                              not too late to cancel it and say so.
  3. `decision_fingerprint()` / `inference_unchanged()` — around the model call, so a
                              recommendation computed against state that has since
                              changed is not acted on.

**The design rule: a fence degrades, it never raises.** A transport fault inside a
fence is caught, logged, and reported as data. A fence that can crash the pipeline is
strictly worse than no fence at all, and Razorpay's test-mode rate limits make that a
real path rather than a theoretical one. When a fence cannot reach the truth it returns
`UNVERIFIED`, which does **not** block — and it is recorded, so "we did not check" is
never silently indistinguishable from "we checked and it was fine".

Every evaluation is written to `dispatch_fence`, verdict and all. That table is the
denominator: *"outreach to already-settled customers: 0 of N dispatches fenced"*. A zero
with no denominator attests to nothing.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from app import audit, clock, config, db

log = logging.getLogger(__name__)

# ------------------------------------------------------------------- verdicts
CLEAR = "clear"              # the world still matches the decision; proceed
SETTLED = "settled"          # the money already arrived; do not contact
CHANGED = "changed"          # decision-relevant state moved under the inference
UNVERIFIED = "unverified"    # the fence could not reach the truth; proceed, recorded

BLOCKING = frozenset({SETTLED, CHANGED})

PRE_DISPATCH = "pre_dispatch"
POST_DISPATCH = "post_dispatch"
INFERENCE = "inference"

# Razorpay subscription statuses that mean the charge cycle is no longer outstanding.
# `cancelled` and `expired` are here for the same reason as `active`: whatever else
# they mean, they mean there is nothing for a dunning message to collect.
SETTLED_SUBSCRIPTION_STATUSES = frozenset({"active", "completed", "cancelled", "expired"})


class FenceResult:
    """A verdict plus everything needed to defend it afterwards."""

    __slots__ = ("verdict", "source", "reason", "detail")

    def __init__(self, verdict: str, source: str, reason: str = "", detail: Optional[dict] = None):
        self.verdict = verdict
        self.source = source
        self.reason = reason
        self.detail = detail or {}

    @property
    def blocks(self) -> bool:
        return self.verdict in BLOCKING

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"FenceResult({self.verdict}, {self.source}, {self.reason!r})"


# --------------------------------------------------------------- the re-fetch
def _razorpay_status(subscription_id: str) -> Optional[str]:
    """Ask Razorpay. Returns None when there is nothing to ask or the ask failed —
    never raises, per the module's design rule."""
    from app import executor  # local: fencing must not be part of executor's import cycle

    if not executor.razorpay_configured():
        return None
    try:
        sub = executor.razorpay_client().subscription.fetch(subscription_id)
        return (sub or {}).get("status")
    except Exception as exc:
        log.warning("fence: subscription re-fetch failed for %s: %s", subscription_id, exc)
        return None


def _ledger_settlement(case: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The local re-fetch: has a settlement signal for this subscription arrived since
    this case opened?

    This is not the same question `execute_decision` already asks. That one reloads the
    *case* and stops if it is no longer open. A recovery event can be received, stored,
    and never close this case — it named a `case_id` in its notes that pointed at a
    different, already-closed episode, or it arrived when `find_open_by_subscription`
    matched nothing. `intake()` answers those `no_open_case` and moves on, correctly,
    because ingestion is not the place to guess. The evidence stays in `failure_event`,
    which is immutable after insert, and this is where it gets read.
    """
    row = db.query_one(
        "SELECT id, razorpay_event_id, event_type, received_at, payment_id, amount_paise"
        " FROM failure_event"
        " WHERE subscription_id = ? AND event_type IN ('subscription.charged','payment_link.paid')"
        "   AND received_at >= ?"
        " ORDER BY received_at ASC LIMIT 1",
        (case["subscription_id"], case["created_at"]),
    )
    return db.row_to_dict(row)


def refetch(case: dict[str, Any]) -> FenceResult:
    """Re-read the world for one case, from the best source available.

    Razorpay first when it is configured and the case is real; the local event ledger
    otherwise, and also as the answer for every synthetic case — a synthetic
    subscription id does not exist at Razorpay, and asking about it would produce a
    404 that a naive fence would misread as "not settled".
    """
    try:
        if not int(case.get("synthetic") or 0):
            status = _razorpay_status(case["subscription_id"])
            if status is not None:
                if status in SETTLED_SUBSCRIPTION_STATUSES:
                    return FenceResult(SETTLED, "razorpay_fetch",
                                       f"subscription is {status} at Razorpay",
                                       {"subscription_status": status})
                return FenceResult(CLEAR, "razorpay_fetch",
                                   f"subscription is {status} at Razorpay",
                                   {"subscription_status": status})

        settlement = _ledger_settlement(case)
        if settlement is not None:
            return FenceResult(
                SETTLED, "local_ledger",
                f"{settlement['event_type']} for this subscription was received at "
                f"{settlement['received_at']}, after the case opened",
                {"razorpay_event_id": settlement["razorpay_event_id"],
                 "event_type": settlement["event_type"],
                 "received_at": settlement["received_at"],
                 "payment_id": settlement["payment_id"]})
        return FenceResult(CLEAR, "local_ledger", "no settlement signal since the case opened")
    except Exception as exc:  # the design rule, enforced at the outermost layer
        log.warning("fence: re-fetch degraded for case %s: %s", case.get("id"), exc)
        return FenceResult(UNVERIFIED, "degraded", f"{type(exc).__name__}: {exc}")


# ------------------------------------------------------------------ recording
def record(case: dict[str, Any], phase: str, action: str, result: FenceResult, *,
           decision_id: Optional[str] = None, execution_id: Optional[str] = None) -> str:
    """Write one fence evaluation. Every call is recorded, including the clear ones —
    they are the denominator, and a zero without one attests to nothing."""
    fence_id = db.new_id("fence")
    try:
        db.insert("dispatch_fence", {
            "id": fence_id,
            "case_id": case["id"],
            "decision_id": decision_id,
            "execution_id": execution_id,
            "phase": phase,
            "action": action,
            "verdict": result.verdict,
            "source": result.source,
            "reason": result.reason,
            "detail": json.dumps(result.detail, sort_keys=True, default=str),
            "checked_at": clock.now_iso(),
            "synthetic": int(case.get("synthetic") or 0),
        })
    except Exception as exc:  # pragma: no cover - a full disk must not stop a dispatch
        log.warning("fence: could not record evaluation for case %s: %s", case.get("id"), exc)
    return fence_id


# ------------------------------------------------------------- fence 1: before
def guard_dispatch(case: dict[str, Any], action: str, *, decision_id: Optional[str] = None) -> FenceResult:
    """Re-fetch immediately before a contact action. Anything blocking stops the cycle;
    the caller takes no further action on this case."""
    result = refetch(case)
    record(case, PRE_DISPATCH, action, result, decision_id=decision_id)
    return result


# -------------------------------------------------------------- fence 2: after
def verify_after_write(case: dict[str, Any], action: str, razorpay_ref: Optional[str], *,
                       decision_id: Optional[str] = None,
                       execution_id: Optional[str] = None) -> FenceResult:
    """Re-fetch once a payment link exists.

    Creating the link is already done and cannot be undone by wishing. What can still
    be done is cancel it and say so — and the compensation entry is appended **whether
    or not the cancellation succeeds**, because the attempt is the evidence. A
    compensation that only appears when it worked is a compensation log that flatters
    itself, and the case where it failed is the one a reader most needs to see.
    """
    result = refetch(case)
    record(case, POST_DISPATCH, action, result, decision_id=decision_id, execution_id=execution_id)
    if not result.blocks:
        return result

    cancellation = _cancel_link(razorpay_ref)
    audit.audit(
        case["id"], "compensate", "system",
        (f"Compensation: {action} was dispatched, then the world moved — {result.reason}. "
         + ("Payment link cancellation " + ("succeeded" if cancellation["ok"] else "FAILED")
            if razorpay_ref else "No live payment link to cancel (simulated dispatch).")),
        {
            "fence": POST_DISPATCH,
            "verdict": result.verdict,
            "source": result.source,
            "reason": result.reason,
            "detail": result.detail,
            "decision_id": decision_id,
            "execution_id": execution_id,
            "razorpay_ref": razorpay_ref,
            "cancellation": cancellation,
            "note": ("appended whether or not the cancellation succeeded — the attempt is "
                     "the evidence, and the failed one is the entry that matters most"),
        },
    )
    return result


def _cancel_link(razorpay_ref: Optional[str]) -> dict[str, Any]:
    """Best effort, and honest about it. Never raises."""
    if not razorpay_ref:
        return {"attempted": False, "ok": False, "reason": "no live payment link was created"}
    from app import executor

    if not executor.razorpay_configured():
        return {"attempted": False, "ok": False, "reason": "Razorpay is not configured"}
    try:
        executor.razorpay_client().payment_link.cancel(razorpay_ref)
        return {"attempted": True, "ok": True, "reason": "payment link cancelled"}
    except Exception as exc:
        log.warning("fence: payment link %s could not be cancelled: %s", razorpay_ref, exc)
        return {"attempted": True, "ok": False, "reason": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------- fence 3: stale inference
# The ONLY fields a decision actually turns on. Everything else about a case may churn
# freely between the model call and the action without invalidating the recommendation:
# `updated_at` moves on every touch, `last_contact_at` is re-read by I2 at execution
# anyway, and customer metadata has never entered a decision at all. A fingerprint that
# covered those would trip constantly, be switched off within a week, and protect
# nothing — the value of this guard is precisely that it is quiet.
_DECISION_RELEVANT_FIELDS = (
    "status",
    "attempt_count",
    "current_category",
    "customer_opted_out",
    "is_holdout",
    "amount_at_risk_paise",
    "currency",
)


def decision_fingerprint(case: dict[str, Any]) -> str:
    """SHA-256 over the decision-relevant fields only, plus whether a payment link
    exists for this case. Snapshot it before the model call; recheck it after."""
    body = {k: case.get(k) for k in _DECISION_RELEVANT_FIELDS}
    body["has_payment_link"] = bool(db.scalar(
        "SELECT COUNT(*) FROM execution_record WHERE case_id = ? AND razorpay_ref IS NOT NULL",
        (case["id"],), 0))
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def inference_unchanged(case: dict[str, Any], snapshot: str, *, action: str = "-",
                        decision_id: Optional[str] = None) -> FenceResult:
    """Recheck the fingerprint taken before an inference. A mismatch means the
    recommendation was computed against a world that no longer exists."""
    try:
        current = decision_fingerprint(case)
    except Exception as exc:
        result = FenceResult(UNVERIFIED, "degraded", f"{type(exc).__name__}: {exc}")
        record(case, INFERENCE, action, result, decision_id=decision_id)
        return result

    if current == snapshot:
        result = FenceResult(CLEAR, "fingerprint", "decision-relevant state is unchanged",
                             {"fingerprint": current})
    else:
        result = FenceResult(
            CHANGED, "fingerprint",
            "decision-relevant state changed while the inference was in flight",
            {"snapshot": snapshot, "current": current})
    record(case, INFERENCE, action, result, decision_id=decision_id)
    return result


# -------------------------------------------------------------------- metrics
def fence_stats() -> dict[str, Any]:
    """The fencing claim, with its denominator attached.

    `outreach_to_settled` is the number this exists to publish, and it is computed the
    hard way — from execution records that went out on a case a pre-dispatch fence had
    already called settled — rather than asserted to be zero.
    """
    by = {phase: {v: 0 for v in (CLEAR, SETTLED, CHANGED, UNVERIFIED)}
          for phase in (PRE_DISPATCH, POST_DISPATCH, INFERENCE)}
    for r in db.query("SELECT phase, verdict, COUNT(*) AS n FROM dispatch_fence GROUP BY phase, verdict"):
        by.setdefault(r["phase"], {})[r["verdict"]] = int(r["n"])

    n_dispatches_fenced = sum(by[PRE_DISPATCH].values())
    breached = db.query(
        "SELECT e.id AS execution_id, e.case_id AS case_id, f.id AS fence_id"
        " FROM dispatch_fence f JOIN execution_record e ON e.case_id = f.case_id"
        " WHERE f.phase = ? AND f.verdict = ? AND e.executed_at > f.checked_at"
        "   AND e.action IN ('SEND_UPDATE_LINK','PROMISE_TO_PAY','VOICE_CALL')",
        (PRE_DISPATCH, SETTLED))
    compensations = db.rows_to_dicts(db.query(
        "SELECT case_id, seq, summary, created_at FROM audit_log WHERE stage = 'compensate'"
        " ORDER BY id"))
    return {
        "n_dispatches_fenced": n_dispatches_fenced,
        "outreach_to_settled": len(breached),
        "outreach_to_settled_case_ids": sorted({r["case_id"] for r in breached}),
        "stopped_already_settled": int(db.scalar(
            "SELECT COUNT(*) FROM recovery_case WHERE status = 'stopped_already_settled'", (), 0)),
        "by_phase": by,
        "n_compensations": len(compensations),
        "compensations": compensations,
        "claim": (f"outreach to already-settled customers: {len(breached)} of "
                  f"{n_dispatches_fenced} dispatches fenced"),
        "note": ("a fence degrades rather than raises: an unverified verdict is recorded and "
                 "does not block, so 'we did not check' is never mistaken for 'we checked "
                 "and it was fine'"),
    }

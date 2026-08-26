"""Decide — a static table lookup, and nothing else (docs/04 §4).

There is no model in this call path. `POLICY` is total over all six categories and
all three attempts, so the lookup cannot raise and cannot fall through to a default.
Every decision cites the exact table cell it came from (`policy_row_ref`) and carries
the invariant receipts that let a reader confirm the gates actually ran.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from app import audit, cases, clock, config, db, executor, invariants


def lookup(category: str, attempt_number: int) -> tuple[str, int]:
    """(category, attempt) -> (action, delay_hours). Total by construction."""
    return config.POLICY[(category, attempt_number)]


def is_total() -> bool:
    return all((c, a) in config.POLICY for c in config.CATEGORIES for a in range(1, config.MAX_ATTEMPTS + 1))


def _supersede_scheduled(case_id: str) -> int:
    """A new failure event on an open case replaces any decision still waiting to run,
    so one case never has two pending interventions."""
    rows = db.query(
        "SELECT id FROM intervention_decision WHERE case_id = ? AND status = 'scheduled'", (case_id,)
    )
    for row in rows:
        db.update("intervention_decision", row["id"], {"status": "superseded"})
    return len(rows)


def decide(case: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Run the pre-decision gate, then pick exactly one intervention."""
    case = cases.get(case["id"])
    if case is None or case["status"] != "open":
        return None

    verdict = invariants.check(case, phase="pre_decision")
    if verdict.is_stop:
        cases.transition(
            case["id"], verdict.status,
            summary=f"Stopped before deciding by {verdict.invariant}: {verdict.reason}",
            detail={
                "invariant": verdict.invariant,
                "invariant_rule": invariants.INVARIANT_TEXT[verdict.invariant],
                "phase": "pre_decision",
                "attempt_count": case["attempt_count"],
                "category": case["current_category"],
                "checks": verdict.details,
            },
        )
        return None

    if int(case.get("is_holdout") or 0) == 1:
        # The control arm. Detected and diagnosed like any other case — the category on
        # its file is real — but no intervention is ever chosen, so the treated arm has
        # something to be measured against.
        #
        # The case stays OPEN rather than closing here, because that is what a holdout
        # is: a case you watch without touching. It can still recover on its own, and
        # that self-recovery is exactly the quantity being measured. Whatever is left
        # unrecovered when the episode window expires closes as `stopped_holdout`
        # (executor._close_expired_episodes).
        #
        # This sits *after* the invariant gate on purpose: a holdout case that opt-out
        # or an unknown cause would have stopped is recorded under that reason, because
        # the same stop would have happened in the treated arm. Keeping the reasons
        # intact is what keeps the arms comparable.
        category = case["current_category"] or "unknown"
        action, delay_hours = lookup(category, int(case["attempt_count"]) + 1)
        audit.audit(
            case["id"], "decide", "system",
            f"Control arm: no intervention. Policy row {category}/"
            f"{int(case['attempt_count']) + 1} would have chosen {action}.",
            {
                "arm": "control",
                "policy_row_ref": f"{category}/{int(case['attempt_count']) + 1}",
                "withheld_action": action,
                "withheld_delay_hours": delay_hours,
            },
        )
        return None

    superseded = _supersede_scheduled(case["id"])
    category = case["current_category"] or "unknown"
    attempt = int(case["attempt_count"]) + 1
    action, delay_hours = lookup(category, attempt)

    decision_id = db.new_id("dec")
    decided_at = clock.now()
    scheduled_for = clock.plus_hours(decided_at, delay_hours)
    db.insert(
        "intervention_decision",
        {
            "id": decision_id,
            "case_id": case["id"],
            "attempt_number": attempt,
            "category": category,
            "action": action,
            "policy_row_ref": f"{category}/{attempt}",
            "invariant_check": json.dumps(invariants.receipts(("pre_decision", verdict)), sort_keys=True),
            "decided_at": clock.to_iso(decided_at),
            "scheduled_for": clock.to_iso(scheduled_for),
            "status": "scheduled",
            "synthetic": case["synthetic"],
        },
    )
    audit.audit(
        case["id"], "decide", "system",
        (f"Attempt {attempt}: {action} per policy row {category}/{attempt}"
         + (f", scheduled for {clock.to_iso(scheduled_for)}" if delay_hours else ", immediately")),
        {
            "decision_id": decision_id,
            "policy_row_ref": f"{category}/{attempt}",
            "action": action,
            "delay_hours": delay_hours,
            "scheduled_for": clock.to_iso(scheduled_for),
            "invariant_check": invariants.receipts(("pre_decision", verdict)),
            "superseded_scheduled_decisions": superseded,
        },
    )

    decision = db.row_to_dict(db.query_one("SELECT * FROM intervention_decision WHERE id = ?", (decision_id,)))

    if action == "STOP_HANDOFF":
        # Carried out by closing the case. No ExecutionRecord exists because a handoff
        # moves no money and contacts nobody, and it does not consume an attempt.
        db.update("intervention_decision", decision_id, {"status": "executed"})
        cases.transition(
            case["id"], "stopped_handoff",
            summary=(f"Policy row {category}/{attempt} says stop: handed off to the human queue "
                     "with a complete case file"),
            detail={"decision_id": decision_id, "policy_row_ref": f"{category}/{attempt}"},
        )
        return decision

    if delay_hours == 0:
        executor.execute_decision(decision)
        return db.row_to_dict(db.query_one("SELECT * FROM intervention_decision WHERE id = ?", (decision_id,)))

    return decision

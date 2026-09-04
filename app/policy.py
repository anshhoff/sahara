"""Decide — a static table lookup, and nothing else (docs/04 §4).

There is no model in this call path. `POLICY` is total over all six categories and
all three attempts, so the lookup cannot raise and cannot fall through to a default.
Every decision cites the exact table cell it came from (`policy_row_ref`) and carries
the invariant receipts that let a reader confirm the gates actually ran.

The table chooses WHAT to do. Gate E1 (app/economics.py) then asks whether doing it
is worth more than it costs, and every decision carries the answer — `ev_paise` and
`ev_detail` are written whether the gate passed or stopped, so a reader can audit the
arithmetic behind an action the agent took, not only one it declined.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from app import audit, cases, clock, config, db, economics, executor, invariants


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
        attempt = int(case["attempt_count"]) + 1
        action, delay_hours = lookup(category, attempt)
        withheld_ev = economics.evaluate(case, action, attempt)
        audit.audit(
            case["id"], "decide", "system",
            f"Control arm: no intervention. Policy row {category}/{attempt} would have "
            f"chosen {action}.",
            {
                "arm": "control",
                "policy_row_ref": f"{category}/{attempt}",
                "withheld_action": action,
                "withheld_delay_hours": delay_hours,
                # The economics of the road not taken. Recorded so the control arm's
                # cost is measurable too: a holdout is not free, it is the price of
                # knowing whether the treated arm did anything.
                "withheld_ev": withheld_ev,
            },
        )
        return None

    category = case["current_category"] or "unknown"
    attempt = int(case["attempt_count"]) + 1
    action, delay_hours = lookup(category, attempt)

    # Gate E1. Run before anything is superseded or written, so a case stopped here
    # leaves no half-made decision behind — the same shape as an invariant stop.
    ev = economics.evaluate(case, action, attempt)
    if ev["verdict"] == "stop_uneconomic":
        # WHERE a refused case goes depends on what the alternative was. If the gate
        # weighed this action against the human queue and the queue won, the case must
        # actually reach the queue — closing it as `uneconomic` would take the Rs 40 out
        # of the comparison after using it to win the comparison, and quietly abandon a
        # case the arithmetic said was worth a person.
        alternative = ev.get("alternative_action")
        if alternative == "STOP_HANDOFF":
            cases.transition(
                case["id"], "stopped_handoff",
                summary=(f"Policy row {category}/{attempt} chose {action}, but at "
                         f"{ev['ev_paise']} paise it is worth less than the human queue at "
                         f"{ev['alternative_ev_paise']} paise: handed off to a person instead"),
                detail={
                    "gate": "E1",
                    "policy_row_ref": f"{category}/{attempt}",
                    "withheld_action": action,
                    "chosen_alternative": alternative,
                    "ev": ev,
                },
            )
            return None
        cases.transition(
            case["id"], "stopped_uneconomic",
            summary=(f"Policy row {category}/{attempt} chose {action}, but its expected value "
                     f"is {ev['ev_paise']} paise: the contact costs more than it is likely to "
                     f"return, so it was not sent"),
            detail={
                "gate": "E1",
                "policy_row_ref": f"{category}/{attempt}",
                "withheld_action": action,
                "ev": ev,
            },
        )
        return None

    superseded = _supersede_scheduled(case["id"])
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
            "ev_paise": ev["ev_paise"],
            "ev_detail": json.dumps(ev, sort_keys=True),
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
            "ev": ev,
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

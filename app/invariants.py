"""The four stopping invariants — the part of this system that says no (docs/04 §5).

This module is deliberately independent: it imports the database, the clock and the
constants, and nothing else. It does not import policy.py, llm.py or executor.py.
A bug in the policy table therefore cannot route around it, and no model output can
reach it.

Every gate reloads the case from the database first. In-memory case state is never
trusted at a gate, because the whole point of the pre-execution re-check is to catch
state that changed after the decision was made — an opt-out arriving between a
scheduled decision and its execution, for example.

  I1  max 3 attempts ever                 -> stopped_max_attempts
  I2  >= 24h between customer contacts    -> defer, or stopped_cooldown_expired
  I3  opt-out always stops                -> stopped_opt_out
  I4  unknown is never guessed            -> stopped_unknown

Ordering is deliberate: consent (I3) beats everything, and refusing to act on an
unknown cause (I4) beats attempt counting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from app import clock, config, db

PASS = "pass"
STOP = "stop"
DEFER = "defer"

INVARIANT_TEXT = {
    "I1": f"at most {config.MAX_ATTEMPTS} executed attempts per case",
    "I2": f"at least {config.COOLDOWN_HOURS}h between customer contacts",
    "I3": "an opted-out customer is never contacted or charged again",
    "I4": "an unknown failure cause is never acted on",
}


@dataclass
class Verdict:
    kind: str                       # pass | stop | defer
    details: dict[str, str] = field(default_factory=dict)
    invariant: Optional[str] = None
    status: Optional[str] = None    # terminal case status, when kind == stop
    until: Optional[datetime] = None  # when kind == defer
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == PASS

    @property
    def is_stop(self) -> bool:
        return self.kind == STOP

    @property
    def is_defer(self) -> bool:
        return self.kind == DEFER


def _load(case_or_id: Any) -> dict[str, Any]:
    case_id = case_or_id["id"] if isinstance(case_or_id, dict) else case_or_id
    row = db.query_one("SELECT * FROM recovery_case WHERE id = ?", (case_id,))
    if row is None:
        raise ValueError(f"unknown case {case_id}")
    return db.row_to_dict(row)


def episode_deadline(case: dict[str, Any]) -> datetime:
    return clock.plus_days(clock.parse_iso(case["created_at"]), config.EPISODE_WINDOW_DAYS)


def episode_expired(case: dict[str, Any]) -> bool:
    return clock.now() >= episode_deadline(case)


def check(case_or_id: Any, proposed_action: Optional[str] = None, *, phase: str = "pre_decision") -> Verdict:
    """Run I1–I4. `phase` is recorded on the decision's receipts, not branched on:
    both phases run the identical checks against freshly loaded state."""
    case = _load(case_or_id)
    details: dict[str, str] = {"I1": PASS, "I2": PASS, "I3": PASS, "I4": PASS, "phase": phase}

    if int(case["customer_opted_out"]) == 1:
        details["I3"] = "violated"
        return Verdict(STOP, details, "I3", "stopped_opt_out",
                       reason="customer has opted out of recovery contact")

    if case["current_category"] == "unknown":
        details["I4"] = "violated"
        return Verdict(STOP, details, "I4", "stopped_unknown",
                       reason="failure cause is unknown; the agent does not guess")

    if int(case["attempt_count"]) >= config.MAX_ATTEMPTS:
        details["I1"] = "violated"
        return Verdict(STOP, details, "I1", "stopped_max_attempts",
                       reason=f"{case['attempt_count']} of {config.MAX_ATTEMPTS} attempts already executed")

    if proposed_action in config.CONTACT_ACTIONS and case["last_contact_at"]:
        earliest = clock.plus_hours(clock.parse_iso(case["last_contact_at"]), config.COOLDOWN_HOURS)
        if clock.now() < earliest:
            if earliest > episode_deadline(case):
                details["I2"] = "violated"
                return Verdict(
                    STOP, details, "I2", "stopped_cooldown_expired",
                    reason=("the next permitted contact falls outside the "
                            f"{config.EPISODE_WINDOW_DAYS}-day episode window"),
                )
            details["I2"] = f"deferred to {clock.to_iso(earliest)}"
            return Verdict(
                DEFER, details, "I2", until=earliest,
                reason=f"cooldown of {config.COOLDOWN_HOURS}h since last contact has not elapsed",
            )

    return Verdict(PASS, details)


def receipts(*verdicts: tuple[str, Verdict]) -> dict[str, dict[str, str]]:
    """Shape the per-phase details for InterventionDecision.invariant_check —
    the receipts proving the gates actually ran (docs/03 §5)."""
    return {phase: dict(v.details) for phase, v in verdicts}

"""The stopping invariants — the part of this system that says no (docs/04 §5).

This module is deliberately independent: it imports the database, the clock and the
constants, and nothing else. It does not import policy.py, llm.py or executor.py.
A bug in the policy table therefore cannot route around it, and no model output can
reach it.

Every gate reloads the case from the database first. In-memory case state is never
trusted at a gate, because the whole point of the pre-execution re-check is to catch
state that changed after the decision was made — an opt-out arriving between a
scheduled decision and its execution, for example.

SAFETY — the original four. Consent, the attempt cap, and the refusal to guess.

  I1  max 3 attempts ever                 -> stopped_max_attempts
  I2  >= 24h between customer contacts    -> defer, or stopped_cooldown_expired
      (evaluable only once an action is known — see check())
  I3  opt-out always stops                -> stopped_opt_out
  I4  unknown is never guessed            -> stopped_unknown

CONTACT HYGIENE — when and how often a real person may be messaged. These exist
because I1-I4 bound the agent per case, and a customer is not a case: they can hold
two failing subscriptions, and nothing above would stop both of them messaging at
2am on the same night.

  I5  no contact during quiet hours       -> defer to 09:00 IST
  I6  suppression list beats everything   -> stopped_suppressed
  I7  daily contact and spend ceilings    -> defer to the next window

Economics (gate E1, app/economics.py) is deliberately NOT here. These are safety and
consent rules that no business case may override; that one is a business case.

I1 is *reported* here but *enforced* in cases.reserve_attempt(): this module only
reads attempt_count, and a read cannot hold a cap against a concurrent execution.
The gate below is the early, explanatory stop; the conditional UPDATE is the one
that cannot be raced.

Ordering is deliberate. Consent first (I3, then I6 — a per-case opt-out and a
per-person suppression are the same answer at different scopes), then the refusal to
act on an unknown cause (I4), then the attempt cap (I1). Only after all four hard
stops do the timing gates run, and they run together: I2, I5 and I7 can each defer
the same contact, so the verdict is the LATEST of their targets rather than whichever
happened to be evaluated first. Returning on the first defer would schedule a contact
for a time a later gate also forbids, and the bug would only show up at 3am.
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
    "I5": (f"no customer contact between {config.QUIET_HOURS_START_IST}:00 and "
           f"{config.QUIET_HOURS_END_IST}:00 IST"),
    "I6": "a suppressed customer is never contacted, on any of their subscriptions",
    "I7": (f"at most {config.MAX_CONTACTS_PER_CUSTOMER_PER_DAY} contacts per customer per IST "
           f"day, and at most Rs {config.DAILY_OUTREACH_BUDGET_PAISE / 100:,.0f} of outreach "
           f"across all customers per IST day"),
}


def is_suppressed(customer_id: str) -> Optional[dict[str, Any]]:
    """The suppression list, read straight from the table. Kept here rather than
    behind a helper in cases.py so that this module's independence claim holds: it
    imports the database, the clock and the constants, and nothing else."""
    return db.row_to_dict(
        db.query_one("SELECT * FROM suppression WHERE customer_id = ?", (customer_id,)))


def contacts_today(customer_id: str, at: Optional[datetime] = None) -> int:
    """Executed contact actions for this CUSTOMER in the current IST day, across every
    case they have. Counting per customer rather than per case is the whole point of
    I7 — I2 already handles the per-case spacing and cannot see the second
    subscription."""
    start, end = clock.ist_day_bounds(at)
    return int(db.scalar(
        "SELECT COUNT(*) FROM execution_record e JOIN recovery_case c ON c.id = e.case_id"
        " WHERE c.customer_id = ? AND e.action IN ('SEND_UPDATE_LINK','PROMISE_TO_PAY')"
        " AND e.executed_at >= ? AND e.executed_at < ?",
        (customer_id, start, end), 0))


def outreach_spend_today(at: Optional[datetime] = None) -> int:
    """System-wide outreach spend, in paise, for the current IST day."""
    start, end = clock.ist_day_bounds(at)
    return int(db.scalar(
        "SELECT COALESCE(SUM(cost_paise), 0) FROM execution_record"
        " WHERE executed_at >= ? AND executed_at < ?", (start, end), 0))


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


NOT_APPLICABLE = "not_applicable"


def check(case_or_id: Any, proposed_action: Optional[str] = None, *, phase: str = "pre_decision") -> Verdict:
    """Run the invariants that can be run, against freshly loaded state. `phase` is
    recorded on the receipts, not branched on — the gates themselves are identical.

    I1, I3, I4 and I6 depend only on case and customer state, so both phases evaluate
    them. I2, I5 and I7 depend on the *proposed action*, because they gate customer
    contact and not a silent mandate re-charge. At the pre-decision gate the action has
    not been chosen yet, so those three are genuinely unevaluable there and their
    receipts say `not_applicable` rather than `pass` — recording a check that did not
    run as one that passed would make the audit trail claim more than it can support.
    Pre-execution always knows the action, so that is where they are really enforced.
    """
    case = _load(case_or_id)
    # The contact gates' receipts reflect whether they were applicable, not merely
    # whether they fired.
    contact = proposed_action in config.CONTACT_ACTIONS
    applicable = PASS if contact else NOT_APPLICABLE
    details: dict[str, str] = {
        "I1": PASS,
        "I2": applicable,
        "I3": PASS,
        "I4": PASS,
        "I5": applicable,
        "I6": PASS,
        "I7": applicable,
        "phase": phase,
    }

    # ---------------------------------------------------------------- hard stops
    if int(case["customer_opted_out"]) == 1:
        details["I3"] = "violated"
        return Verdict(STOP, details, "I3", "stopped_opt_out",
                       reason="customer has opted out of recovery contact")

    suppressed = is_suppressed(case["customer_id"])
    if suppressed is not None:
        # Checked for every action, not only contacts. A suppressed customer is one
        # who asked to be left alone, and silently re-charging their card is not
        # leaving them alone just because it makes no noise.
        details["I6"] = "violated"
        return Verdict(STOP, details, "I6", "stopped_suppressed",
                       reason=(f"customer {case['customer_id']} is on the suppression list "
                               f"({suppressed['reason']}, recorded {suppressed['created_at']})"))

    if case["current_category"] == "unknown":
        details["I4"] = "violated"
        return Verdict(STOP, details, "I4", "stopped_unknown",
                       reason="failure cause is unknown; the agent does not guess")

    if int(case["attempt_count"]) >= config.MAX_ATTEMPTS:
        details["I1"] = "violated"
        return Verdict(STOP, details, "I1", "stopped_max_attempts",
                       reason=f"{case['attempt_count']} of {config.MAX_ATTEMPTS} attempts already executed")

    if not contact:
        return Verdict(PASS, details)

    # ------------------------------------------------------------- timing gates
    # Each of these can push the same contact later. They are collected rather than
    # returned on, and the latest target wins — see the module docstring.
    now = clock.now()
    deferrals: list[tuple[datetime, str, str]] = []   # (until, invariant, reason)

    if case["last_contact_at"]:
        earliest = clock.plus_hours(clock.parse_iso(case["last_contact_at"]), config.COOLDOWN_HOURS)
        if now < earliest:
            deferrals.append((earliest, "I2",
                              f"cooldown of {config.COOLDOWN_HOURS}h since last contact has not elapsed"))

    if config.QUIET_HOURS_ENABLED and clock.in_quiet_hours(now):
        deferrals.append((
            clock.next_ist_hour(now, config.QUIET_HOURS_END_IST), "I5",
            f"quiet hours: it is {clock.ist_hour(now):02d}:00 IST, inside the "
            f"{config.QUIET_HOURS_START_IST}:00-{config.QUIET_HOURS_END_IST}:00 IST window"))

    n_today = contacts_today(case["customer_id"], now)
    if n_today >= config.MAX_CONTACTS_PER_CUSTOMER_PER_DAY:
        deferrals.append((
            clock.next_ist_hour(now, config.QUIET_HOURS_END_IST), "I7",
            f"customer {case['customer_id']} has already had {n_today} contacts today, "
            f"the daily maximum is {config.MAX_CONTACTS_PER_CUSTOMER_PER_DAY}"))

    # The budget is checked against what this contact WOULD cost, not against what has
    # been spent, so the ceiling is never crossed and then noticed.
    spent = outreach_spend_today(now)
    would_cost = int(config.ACTION_COST_PAISE.get(proposed_action, 0))
    if spent + would_cost > config.DAILY_OUTREACH_BUDGET_PAISE:
        deferrals.append((
            clock.next_ist_hour(now, config.QUIET_HOURS_END_IST), "I7",
            f"today's outreach spend of {spent} paise plus {would_cost} paise for this "
            f"contact would exceed the daily ceiling of {config.DAILY_OUTREACH_BUDGET_PAISE} paise"))

    if not deferrals:
        return Verdict(PASS, details)

    until, invariant, reason = max(deferrals, key=lambda d: d[0])
    if until > episode_deadline(case):
        # Every gate that defers shares this ending: if the next permitted moment is
        # outside the episode window there is no later attempt to make, so the case
        # closes now rather than holding a contact that can never legally go out.
        details[invariant] = "violated"
        return Verdict(
            STOP, details, invariant, "stopped_cooldown_expired",
            reason=(f"{reason}; the next permitted contact falls outside the "
                    f"{config.EPISODE_WINDOW_DAYS}-day episode window"),
        )
    for _, code, _r in deferrals:
        details[code] = f"deferred to {clock.to_iso(until)}"
    return Verdict(DEFER, details, invariant, until=until, reason=reason)


def receipts(*verdicts: tuple[str, Verdict]) -> dict[str, dict[str, str]]:
    """Shape the per-phase details for InterventionDecision.invariant_check —
    the receipts proving the gates actually ran (docs/03 §5)."""
    return {phase: dict(v.details) for phase, v in verdicts}

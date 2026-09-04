"""What an intervention is worth, and what it costs (gate E1).

Gross recovery is the number every dunning tool reports and the one number that
cannot go down by sending more messages. That is precisely what makes it the wrong
headline: a system optimising it will always send one more, because on a gross ledger
the marginal message is free. It is not free. It costs a fraction of a rupee to
deliver, some chance of an inbound support contact, and some chance the customer
cancels rather than pays.

So every decision is priced before it is taken:

    EV = p_recover x amount  -  direct_cost  -  annoyance_cost

and a negative EV stops the case as `stopped_uneconomic`. The stop is the backstop,
not the point. The point is that `ev_paise` and `ev_detail` are written on EVERY
decision, including the ones that proceeded, so a reader can see what the agent
expected to gain from an action it went ahead with — and disagree with the arithmetic
if they think the priors are wrong, because the priors are right there in the record.

Deliberately NOT in invariants.py. Those four are safety: they encode consent, the
attempt cap and the refusal to guess, and none of them is negotiable by a business
that would prefer a different answer. This is a business judgement made of estimates.
Keeping the two apart means a bad estimate here can cost money and cannot cost
someone their consent.

Nothing in this module may consult scripts/run_batch.py. Its SUCCESS_PROBABILITY is
the simulated world's ground truth; config.P_RECOVER_PRIOR is what the agent believes
beforehand. Wiring them together would score every decision with the answer key.
"""
from __future__ import annotations

from typing import Any

from app import config

# Actions that are decisions to stop rather than interventions to run. They still
# cost something (a handoff occupies a person), but there is no alternative to weigh
# them against, so the EV gate has nothing to say about them.
NON_INTERVENTION_ACTIONS = frozenset({"STOP_HANDOFF"})


def _clamp_attempt(attempt: int) -> int:
    return max(1, min(int(attempt), config.MAX_ATTEMPTS))


def p_recover(category: str, action: str, attempt: int) -> float:
    """The agent's prior that this action, at this attempt, gets the money back."""
    row = config.P_RECOVER_PRIOR.get((category, action), config.P_RECOVER_DEFAULT)
    return float(row[_clamp_attempt(attempt) - 1])


def direct_cost_paise(action: str) -> int:
    """The invoice-side cost: the channel, plus the support load a contact creates."""
    return int(config.ACTION_COST_PAISE.get(action, 0))


def annoyance_cost_paise(action: str, attempt: int, amount_paise: int) -> int:
    """The cost that never appears on an invoice: the chance this message is the one
    that makes the customer cancel instead of pay.

    Priced as `hazard(attempt) x horizon x amount` — the hazard of losing them times
    what losing them is worth. Zero for RETRY_LATER, which contacts nobody: a silent
    re-charge of a mandate the customer already authorised cannot irritate them,
    which is the entire reason the policy table reaches for it first.
    """
    if action not in config.CONTACT_ACTIONS:
        return 0
    hazard = config.CONTACT_CHURN_HAZARD[_clamp_attempt(attempt) - 1]
    return int(round(hazard * config.LTV_HORIZON_MONTHS * int(amount_paise)))


def evaluate(case: dict[str, Any], action: str, attempt: int) -> dict[str, Any]:
    """Price one proposed intervention. Pure arithmetic over config and the case —
    no database, no clock, no model — so it is trivially testable and cannot have a
    side effect on the case it is judging."""
    category = case.get("current_category") or "unknown"
    amount = int(case["amount_at_risk_paise"])
    attempt = _clamp_attempt(attempt)

    p = p_recover(category, action, attempt)
    gross = p * amount
    direct = direct_cost_paise(action)
    annoyance = annoyance_cost_paise(action, attempt, amount)
    ev = int(round(gross - direct - annoyance))

    gated = action not in NON_INTERVENTION_ACTIONS
    return {
        "action": action,
        "category": category,
        "attempt": attempt,
        "amount_paise": amount,
        "p_recover": round(p, 4),
        "expected_gross_paise": int(round(gross)),
        "direct_cost_paise": direct,
        "annoyance_cost_paise": annoyance,
        "ev_paise": ev,
        # `gated` says whether this EV is allowed to stop anything, and it is recorded
        # so that a STOP_HANDOFF carrying a negative EV cannot be misread as a gate
        # that failed to fire.
        "gated": gated,
        "positive": ev > 0,
        "verdict": "proceed" if (not gated or ev > 0) else "stop_uneconomic",
        "basis": ("p_recover is config.P_RECOVER_PRIOR — the agent's prior, deliberately "
                  "not the simulator's outcome model; costs are stated assumptions in "
                  "config.py, not measurements"),
    }


def execution_cost_paise(action: str) -> int:
    """What to record on an ExecutionRecord. The annoyance term is a modelled risk,
    not money that left the account, so it prices the decision but is never booked as
    a cost — the net-recovery metrics stay made of rupees that actually moved."""
    return direct_cost_paise(action)

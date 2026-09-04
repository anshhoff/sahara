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

from typing import Any, Optional

from app import config

# A sentinel distinct from None, because None is a meaningful value for `alternative`:
# it means "the counterfactual is to do nothing, at no cost". Defaulting to None would
# make "I did not say" and "I said nothing happens" indistinguishable.
_UNSET: Any = object()

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


def fallback_action(attempt: int) -> Optional[str]:
    """What happens to a case if the proposed intervention is refused.

    This is the single most consequential thing about the gate, and it was wrong.

    Refusing an early attempt closes the case as `stopped_uneconomic`: nobody works it,
    and that costs nothing — which is why the alternative EV there is zero. But at the
    FINAL attempt the alternative is not "abandon the case". It is the human queue,
    which is exactly what the policy table did at attempt 3 before there was any other
    rung, and a person costs Rs 40.

    Pricing an action against "do nothing" when doing nothing is not on the menu is the
    wrong comparison, and it has a direction: it makes every last-rung intervention look
    unaffordable no matter how much cheaper it is than the alternative it replaces.
    """
    return "STOP_HANDOFF" if int(attempt) >= config.MAX_ATTEMPTS else None


def evaluate(case: dict[str, Any], action: str, attempt: int,
             alternative: Optional[str] = _UNSET) -> dict[str, Any]:
    """Price one proposed intervention AGAINST WHAT WOULD HAPPEN INSTEAD.

    Pure arithmetic over config and the case — no database, no clock, no model — so it
    is trivially testable and cannot have a side effect on the case it is judging.

    `alternative` defaults to `fallback_action(attempt)`. Pass it explicitly (including
    as None, meaning "the alternative is to do nothing, at no cost") to price a decision
    against a specific counterfactual.
    """
    category = case.get("current_category") or "unknown"
    amount = int(case["amount_at_risk_paise"])
    attempt = _clamp_attempt(attempt)
    if alternative is _UNSET:
        alternative = fallback_action(attempt)

    p = p_recover(category, action, attempt)
    gross = p * amount
    direct = direct_cost_paise(action)
    annoyance = annoyance_cost_paise(action, attempt, amount)
    ev = int(round(gross - direct - annoyance))

    # The counterfactual, priced the same way. A STOP_HANDOFF recovers nothing on its
    # own in this model and irritates nobody, so its EV is simply minus its cost.
    if alternative is None:
        alt_ev = 0
    else:
        # A non-intervention recovers NOTHING on its own. It must not inherit
        # P_RECOVER_DEFAULT, which would credit a handoff with an 8% chance of the money
        # arriving by itself and make the human queue the best-value option on the board
        # — beating every intervention it was supposed to be compared against. The
        # default exists for pairs nobody reasoned about; this is a pair with an answer.
        alt_p = 0.0 if alternative in NON_INTERVENTION_ACTIONS else p_recover(
            category, alternative, attempt)
        alt_ev = int(round(
            alt_p * amount - direct_cost_paise(alternative)
            - annoyance_cost_paise(alternative, attempt, amount)))

    gated = action not in NON_INTERVENTION_ACTIONS
    beats_alternative = ev > alt_ev
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
        "alternative_action": alternative,
        "alternative_ev_paise": alt_ev,
        "margin_over_alternative_paise": ev - alt_ev,
        # `gated` says whether this EV is allowed to stop anything, and it is recorded
        # so that a STOP_HANDOFF carrying a negative EV cannot be misread as a gate
        # that failed to fire.
        "gated": gated,
        "positive": ev > 0,
        "verdict": "proceed" if (not gated or beats_alternative) else "stop_uneconomic",
        "basis": ("p_recover is config.P_RECOVER_PRIOR — the agent's prior, deliberately "
                  "not the simulator's outcome model; costs are stated assumptions in "
                  "config.py, not measurements. The gate compares this action against "
                  "`alternative_action`, which is what the case would do instead — not "
                  "against zero, because doing nothing is not always on the menu."),
    }


def execution_cost_paise(action: str) -> int:
    """What to record on an ExecutionRecord. The annoyance term is a modelled risk,
    not money that left the account, so it prices the decision but is never booked as
    a cost — the net-recovery metrics stay made of rupees that actually moved."""
    return direct_cost_paise(action)

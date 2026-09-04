"""Gate E1 — the expected-value arithmetic, and the boundary that keeps it honest
(app/economics.py).

The tests that matter most here are not the arithmetic ones. They are the two that
assert the agent's priors are NOT the simulator's outcome model, because an agent
scored against its own answer key produces a perfect number that means nothing.
"""
from __future__ import annotations

import pathlib
import re

from app import cases, config, db, economics, policy


def _case(amount=49900, category="card_expired"):
    c = cases.create(subscription_id="SYNTH-sub-e", customer_id="SYNTH-cust-e",
                     amount_at_risk_paise=amount, synthetic=True)
    cases.set_category(c["id"], category)
    return cases.get(c["id"])


# ------------------------------------------------------------------ arithmetic
def test_ev_is_expected_gross_minus_both_costs(fresh_db):
    case = _case(amount=49900, category="card_expired")
    ev = economics.evaluate(case, "SEND_UPDATE_LINK", 1)
    p = config.P_RECOVER_PRIOR[("card_expired", "SEND_UPDATE_LINK")][0]
    hazard = config.CONTACT_CHURN_HAZARD[0]
    expected = round(p * 49900
                     - config.ACTION_COST_PAISE["SEND_UPDATE_LINK"]
                     - hazard * config.LTV_HORIZON_MONTHS * 49900)
    assert ev["ev_paise"] == expected
    assert ev["expected_gross_paise"] == round(p * 49900)


def test_a_silent_retry_carries_no_annoyance_cost(fresh_db):
    """RETRY_LATER contacts nobody, so it cannot irritate anybody. This is the whole
    reason the policy table reaches for it first wherever the instrument might work."""
    case = _case(category="insufficient_funds")
    ev = economics.evaluate(case, "RETRY_LATER", 1)
    assert ev["annoyance_cost_paise"] == 0
    assert ev["direct_cost_paise"] == 0


def test_annoyance_escalates_with_the_attempt_number(fresh_db):
    case = _case(category="card_expired")
    costs = [economics.evaluate(case, "SEND_UPDATE_LINK", a)["annoyance_cost_paise"]
             for a in (1, 2, 3)]
    assert costs[0] < costs[1] < costs[2], costs


def test_a_stop_handoff_is_priced_but_never_gated(fresh_db):
    """A handoff costs a person's time, so it is priced. It is not an intervention
    with an alternative to weigh it against, so a negative number there must not be
    read as a gate that failed to fire."""
    ev = economics.evaluate(_case(amount=100), "STOP_HANDOFF", 3)
    assert ev["ev_paise"] < 0
    assert ev["gated"] is False
    assert ev["verdict"] == "proceed"


# ------------------------------------------------------------------- the gate
def test_the_gate_stops_a_case_whose_next_contact_cannot_pay_for_itself(fresh_db):
    """The shipped policy table and the shipped priors never produce a negative EV on
    a real batch — every contact it chooses also clears its own economics. So the gate
    is driven directly, exactly as test_invariants.py drives a case to the attempt cap:
    a tiny amount where one message costs more than the whole charge is worth.
    """
    case = _case(amount=100, category="card_expired")   # Rs 1 at risk, Rs 12 to contact
    ev = economics.evaluate(case, "SEND_UPDATE_LINK", 1)
    assert ev["ev_paise"] < 0 and ev["verdict"] == "stop_uneconomic"

    assert policy.decide(case) is None
    closed = cases.get(case["id"])
    assert closed["status"] == "stopped_uneconomic"
    # Nothing was half-written on the way out.
    assert db.query("SELECT * FROM intervention_decision WHERE case_id = ?", (case["id"],)) == []
    assert db.query("SELECT * FROM execution_record WHERE case_id = ?", (case["id"],)) == []


def test_every_decision_carries_its_own_arithmetic(fresh_db):
    """The stop is the backstop; the point is that a decision which went AHEAD also
    records what it expected to gain, so the reasoning can be audited either way."""
    case = _case(amount=199900, category="card_expired")
    decision = policy.decide(case)
    assert decision is not None
    row = db.row_to_dict(db.query_one(
        "SELECT ev_paise, ev_detail FROM intervention_decision WHERE id = ?", (decision["id"],)))
    assert row["ev_paise"] is not None and row["ev_paise"] > 0
    assert '"p_recover"' in row["ev_detail"]


def test_cost_is_booked_on_the_execution_even_when_it_failed(fresh_db):
    case = _case(amount=199900, category="card_expired")
    policy.decide(case)
    row = db.query_one("SELECT action, cost_paise FROM execution_record WHERE case_id = ?", (case["id"],))
    assert row is not None
    assert row["cost_paise"] == config.ACTION_COST_PAISE[row["action"]]


# ------------------------------------------------------ the anti-cheat boundary
def test_the_agents_priors_are_not_the_simulators_outcome_model():
    """If these two tables agreed, the agent would be scoring its decisions with the
    answer key: every EV would be correct by construction and the measurement would be
    circular. They must be independently chosen, and they must stay that way."""
    import scripts.run_batch as run_batch

    shared = set(config.P_RECOVER_PRIOR) & set(run_batch.SUCCESS_PROBABILITY)
    assert shared, "the two tables no longer describe the same situations; check this test"
    for key in shared:
        prior = tuple(config.P_RECOVER_PRIOR[key])
        truth = tuple(run_batch.SUCCESS_PROBABILITY[key])
        assert prior != truth, f"{key}: the agent's prior is the simulator's ground truth"


def test_nothing_in_app_imports_the_batch_simulator():
    """The structural half of the claim above. A test comparing two tables can be
    satisfied by nudging a number; an import grep cannot."""
    offenders = []
    for path in sorted(pathlib.Path("app").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(from|import)\s+scripts", text, re.MULTILINE):
            offenders.append(path.name)
    assert offenders == [], f"app/ must not import the simulator: {offenders}"

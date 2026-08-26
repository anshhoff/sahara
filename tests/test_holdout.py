"""The randomised control arm, and the incremental-recovery metric built on it.

Gross recovery cannot separate "came back" from "came back because of us". These
tests pin the two properties that make the difference measurable: a control case is
never touched, and it is still observed.
"""
from __future__ import annotations

from app import cases, clock, config, db, executor, metrics, webhooks
from tests.conftest import failure_payload, recovery_payload


def _intake(holdout: bool, **kw):
    payload = failure_payload(**kw)
    payload["holdout"] = holdout
    webhooks.intake(payload, source="synthetic")
    return cases.find_latest_by_subscription(kw.get("sub_id", "SYNTH-sub-t"))


def test_a_control_case_is_diagnosed_but_never_intervened_on(fresh_db, sim_clock):
    case = _intake(True)
    assert case["is_holdout"] == 1
    # Diagnosis still runs — the case file is real, only the intervention is withheld.
    assert case["current_category"] == "insufficient_funds"
    assert case["status"] == "open", "a holdout case stays observable, it is not closed early"
    assert case["attempt_count"] == 0
    assert db.query("SELECT id FROM execution_record WHERE case_id = ?", (case["id"],)) == []
    assert db.query("SELECT id FROM intervention_decision WHERE case_id = ?", (case["id"],)) == []


def test_the_withheld_action_is_recorded_so_the_counterfactual_is_auditable(fresh_db, sim_clock):
    case = _intake(True)
    entry = db.query_one(
        "SELECT summary, detail FROM audit_log WHERE case_id = ? AND stage = 'decide'", (case["id"],))
    assert entry is not None, "a control case must still say what it would have done"
    assert "Control arm" in entry["summary"]
    assert "RETRY_LATER" in entry["detail"]


def test_a_control_case_closes_as_holdout_when_the_window_expires(fresh_db, sim_clock):
    case = _intake(True)
    sim_clock.advance(days=config.EPISODE_WINDOW_DAYS + 1)
    executor.tick()
    closed = cases.get(case["id"])
    assert closed["status"] == "stopped_holdout"
    assert closed["status"] != "stopped_cooldown_expired", "control must not be mixed in with failed treatment"


def test_a_control_case_can_still_recover_on_its_own(fresh_db, sim_clock):
    """The whole point of the arm: self-recovery must remain observable, or the
    baseline is structurally zero and the agent appears to earn every rupee."""
    case = _intake(True)
    sim_clock.advance(days=3)
    webhooks.intake(recovery_payload(case_id=case["id"]), source="synthetic")
    assert cases.get(case["id"])["status"] == "recovered"


def test_a_treated_case_is_unaffected(fresh_db, sim_clock):
    case = _intake(False)
    assert case["is_holdout"] == 0
    assert db.query("SELECT id FROM intervention_decision WHERE case_id = ?", (case["id"],)) != []


# ------------------------------------------------------------------- the metric
def _case(holdout: bool, status: str, paise: int = 10000):
    c = cases.create(subscription_id=db.new_id("sub"), customer_id="c",
                     amount_at_risk_paise=paise, synthetic=True, holdout=holdout)
    cases.set_category(c["id"], "insufficient_funds")
    if status != "open":
        cases.transition(c["id"], status, summary="test")
    return c


def test_incremental_is_unavailable_without_a_control_arm(fresh_db):
    _case(False, "recovered")
    assert metrics.incremental_recovery()["available"] is False


def test_incremental_is_the_difference_between_the_arms(fresh_db):
    for _ in range(6):
        _case(False, "recovered")
    for _ in range(4):
        _case(False, "stopped_handoff")
    for _ in range(2):
        _case(True, "recovered")
    for _ in range(8):
        _case(True, "stopped_holdout")

    inc = metrics.incremental_recovery(bootstrap=2000)
    assert inc["treated"]["rate"] == 0.6      # 6/10
    assert inc["control"]["rate"] == 0.2      # 2/10
    assert inc["lift"] == 0.4
    lo, hi = inc["lift_ci95"]
    assert lo <= inc["lift"] <= hi
    # Gross credits the agent all six; incremental credits it the four it actually won.
    assert inc["gross_recovered_paise"] == 60000
    assert inc["incremental_paise_total"] == 40000
    # Incremental can never exceed gross: the control arm only ever subtracts.
    assert inc["incremental_paise_total"] <= inc["gross_recovered_paise"]


def test_intention_to_treat_keeps_refused_cases_in_the_treated_arm(fresh_db):
    """A treated case the agent refused to act on (unknown cause) stays treated.
    Dropping those would flatter the result by exactly the cases handled most
    conservatively — the ones the safety rules are for."""
    _case(False, "recovered")
    _case(False, "stopped_unknown")
    _case(True, "stopped_holdout")

    inc = metrics.incremental_recovery(bootstrap=500)
    assert inc["treated"]["n"] == 2, "the refused case must not be dropped from the denominator"
    assert inc["treated"]["rate"] == 0.5


def test_the_metric_is_deterministic_for_a_seed(fresh_db):
    for _ in range(5):
        _case(False, "recovered")
        _case(True, "stopped_holdout")
    a = metrics.incremental_recovery(bootstrap=1000, seed=7)
    b = metrics.incremental_recovery(bootstrap=1000, seed=7)
    assert a["lift_ci95"] == b["lift_ci95"]


def test_incremental_never_exceeds_gross(fresh_db):
    """A high lift with uneven amounts used to produce incremental > gross, which is
    incoherent — the agent cannot win more than actually came back."""
    for _ in range(9):
        _case(False, "recovered", paise=1000)     # many small treated wins
    _case(False, "stopped_handoff", paise=900000)  # one huge case that did not recover
    _case(True, "stopped_holdout", paise=1000)
    _case(True, "stopped_holdout", paise=900000)

    inc = metrics.incremental_recovery(bootstrap=500)
    assert inc["incremental_paise_total"] <= inc["gross_recovered_paise"]

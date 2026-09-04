"""Phase 4 reporting — the numbers that turn assumptions into measurements.

The two tests that matter most here are about METHOD, not output:

* `lift_by_category` must report an empty arm as *unavailable*, not as zero lift. A
  category with no control cases has nothing to say, and saying zero is a claim it
  cannot support.
* `prior_calibration` must score every execution on a closed case, not only the last
  one. Scoring only the last keeps every success and discards every failure, which is
  not an attribution rule — it is a way of proving whatever you like.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import cases, clock, config, db, metrics  # noqa: E402


def _case(status, category, holdout=0, amount=49900, sub=None):
    case = cases.create(subscription_id=sub or f"s{db.new_id('x')}", customer_id="c",
                        amount_at_risk_paise=amount, synthetic=True, holdout=bool(holdout))
    db.update("recovery_case", case["id"], {"current_category": category, "status": status,
                                            "closed_at": clock.now_iso()})
    return cases.get(case["id"])


def _execution(case, action, attempt, exec_id=None):
    dec_id, exe_id = db.new_id("dec"), exec_id or db.new_id("exe")
    db.insert("intervention_decision", {
        "id": dec_id, "case_id": case["id"], "attempt_number": attempt,
        "category": case["current_category"], "action": action,
        "policy_row_ref": "x/1", "invariant_check": "{}",
        "decided_at": clock.now_iso(), "scheduled_for": clock.now_iso(),
        "status": "executed", "synthetic": 1})
    db.insert("execution_record", {
        "id": exe_id, "decision_id": dec_id, "case_id": case["id"], "action": action,
        "mode": "simulated", "status": "success", "result_payload": "{}", "cost_paise": 1200,
        "executed_at": clock.now_iso(), "synthetic": 1})
    return exe_id


# ------------------------------------------------------ 4.1 per-category lift
def test_lift_is_reported_per_category_with_arm_counts(fresh_db, sim_clock):
    for _ in range(6):
        _case("recovered", "card_expired")
    for _ in range(4):
        _case("stopped_handoff", "card_expired")
    for _ in range(10):
        _case("stopped_holdout", "card_expired", holdout=1)

    row = next(r for r in metrics.lift_by_category() if r["category"] == "card_expired")
    assert row["treated"] == {"n": 10, "recovered": 6, "rate": 0.6}
    assert row["control"] == {"n": 10, "recovered": 0, "rate": 0.0}
    assert row["lift"] == 0.6
    assert row["lift_ci95"][0] <= 0.6 <= row["lift_ci95"][1]


def test_a_category_where_the_agent_is_flat_is_published_not_hidden(fresh_db, sim_clock):
    """Publishing where it does nothing is what makes the rest believable."""
    for _ in range(8):
        _case("stopped_handoff", "issuer_declined")
    for _ in range(8):
        _case("stopped_holdout", "issuer_declined", holdout=1)
    row = next(r for r in metrics.lift_by_category() if r["category"] == "issuer_declined")
    assert row["lift"] == 0.0
    assert row["significant"] is False


def test_an_empty_arm_is_unavailable_rather_than_zero(fresh_db, sim_clock):
    """A category with no control cases has nothing to say. Reporting zero lift would
    be a claim it cannot support, and a reader would have no way to tell the two apart."""
    _case("stopped_holdout", "card_expired", holdout=1)   # so an arm exists somewhere
    for _ in range(4):
        _case("recovered", "authentication_failed")
    row = next(r for r in metrics.lift_by_category() if r["category"] == "authentication_failed")
    assert row["lift"] is None
    assert row["significant"] is None
    assert "empty" in row["reason"]


def test_lift_by_category_is_empty_without_a_control_arm(fresh_db, sim_clock):
    _case("recovered", "card_expired")
    assert metrics.lift_by_category() == []


# -------------------------------------------------------- 4.2 prior calibration
def test_calibration_scores_every_execution_not_only_the_last(fresh_db, sim_clock):
    """The selection bias this test exists to stop. Scoring only the last execution on
    a recovered case keeps the one that worked and discards the two that did not, so
    every prior comes back "pessimistic" with a realised rate of 1.000."""
    case = _case("recovered", "card_expired")
    _execution(case, "SEND_UPDATE_LINK", 1)
    _execution(case, "SEND_UPDATE_LINK", 2)
    _execution(case, "VOICE_CALL", 3)

    cal = metrics.prior_calibration()
    assert cal["n_scored"] == 3
    scored = {(p["category"], p["action"]): p for p in cal["by_pair"]}
    # Two link sends, of which the last-touch rule credits neither.
    assert scored[("card_expired", "SEND_UPDATE_LINK")]["realised"] == 0.0
    assert scored[("card_expired", "VOICE_CALL")]["realised"] == 1.0


def test_an_intervention_followed_by_another_one_scores_zero(fresh_db, sim_clock):
    """Which is what the next one being necessary means."""
    case = _case("stopped_handoff", "card_expired")
    _execution(case, "SEND_UPDATE_LINK", 1)
    _execution(case, "SEND_UPDATE_LINK", 2)
    cal = metrics.prior_calibration()
    assert all(r["recovered"] == 0 for r in metrics._realised_outcomes())
    assert cal["by_pair"][0]["realised"] == 0.0


def test_open_cases_are_not_scored(fresh_db, sim_clock):
    """An open case has not had its outcome yet, and counting it as a failure would
    score the priors against the passage of time."""
    case = cases.create(subscription_id="s1", customer_id="c", amount_at_risk_paise=49900,
                        synthetic=True)
    db.update("recovery_case", case["id"], {"current_category": "card_expired"})
    _execution(cases.get(case["id"]), "SEND_UPDATE_LINK", 1)
    assert metrics.prior_calibration()["available"] is False


def test_a_perfect_prior_scores_a_brier_of_zero(fresh_db, sim_clock, monkeypatch):
    monkeypatch.setitem(config.P_RECOVER_PRIOR, ("card_expired", "SEND_UPDATE_LINK"),
                        (1.0, 1.0, 1.0))
    case = _case("recovered", "card_expired")
    _execution(case, "SEND_UPDATE_LINK", 1)
    cal = metrics.prior_calibration()
    assert cal["brier_score"] == 0.0
    assert cal["ece"] == 0.0


def test_calibration_names_the_direction_of_each_error(fresh_db, sim_clock, monkeypatch):
    """Brier punishes confident errors; the per-pair gap says which way they run."""
    monkeypatch.setitem(config.P_RECOVER_PRIOR, ("card_expired", "SEND_UPDATE_LINK"),
                        (0.9, 0.9, 0.9))
    _execution(_case("stopped_handoff", "card_expired"), "SEND_UPDATE_LINK", 1)
    pair = metrics.prior_calibration()["by_pair"][0]
    assert pair["direction"] == "optimistic"
    assert pair["gap"] < 0


def test_the_reliability_table_bins_every_scored_execution(fresh_db, sim_clock):
    for _ in range(5):
        _execution(_case("recovered", "card_expired"), "SEND_UPDATE_LINK", 1)
    cal = metrics.prior_calibration()
    assert sum(b["n"] for b in cal["reliability"]) == cal["n_scored"]


# ------------------------------------------------------- 4.3 what it declined
def test_declined_counts_every_refusal_with_its_reason(fresh_db, sim_clock):
    _case("stopped_unknown", "unknown")
    _case("stopped_opt_out", "card_expired")
    _case("stopped_uneconomic", "card_expired")
    _case("stopped_already_settled", "card_expired")
    out = metrics.declined_to_contact()
    assert out["n_declined"] == 4
    assert len(out["case_ids"]) == 4
    reasons = {r["status"]: r for r in out["by_reason"]}
    assert reasons["stopped_unknown"]["n"] == 1
    assert "does not guess" in reasons["stopped_unknown"]["why"]


def test_the_control_arm_is_counted_separately_from_refusals(fresh_db, sim_clock):
    """Those cases were withheld to measure the rest, not refused by a rule. Folding
    them in would inflate the restraint figure with an experimental design choice."""
    _case("stopped_holdout", "card_expired", holdout=1)
    _case("stopped_unknown", "unknown")
    out = metrics.declined_to_contact()
    assert out["n_declined"] == 1
    assert out["n_control_arm"] == 1


# --------------------------------------------------------------- 4.5 latency
def test_stage_latency_reports_percentiles_per_stage(fresh_db, sim_clock):
    for _ in range(20):
        with clock.timed("diagnose"):
            pass
    stats = metrics.stage_latency()["by_stage"]["diagnose"]
    assert stats["n"] == 20
    assert 0 <= stats["p50_ms"] <= stats["p95_ms"] <= stats["max_ms"]


def test_a_stage_that_raises_is_still_timed_and_the_error_propagates(fresh_db, sim_clock):
    """A stage that reliably fails must not simply vanish from the percentiles."""
    with pytest.raises(ValueError):
        with clock.timed("execute"):
            raise ValueError("boom")
    assert metrics.stage_latency()["by_stage"]["execute"]["n"] == 1
    assert db.scalar("SELECT ok FROM stage_timing WHERE stage = 'execute'") == 0


def test_timing_never_breaks_the_pipeline(fresh_db, sim_clock, monkeypatch):
    monkeypatch.setattr(db, "insert",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    with clock.timed("detect"):
        pass          # must not raise


def test_latency_is_wall_clock_not_the_simulated_clock(fresh_db, sim_clock):
    """The simulated clock measures the modelled world's calendar; this measures how
    long the code took. Reporting either as the other would be nonsense."""
    with clock.timed("decide"):
        sim_clock.advance(hours=6)
    assert metrics.stage_latency()["by_stage"]["decide"]["max_ms"] < 1000


# ----------------------------------------------------------- served on the API
def test_every_new_report_is_on_the_summary(fresh_db, sim_clock):
    summary = metrics.summary()
    for key in ("lift_by_category", "calibration", "declined_to_contact", "latency",
                "promises", "fencing", "executions_by_action"):
        assert key in summary

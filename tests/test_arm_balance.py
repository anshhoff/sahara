"""The arm-balance check, and the defect it exists to stop from coming back.

Self-cure is a property of the world, not of the policy: an account refills on payday
and Razorpay's own retry then succeeds whether or not we sent anything. It was once
rolled for control cases ONLY, which made the treated arm recover through
interventions and the control arm through self-cure — two different generative
processes, not one world under two policies — and biased every lift number computed
from them.

These tests check the coin flips rather than the outcomes. By the time a treated case
has recovered there is no way to tell an organic recovery from an earned one, so a
test written against outcomes would pass under the defect.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_batch import ARM_BALANCE_MAX_Z, _two_proportion_z, acceptance_checks  # noqa: E402


RATE_CHECK = "arm balance: self-cure rolled in both arms, rates within 3 SE"
COVERAGE_CHECK = "arm balance: every diagnosed case had self-cure rolled exactly once"


class _FakeRunner:
    """Only the two fields acceptance_checks() reads. A real batch run is exercised
    end to end by scripts/run_batch.py itself; what is under test here is the
    statistic and its verdict, which must not need a 14-day simulation to check.

    Arguments are (rolled, cured) per arm — the same order the runner records them in,
    because a test that silently reverses a pair proves the opposite of what it says.
    """

    def __init__(self, treated, control, holdout_fraction=0.35):
        self.self_cure_rolls = {"treated": list(treated), "control": list(control)}
        self.holdout_fraction = holdout_fraction


def _verdicts(runner) -> dict:
    return {name: (ok, detail) for name, ok, detail in acceptance_checks(runner)
            if name.startswith("arm balance:")}


def _diagnosed(n: int) -> None:
    """n cases carrying a category, so the coverage check has a denominator."""
    from app import cases, db

    for i in range(n):
        case = cases.create(subscription_id=f"sub-{i}", customer_id=f"cust-{i}",
                            amount_at_risk_paise=49900, synthetic=True)
        db.update("recovery_case", case["id"], {"current_category": "insufficient_funds"})


# ------------------------------------------------------------------ the statistic
def test_identical_rates_give_a_z_of_zero():
    assert _two_proportion_z(20, 100, 20, 100) == pytest.approx(0.0)


def test_an_empty_arm_makes_the_statistic_undefined():
    """The exact shape of the original defect: the treated arm never rolled, so it has
    no denominator. Undefined must never be read as balanced."""
    assert _two_proportion_z(0, 0, 25, 100) is None


def test_no_successes_anywhere_makes_the_statistic_undefined():
    assert _two_proportion_z(0, 100, 0, 100) is None


def test_the_statistic_grows_with_the_gap():
    near = abs(_two_proportion_z(22, 100, 20, 100))
    far = abs(_two_proportion_z(60, 100, 20, 100))
    assert far > near


# --------------------------------------------------------------------- the check
def test_balanced_arms_pass(fresh_db):
    _diagnosed(200)
    verdicts = _verdicts(_FakeRunner((100, 22), (100, 25)))
    assert verdicts[RATE_CHECK][0] is True, verdicts[RATE_CHECK]
    assert verdicts[COVERAGE_CHECK][0] is True, verdicts[COVERAGE_CHECK]


def test_an_unrolled_treated_arm_turns_the_check_red(fresh_db):
    """Deliberately reproduce the defect: roll self-cure for the control arm only.
    This is the assertion that stops it regressing."""
    _diagnosed(100)
    ok, detail = _verdicts(_FakeRunner((0, 0), (100, 25)))[RATE_CHECK]
    assert ok is False
    assert "undefined" in detail


def test_a_deliberately_unbalanced_roll_turns_the_check_red(fresh_db):
    """Both arms rolled, but from visibly different distributions."""
    _diagnosed(200)
    ok, _ = _verdicts(_FakeRunner((100, 70), (100, 20)))[RATE_CHECK]
    assert ok is False
    assert abs(_two_proportion_z(70, 100, 20, 100)) > ARM_BALANCE_MAX_Z


def test_the_check_is_skipped_when_there_is_no_control_arm(fresh_db):
    """A batch run without --holdout has no arms to balance, and a check that cannot
    run must be absent rather than green."""
    assert _verdicts(_FakeRunner((0, 0), (0, 0), holdout_fraction=0.0)) == {}


def test_every_diagnosed_case_must_have_been_rolled(fresh_db):
    """A case whose coin was never flipped contributes to neither count, so it is
    invisible to the rate comparison above. This is the check that sees it."""
    _diagnosed(4)
    assert _verdicts(_FakeRunner((2, 1), (2, 1)))[COVERAGE_CHECK][0] is True
    assert _verdicts(_FakeRunner((2, 1), (1, 1)))[COVERAGE_CHECK][0] is False

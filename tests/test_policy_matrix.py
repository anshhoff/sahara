"""The decision table must be total and must only ever emit enum actions (docs/04 §4)."""
from __future__ import annotations

from app import config, policy


def test_policy_is_total_over_the_full_matrix():
    assert policy.is_total()
    for category in config.CATEGORIES:
        for attempt in range(1, config.MAX_ATTEMPTS + 1):
            action, delay = policy.lookup(category, attempt)
            assert action in config.ACTIONS, (category, attempt, action)
            assert isinstance(delay, int) and delay >= 0


def test_no_undefined_cells_and_no_extras():
    expected = {(c, a) for c in config.CATEGORIES for a in range(1, config.MAX_ATTEMPTS + 1)}
    assert set(config.POLICY) == expected


def test_unknown_never_acts():
    for attempt in range(1, config.MAX_ATTEMPTS + 1):
        assert policy.lookup("unknown", attempt)[0] == "STOP_HANDOFF"


def test_dead_instruments_are_never_retried():
    """Retrying an expired or dead card burns an attempt and annoys the issuer."""
    for category in ("card_expired", "invalid_payment_method"):
        for attempt in range(1, config.MAX_ATTEMPTS + 1):
            assert policy.lookup(category, attempt)[0] != "RETRY_LATER"


def test_promise_to_pay_is_reachable_exactly_once():
    cells = [(c, a) for (c, a), (action, _) in config.POLICY.items() if action == "PROMISE_TO_PAY"]
    assert cells == [("insufficient_funds", 3)]


def test_contact_actions_carry_no_policy_delay():
    """Spacing between contacts is invariant I2's job, not the policy table's, so
    that spacing survives a wrong policy row."""
    for (category, attempt), (action, delay) in config.POLICY.items():
        if action in config.CONTACT_ACTIONS:
            assert delay == 0, (category, attempt)

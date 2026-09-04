"""I5, I6 and I7 — when, and how often, a real person may be messaged.

These three exist because I1-I4 bound the agent per CASE, and a customer is not a
case. Each test below attacks the gap that leaves: a message at 2am, a second
subscription the per-case cooldown cannot see, and a runaway spend.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import cases, clock, config, db, executor, invariants, policy

# 21:30 IST and 14:30 IST. Written as UTC because that is what the clock stores, with
# the IST reading in the name so a failure says which side of the line it was on.
T_2130_IST = datetime(2026, 3, 2, 16, 0, 0, tzinfo=timezone.utc)
T_1430_IST = datetime(2026, 3, 2, 9, 0, 0, tzinfo=timezone.utc)


def _case(sub="SYNTH-sub-h", cust="SYNTH-cust-h", amount=49900, category="card_expired"):
    c = cases.create(subscription_id=sub, customer_id=cust,
                     amount_at_risk_paise=amount, synthetic=True)
    cases.set_category(c["id"], category)
    return cases.get(c["id"])


# --------------------------------------------------------------- I5 quiet hours
def test_i5_defers_a_contact_that_comes_due_at_night(fresh_db, sim_clock):
    sim_clock.set(T_2130_IST)
    assert clock.in_quiet_hours()
    case = _case()
    v = invariants.check(case["id"], "SEND_UPDATE_LINK", phase="pre_execution")
    assert v.is_defer and v.invariant == "I5"
    # Deferred to 09:00 IST the next morning, not to "some hours later".
    assert clock.to_ist(v.until).hour == config.QUIET_HOURS_END_IST
    assert clock.to_ist(v.until).minute == 0
    assert v.until > clock.now()


def test_i5_lets_a_daytime_contact_through(fresh_db, sim_clock):
    sim_clock.set(T_1430_IST)
    assert not clock.in_quiet_hours()
    assert invariants.check(_case()["id"], "SEND_UPDATE_LINK", phase="pre_execution").ok


def test_i5_does_not_gate_a_silent_retry(fresh_db, sim_clock):
    """A mandate re-charge at 2am wakes nobody. Quiet hours protect a person's evening,
    not the bank's."""
    sim_clock.set(T_2130_IST)
    case = _case(category="insufficient_funds")
    assert invariants.check(case["id"], "RETRY_LATER", phase="pre_execution").ok


def test_i5_and_i2_together_pick_the_later_target_not_the_first_one(fresh_db, sim_clock):
    """The bug this prevents only appears at night: if the gates returned on the first
    deferral, a contact inside BOTH the cooldown and quiet hours would be scheduled for
    whichever gate happened to run first — and half the time that is 3am."""
    sim_clock.set(T_1430_IST)
    case = _case()
    cases.record_execution(case["id"], "SEND_UPDATE_LINK")     # arms I2 from 14:30 IST
    sim_clock.set(T_2130_IST)                                  # now also inside I5
    v = invariants.check(case["id"], "SEND_UPDATE_LINK", phase="pre_execution")
    assert v.is_defer
    cooldown_target = clock.plus_hours(
        clock.parse_iso(cases.get(case["id"])["last_contact_at"]), config.COOLDOWN_HOURS)
    quiet_target = clock.next_ist_hour(clock.now(), config.QUIET_HOURS_END_IST)
    assert v.until == max(cooldown_target, quiet_target)
    # Both gates are recorded as having deferred it, not only the binding one.
    assert v.details["I2"].startswith("deferred") and v.details["I5"].startswith("deferred")


# --------------------------------------------------------------- I6 suppression
def test_i6_stops_a_suppressed_customer(fresh_db, sim_clock):
    sim_clock.set(T_1430_IST)
    case = _case()
    cases.suppress_customer("SYNTH-cust-h", "complaint", source="test")
    v = invariants.check(case["id"], "SEND_UPDATE_LINK", phase="pre_execution")
    assert v.is_stop and v.invariant == "I6" and v.status == "stopped_suppressed"


def test_i6_reaches_the_customers_other_subscriptions(fresh_db, sim_clock):
    """The gap I3 cannot close. One person, two failing subscriptions, two cases: an
    opt-out recorded on the first must stop the second, whose case never saw it."""
    sim_clock.set(T_1430_IST)
    first = _case(sub="SYNTH-sub-h1")
    second = _case(sub="SYNTH-sub-h2")
    cases.set_opted_out(first["id"], True)
    cases.suppress_customer("SYNTH-cust-h", "opt_out", source="test")

    assert invariants.check(second["id"], phase="pre_decision").invariant == "I6"
    assert policy.decide(second) is None
    assert cases.get(second["id"])["status"] == "stopped_suppressed"


def test_i6_stops_a_silent_retry_too(fresh_db, sim_clock):
    """Someone who asked to be left alone was not asking to be left alone noisily.
    A suppression gates every action, not only the ones that send something."""
    sim_clock.set(T_1430_IST)
    case = _case(category="insufficient_funds")
    cases.suppress_customer("SYNTH-cust-h", "opt_out", source="test")
    assert invariants.check(case["id"], "RETRY_LATER", phase="pre_execution").invariant == "I6"


def test_suppression_keeps_the_first_reason(fresh_db, sim_clock):
    """A complaint must never be downgraded to a routine opt-out by a later write."""
    sim_clock.set(T_1430_IST)
    cases.suppress_customer("SYNTH-cust-h", "complaint", source="test")
    row = cases.suppress_customer("SYNTH-cust-h", "manual", source="test")
    assert row["reason"] == "complaint"


# ------------------------------------------------------------ I7 daily ceilings
def test_i7_defers_once_the_customer_has_had_their_daily_contacts(fresh_db, sim_clock):
    """Per CUSTOMER, across cases — which is exactly what I2 cannot do, because it
    only ever looks at the one case in front of it."""
    sim_clock.set(T_1430_IST)
    others = [_case(sub=f"SYNTH-sub-h{i}") for i in range(config.MAX_CONTACTS_PER_CUSTOMER_PER_DAY)]
    for c in others:
        policy.decide(c)                       # card_expired/1 -> SEND_UPDATE_LINK, immediate
    assert invariants.contacts_today("SYNTH-cust-h") == config.MAX_CONTACTS_PER_CUSTOMER_PER_DAY

    fresh = _case(sub="SYNTH-sub-h-late")
    v = invariants.check(fresh["id"], "SEND_UPDATE_LINK", phase="pre_execution")
    assert v.is_defer and v.invariant == "I7"
    assert clock.to_ist(v.until).hour == config.QUIET_HOURS_END_IST


def test_i7_ceilings_reset_on_the_ist_day_not_a_rolling_window(fresh_db, sim_clock):
    sim_clock.set(T_1430_IST)
    for i in range(config.MAX_CONTACTS_PER_CUSTOMER_PER_DAY):
        policy.decide(_case(sub=f"SYNTH-sub-h{i}"))
    assert invariants.contacts_today("SYNTH-cust-h") == config.MAX_CONTACTS_PER_CUSTOMER_PER_DAY
    sim_clock.advance(days=1)
    assert invariants.contacts_today("SYNTH-cust-h") == 0


def test_i7_defers_when_the_daily_outreach_budget_would_be_exceeded(fresh_db, sim_clock, monkeypatch):
    """The budget is checked against what this contact WOULD cost, so the ceiling is
    never crossed and then noticed. At the shipped Rs 5,000/day it does not bind at
    demo scale, which is why the test sets it to the price of a single message."""
    sim_clock.set(T_1430_IST)
    monkeypatch.setattr(config, "DAILY_OUTREACH_BUDGET_PAISE",
                        config.ACTION_COST_PAISE["SEND_UPDATE_LINK"])
    policy.decide(_case(sub="SYNTH-sub-h1", cust="SYNTH-cust-A"))
    assert invariants.outreach_spend_today() == config.ACTION_COST_PAISE["SEND_UPDATE_LINK"]

    # A different customer, so the per-customer ceiling is not what stops this.
    other = _case(sub="SYNTH-sub-h2", cust="SYNTH-cust-B")
    v = invariants.check(other["id"], "SEND_UPDATE_LINK", phase="pre_execution")
    assert v.is_defer and v.invariant == "I7" and "ceiling" in v.reason


def test_a_deferral_past_the_episode_window_closes_the_case_instead(fresh_db, sim_clock):
    """Every timing gate shares this ending: if the next permitted moment is outside
    the episode window there is no later attempt to make, so the case closes rather
    than holding a contact that can never legally go out."""
    sim_clock.set(T_1430_IST)
    case = _case()
    sim_clock.advance(days=config.EPISODE_WINDOW_DAYS)
    sim_clock.set(clock.now().replace(hour=16, minute=0))      # 21:30 IST, inside I5
    v = invariants.check(case["id"], "SEND_UPDATE_LINK", phase="pre_execution")
    assert v.is_stop and v.status == "stopped_cooldown_expired"
    assert v.invariant in ("I2", "I5", "I7")

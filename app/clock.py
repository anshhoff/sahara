"""The injectable clock (docs/01 §2).

No module in app/ may call datetime.now() directly. Everything reads clock.now(),
so the identical code path serves live mode (real time) and batch mode (a simulated
clock that fast-forwards a 14-day recovery episode in milliseconds).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Optional

from app import config


class Clock:
    """Real wall-clock, UTC."""

    simulated = False

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SimulatedClock(Clock):
    """Deterministic clock for the batch runner and tests."""

    simulated = True

    def __init__(self, start: datetime):
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        self._now = start.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> datetime:
        self._now = self._now + timedelta(**kwargs)
        return self._now

    def set(self, when: datetime) -> None:
        self._now = when.astimezone(timezone.utc)


_clock: Clock = Clock()


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> None:
    global _clock
    _clock = clock


def now() -> datetime:
    return _clock.now()


def now_iso() -> str:
    return to_iso(_clock.now())


def is_simulated() -> bool:
    return _clock.simulated


def to_iso(dt: datetime) -> str:
    """Canonical storage format: UTC, second precision, trailing Z.

    Fixed width and lexicographically sortable, which is what lets SQLite compare
    `scheduled_for <= :now` as a plain string comparison.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> datetime:
    if s is None:
        raise ValueError("parse_iso(None)")
    t = s.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def plus_hours(dt: datetime, hours: float) -> datetime:
    return dt + timedelta(hours=hours)


def plus_days(dt: datetime, days: float) -> datetime:
    return dt + timedelta(days=days)


# ------------------------------------------------------------------------- IST
# Contact-time rules (I5) and the daily ceilings (I7) are stated in Indian Standard
# Time, because that is the timezone the customer is in and the one the compliance
# rules are written against. IST is UTC+05:30 all year with no daylight saving, so a
# fixed offset is exactly right and a timezone database would add a dependency
# without adding correctness.
IST = timezone(timedelta(minutes=config.IST_OFFSET_MINUTES))


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def ist_hour(dt: Optional[datetime] = None) -> int:
    return to_ist(dt or now()).hour


def ist_day(dt: Optional[datetime] = None) -> str:
    """The IST calendar date as `YYYY-MM-DD`. The key the daily ceilings reset on."""
    return to_ist(dt or now()).date().isoformat()


def ist_day_bounds(dt: Optional[datetime] = None) -> tuple[str, str]:
    """The UTC half-open interval `[start, end)` covering one IST calendar day, as
    the ISO strings stored in the database — so a daily total is a plain BETWEEN over
    an indexed column rather than a per-row timezone conversion in Python."""
    local = to_ist(dt or now())
    start = datetime.combine(local.date(), time(0, 0), tzinfo=IST)
    return to_iso(start), to_iso(start + timedelta(days=1))


def next_ist_hour(dt: datetime, hour: int) -> datetime:
    """The next instant strictly after `dt` at which the IST wall clock reads `hour`.

    Used to answer "when may this contact go out?" — never to answer "how long do we
    wait?", because a duration computed on a wall clock crossing a day boundary is
    the classic way to end up sending at 3am.
    """
    local = to_ist(dt)
    target = datetime.combine(local.date(), time(hour, 0), tzinfo=IST)
    if target <= local:
        target = target + timedelta(days=1)
    return target.astimezone(timezone.utc)


def in_quiet_hours(dt: Optional[datetime] = None) -> bool:
    """True when the IST wall clock is inside the do-not-disturb window. The window
    wraps midnight, so this is an OR and not a BETWEEN."""
    h = ist_hour(dt)
    return h >= config.QUIET_HOURS_START_IST or h < config.QUIET_HOURS_END_IST

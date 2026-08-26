"""The injectable clock (docs/01 §2).

No module in app/ may call datetime.now() directly. Everything reads clock.now(),
so the identical code path serves live mode (real time) and batch mode (a simulated
clock that fast-forwards a 14-day recovery episode in milliseconds).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


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

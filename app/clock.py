"""The injectable clock (docs/01 §2).

No module in app/ may call datetime.now() directly. Everything reads clock.now(),
so the identical code path serves live mode (real time) and batch mode (a simulated
clock that fast-forwards a 14-day recovery episode in milliseconds).
"""
from __future__ import annotations

import logging
# `time` the class is already imported from datetime below, so the module is aliased.
# Naming this `_time` rather than renaming the datetime import keeps every existing
# `time(...)` call in this file meaning what it has always meant.
import time as _time
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from app import config

log = logging.getLogger(__name__)


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


class timed:
    """Time one pipeline stage on the WALL clock and record it.

    A context manager rather than a decorator so it can wrap part of a function — the
    interesting stage boundaries here are not function boundaries. `time.perf_counter`
    rather than the simulated clock, always: a batch replay advances its clock by hours
    at a time, and asking it how long a database write took would answer "3600 seconds".

    Never raises and never swallows: a failure inside the recording is logged and
    dropped, the stage's own exception propagates, and the row is still written with
    ok = 0 so a stage that reliably fails does not simply vanish from the percentiles.
    """

    __slots__ = ("stage", "case_id", "_t0")

    def __init__(self, stage: str, case_id: Optional[str] = None):
        self.stage = stage
        self.case_id = case_id
        self._t0 = 0.0

    def __enter__(self) -> "timed":
        self._t0 = _time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        elapsed_ms = (_time.perf_counter() - self._t0) * 1000.0
        try:
            from app import db

            db.insert("stage_timing", {
                "stage": self.stage,
                "case_id": self.case_id,
                "duration_ms": elapsed_ms,
                "ok": 0 if exc_type is not None else 1,
                "recorded_at": now_iso(),
                "synthetic": 1 if is_simulated() else 0,
            })
        except Exception:  # pragma: no cover - telemetry must never break the pipeline
            log.debug("stage timing for %s could not be recorded", self.stage, exc_info=True)
        return False


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

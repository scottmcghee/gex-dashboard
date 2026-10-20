"""Injectable clocks and US equity session arithmetic.

Every time-dependent calculation takes the current time from a ``Clock``
instead of reading the wall clock, so the demo can run a market session at
any hour and tests can pin the time.

Time to expiry is measured in **trading time**: only minutes inside the
regular session (09:30-16:00 America/New_York, Monday-Friday) count, and an
option expires at the 16:00 close of its expiration date. A trading year is
252 sessions of 390 minutes. Exchange holidays are not modelled; they count
as ordinary weekdays.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Final, Protocol
from zoneinfo import ZoneInfo

import numpy as np

MARKET_TZ: Final = ZoneInfo("America/New_York")
SESSION_OPEN: Final = time(9, 30)
SESSION_CLOSE: Final = time(16, 0)
SESSION_MINUTES: Final = 390
TRADING_DAYS_PER_YEAR: Final = 252
TRADING_MINUTES_PER_YEAR: Final = TRADING_DAYS_PER_YEAR * SESSION_MINUTES


class Clock(Protocol):
    """Source of the current time; ``now`` is always timezone-aware."""

    def now(self) -> datetime: ...


class SystemClock:
    """The real wall clock."""

    def now(self) -> datetime:
        return datetime.now(MARKET_TZ)


@dataclass(frozen=True)
class FixedClock:
    """A clock stopped at ``at`` (naive values are read as New York time)."""

    at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "at", _as_market_time(self.at))

    def now(self) -> datetime:
        return self.at


@dataclass
class ReplayClock:
    """Replays one session in real time (times ``speed``), starting at ``start``.

    A ``start`` outside a session (before the open, after the close or on a
    weekend) rolls forward to the next session's 09:30 open, so the replay
    always shows a trading session. The clock then loops inside that session:
    at 16:00 it wraps back to 09:30 of the same day. This lets a synthetic
    market look alive on every refresh at any hour while staying reproducible
    for a given start time.
    """

    start: datetime
    speed: float = 1.0
    _origin: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.start = next_session_instant(_as_market_time(self.start))
        if self.speed <= 0:
            raise ValueError("speed must be positive")
        self._origin = _time.monotonic()

    def now(self) -> datetime:
        open_dt, _ = session_bounds(self.start.date())
        offset = (self.start - open_dt).total_seconds()
        elapsed = (_time.monotonic() - self._origin) * self.speed
        session_seconds = SESSION_MINUTES * 60
        return open_dt + timedelta(seconds=(offset + elapsed) % session_seconds)


def _as_market_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=MARKET_TZ)
    return value.astimezone(MARKET_TZ)


def is_trading_day(day: date) -> bool:
    """Monday to Friday (holidays are not modelled)."""
    return day.weekday() < 5


def market_date(now: datetime) -> date:
    """The New York calendar date of ``now``."""
    return _as_market_time(now).date()


def session_bounds(day: date) -> tuple[datetime, datetime]:
    """The 09:30 open and 16:00 close of ``day`` in New York time."""
    return (
        datetime.combine(day, SESSION_OPEN, tzinfo=MARKET_TZ),
        datetime.combine(day, SESSION_CLOSE, tzinfo=MARKET_TZ),
    )


def next_trading_day(day: date) -> date:
    """The first trading day strictly after ``day``."""
    nxt = day + timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def previous_trading_day(day: date) -> date:
    """The last trading day strictly before ``day``."""
    prev = day - timedelta(days=1)
    while not is_trading_day(prev):
        prev -= timedelta(days=1)
    return prev


def _session_minutes_on(day: date, start: datetime, end: datetime) -> float:
    """Minutes of ``day``'s session that fall inside [start, end]."""
    if not is_trading_day(day):
        return 0.0
    open_dt, close_dt = session_bounds(day)
    lo, hi = max(open_dt, start), min(close_dt, end)
    return max((hi - lo).total_seconds() / 60.0, 0.0)


def trading_minutes_between(start: datetime, end: datetime) -> float:
    """Regular-session minutes between two instants (0 if ``end`` <= ``start``)."""
    start, end = _as_market_time(start), _as_market_time(end)
    if end <= start:
        return 0.0
    first, last = start.date(), end.date()
    if first == last:
        return _session_minutes_on(first, start, end)
    minutes = _session_minutes_on(first, start, end) + _session_minutes_on(last, start, end)
    full_days = int(np.busday_count(first + timedelta(days=1), last))
    return minutes + full_days * SESSION_MINUTES


def next_session_instant(now: datetime) -> datetime:
    """``now`` if it falls inside a session, else the next session's 09:30 open."""
    now = _as_market_time(now)
    day = market_date(now)
    if is_trading_day(day):
        open_dt, close_dt = session_bounds(day)
        if now < open_dt:
            return open_dt
        if now < close_dt:
            return now
    return session_bounds(next_trading_day(day))[0]


def expiration_close(expiration: date) -> datetime:
    """The instant an option expiring on ``expiration`` stops trading."""
    return session_bounds(expiration)[1]


def is_expired(expiration: date, now: datetime) -> bool:
    """True once ``expiration``'s close is at or before ``now``."""
    return expiration_close(expiration) <= _as_market_time(now)


def years_to_expiry(now: datetime, expiration: date) -> float:
    """Trading-time years from ``now`` to the expiration close (0 once expired)."""
    return trading_minutes_between(now, expiration_close(expiration)) / TRADING_MINUTES_PER_YEAR


def session_fraction_remaining(now: datetime) -> float:
    """Share of today's session still to trade: 1 before the open, 0 after the close.

    Weekends return 0 because there is no session that day.
    """
    day = market_date(now)
    if not is_trading_day(day):
        return 0.0
    _, close_dt = session_bounds(day)
    return _session_minutes_on(day, now, close_dt) / SESSION_MINUTES


def simulation_window(now: datetime) -> tuple[datetime, int]:
    """Start time and length in minutes of the session a price simulation should cover.

    During the session that is the rest of today. Outside it, it is the whole
    next session (today's, before the open).
    """
    now = _as_market_time(now)
    day = market_date(now)
    if is_trading_day(day):
        open_dt, close_dt = session_bounds(day)
        if now < open_dt:
            return open_dt, SESSION_MINUTES
        if now < close_dt:
            return now, max(1, int((close_dt - now).total_seconds() // 60))
    open_dt, _ = session_bounds(next_trading_day(day))
    return open_dt, SESSION_MINUTES


def default_replay_start(today: date, at: time = time(9, 30)) -> datetime:
    """``at`` on ``today``, or on the last trading day when ``today`` is a weekend."""
    day = today if is_trading_day(today) else previous_trading_day(today)
    return datetime.combine(day, at, tzinfo=MARKET_TZ)

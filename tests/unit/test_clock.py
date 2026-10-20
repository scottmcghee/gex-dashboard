"""Clocks and trading-time arithmetic: no hidden wall-clock reads, weekends excluded."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from gex_dashboard import clock as ck

ET = ck.MARKET_TZ


def et(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=ET)


class TestClocks:
    def test_system_clock_is_aware(self):
        assert ck.SystemClock().now().tzinfo is not None

    def test_fixed_clock_reads_naive_as_new_york(self):
        c = ck.FixedClock(datetime(2026, 10, 19, 11, 0))
        assert c.now() == et("2026-10-19T11:00")
        utc = ck.FixedClock(datetime(2026, 10, 19, 15, 0, tzinfo=UTC))
        assert utc.now().hour == 11

    def test_replay_clock_advances_and_wraps(self, monkeypatch):
        ticks = iter([1000.0, 1000.0, 1060.0, 1000.0 + 6.5 * 3600])
        monkeypatch.setattr(ck._time, "monotonic", lambda: next(ticks))
        c = ck.ReplayClock(datetime(2026, 10, 19, 10, 0), speed=1.0)
        assert c.now() == et("2026-10-19T10:00")
        assert c.now() == et("2026-10-19T10:01")
        assert c.now() == et("2026-10-19T10:00")  # one full session later, wrapped

    def test_replay_speed(self, monkeypatch):
        ticks = iter([0.0, 60.0])
        monkeypatch.setattr(ck._time, "monotonic", lambda: next(ticks))
        c = ck.ReplayClock(datetime(2026, 10, 19, 15, 50), speed=20.0)
        assert c.now() == et("2026-10-19T09:40")  # 15:50 + 20 min wraps past 16:00

    @pytest.mark.parametrize(
        ("start", "rolled"),
        [
            ("2026-10-24T12:00", "2026-10-26T09:30"),  # Saturday -> Monday open
            ("2026-10-19T17:00", "2026-10-20T09:30"),  # after the close -> next open
            ("2026-10-23T16:00", "2026-10-26T09:30"),  # Friday close -> Monday open
            ("2026-10-19T07:00", "2026-10-19T09:30"),  # before the open -> that open
            ("2026-10-19T11:00", "2026-10-19T11:00"),  # in session: unchanged
        ],
    )
    def test_replay_start_outside_a_session_rolls_forward(self, monkeypatch, start, rolled):
        monkeypatch.setattr(ck._time, "monotonic", lambda: 0.0)
        c = ck.ReplayClock(et(start))
        assert c.start == et(rolled)
        assert c.now() == et(rolled)

    def test_replay_rejects_bad_speed(self):
        with pytest.raises(ValueError, match="positive"):
            ck.ReplayClock(datetime(2026, 10, 19, 10, 0), speed=0)


class TestTradingDays:
    def test_weekdays(self):
        assert ck.is_trading_day(date(2026, 10, 23))
        assert not ck.is_trading_day(date(2026, 10, 24))

    def test_next_and_previous(self):
        assert ck.next_trading_day(date(2026, 10, 23)) == date(2026, 10, 26)
        assert ck.previous_trading_day(date(2026, 10, 26)) == date(2026, 10, 23)

    def test_market_date_uses_new_york(self):
        late_utc = datetime(2026, 10, 20, 2, 0, tzinfo=UTC)  # 22:00 ET on the 19th
        assert ck.market_date(late_utc) == date(2026, 10, 19)


class TestTradingMinutes:
    @pytest.mark.parametrize(
        ("start", "end", "minutes"),
        [
            ("2026-10-19T11:00", "2026-10-19T16:00", 300),
            ("2026-10-19T08:00", "2026-10-19T17:00", 390),
            ("2026-10-19T17:00", "2026-10-20T10:00", 30),
            ("2026-10-23T15:00", "2026-10-26T16:00", 60 + 390),  # weekend skipped
            ("2026-10-19T09:30", "2026-10-23T16:00", 5 * 390),
            ("2026-10-24T10:00", "2026-10-25T15:00", 0),  # Saturday to Sunday
            ("2026-10-19T12:00", "2026-10-19T11:00", 0),
        ],
    )
    def test_cases(self, start, end, minutes):
        assert ck.trading_minutes_between(et(start), et(end)) == pytest.approx(minutes)

    def test_years_to_expiry(self):
        now = et("2026-10-19T11:00")
        assert ck.years_to_expiry(now, date(2026, 10, 19)) == pytest.approx(
            300 / ck.TRADING_MINUTES_PER_YEAR
        )
        assert ck.years_to_expiry(et("2026-10-19T16:30"), date(2026, 10, 19)) == 0.0
        assert ck.expiration_close(date(2026, 10, 19)) == et("2026-10-19T16:00")


class TestSession:
    @pytest.mark.parametrize(
        ("now", "fraction"),
        [
            ("2026-10-19T08:00", 1.0),
            ("2026-10-19T12:45", 0.5),
            ("2026-10-19T16:01", 0.0),
            ("2026-10-24T12:00", 0.0),
        ],
    )
    def test_fraction_remaining(self, now, fraction):
        assert ck.session_fraction_remaining(et(now)) == pytest.approx(fraction)

    @pytest.mark.parametrize(
        ("now", "start", "minutes"),
        [
            ("2026-10-19T11:00", "2026-10-19T11:00", 300),
            ("2026-10-19T07:00", "2026-10-19T09:30", 390),
            ("2026-10-19T17:00", "2026-10-20T09:30", 390),
            ("2026-10-23T17:00", "2026-10-26T09:30", 390),
            ("2026-10-25T12:00", "2026-10-26T09:30", 390),
            ("2026-10-19T15:59:30", "2026-10-19T15:59:30", 1),
        ],
    )
    def test_simulation_window(self, now, start, minutes):
        assert ck.simulation_window(et(now)) == (et(start), minutes)

    def test_default_replay_start(self):
        assert ck.default_replay_start(date(2026, 10, 19)) == et("2026-10-19T10:30")
        assert ck.default_replay_start(date(2026, 10, 25), time(9, 45)) == et("2026-10-23T09:45")


@pytest.mark.parametrize(
    ("now", "expired"),
    [
        ("2026-10-19T15:59:59-04:00", False),
        ("2026-10-19T16:00:00-04:00", True),
        ("2026-10-19T20:00:00+00:00", True),  # 16:00 New York time
        ("2026-10-19T12:00:00", False),  # naive times are New York time
        ("2026-10-18T23:00:00-04:00", False),
    ],
)
def test_is_expired(now, expired):
    assert ck.is_expired(date(2026, 10, 19), datetime.fromisoformat(now)) is expired

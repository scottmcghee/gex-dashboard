"""Environment-only settings and clock selection."""

from __future__ import annotations

from datetime import datetime

import pytest

from gex_dashboard.clock import MARKET_TZ, FixedClock, ReplayClock, SystemClock
from gex_dashboard.config import (
    STRIKE_RANGE_MAX,
    STRIKE_RANGE_MIN,
    Settings,
    get_settings,
    make_clock,
)


def test_defaults():
    s = Settings()
    assert s.provider == "synthetic"
    assert s.symbols == ("SPY", "SPX", "QQQ")
    assert s.clock_mode == "replay"
    assert s.clock_start is None
    assert s.strike_range == 0.10
    assert s.convex_user == ""
    assert s.convex_password == ""


def test_password_is_not_in_repr(monkeypatch):
    monkeypatch.setenv("CONVEX_PASSWORD", "hunter2")
    s = Settings()
    assert s.convex_password == "hunter2"
    assert "hunter2" not in repr(s)


def test_symbols_parsing(monkeypatch):
    monkeypatch.setenv("GEX_SYMBOLS", " spy, ,iwm ")
    assert Settings().symbols == ("SPY", "IWM")
    monkeypatch.setenv("GEX_SYMBOLS", " , ")
    assert Settings().symbols == ("SPY", "SPX", "QQQ")


def test_live_provider_defaults_to_system_clock(monkeypatch):
    monkeypatch.setenv("GEX_PROVIDER", "ConvexValue")
    s = Settings()
    assert s.provider == "convexvalue"
    assert s.clock_mode == "system"


def test_invalid_values(monkeypatch):
    monkeypatch.setenv("GEX_CLOCK", "sundial")
    with pytest.raises(ValueError, match="GEX_CLOCK"):
        Settings()
    monkeypatch.delenv("GEX_CLOCK")
    monkeypatch.setenv("GEX_CLOCK_SPEED", "0")
    with pytest.raises(ValueError, match="GEX_CLOCK_SPEED"):
        Settings()


@pytest.mark.parametrize("raw", ["0.01", "0.3", "-0.1", "10"])
def test_strike_range_outside_the_slider_bounds_names_the_variable(monkeypatch, raw):
    monkeypatch.setenv("GEX_STRIKE_RANGE", raw)
    with pytest.raises(ValueError, match=r"GEX_STRIKE_RANGE must be between 0\.02 and 0\.25"):
        Settings()


@pytest.mark.parametrize("raw", ["0.02", "0.05", "0.25"])
def test_strike_range_within_the_slider_bounds(monkeypatch, raw):
    monkeypatch.setenv("GEX_STRIKE_RANGE", raw)
    assert STRIKE_RANGE_MIN <= Settings().strike_range == float(raw) <= STRIKE_RANGE_MAX


def test_get_settings_is_cached(monkeypatch):
    first = get_settings()
    monkeypatch.setenv("GEX_SYMBOLS", "IWM")
    assert get_settings() is first


class TestMakeClock:
    def test_system(self, monkeypatch):
        monkeypatch.setenv("GEX_CLOCK", "system")
        assert isinstance(make_clock(Settings()), SystemClock)

    def test_fixed_with_start(self, monkeypatch):
        monkeypatch.setenv("GEX_CLOCK", "fixed")
        monkeypatch.setenv("GEX_CLOCK_START", "2026-10-19T14:15")
        c = make_clock(Settings())
        assert isinstance(c, FixedClock)
        assert c.now() == datetime(2026, 10, 19, 14, 15, tzinfo=MARKET_TZ)

    def test_full_iso_datetime_with_offset(self, monkeypatch):
        monkeypatch.setenv("GEX_CLOCK", "fixed")
        monkeypatch.setenv("GEX_CLOCK_START", "2026-10-19T15:00:00+00:00")
        assert make_clock(Settings()).now() == datetime(2026, 10, 19, 11, 0, tzinfo=MARKET_TZ)

    def test_invalid_start_names_the_variable(self, monkeypatch):
        monkeypatch.setenv("GEX_CLOCK_START", "half past ten")
        with pytest.raises(ValueError, match="GEX_CLOCK_START must be an ISO datetime"):
            Settings()

    def test_replay_default_start(self, monkeypatch):
        monkeypatch.setenv("GEX_CLOCK_SPEED", "5")
        c = make_clock(Settings())
        assert isinstance(c, ReplayClock)
        assert c.speed == 5.0
        assert c.start.weekday() < 5
        assert (c.start.hour, c.start.minute) == (9, 30)

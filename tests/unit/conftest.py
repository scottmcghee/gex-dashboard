"""Shared fixtures: isolated settings, a pinned clock and small sample frames."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from gex_dashboard.clock import FixedClock
from gex_dashboard.config import get_settings
from gex_dashboard.providers.synthetic import SyntheticProvider

_SETTINGS_ENV = (
    "GEX_PROVIDER",
    "GEX_SYMBOLS",
    "GEX_CLOCK",
    "GEX_CLOCK_START",
    "GEX_CLOCK_SPEED",
    "GEX_STRIKE_RANGE",
    "GEX_RISK_FREE_RATE",
    "GEX_SYNTHETIC_SEED",
    "CONVEX_USER",
    "CONVEX_PASSWORD",
)

# Monday 2026-10-19, mid-session.
SESSION_TIME = datetime.fromisoformat("2026-10-19T11:00:00-04:00")
EXPIRY = date(2026, 10, 23)


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    for name in _SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(SESSION_TIME)


@pytest.fixture
def synthetic(clock) -> SyntheticProvider:
    return SyntheticProvider(clock, seed=0)


def make_chain(rows: list[dict[str, object]]) -> pd.DataFrame:
    """A canonical chain from partial rows; unspecified columns get neutral values."""
    defaults: dict[str, object] = {
        "expiration": EXPIRY,
        "bid": 1.0,
        "ask": 1.1,
        "last": 1.05,
        "volume": 0,
        "open_interest": 0,
        "iv": 0.2,
        "delta": 0.0,
        "gamma": 0.0,
        "vega": 0.0,
        "theta": 0.0,
        "vanna": 0.0,
        "charm": 0.0,
        "volume_buy": 0,
        "volume_sell": 0,
        "premium_buy": 0.0,
        "premium_sell": 0.0,
    }
    return pd.DataFrame([{**defaults, **row} for row in rows])


def gex_profile(strikes: list[float], gex: list[float]) -> pd.DataFrame:
    """A GEX-by-strike profile; positive GEX is booked as calls, negative as puts."""
    return pd.DataFrame(
        {
            "strike": strikes,
            "gex": gex,
            "call_gex": [max(g, 0.0) for g in gex],
            "put_gex": [min(g, 0.0) for g in gex],
            "open_interest": [1000] * len(strikes),
            "volume": [500] * len(strikes),
        }
    )

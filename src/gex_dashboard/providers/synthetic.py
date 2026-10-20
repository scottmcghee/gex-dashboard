"""A deterministic synthetic option market for demos and tests.

Each symbol gets its own seeded market:

- **Spot** opens each session at a seeded level near the symbol's base price
  and follows a seeded one-minute random walk through the session. The price
  depends only on the seed, the symbol and the clock time, so refreshes move
  (with a replay clock) yet any given instant is reproducible.
- **Expirations**: on a trading day, today's (0DTE) until the 16:00 close
  (so also before the open), then the next trading day, the next four
  unexpired Fridays and the next three monthly (third-Friday) dates.
- **Volatility** follows a smile in log-moneyness ``m = ln(K / S)``:
  ``iv = atm + skew * m + curvature * m^2``.
- **Open interest** is put-heavy below the session's opening price and
  call-heavy above it, with extra interest at round strikes and on monthlies,
  so a gamma flip, call and put walls and a vanna flip all appear. Same-day
  (0DTE) interest sits nearer the money: its peaks are at
  ``ZERO_DTE_CENTER_SCALE`` of the other expirations' distance from the open
  and ``ZERO_DTE_WIDTH_SCALE`` of their width. A same-day option's gamma is
  concentrated within a fraction of a percent of spot, so interest placed
  2-3% away would leave the 0DTE column of the demo nearly empty.
- **Volume** accumulates through the session; 0DTE turns over the most and its
  customer flow leans to buying. A few contracts get a volume spike.

Greeks come from ``gex_dashboard.bsm`` and are converted to the chain units in
``gex_dashboard.providers.base``. Exchange holidays are not modelled.
"""

from __future__ import annotations

import logging
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

import numpy as np
import pandas as pd

from gex_dashboard import bsm
from gex_dashboard.clock import (
    SESSION_MINUTES,
    TRADING_DAYS_PER_YEAR,
    TRADING_MINUTES_PER_YEAR,
    Clock,
    is_trading_day,
    market_date,
    next_trading_day,
    previous_trading_day,
    session_bounds,
    years_to_expiry,
)
from gex_dashboard.exposures import CONTRACT_MULTIPLIER
from gex_dashboard.providers.base import (
    DEFAULT_STRIKE_RANGE,
    Capability,
    chain_columns,
    empty_chain,
)

logger = logging.getLogger(__name__)

VOL_POINT: Final = 0.01
GRID_HALF_WIDTH: Final = 0.25  # strikes are listed out to +/-25% of the opening price
MIN_YEARS: Final = 1.0 / TRADING_MINUTES_PER_YEAR  # one trading minute
WEEKLY_COUNT: Final = 4
MONTHLY_COUNT: Final = 3
SPIKES_PER_EXPIRATION: Final = 3
SPIKE_MULTIPLIER: Final = 6.0
OPEN_GAP_SIGMA: Final = 0.005  # sessions open within about +/-1% of the base price
ZERO_DTE_CENTER_SCALE: Final = 0.3  # 0DTE OI peaks at 30% of the usual distance from the open
ZERO_DTE_WIDTH_SCALE: Final = 0.5  # and half the usual width
# Open-interest multiplier at round strikes: (every n strike steps, multiplier), the coarser
# grid taking precedence. Other strikes keep 1.
ROUND_STRIKE_BONUS: Final = ((10, 2.0), (5, 1.25))


@dataclass(frozen=True)
class SymbolProfile:
    """Shape parameters for one synthetic underlying."""

    base_spot: float
    strike_step: float
    atm_iv: float
    skew: float
    curvature: float
    base_oi: float
    put_call_ratio: float
    call_center: float = 0.025  # call OI peak, as log-moneyness from the open
    put_center: float = -0.03  # put OI peak
    oi_width: float = 0.015


PROFILES: Final[dict[str, SymbolProfile]] = {
    "SPY": SymbolProfile(580.0, 1.0, 0.15, -0.6, 1.5, 6000, 1.5),
    "SPX": SymbolProfile(5800.0, 5.0, 0.15, -0.7, 1.5, 2500, 1.8),
    "QQQ": SymbolProfile(500.0, 1.0, 0.19, -0.5, 1.2, 4000, 0.9),
    "IWM": SymbolProfile(220.0, 1.0, 0.22, -0.4, 1.0, 3000, 1.3),
}


def _profile_for(symbol: str) -> SymbolProfile:
    """The listed profile, or a seeded mid-priced one for any other symbol."""
    if symbol in PROFILES:
        return PROFILES[symbol]
    rng = np.random.default_rng(zlib.crc32(symbol.encode()))
    return SymbolProfile(
        base_spot=float(np.round(rng.uniform(50, 400))),
        strike_step=1.0,
        atm_iv=float(rng.uniform(0.18, 0.40)),
        skew=-0.4,
        curvature=1.0,
        base_oi=2000,
        put_call_ratio=float(rng.uniform(0.8, 1.6)),
    )


def _third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    first_friday = first + timedelta(days=(4 - first.weekday()) % 7)
    return first_friday + timedelta(days=14)


def _is_monthly(day: date) -> bool:
    return day == _third_friday(day.year, day.month)


def expiration_schedule(now: datetime) -> list[date]:
    """The synthetic expiration calendar as seen at ``now`` (unexpired only)."""
    today = market_date(now)
    _, close_today = session_bounds(today)
    dates: set[date] = set()
    if is_trading_day(today) and now < close_today:
        dates.add(today)
    dates.add(next_trading_day(today))
    friday = today + timedelta(days=(4 - today.weekday()) % 7)
    weeklies = 0
    while weeklies < WEEKLY_COUNT:
        if friday > today or (friday == today and today in dates):
            dates.add(friday)
            weeklies += 1
        friday += timedelta(days=7)
    year, month, found = today.year, today.month, 0
    while found < MONTHLY_COUNT:
        monthly = _third_friday(year, month)
        if monthly > today:
            dates.add(monthly)
            found += 1
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return sorted(dates)


def _round_strike_bonus(strikes: np.ndarray, step: float) -> np.ndarray:
    """The ``ROUND_STRIKE_BONUS`` multiplier for each strike."""
    bonus = np.ones_like(strikes, dtype=np.float64)
    for steps, multiplier in reversed(ROUND_STRIKE_BONUS):
        bonus[np.isclose(np.mod(strikes, steps * step), 0)] = multiplier
    return bonus


class SyntheticProvider:
    """Seeded BSM option chains; see the module docstring for the market's shape."""

    name = "synthetic"
    capabilities = Capability.MULTI_EXPIRATION | Capability.SECOND_ORDER | Capability.FLOW

    def __init__(
        self, clock: Clock, seed: int = 0, risk_free_rate: float = bsm.DEFAULT_RISK_FREE_RATE
    ) -> None:
        self.clock = clock
        self.seed = seed
        self.risk_free_rate = risk_free_rate

    # ----------------------------------------------------------------- seeding

    def _rng(self, symbol: str, *parts: int) -> np.random.Generator:
        return np.random.default_rng([self.seed, zlib.crc32(symbol.encode()), *parts])

    @staticmethod
    def _session_day(now: datetime) -> date:
        """The session whose prices are current: today, or the last one on a weekend."""
        today = market_date(now)
        return today if is_trading_day(today) else previous_trading_day(today)

    @staticmethod
    def _session_minute(now: datetime, day: date) -> float:
        """Minutes since ``day``'s open, clamped to the session."""
        open_dt, _ = session_bounds(day)
        minutes = (now - open_dt).total_seconds() / 60.0
        return float(np.clip(minutes, 0.0, SESSION_MINUTES))

    def _open_price(self, symbol: str, day: date) -> float:
        profile = _profile_for(symbol)
        gap = self._rng(symbol, day.toordinal(), 0).standard_normal()
        return profile.base_spot * float(np.exp(OPEN_GAP_SIGMA * gap))

    def _intraday_path(self, symbol: str, day: date) -> np.ndarray:
        """Log-returns from the open at each session minute (391 points)."""
        sigma_minute = _profile_for(symbol).atm_iv / np.sqrt(TRADING_MINUTES_PER_YEAR)
        steps = self._rng(symbol, day.toordinal(), 1).standard_normal(SESSION_MINUTES)
        return np.concatenate([[0.0], np.cumsum(steps * sigma_minute)])

    # ---------------------------------------------------------------- protocol

    def spot(self, symbol: str, now: datetime | None = None) -> float:
        return self._spot_at(symbol.upper(), now or self.clock.now())

    def _spot_at(self, symbol: str, now: datetime) -> float:
        day = self._session_day(now)
        minute = self._session_minute(now, day) if day == market_date(now) else SESSION_MINUTES
        path = self._intraday_path(symbol, day)
        log_return = float(np.interp(minute, np.arange(SESSION_MINUTES + 1), path))
        return round(self._open_price(symbol, day) * float(np.exp(log_return)), 2)

    def expirations(self, symbol: str, now: datetime | None = None) -> list[date]:
        return expiration_schedule(now or self.clock.now())

    def chain(
        self,
        symbol: str,
        expirations: Sequence[date] | None = None,
        strike_range: float = DEFAULT_STRIKE_RANGE,
        now: datetime | None = None,
    ) -> pd.DataFrame:
        symbol = symbol.upper()
        now = now or self.clock.now()
        listed = expiration_schedule(now)
        wanted = listed if expirations is None else [e for e in listed if e in set(expirations)]
        if not wanted:
            return empty_chain(self.capabilities)
        spot = self._spot_at(symbol, now)
        frames = [self._expiration_rows(symbol, now, spot, exp) for exp in wanted]
        df = pd.concat(frames, ignore_index=True)
        in_range = (df["strike"] / spot - 1.0).abs() <= strike_range
        df = df.loc[in_range, chain_columns(self.capabilities)].reset_index(drop=True)
        logger.debug("synthetic chain %s: %d contracts at spot %.2f", symbol, len(df), spot)
        return df

    # ------------------------------------------------------------------- shape

    def _strike_grid(self, symbol: str, anchor: float) -> np.ndarray:
        step = _profile_for(symbol).strike_step
        lo = np.floor(anchor * (1 - GRID_HALF_WIDTH) / step) * step
        hi = np.ceil(anchor * (1 + GRID_HALF_WIDTH) / step) * step
        return np.round(np.arange(lo, hi + step / 2, step), 4)

    def _open_interest(
        self, profile: SymbolProfile, strikes: np.ndarray, anchor: float, exp: date, today: date
    ) -> tuple[np.ndarray, np.ndarray]:
        """Call and put open interest per strike before jitter."""
        m = np.log(strikes / anchor)

        def bump(center: float, width: float) -> np.ndarray:
            return np.asarray(np.exp(-(((m - center) / width) ** 2)), dtype=np.float64)

        is_0dte = exp == today
        center_scale = ZERO_DTE_CENTER_SCALE if is_0dte else 1.0
        width = profile.oi_width * (ZERO_DTE_WIDTH_SCALE if is_0dte else 1.0)
        near_money = 0.1 * bump(0.0, 0.06)
        calls = near_money + bump(profile.call_center * center_scale, width)
        puts = profile.put_call_ratio * (
            near_money + bump(profile.put_center * center_scale, width)
        )
        round_bonus = _round_strike_bonus(strikes, profile.strike_step)
        if is_0dte:
            weight = 0.3
        elif _is_monthly(exp):
            weight = 2.5
        else:
            weight = 1.0
        scale = profile.base_oi * weight * round_bonus
        return calls * scale, puts * scale

    def _expiration_rows(self, symbol: str, now: datetime, spot: float, exp: date) -> pd.DataFrame:
        profile = _profile_for(symbol)
        day = self._session_day(now)
        today = market_date(now)
        anchor = self._open_price(symbol, day)
        strikes = self._strike_grid(symbol, anchor)
        n = len(strikes)
        rng = self._rng(symbol, day.toordinal(), exp.toordinal(), 2)

        call_oi, put_oi = self._open_interest(profile, strikes, anchor, exp, today)
        jitter = rng.lognormal(0.0, 0.25, size=(2, n))
        oi = np.round(np.concatenate([call_oi * jitter[0], put_oi * jitter[1]])).astype(np.int64)

        k = np.concatenate([strikes, strikes])
        is_call = np.concatenate([np.ones(n, dtype=bool), np.zeros(n, dtype=bool)])
        t = max(years_to_expiry(now, exp), MIN_YEARS)
        m = np.log(k / spot)
        iv = np.maximum(profile.atm_iv + profile.skew * m + profile.curvature * m**2, 0.05)
        r = self.risk_free_rate

        mid = bsm.price(spot, k, t, iv, is_call, r)
        half_spread = np.maximum(0.01, 0.01 * mid) / 2
        bid = np.round(np.maximum(mid - half_spread, 0.0), 2)
        ask = np.round(mid + half_spread, 2)
        last = np.round(mid * (1 + rng.normal(0.0, 0.01, size=2 * n)), 2)

        volume = self._volume(rng, oi, m, exp, now, day)
        buy_share = np.clip(
            (0.62 if exp == today else 0.5) + rng.normal(0.0, 0.05, size=2 * n), 0.2, 0.8
        )
        volume_buy = np.round(volume * buy_share).astype(np.int64)
        volume_sell = volume - volume_buy
        premium = mid * CONTRACT_MULTIPLIER

        return pd.DataFrame(
            {
                "expiration": exp,
                "strike": k,
                "right": np.where(is_call, "C", "P"),
                "bid": bid,
                "ask": ask,
                "last": np.maximum(last, 0.0),
                "volume": volume,
                "open_interest": oi,
                "iv": iv,
                "delta": bsm.delta(spot, k, t, iv, is_call, r),
                "gamma": bsm.gamma(spot, k, t, iv, r),
                "vega": bsm.vega(spot, k, t, iv, r) * VOL_POINT,
                "theta": bsm.theta(spot, k, t, iv, is_call, r) / TRADING_DAYS_PER_YEAR,
                "vanna": bsm.vanna(spot, k, t, iv, r) * VOL_POINT,
                "charm": bsm.charm(spot, k, t, iv, is_call, r) / TRADING_DAYS_PER_YEAR,
                "volume_buy": volume_buy,
                "volume_sell": volume_sell,
                "premium_buy": np.round(volume_buy * premium, 2),
                "premium_sell": np.round(volume_sell * premium, 2),
            }
        )

    def _volume(
        self,
        rng: np.random.Generator,
        oi: np.ndarray,
        m: np.ndarray,
        exp: date,
        now: datetime,
        day: date,
    ) -> np.ndarray:
        """Contracts traded so far today: a full-day amount scaled by session progress."""
        today = market_date(now)
        if exp == today:
            turnover = 2.0
        elif exp == next_trading_day(today):
            turnover = 0.6
        else:
            turnover = 0.15
        activity = 0.2 + np.exp(-((m / 0.03) ** 2))
        full_day = oi * turnover * activity * rng.lognormal(0.0, 0.3, size=len(oi))
        spikes = rng.choice(len(oi), size=min(SPIKES_PER_EXPIRATION, len(oi)), replace=False)
        full_day[spikes] *= SPIKE_MULTIPLIER
        elapsed = 1.0 if day != today else self._session_minute(now, day) / SESSION_MINUTES
        return np.round(full_day * elapsed).astype(np.int64)

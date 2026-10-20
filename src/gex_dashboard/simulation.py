"""Monte Carlo price paths whose volatility depends on dealer GEX at the price.

At each one-minute step a path's volatility is

    sigma_eff(S) = sigma_base * (1 - dampening * tanh(GEX(S) / max|GEX|))

where ``GEX(S)`` is the per-strike GEX profile (``exposures.gex_by_strike``)
interpolated linearly at the path's price ``S`` and taken as 0 outside the
listed strikes. Volatility is therefore lower where that interpolated GEX is
positive and higher where it is negative. ``dampening`` is a free parameter,
not an estimate. The profile is normalized by its own largest absolute value,
so the result does not depend on GEX units. Drift is zero. Time steps are
trading minutes (252 x 390 per year).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import numpy as np
import numpy.typing as npt
import pandas as pd

from gex_dashboard.clock import TRADING_MINUTES_PER_YEAR

FloatArray = npt.NDArray[np.float64]
DEFAULT_PERCENTILES: Final = (5, 25, 50, 75, 95)


def interpolate_gex(profile: pd.DataFrame, prices: FloatArray) -> FloatArray:
    """The per-strike GEX profile interpolated linearly at ``prices``; 0 outside its strikes.

    This interpolates between strikes' GEX; it does not reprice gamma at those
    prices (``exposures.gex_curve`` does that).
    """
    prices = np.asarray(prices, dtype=np.float64)
    if profile.empty or not {"strike", "gex"}.issubset(profile.columns):
        return np.zeros_like(prices)
    order = np.argsort(profile["strike"].to_numpy(dtype=np.float64))
    strikes = profile["strike"].to_numpy(dtype=np.float64)[order]
    gex = profile["gex"].to_numpy(dtype=np.float64)[order]
    result = np.interp(prices, strikes, gex)
    outside = (prices < strikes[0]) | (prices > strikes[-1])
    return np.asarray(np.where(outside, 0.0, result), dtype=np.float64)


def simulate_paths(
    spot: float,
    profile: pd.DataFrame,
    sigma_base: float,
    n_steps: int,
    dt_minutes: float = 1.0,
    dampening: float = 0.4,
    n_paths: int = 500,
    seed: int | None = None,
) -> FloatArray:
    """Simulated prices, shape ``(n_paths, n_steps + 1)``; column 0 is ``spot``."""
    rng = np.random.default_rng(seed)
    sqrt_dt = np.sqrt(dt_minutes / TRADING_MINUTES_PER_YEAR)
    scale = 1.0
    if not profile.empty and "gex" in profile:
        peak = float(np.abs(profile["gex"].to_numpy(dtype=np.float64)).max())
        scale = peak if peak > 0 else 1.0
    shocks = rng.standard_normal((n_steps, n_paths))
    paths = np.empty((n_paths, n_steps + 1))
    paths[:, 0] = spot
    for step in range(n_steps):
        current = paths[:, step]
        sigma = sigma_base * (1.0 - dampening * np.tanh(interpolate_gex(profile, current) / scale))
        paths[:, step + 1] = current * (1.0 + sigma * sqrt_dt * shocks[step])
    return paths


def percentile_bands(
    paths: FloatArray, percentiles: tuple[int, ...] = DEFAULT_PERCENTILES
) -> dict[int, FloatArray]:
    """Each percentile of the paths at every step."""
    return {p: np.percentile(paths, p, axis=0) for p in percentiles}


def time_labels(n_steps: int, start: datetime, dt_minutes: float = 1.0) -> list[str]:
    """``HH:MM`` for each of the ``n_steps + 1`` points."""
    return [
        (start + timedelta(minutes=i * dt_minutes)).strftime("%H:%M") for i in range(n_steps + 1)
    ]


@dataclass(frozen=True)
class PathSummary:
    p5: float
    median: float
    p95: float


def summarize_paths(paths: FloatArray) -> PathSummary:
    """Percentiles of the simulated prices at the final step."""
    final = paths[:, -1]
    return PathSummary(
        p5=float(np.percentile(final, 5)),
        median=float(np.percentile(final, 50)),
        p95=float(np.percentile(final, 95)),
    )

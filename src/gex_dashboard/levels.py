"""Price levels derived from exposure profiles and the chain.

- **Gamma flip**: the price at which net dealer GEX, repriced at hypothetical
  spot prices (``exposures.gex_curve``), crosses zero from negative below to
  positive above. It is a price, not necessarily a listed strike.
  ``gamma_flip_note`` says why there is none.
- **Call / put walls**: strikes with the largest positive call GEX and the
  most negative put GEX.
- **Vanna flip**: where per-strike dealer vanna exposure changes sign,
  nearest to spot.
- **Vega peaks**: the strikes with the most gross vega above and below spot.
- **Expected move**: one standard deviation of price to expiration,
  ``S * atm_iv * sqrt(T)``, recomputed from the chain on every call.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final

import numpy as np
import pandas as pd

from gex_dashboard.clock import years_to_expiry

logger = logging.getLogger(__name__)

DEFAULT_WALLS: Final = 3
NO_REPRICING_NOTE: Final = (
    "No usable implied volatility to reprice: no contract in view has open interest, a "
    "positive IV and time left to expiry, so the repriced curve is zero everywhere."
)


@dataclass(frozen=True)
class Wall:
    strike: float
    gex: float


@dataclass(frozen=True)
class GammaWalls:
    """Call walls by largest call GEX; put walls by most negative put GEX."""

    call: tuple[Wall, ...] = field(default_factory=tuple)
    put: tuple[Wall, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class VegaPeaks:
    upper: float | None = None
    lower: float | None = None


def curve_value_at(curve: pd.DataFrame, price: float) -> float | None:
    """The repriced net GEX at ``price``, interpolated linearly; None for an empty curve."""
    if curve.empty:
        return None
    df = curve.sort_values("price")
    return float(np.interp(price, df["price"].to_numpy(), df["gex"].to_numpy()))


def gamma_flip(curve: pd.DataFrame, spot: float, net_gex: float | None = None) -> float | None:
    """The price nearest spot where net GEX turns from negative below to positive above.

    ``curve`` is ``exposures.gex_curve`` output (columns ``price, gex``). The
    sign of the curve's own value at spot picks the side: when it is negative
    the flip is the first such crossing above spot, otherwise the nearest one
    at or below spot. Spot is therefore below the flip exactly when repriced
    net GEX at spot is negative. ``net_gex`` (net GEX from the chain's own
    gamma) is only compared with that sign: a disagreement, possible when a
    vendor's gamma follows other conventions than the repricing, is logged.
    The crossing is interpolated linearly between grid prices. None when
    there is no crossing on that side within the curve (see
    ``gamma_flip_note``).
    """
    at_spot = curve_value_at(curve, spot)
    if at_spot is None:
        return None
    if net_gex is not None and (net_gex < 0) != (at_spot < 0):
        logger.warning(
            "net GEX from the chain's gamma (%.4g) and the repriced curve at spot (%.4g) differ "
            "in sign; the gamma flip follows the curve",
            net_gex,
            at_spot,
        )
    df = curve.sort_values("price")
    price = df["price"].to_numpy(dtype=np.float64)
    gex = df["gex"].to_numpy(dtype=np.float64)
    lo, hi = gex[:-1], gex[1:]
    rising = np.flatnonzero((lo < 0) & (hi >= 0))
    crossings = price[rising] + (price[rising + 1] - price[rising]) * (
        -lo[rising] / (hi[rising] - lo[rising])
    )
    if at_spot < 0:
        above = crossings[crossings > spot]
        return float(above.min()) if above.size else None
    below = crossings[crossings <= spot]
    return float(below.max()) if below.size else None


def gamma_flip_note(curve: pd.DataFrame, spot: float) -> str:
    """Why ``gamma_flip`` found no flip on ``curve``, in one sentence."""
    at_spot = curve_value_at(curve, spot)
    if at_spot is None or not bool((curve["gex"] != 0).any()):
        return NO_REPRICING_NOTE
    upper = float(curve["price"].max()) / spot - 1
    lower = float(curve["price"].min()) / spot - 1
    if at_spot < 0:
        return (
            "Repriced net GEX at spot is negative, and the curve does not turn from negative to "
            f"positive anywhere above spot up to {upper:+.0%}."
        )
    return (
        "Repriced net GEX at spot is zero or positive, and the curve does not turn from "
        f"negative to positive anywhere at or below spot down to {lower:+.0%}."
    )


def gamma_walls(profile: pd.DataFrame, n_walls: int = DEFAULT_WALLS) -> GammaWalls:
    """The ``n_walls`` largest positive call-GEX strikes and most negative put-GEX strikes.

    Strikes with no call GEX (or no put GEX) are never walls, so a chain
    without puts has no put walls.
    """
    if profile.empty:
        return GammaWalls()
    calls = profile[profile["call_gex"] > 0].nlargest(n_walls, "call_gex")
    puts = profile[profile["put_gex"] < 0].nsmallest(n_walls, "put_gex")
    return GammaWalls(
        call=tuple(
            Wall(float(s), float(g))
            for s, g in zip(calls["strike"], calls["call_gex"], strict=True)
        ),
        put=tuple(
            Wall(float(s), float(g)) for s, g in zip(puts["strike"], puts["put_gex"], strict=True)
        ),
    )


def vanna_flip(profile: pd.DataFrame, spot: float) -> float | None:
    """The strike nearest spot where per-strike vanna exposure changes sign.

    Strikes with zero or missing vanna exposure are skipped, so a zero strike
    at the crossing does not hide it; the crossing is reported at the first
    non-zero strike above it. ``profile`` is ``exposures.vanna_profile``
    output.
    """
    if profile.empty or "vex" not in profile:
        return None
    df = profile[profile["vex"].notna() & (profile["vex"] != 0)]
    df = df.sort_values("strike").reset_index(drop=True)
    changes = df.loc[df["vex"] * df["vex"].shift(1) < 0, "strike"]
    if changes.empty:
        return None
    return float(min(changes, key=lambda s: abs(s - spot)))


def strike_with_max(frame: pd.DataFrame, column: str) -> float | None:
    """The strike on the row with the largest ``column`` (first on ties); None if empty."""
    if frame.empty:
        return None
    return float(frame["strike"].to_numpy()[int(frame[column].to_numpy().argmax())])


def vega_peaks(profile: pd.DataFrame, spot: float) -> VegaPeaks:
    """Strikes with the most gross vega strictly above and strictly below spot."""
    if profile.empty or "vega_exposure" not in profile:
        return VegaPeaks()
    above = profile[profile["strike"] > spot]
    below = profile[profile["strike"] < spot]
    return VegaPeaks(
        upper=strike_with_max(above, "vega_exposure"),
        lower=strike_with_max(below, "vega_exposure"),
    )


def atm_iv(chain: pd.DataFrame, spot: float) -> float | None:
    """Mean implied volatility of the contracts at the valid-IV strike nearest spot."""
    if chain.empty or spot <= 0 or not {"strike", "iv"}.issubset(chain.columns):
        return None
    valid = chain[chain["iv"].notna() & (chain["iv"] > 0)]
    if valid.empty:
        return None
    nearest = float(min(valid["strike"].unique(), key=lambda s: abs(s - spot)))
    return float(valid.loc[valid["strike"] == nearest, "iv"].mean())


def expected_move(chain: pd.DataFrame, spot: float, years: float) -> float | None:
    """One standard deviation of price to expiration, in dollars: ``S * atm_iv * sqrt(T)``.

    ``years`` is trading-time years to expiration (``clock.years_to_expiry``);
    pass a single expiration's chain.
    """
    if years <= 0:
        return None
    sigma = atm_iv(chain, spot)
    if sigma is None:
        return None
    return spot * sigma * math.sqrt(years)


def expected_move_at(
    chain: pd.DataFrame, spot: float, expiration: date, now: datetime
) -> float | None:
    """``expected_move`` for one expiration of a (possibly multi-expiration) chain."""
    if chain.empty:
        return None
    rows = chain[chain["expiration"] == expiration]
    return expected_move(rows, spot, years_to_expiry(now, expiration))


def tradingview_levels(
    spot: float,
    gamma_flip: float | None = None,
    walls: GammaWalls | None = None,
    expected_move: float | None = None,
    vega_peaks: VegaPeaks | None = None,
    vanna_flip: float | None = None,
) -> str:
    """Levels as a pipe-delimited string for a TradingView indicator input.

    For example ``GF=5870|CW1=5920|PW1=5800|EU=5950|EL=5790|VPU=5910|VPL=5830|VF=5840``;
    an empty string when there are no levels.
    """

    def fmt(value: float) -> str:
        return f"{value:.2f}".rstrip("0").rstrip(".")

    parts: list[str] = []
    if gamma_flip is not None:
        parts.append(f"GF={fmt(gamma_flip)}")
    if walls is not None:
        parts += [f"CW{i}={fmt(w.strike)}" for i, w in enumerate(walls.call[:3], 1)]
        parts += [f"PW{i}={fmt(w.strike)}" for i, w in enumerate(walls.put[:3], 1)]
    if expected_move is not None and spot > 0:
        parts += [f"EU={fmt(spot + expected_move)}", f"EL={fmt(spot - expected_move)}"]
    if vega_peaks is not None:
        if vega_peaks.upper is not None:
            parts.append(f"VPU={fmt(vega_peaks.upper)}")
        if vega_peaks.lower is not None:
            parts.append(f"VPL={fmt(vega_peaks.lower)}")
    if vanna_flip is not None:
        parts.append(f"VF={fmt(vanna_flip)}")
    return "|".join(parts)

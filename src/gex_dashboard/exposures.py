"""Per-contract greeks to dealer dollar exposures: the one place units are converted.

**Dealer sign convention.** Exposures are reported from the market maker's
side under the conventional assumption that customers sell calls and buy
puts, so dealers are **long calls (+1) and short puts (-1)** for all open
interest. ``DEALER_SIGN`` is the single constant that decides this; every
signed exposure is ``DEALER_SIGN[right] * dollar_<greek>(...)``. Real
positioning can differ from this assumption.

**Dollar conventions** (``contracts`` is open interest, or traded volume for
flow; ``S`` is spot; greeks are in the chain units of
``gex_dashboard.providers.base``):

| Exposure | Formula | Meaning |
|---|---|---|
| delta | ``delta * contracts * 100 * S`` | dollars of underlying the position equals |
| gamma (GEX) | ``gamma * contracts * 100 * S^2 * 0.01`` | dollar-delta change, 1% spot move |
| vanna | ``vanna * contracts * 100 * S`` | dollar-delta change, IV up 1 vol point |
| charm | ``charm * contracts * 100 * S`` | dollar-delta change over 1 trading day |
| vega | ``vega * contracts * 100`` | option-value change, IV up 1 vol point |

Delta, gamma, vanna and charm exposures are dealer-signed. Vega exposure is
**gross** (unsigned): it is used only to locate where volatility sensitivity
concentrates (the vega peaks), which the sign convention would distort.

**Missing greeks.** A contract whose greek is NaN gets a NaN exposure for
that greek. Sums (net exposure, per-strike profiles, heatmap cells) leave such
contracts out rather than counting them as zero, and a metric is unavailable
only when no contract has it (``has_metric``).

**GEX at other prices.** ``gex_curve`` reprices every contract's gamma with
Black-Scholes-Merton at hypothetical spot prices, using each contract's own
implied volatility and trading-time years to expiry (and the risk-free rate,
which matters for longer expirations), and converts it with
``dollar_gamma`` at that price. ``levels.gamma_flip`` finds where it crosses
zero.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Final, Literal

import numpy as np
import pandas as pd

from gex_dashboard import bsm
from gex_dashboard.clock import years_to_expiry

logger = logging.getLogger(__name__)

CONTRACT_MULTIPLIER: Final = 100
ONE_PERCENT: Final = 0.01
DEALER_SIGN: Final = {"C": 1.0, "P": -1.0}

Metric = Literal["delta", "gamma", "vanna", "charm", "vega"]
EXPOSURE_COLUMNS: Final[dict[Metric, str]] = {
    "delta": "delta_exposure",
    "gamma": "gamma_exposure",
    "vanna": "vanna_exposure",
    "charm": "charm_exposure",
    "vega": "vega_exposure",
}
GEX_PROFILE_COLUMNS: Final = ("strike", "gex", "call_gex", "put_gex", "open_interest", "volume")
GEX_CURVE_COLUMNS: Final = ("price", "gex")
CURVE_HALF_WIDTH: Final = 0.10  # gex_curve spans spot +/- 10%
CURVE_POINTS: Final = 201  # an odd count puts spot itself on the grid

Numeric = float | np.ndarray | pd.Series


def dollar_delta(delta: Numeric, contracts: Numeric, spot: float) -> Numeric:
    """Dollar delta: ``delta * contracts * 100 * spot``."""
    return delta * contracts * CONTRACT_MULTIPLIER * spot


def dollar_gamma(gamma: Numeric, contracts: Numeric, spot: float | np.ndarray) -> Numeric:
    """Dollar gamma per 1% move: ``gamma * contracts * 100 * spot^2 * 0.01``."""
    return gamma * contracts * CONTRACT_MULTIPLIER * spot**2 * ONE_PERCENT


def dollar_vanna(vanna: Numeric, contracts: Numeric, spot: float) -> Numeric:
    """Dollar delta change per vol point: ``vanna * contracts * 100 * spot``."""
    return vanna * contracts * CONTRACT_MULTIPLIER * spot


def dollar_charm(charm: Numeric, contracts: Numeric, spot: float) -> Numeric:
    """Dollar delta change per trading day: ``charm * contracts * 100 * spot``."""
    return charm * contracts * CONTRACT_MULTIPLIER * spot


def dollar_vega(vega: Numeric, contracts: Numeric) -> Numeric:
    """Dollar value change per vol point: ``vega * contracts * 100``."""
    return vega * contracts * CONTRACT_MULTIPLIER


def dealer_sign(right: pd.Series) -> pd.Series:
    """+1 for calls, -1 for puts (see ``DEALER_SIGN``)."""
    return right.map(DEALER_SIGN).astype(float)


def contract_exposures(chain: pd.DataFrame, spot: float) -> pd.DataFrame:
    """The chain plus ``dealer_sign`` and one dollar column per greek.

    Greeks the chain lacks (vanna and charm without ``SECOND_ORDER``) give
    NaN exposure columns, which the profiles below treat as unavailable.
    """
    df = chain.copy()
    if df.empty:
        for col in ("dealer_sign", *EXPOSURE_COLUMNS.values()):
            df[col] = pd.Series(dtype=float)
        return df
    oi = df["open_interest"].astype(float)
    sign = dealer_sign(df["right"])
    df["dealer_sign"] = sign

    def greek(name: str) -> pd.Series:
        return df[name].astype(float) if name in df else pd.Series(np.nan, index=df.index)

    df["delta_exposure"] = sign * dollar_delta(greek("delta"), oi, spot)
    df["gamma_exposure"] = sign * dollar_gamma(greek("gamma"), oi, spot)
    df["vanna_exposure"] = sign * dollar_vanna(greek("vanna"), oi, spot)
    df["charm_exposure"] = sign * dollar_charm(greek("charm"), oi, spot)
    df["vega_exposure"] = dollar_vega(greek("vega"), oi)
    for metric, col in EXPOSURE_COLUMNS.items():
        missing = int(df[col].isna().sum())
        if 0 < missing < len(df):
            logger.warning(
                "%d of %d contracts have no %s; they are left out of its sums",
                missing,
                len(df),
                metric,
            )
    return df


def has_metric(exposures: pd.DataFrame, metric: Metric) -> bool:
    """True when ``metric``'s exposure column exists and is not all NaN."""
    col = EXPOSURE_COLUMNS[metric]
    return not exposures.empty and col in exposures and bool(exposures[col].notna().any())


def net_exposure(exposures: pd.DataFrame, metric: Metric) -> float | None:
    """Sum of ``metric``'s exposure over every contract, or None if unavailable."""
    if not has_metric(exposures, metric):
        return None
    return float(exposures[EXPOSURE_COLUMNS[metric]].sum(skipna=True))


def strike_profile(exposures: pd.DataFrame, metric: Metric, name: str = "value") -> pd.DataFrame:
    """``metric``'s exposure summed per strike (across rights and expirations)."""
    if not has_metric(exposures, metric):
        return pd.DataFrame({"strike": pd.Series(dtype=float), name: pd.Series(dtype=float)})
    col = EXPOSURE_COLUMNS[metric]
    # skipna: a contract without the greek is left out; a strike where none has it sums to 0.
    return exposures.groupby("strike")[col].sum(min_count=0).rename(name).reset_index()


def gex_by_strike(exposures: pd.DataFrame) -> pd.DataFrame:
    """Dealer GEX per strike with its call and put parts, OI and volume.

    Columns ``strike, gex, call_gex, put_gex, open_interest, volume``, sorted by
    strike. Works for one expiration or many: it sums whatever rows it gets.
    """
    if not has_metric(exposures, "gamma"):
        return pd.DataFrame({col: pd.Series(dtype=float) for col in GEX_PROFILE_COLUMNS})
    df = exposures.assign(
        call_gex=exposures["gamma_exposure"].where(exposures["right"] == "C", 0.0),
        put_gex=exposures["gamma_exposure"].where(exposures["right"] == "P", 0.0),
    )
    out = df.groupby("strike", as_index=False).agg(
        gex=("gamma_exposure", "sum"),
        call_gex=("call_gex", "sum"),
        put_gex=("put_gex", "sum"),
        open_interest=("open_interest", "sum"),
        volume=("volume", "sum"),
    )
    return out.sort_values("strike").reset_index(drop=True)


def vanna_profile(exposures: pd.DataFrame) -> pd.DataFrame:
    """Dealer vanna exposure per strike: columns ``strike, vex``."""
    return strike_profile(exposures, "vanna", "vex")


def charm_profile(exposures: pd.DataFrame) -> pd.DataFrame:
    """Dealer charm exposure per strike: columns ``strike, charm_exposure``."""
    return strike_profile(exposures, "charm", "charm_exposure")


def vega_profile(exposures: pd.DataFrame) -> pd.DataFrame:
    """Gross vega exposure per strike: columns ``strike, vega_exposure``."""
    return strike_profile(exposures, "vega", "vega_exposure")


def gamma_ratio(call_gex: float, put_gex: float) -> float | None:
    """Call share of total absolute GEX, in [0, 1]; None when both are zero."""
    total = abs(call_gex) + abs(put_gex)
    return None if total == 0 else abs(call_gex) / total


def curve_prices(spot: float) -> np.ndarray:
    """The default ``gex_curve`` grid: ``CURVE_POINTS`` prices across spot +/- 10%."""
    return spot * (1.0 + np.linspace(-CURVE_HALF_WIDTH, CURVE_HALF_WIDTH, CURVE_POINTS))


def gex_curve(
    chain: pd.DataFrame,
    spot: float,
    now: datetime,
    prices: np.ndarray | None = None,
    risk_free_rate: float = bsm.DEFAULT_RISK_FREE_RATE,
) -> pd.DataFrame:
    """Net dealer GEX (dollars per 1% move) if spot were at each of ``prices``.

    Every contract's gamma is repriced with Black-Scholes-Merton (rate
    ``risk_free_rate``, no dividend yield) at each price, from its own ``iv``
    and its trading-time years to expiry at ``now``, then dealer-signed, weighted by open interest
    and converted with ``dollar_gamma`` at that price. Contracts without a
    positive IV or open interest, or already expired, contribute nothing.
    ``prices`` defaults to ``curve_prices(spot)``. Columns ``price, gex``;
    empty when there is nothing to price.
    """
    grid = curve_prices(spot) if prices is None else np.asarray(prices, dtype=np.float64)
    needed = {"expiration", "strike", "right", "iv", "open_interest"}
    if chain.empty or grid.size == 0 or not needed.issubset(chain.columns):
        return pd.DataFrame({col: pd.Series(dtype=float) for col in GEX_CURVE_COLUMNS})
    weight = dealer_sign(chain["right"]) * chain["open_interest"].astype(float)
    df = pd.DataFrame(
        {
            "t": chain["expiration"].map(
                {e: years_to_expiry(now, e) for e in chain["expiration"].unique()}
            ),
            "strike": chain["strike"].astype(float),
            "iv": chain["iv"].astype(float),
            "weight": weight,
        }
    )
    df = df[(df["t"] > 0) & (df["iv"] > 0) & (df["weight"] != 0) & df["weight"].notna()]
    # Calls and puts with the same strike, expiry and IV share a gamma: price them once.
    df = df.groupby(["t", "strike", "iv"], as_index=False).agg(weight=("weight", "sum"))
    net_gamma = np.zeros_like(grid)
    if not df.empty:
        gammas = bsm.gamma(
            grid[None, :],
            df["strike"].to_numpy()[:, None],
            df["t"].to_numpy()[:, None],
            df["iv"].to_numpy()[:, None],
            r=risk_free_rate,
        )
        net_gamma = df["weight"].to_numpy() @ gammas
    gex = np.asarray(dollar_gamma(net_gamma, 1.0, grid), dtype=np.float64)
    return pd.DataFrame({"price": grid, "gex": gex})


def exposure_matrix(
    exposures: pd.DataFrame, metric: Metric, max_expirations: int = 10
) -> pd.DataFrame:
    """Strike x expiration grid of ``metric``'s exposure for a heatmap.

    Index: strike ascending. Columns: the nearest ``max_expirations``
    expiration dates in order. Missing cells are 0. Empty if unavailable.
    """
    if not has_metric(exposures, metric):
        return pd.DataFrame()
    col = EXPOSURE_COLUMNS[metric]
    keep = sorted(exposures["expiration"].unique())[:max_expirations]
    df = exposures[exposures["expiration"].isin(keep)]
    grid = df.pivot_table(index="strike", columns="expiration", values=col, aggfunc="sum")
    return grid.reindex(columns=keep).sort_index().fillna(0.0)

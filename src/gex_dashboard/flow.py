"""Customer flow (buy minus sell) and unusual-volume detection.

**Unusual volume** compares each contract's volume-to-open-interest ratio
with that of its neighbours: the median ratio over the ``2 * NEIGHBOUR_STRIKES
+ 1`` listed strikes centred on it, in the same expiration and right,
counting only contracts that traded. Normalizing by open interest removes the
differences in size between strikes (round strikes, the open-interest peaks),
a floor of ``MIN_SPIKE_VOLUME`` contracts keeps single lots on quiet strikes
out, and a local median makes the baseline robust to the spikes themselves and
independent of how wide a strike range was fetched, for every contract at
least ``NEIGHBOUR_STRIKES`` strikes inside it.

Flow figures are from the **customer** side: positive means customers bought
more than they sold. They are not dealer-signed. Dollar units follow
``gex_dashboard.exposures`` with net traded contracts in place of open
interest:

- ``volume``: net contracts.
- ``premium``: net premium in dollars.
- ``delta``: net dollar delta (``exposures.dollar_delta``); a put's negative
  delta makes bought puts count as negative delta.
- ``gamma``: net dollar gamma per 1% move (``exposures.dollar_gamma``).

Every function here needs a chain with the ``FLOW`` columns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

import pandas as pd

from gex_dashboard.exposures import dollar_delta, dollar_gamma
from gex_dashboard.providers.base import FLOW_COLUMNS

FlowMetric = Literal["gamma", "delta", "volume", "premium"]
FLOW_METRICS: Final[tuple[FlowMetric, ...]] = ("gamma", "delta", "volume", "premium")
_NET_COLUMN: Final[dict[FlowMetric, str]] = {
    "gamma": "net_gamma",
    "delta": "net_delta",
    "volume": "net_volume",
    "premium": "net_premium",
}
SPIKE_COLUMNS: Final = (
    "strike",
    "expiration",
    "right",
    "volume",
    "open_interest",
    "vol_oi_ratio",
    "vs_neighbours",
)
NEIGHBOUR_STRIKES: Final = 5  # strikes either side in the unusual-volume baseline
MIN_NEIGHBOURS: Final = 3  # traded contracts needed in the window for a baseline
DEFAULT_SPIKE_THRESHOLD: Final = 3.0
MIN_SPIKE_VOLUME: Final = 10  # contracts; below this, single lots dominate the ratio


def has_flow(chain: pd.DataFrame) -> bool:
    """True when the chain carries the customer buy/sell columns."""
    return not chain.empty and set(FLOW_COLUMNS).issubset(chain.columns)


def net_flow(chain: pd.DataFrame, spot: float) -> pd.DataFrame:
    """The chain plus ``net_volume``, ``net_premium``, ``net_delta`` and ``net_gamma``."""
    df = chain.copy()
    net_contracts = df["volume_buy"].astype(float) - df["volume_sell"].astype(float)
    df["net_volume"] = net_contracts
    df["net_premium"] = df["premium_buy"].astype(float) - df["premium_sell"].astype(float)
    df["net_delta"] = dollar_delta(df["delta"].astype(float), net_contracts, spot)
    df["net_gamma"] = dollar_gamma(df["gamma"].astype(float), net_contracts, spot)
    return df


def flow_by_expiration(
    chain: pd.DataFrame, spot: float, metric: FlowMetric, max_expirations: int = 10
) -> pd.DataFrame:
    """Customer net ``metric`` per expiration, split by right.

    Columns ``expiration, calls, puts, total`` for the nearest
    ``max_expirations`` expirations. Empty without flow data.
    """
    if not has_flow(chain):
        return pd.DataFrame(columns=["expiration", "calls", "puts", "total"])
    col = _NET_COLUMN[metric]
    df = net_flow(chain, spot)
    keep = sorted(df["expiration"].unique())[:max_expirations]
    df = df[df["expiration"].isin(keep)]
    grid = df.pivot_table(index="expiration", columns="right", values=col, aggfunc="sum")
    grid = grid.reindex(index=keep, columns=["C", "P"]).fillna(0.0)
    out = pd.DataFrame(
        {"expiration": keep, "calls": grid["C"].to_numpy(), "puts": grid["P"].to_numpy()}
    )
    out["total"] = out["calls"] + out["puts"]
    return out


def flow_matrix(
    chain: pd.DataFrame, spot: float, metric: FlowMetric, max_expirations: int = 10
) -> pd.DataFrame:
    """Strike x expiration grid of customer net ``metric`` (calls plus puts)."""
    if not has_flow(chain):
        return pd.DataFrame()
    col = _NET_COLUMN[metric]
    df = net_flow(chain, spot)
    keep = sorted(df["expiration"].unique())[:max_expirations]
    df = df[df["expiration"].isin(keep)]
    grid = df.pivot_table(index="strike", columns="expiration", values=col, aggfunc="sum")
    return grid.reindex(columns=keep).sort_index().fillna(0.0)


def flow_summary(chain: pd.DataFrame, spot: float, max_expirations: int = 10) -> pd.DataFrame:
    """One row per expiration with the net of every flow metric (calls plus puts)."""
    if not has_flow(chain):
        return pd.DataFrame(columns=["expiration", *FLOW_METRICS])
    df = net_flow(chain, spot)
    keep = sorted(df["expiration"].unique())[:max_expirations]
    net_columns = [_NET_COLUMN[m] for m in FLOW_METRICS]
    sums = (
        df[df["expiration"].isin(keep)]
        .groupby("expiration")[net_columns]
        .sum()
        .reindex(keep)
        .fillna(0.0)
    )
    columns: dict[str, object] = {"expiration": keep}
    columns.update({m: sums[_NET_COLUMN[m]].to_numpy() for m in FLOW_METRICS})
    return pd.DataFrame(columns)


@dataclass(frozen=True)
class NetDeltaFlow:
    """Customer net dollar delta traded today."""

    net: float
    calls: float
    puts: float


def net_delta_flow(chain: pd.DataFrame, spot: float) -> NetDeltaFlow | None:
    """Customer net dollar delta, split by right; None without flow data."""
    if not has_flow(chain):
        return None
    df = net_flow(chain, spot)
    calls = float(df.loc[df["right"] == "C", "net_delta"].sum())
    puts = float(df.loc[df["right"] == "P", "net_delta"].sum())
    return NetDeltaFlow(net=calls + puts, calls=calls, puts=puts)


def volume_spikes(
    chain: pd.DataFrame,
    threshold: float = DEFAULT_SPIKE_THRESHOLD,
    neighbours: int = NEIGHBOUR_STRIKES,
    min_volume: int = MIN_SPIKE_VOLUME,
) -> pd.DataFrame:
    """Contracts whose volume / OI is at least ``threshold`` times their neighbours'.

    ``vol_oi_ratio`` is volume / (open interest + 1). The baseline is the
    median ratio of the contracts that traded among the ``2 * neighbours + 1``
    listed strikes centred on the contract (same expiration and right; at
    least ``MIN_NEIGHBOURS`` needed). ``vs_neighbours`` is the ratio over that
    baseline. A contract needs at least ``min_volume`` contracts traded to
    be flagged, since a few lots on a quiet strike swing the ratio. Most
    unusual first. Includes ``volume_buy``/``volume_sell``
    when the chain has flow data.
    """
    extra = ["volume_buy", "volume_sell"] if has_flow(chain) else []
    columns = [*SPIKE_COLUMNS, *extra]
    if chain.empty:
        return pd.DataFrame(columns=columns)
    df = chain.sort_values(["expiration", "right", "strike"]).reset_index(drop=True)
    volume = df["volume"].astype(float)
    df["vol_oi_ratio"] = volume / (df["open_interest"].astype(float) + 1)
    traded = df["vol_oi_ratio"].where(volume > 0)
    baseline = traded.groupby([df["expiration"], df["right"]]).transform(
        lambda s: s.rolling(2 * neighbours + 1, center=True, min_periods=MIN_NEIGHBOURS).median()
    )
    df["vs_neighbours"] = traded / baseline.where(baseline > 0)
    spikes = df[(df["vs_neighbours"] >= threshold) & (volume >= min_volume)]
    return spikes.sort_values("vs_neighbours", ascending=False)[columns].reset_index(drop=True)

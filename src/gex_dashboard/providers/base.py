"""The chain-provider seam: what every data source must return.

A provider returns one row per option contract with the canonical columns
below. Everything else (exposures, levels, flow) is computed by the core from
these columns, so a provider never pre-computes exposures.

Per-contract units (per share, like a vendor chain):

| Column | Unit |
|---|---|
| ``expiration`` | ``datetime.date`` |
| ``strike``, ``bid``, ``ask``, ``last`` | dollars |
| ``right`` | ``"C"`` or ``"P"`` |
| ``volume``, ``open_interest`` | contracts (int) |
| ``iv`` | annualized volatility as a fraction (0.20 = 20%) |
| ``delta`` | dV/dS |
| ``gamma`` | d(delta)/dS, per $1 of spot |
| ``vega`` | dollars per 1 volatility point (sigma + 0.01) |
| ``theta`` | dollars per trading day |
| ``vanna`` | change in delta per 1 volatility point (``SECOND_ORDER``) |
| ``charm`` | change in delta per trading day (``SECOND_ORDER``) |
| ``volume_buy``, ``volume_sell`` | contracts customers bought / sold today (``FLOW``) |
| ``premium_buy``, ``premium_sell`` | dollars customers paid / received today (``FLOW``) |

A value the source does not supply for a contract (a greek, IV or a price) is
NaN, never a made-up 0; the count columns (``INT_COLUMNS``) are 0 when missing.

**One instant per snapshot.** ``spot``, ``expirations`` and ``chain`` take an
optional ``now``: the instant the caller is analysing. A caller that needs
spot and the chain to describe the same moment reads its clock once and passes
the same ``now`` to both (``analysis.build_market_view`` does). A simulated
provider prices at ``now``; a live provider returns its latest data and uses
``now`` only for calendar decisions such as which expirations have passed.
When ``now`` is None a provider uses its own clock.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from enum import Flag, auto
from typing import Final, Protocol, runtime_checkable

import pandas as pd

REQUIRED_COLUMNS: Final = (
    "expiration",
    "strike",
    "right",
    "bid",
    "ask",
    "last",
    "volume",
    "open_interest",
    "iv",
    "delta",
    "gamma",
    "vega",
    "theta",
)
SECOND_ORDER_COLUMNS: Final = ("vanna", "charm")
FLOW_COLUMNS: Final = ("volume_buy", "volume_sell", "premium_buy", "premium_sell")
INT_COLUMNS: Final = ("volume", "open_interest", "volume_buy", "volume_sell")
DEFAULT_STRIKE_RANGE: Final = 0.10


class Capability(Flag):
    """Optional features a provider supports."""

    NONE = 0
    MULTI_EXPIRATION = auto()  # chain(expirations=None) returns every expiration
    SECOND_ORDER = auto()  # vanna and charm columns are populated
    FLOW = auto()  # customer buy/sell split columns are populated


class ProviderError(RuntimeError):
    """A provider could not return data (network, auth, missing package, bad payload)."""


@runtime_checkable
class ChainProvider(Protocol):
    name: str
    capabilities: Capability

    def spot(self, symbol: str, now: datetime | None = None) -> float:
        """Price of the underlying at ``now`` (live providers: the latest price)."""
        ...

    def expirations(self, symbol: str, now: datetime | None = None) -> list[date]:
        """Expiration dates not yet expired at ``now``, ascending."""
        ...

    def chain(
        self,
        symbol: str,
        expirations: Sequence[date] | None = None,
        strike_range: float = DEFAULT_STRIKE_RANGE,
        now: datetime | None = None,
    ) -> pd.DataFrame:
        """Contracts for ``expirations`` (all of them when None) with strikes within
        ``strike_range`` of spot, as canonical columns, priced at the same spot
        that ``spot(symbol, now)`` returns."""
        ...


def chain_columns(capabilities: Capability) -> list[str]:
    """The canonical columns a provider with ``capabilities`` must return."""
    columns = list(REQUIRED_COLUMNS)
    if Capability.SECOND_ORDER in capabilities:
        columns += SECOND_ORDER_COLUMNS
    if Capability.FLOW in capabilities:
        columns += FLOW_COLUMNS
    return columns


def empty_chain(capabilities: Capability = Capability.NONE) -> pd.DataFrame:
    """A zero-row chain with the canonical columns."""
    return pd.DataFrame({col: pd.Series(dtype=object) for col in chain_columns(capabilities)})

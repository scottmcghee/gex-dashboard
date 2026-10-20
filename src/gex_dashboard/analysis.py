"""One call that fetches a chain and computes everything the dashboard shows.

``build_market_view`` reads the clock once, asks the provider for spot and the
chain at that instant, converts units once (``exposures.contract_exposures``)
and derives every level, profile and label from that single snapshot. Nothing
is cached between calls, so the expected move always matches the chain on
screen.

Contracts whose expiration has closed at ``now`` are dropped first. A
provider may still list a same-day expiration after the close; kept, it would
count in net GEX, the walls and the profiles but not in the repriced curve
(which needs time to expiry), and as the front expiration it would leave no
expected move. Dropped, every figure describes the same contracts, and the
front expiration is the nearest unexpired one.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from gex_dashboard import exposures as ex
from gex_dashboard import flow, levels, profiles
from gex_dashboard.bsm import DEFAULT_RISK_FREE_RATE
from gex_dashboard.clock import (
    Clock,
    is_expired,
    session_fraction_remaining,
    years_to_expiry,
)
from gex_dashboard.providers.base import DEFAULT_STRIKE_RANGE, ChainProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketView:
    symbol: str
    spot: float
    as_of: datetime
    expirations: tuple[date, ...]  # the expirations included, ascending
    exposures: pd.DataFrame  # the chain plus dollar exposure columns
    gex_profile: pd.DataFrame  # strike, gex, call_gex, put_gex, open_interest, volume
    gex_curve: pd.DataFrame  # price, gex: net GEX repriced across spot +/- 10%
    net_gex: float
    gamma_ratio: float | None  # None when there is no GEX
    gamma_flip: float | None  # a price, not necessarily a strike
    gamma_flip_note: str | None  # why there is no gamma flip; None when there is one
    walls: levels.GammaWalls
    vanna_profile: pd.DataFrame  # strike, vex (empty without SECOND_ORDER)
    net_vanna: float | None
    vanna_flip: float | None
    charm_profile: pd.DataFrame  # strike, charm_exposure
    net_charm: float | None
    vega_profile: pd.DataFrame  # strike, vega_exposure
    vega_peaks: levels.VegaPeaks
    front_expiration: date | None
    atm_iv: float | None  # front expiration
    expected_move: float | None  # front expiration, dollars
    regime: profiles.GammaRegime | None  # None for an empty chain
    alignment: profiles.ExposureAlignment | None
    hedging: profiles.HedgingImpact | None
    net_delta_flow: flow.NetDeltaFlow | None  # None without FLOW

    @property
    def has_data(self) -> bool:
        return not self.gex_profile.empty

    def tradingview_levels(self) -> str:
        return levels.tradingview_levels(
            self.spot,
            gamma_flip=self.gamma_flip,
            walls=self.walls,
            expected_move=self.expected_move,
            vega_peaks=self.vega_peaks,
            vanna_flip=self.vanna_flip,
        )


def build_market_view(
    provider: ChainProvider,
    symbol: str,
    clock: Clock,
    expirations: Sequence[date] | None = None,
    strike_range: float = DEFAULT_STRIKE_RANGE,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> MarketView:
    """Fetch ``symbol``'s chain for ``expirations`` (all when None) and analyze it.

    ``risk_free_rate`` is used only to reprice gamma for the gamma flip.
    Provider failures propagate as ``ProviderError`` for the UI to report.
    """
    now = clock.now()  # read once: spot, chain, expected move and flip share this instant
    spot = provider.spot(symbol, now)
    chain = provider.chain(symbol, expirations, strike_range=strike_range, now=now)
    return analyze_chain(symbol, spot, chain, now, risk_free_rate)


def unexpired(chain: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """The contracts whose expiration close is still after ``now``."""
    if chain.empty:
        return chain
    live = {e: not is_expired(e, now) for e in chain["expiration"].unique()}
    keep = chain["expiration"].map(live).astype(bool)
    if not keep.all():
        logger.info("dropping %d contracts whose expiration has closed", int((~keep).sum()))
    return chain[keep].reset_index(drop=True)


def analyze_chain(
    symbol: str,
    spot: float,
    chain: pd.DataFrame,
    now: datetime,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> MarketView:
    """``build_market_view`` for a chain already in hand."""
    chain = unexpired(chain, now)
    exp = ex.contract_exposures(chain, spot)
    gex_profile = ex.gex_by_strike(exp)
    has_gex = not gex_profile.empty
    net_gex = float(gex_profile["gex"].sum()) if has_gex else 0.0
    curve = ex.gex_curve(chain, spot, now, risk_free_rate=risk_free_rate)
    vanna_prof = ex.vanna_profile(exp)
    charm_prof = ex.charm_profile(exp)
    vega_prof = ex.vega_profile(exp)
    net_vanna = ex.net_exposure(exp, "vanna")
    net_charm = ex.net_exposure(exp, "charm")

    exps = tuple(sorted(chain["expiration"].unique())) if not chain.empty else ()
    front = exps[0] if exps else None
    front_chain = chain[chain["expiration"] == front] if front is not None else chain
    move = levels.expected_move(front_chain, spot, years_to_expiry(now, front)) if front else None

    flip = levels.gamma_flip(curve, spot, net_gex) if has_gex else None
    flip_note = None
    if flip is None:
        flip_note = levels.gamma_flip_note(curve, spot) if has_gex else "The chain has no GEX."

    hedging = None
    if net_vanna is not None and net_charm is not None:
        hedging = profiles.hedging_impact(
            net_gex, net_vanna, net_charm, session_fraction_remaining(now)
        )
    logger.debug("market view %s: %d contracts, net GEX %.0f", symbol, len(chain), net_gex)
    return MarketView(
        symbol=symbol,
        spot=spot,
        as_of=now,
        expirations=exps,
        exposures=exp,
        gex_profile=gex_profile,
        gex_curve=curve,
        net_gex=net_gex,
        gamma_ratio=ex.gamma_ratio(
            float(gex_profile["call_gex"].sum()), float(gex_profile["put_gex"].sum())
        )
        if has_gex
        else None,
        gamma_flip=flip,
        gamma_flip_note=flip_note,
        walls=levels.gamma_walls(gex_profile),
        vanna_profile=vanna_prof,
        net_vanna=net_vanna,
        vanna_flip=levels.vanna_flip(vanna_prof, spot),
        charm_profile=charm_prof,
        net_charm=net_charm,
        vega_profile=vega_prof,
        vega_peaks=levels.vega_peaks(vega_prof, spot),
        front_expiration=front,
        atm_iv=levels.atm_iv(front_chain, spot) if front else None,
        expected_move=move,
        regime=profiles.classify_gamma_regime(net_gex, net_vanna) if has_gex else None,
        alignment=profiles.classify_gex_vex_alignment(net_gex, net_vanna)
        if net_vanna is not None and has_gex
        else None,
        hedging=hedging,
        net_delta_flow=flow.net_delta_flow(chain, spot),
    )

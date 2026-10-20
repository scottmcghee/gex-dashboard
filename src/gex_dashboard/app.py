"""Streamlit entry point: sidebar, one market snapshot per run, metric cards and tabs.

Run with ``streamlit run src/gex_dashboard/app.py``. Settings come from the
environment (see ``gex_dashboard.config``); the default is the synthetic demo market.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Final

import streamlit as st

from gex_dashboard.analysis import MarketView, build_market_view
from gex_dashboard.clock import Clock, is_expired, market_date
from gex_dashboard.config import (
    STRIKE_RANGE_MAX,
    STRIKE_RANGE_MIN,
    Settings,
    get_settings,
    make_clock,
)
from gex_dashboard.formatting import (
    format_compact_dollars,
    format_expiration_display,
    format_from_spot,
)
from gex_dashboard.profiles import classify_index_agreement
from gex_dashboard.providers import Capability, ChainProvider, create_provider
from gex_dashboard.tabs import flow, heatmap, ladder, levels, profile, simulation, volume
from gex_dashboard.ui.agreement import ScopeRegimes, scope_regimes
from gex_dashboard.ui.context import BadgeColor, TabContext
from gex_dashboard.ui.errors import provider_errors

VIEW_TTL_SECONDS: Final = 60
ALL_EXPIRATIONS: Final = "All expirations"
PROVIDER_LABELS: Final = {"synthetic": "synthetic demo market", "convexvalue": "ConvexValue API"}

TABS: Final[list[tuple[str, Callable[[TabContext], None]]]] = [
    ("GEX Profile", profile.render),
    ("Volume Spikes", volume.render),
    ("Exposure Heatmap", heatmap.render),
    ("Live Levels", levels.render),
    ("Customer Flow by Expiration", flow.render),
    ("Simulation", simulation.render),
    ("GEX Ladder", ladder.render),
]

ASSUMPTIONS = """
- **Strike range.** Every figure on this page (net GEX, the gamma flip and the curve it is found
  on, the walls, the profiles, heatmaps and ladder, customer flow, volume spikes and the
  index-agreement badge) covers only the strikes within ±{strike_pct}% of spot, the sidebar's
  strike range. Exposure outside it is left out, so a wider range can change any of them.
- **Dealer sign convention.** Exposures are shown from the market maker's side, assuming
  customers sell calls and buy puts. Dealers are therefore counted as long every call (+) and
  short every put (−) in open interest. Where real positioning differs, so do the signs.
- **Estimates from open interest.** Exposures multiply each contract's greeks at the current
  spot by its open interest. Open interest is published once a day, so today's trades are not
  in it.
- **Units.** GEX is the dollars of delta dealers would trade for a 1% move in the underlying
  (gamma × open interest × 100 × spot² × 1%). Vanna exposure is dollars of delta per one point
  of implied volatility, and charm exposure dollars of delta per trading day.
- **Customer flow** (bought minus sold) is reported from the customer side and is not
  dealer-signed.
- Every figure describes the current chain. None of it is a forecast or a recommendation.
"""


def provider_label(name: str) -> str:
    return PROVIDER_LABELS.get(name, name)


@st.cache_resource(show_spinner=False)
def market_source(_settings: Settings, settings_key: str) -> tuple[Clock, ChainProvider]:
    """One clock and provider per settings; ``settings_key`` is the cache key."""
    clock = make_clock(_settings)
    return clock, create_provider(_settings, clock)


@st.cache_data(ttl=VIEW_TTL_SECONDS, show_spinner=False)
def load_expirations(
    _provider: ChainProvider, provider_key: str, symbol: str, _now: datetime
) -> list[date]:
    """The provider's expirations as of ``_now`` (not part of the cache key)."""
    return _provider.expirations(symbol, _now)


def open_expirations(provider: ChainProvider, key: str, symbol: str, now: datetime) -> list[date]:
    """``load_expirations`` without any whose close has passed at ``now``.

    The list is cached for a minute, and a live source may list today's
    expiration after the close, so the filter is applied on every run.
    """
    return [e for e in load_expirations(provider, key, symbol, now) if not is_expired(e, now)]


@st.cache_data(ttl=VIEW_TTL_SECONDS, show_spinner="Loading the option chain...")
def load_view(
    _provider: ChainProvider,
    _clock: Clock,
    provider_key: str,
    symbol: str,
    expirations: tuple[date, ...] | None,
    strike_range: float,
    risk_free_rate: float,
) -> MarketView:
    """The market view, cached briefly so widget changes do not refetch the chain."""
    chosen = list(expirations) if expirations is not None else None
    return build_market_view(_provider, symbol, _clock, chosen, strike_range, risk_free_rate)


def index_agreement_badge(
    regimes: ScopeRegimes, all_expirations: bool, strike_range: float
) -> None:
    """Whether the configured symbols share a gamma regime; the help names the scope."""
    agreement = classify_index_agreement(regimes.regimes)
    breakdown = "; ".join(f"{s}: {label or 'n/a'}" for s, label in agreement.breakdown.items())
    if all_expirations:
        scope_text = "Scope: all expirations of each symbol"
    else:
        dates = ", ".join(f"{s} {e:%b %d}" for s, e in regimes.fronts.items())
        scope_text = (
            f"Scope: each symbol's front expiration ({dates}), regardless of the expiration chosen "
            "in the sidebar"
        )
    colors: dict[str, BadgeColor] = {
        "aligned": "green",
        "partial": "orange",
        "divergent": "red",
        "unavailable": "gray",
    }
    st.badge(
        f"Index regime {agreement.label.lower()}",
        color=colors[agreement.status],
        help=f"{agreement.description} ({breakdown}). {scope_text}, strikes within "
        f"±{strike_range:.0%} of spot. Each symbol's regime is computed from the sign of its net "
        "GEX, refined by net vanna exposure when GEX is negative.",
    )


def metric_cards(view: MarketView, capabilities: Capability, strike_pct: int) -> None:
    spot = view.spot

    def from_spot(level: float) -> str:
        return f"{format_from_spot(level, spot)} from spot"

    in_range = f"It covers only the strikes within ±{strike_pct}% of spot."
    top = st.columns(4)
    top[0].metric(
        "Net GEX",
        format_compact_dollars(view.net_gex),
        help="Sum of dealer GEX over every contract in view: dollars of delta dealers would "
        f"trade for a 1% move. {in_range}",
    )
    flip_help = (
        "The price at which net dealer GEX, recomputed at hypothetical spot prices, turns from "
        f"negative below to positive above. It need not be a listed strike. {in_range}"
    )
    if view.gamma_flip is not None:
        top[1].metric(
            "Gamma flip",
            f"{view.gamma_flip:,.2f}",
            from_spot(view.gamma_flip),
            delta_color="off",
            help=flip_help,
        )
    else:
        top[1].metric("Gamma flip", "n/a", help=f"{flip_help} {view.gamma_flip_note}")
    for col, label, walls in (
        (top[2], "Top call wall", view.walls.call),
        (top[3], "Top put wall", view.walls.put),
    ):
        if walls:
            col.metric(
                label,
                f"{walls[0].strike:,.2f}",
                from_spot(walls[0].strike),
                delta_color="off",
                help=f"GEX {format_compact_dollars(walls[0].gex)}",
            )
        else:
            col.metric(label, "n/a")

    bottom = st.columns(4)
    bottom[0].metric(
        "Gamma ratio",
        f"{view.gamma_ratio:.0%} calls" if view.gamma_ratio is not None else "n/a",
        help="Call GEX as a share of absolute call plus put GEX.",
    )
    if Capability.SECOND_ORDER not in capabilities:
        bottom[1].metric("Vanna flip", "n/a", help="The data source does not supply vanna.")
    elif view.vanna_flip is not None:
        bottom[1].metric(
            "Vanna flip", f"{view.vanna_flip:,.2f}", from_spot(view.vanna_flip), delta_color="off"
        )
    else:
        bottom[1].metric("Vanna flip", "n/a", help="No sign change in this chain.")
    if view.net_delta_flow is not None:
        bottom[2].metric(
            "Customer net delta",
            format_compact_dollars(view.net_delta_flow.net),
            help="Delta customers bought minus sold today, in dollars of the underlying.",
        )
    else:
        bottom[2].metric("Customer net delta", "n/a", help="The data source has no buy/sell split.")
    if view.alignment is not None:
        bottom[3].metric("GEX / vanna", view.alignment.label, help=view.alignment.description)
    else:
        bottom[3].metric("GEX / vanna", "n/a", help="The data source does not supply vanna.")


def main() -> None:
    st.set_page_config(
        page_title="GEX Dashboard", page_icon=":material/stacked_bar_chart:", layout="wide"
    )
    settings = get_settings()
    label = provider_label(settings.provider)
    with provider_errors(label):
        clock, provider = market_source(settings, repr(settings))
    key = f"{provider.name}:{id(provider)}"
    capabilities = provider.capabilities
    now = clock.now()  # one read for the sidebar; each view reads the clock once itself
    today = market_date(now)

    with st.sidebar:
        st.header("Market")
        symbol = st.selectbox("Symbol", settings.symbols, key="symbol")
        with provider_errors(label):
            expirations = open_expirations(provider, key, symbol, now)
        if not expirations:
            st.error(f"The data source lists no expirations for {symbol}.")
            st.stop()
        options: list[date | str] = list(expirations)
        if Capability.MULTI_EXPIRATION in capabilities:
            options.insert(0, ALL_EXPIRATIONS)
        choice = st.selectbox(
            "Expiration",
            options,
            format_func=lambda e: e if isinstance(e, str) else format_expiration_display(e, today),
            key=f"expiration_{symbol}",
        )
        strike_pct = st.slider(
            "Strike range (± % of spot)",
            min_value=round(STRIKE_RANGE_MIN * 100),
            max_value=round(STRIKE_RANGE_MAX * 100),
            value=round(settings.strike_range * 100),
            key="strike_range",
        )
        if st.button("Refresh data", icon=":material/refresh:", width="stretch"):
            load_view.clear()
            load_expirations.clear()
        st.caption(f"Data: {label}")

    scope = None if isinstance(choice, str) else (choice,)
    with provider_errors(label):
        view = load_view(
            provider, clock, key, symbol, scope, strike_pct / 100, settings.risk_free_rate
        )
    if not view.has_data:
        st.warning(f"No open interest for {symbol} in the selected expirations and strikes.")
        st.stop()

    st.title(f"{symbol} dealer exposure")
    scope_text = "all expirations" if scope is None else format_expiration_display(scope[0], today)
    st.caption(
        f"Spot {view.spot:,.2f} · {scope_text} · strikes within ±{strike_pct}% · "
        f"as of {view.as_of:%a %b %d %Y %H:%M} New York time"
    )
    metric_cards(view, capabilities, strike_pct)

    with st.container(horizontal=True):
        if view.regime is not None:
            regime_help = view.regime.description
            if view.net_gex < 0 and view.net_vanna is None:
                regime_help += (
                    " Net vanna exposure is not available, so this regime is not refined by it."
                )
            st.badge(
                view.regime.label,
                color="blue" if view.net_gex >= 0 else "red",
                help=regime_help,
            )
        if len(settings.symbols) > 1:
            regimes = scope_regimes(
                settings.symbols,
                view,
                scope is None,
                expirations=lambda sym: open_expirations(provider, key, sym, now),
                load=lambda sym, exps: load_view(
                    provider, clock, key, sym, exps, strike_pct / 100, settings.risk_free_rate
                ),
            )
            index_agreement_badge(regimes, scope is None, strike_pct / 100)
    with st.expander("Assumptions"):
        st.markdown(ASSUMPTIONS.format(strike_pct=strike_pct))

    ctx = TabContext(view=view, capabilities=capabilities, provider_label=label, now=view.as_of)
    for tab, (_, render) in zip(st.tabs([title for title, _ in TABS]), TABS, strict=True):
        with tab:
            render(ctx)

    st.divider()
    st.caption(f"Data: {label}. Exposures are open-interest estimates; see Assumptions.")


main()

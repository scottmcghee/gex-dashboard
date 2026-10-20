"""Live Levels tab: every computed level and sensitivity in one place, plus an export string."""

from __future__ import annotations

import streamlit as st

from gex_dashboard.formatting import format_compact_dollars, format_from_spot
from gex_dashboard.levels import Wall
from gex_dashboard.providers.base import Capability
from gex_dashboard.ui.context import TabContext, require


def _wall_list(title: str, caption: str, walls: tuple[Wall, ...], kind: str, spot: float) -> None:
    st.markdown(f"##### {title}")
    st.caption(caption)
    if not walls:
        st.markdown(f"None: no strike has {kind} GEX.")
    for i, wall in enumerate(walls, 1):
        st.markdown(
            f"**{i}.** {wall.strike:,.2f} · {format_compact_dollars(wall.gex)} · "
            f"{format_from_spot(wall.strike, spot)} from spot"
        )


def _walls(ctx: TabContext) -> None:
    view = ctx.view
    calls, flip, puts = st.columns(3)
    with calls:
        _wall_list(
            "Call walls",
            "Up to three strikes with the largest positive call GEX.",
            view.walls.call,
            "call",
            view.spot,
        )
    with flip:
        st.markdown("##### Gamma flip")
        st.caption(
            "The price at which net dealer GEX, with every contract's gamma recomputed at "
            "hypothetical spot prices, turns from negative below to positive above. It is a "
            "price, not necessarily a listed strike, and it reflects only the strikes within the "
            "sidebar's strike range."
        )
        if view.gamma_flip is None:
            st.markdown(f"None. {view.gamma_flip_note}")
        else:
            side = (
                "below it, where repriced net GEX is negative"
                if view.spot < view.gamma_flip
                else "at or above it, where repriced net GEX is zero or positive"
            )
            st.markdown(
                f"**{view.gamma_flip:,.2f}** · {format_from_spot(view.gamma_flip, view.spot)} "
                f"from spot; spot is {side}."
            )
    with puts:
        _wall_list(
            "Put walls",
            "Up to three strikes with the most negative put GEX.",
            view.walls.put,
            "put",
            view.spot,
        )


def _gamma_ratio(ctx: TabContext) -> None:
    ratio = ctx.view.gamma_ratio
    st.markdown("##### Gamma ratio")
    st.caption(
        "Call GEX as a share of call plus put GEX, both taken as absolute values. 50% means "
        "the two sides are the same size."
    )
    if ratio is None:
        st.markdown("Not available: the chain has no GEX.")
        return
    a, b = st.columns(2)
    a.metric("Call share", f"{ratio:.1%}")
    b.metric("Put share", f"{1 - ratio:.1%}")


def _delta_flow(ctx: TabContext) -> None:
    st.markdown("##### Customer net delta traded today")
    if not require(ctx, Capability.FLOW, "Customer delta flow"):
        return
    flow = ctx.view.net_delta_flow
    if flow is None:
        st.info("The chain carries no customer buy/sell volume.")
        return
    st.caption(
        "Delta bought minus delta sold by customers today, in dollars of the underlying "
        "(delta × net contracts × 100 × spot). Bought puts count as negative delta."
    )
    a, b, c = st.columns(3)
    a.metric("Net", format_compact_dollars(flow.net))
    b.metric("Calls", format_compact_dollars(flow.calls))
    c.metric("Puts", format_compact_dollars(flow.puts))


def _vanna_flip(ctx: TabContext) -> None:
    view = ctx.view
    st.markdown("##### Vanna flip")
    if not require(ctx, Capability.SECOND_ORDER, "The vanna flip"):
        return
    st.caption("The strike nearest spot where per-strike dealer vanna exposure changes sign.")
    if view.vanna_flip is None:
        st.markdown("No sign change in this chain.")
        return
    a, b = st.columns(2)
    a.metric("Strike", f"{view.vanna_flip:,.2f}")
    b.metric("From spot", format_from_spot(view.vanna_flip, view.spot))


def _expected_move(ctx: TabContext) -> None:
    view = ctx.view
    st.markdown("##### Expected move")
    st.caption(
        "One standard deviation of price to the front expiration's close, from its at-the-money "
        "implied volatility: spot × IV × √T, with T in trading-time years."
    )
    if view.expected_move is None:
        st.markdown("Not available: the front expiration has no valid at-the-money IV.")
        return
    move = view.expected_move
    a, b, c = st.columns(3)
    a.metric("Range", f"{view.spot - move:,.2f} – {view.spot + move:,.2f}")
    b.metric("Move", f"±{move:,.2f}")
    c.metric("Of spot", f"±{move / view.spot:.2%}")


def _vega_peaks(ctx: TabContext) -> None:
    peaks = ctx.view.vega_peaks
    st.markdown("##### Vega peaks")
    st.caption("The strikes with the most vega (unsigned) strictly above and below spot.")
    if peaks.upper is None and peaks.lower is None:
        st.markdown("Not available for this chain.")
        return
    a, b = st.columns(2)
    a.metric("Below spot", f"{peaks.lower:,.2f}" if peaks.lower is not None else "n/a")
    b.metric("Above spot", f"{peaks.upper:,.2f}" if peaks.upper is not None else "n/a")


def _hedging(ctx: TabContext) -> None:
    st.markdown("##### Dealer delta sensitivities")
    if not require(ctx, Capability.SECOND_ORDER, "The vanna and charm sensitivities"):
        return
    hedging = ctx.view.hedging
    if hedging is None:
        st.markdown("Not available for this chain.")
        return
    st.caption(
        "How much dealer delta, in dollars, changes for a 1% spot move (gamma), a one-point "
        "rise in implied volatility (vanna) and one trading day of time decay (charm). "
        "Remaining charm scales the one-day figure by the share of today's session still to "
        "trade. The largest of the three at these unit shocks is listed as largest; other "
        "shock sizes would rank them differently."
    )
    a, b, c, d = st.columns(4)
    a.metric("Gamma (1% move)", format_compact_dollars(hedging.gamma))
    b.metric("Vanna (1 vol point)", format_compact_dollars(hedging.vanna))
    c.metric("Charm (1 day)", format_compact_dollars(hedging.charm))
    d.metric("Charm, rest of session", format_compact_dollars(hedging.charm_remaining))
    signs = {
        "same_sign": "every non-zero one has the same sign",
        "mixed": "the signs differ",
        "neutral": "all three are zero",
    }[hedging.alignment]
    st.markdown(f"Largest at these shocks: **{hedging.dominant}**; {signs}.")


def _tradingview(ctx: TabContext) -> None:
    st.markdown("##### TradingView export")
    levels = ctx.view.tradingview_levels()
    if not levels:
        st.markdown("No levels to export.")
        return
    st.caption(
        "The levels as one string (GF gamma flip, CW/PW call and put walls, EU/EL expected move "
        "up and down, VPU/VPL vega peaks, VF vanna flip). Copy it into the Levels input of the "
        "Pine Script indicator in the repository's `extras/` folder."
    )
    st.code(levels, language=None)


def render(ctx: TabContext) -> None:
    st.subheader("Levels")
    sections = (
        _walls,
        _gamma_ratio,
        _delta_flow,
        _vanna_flip,
        _expected_move,
        _vega_peaks,
        _hedging,
        _tradingview,
    )
    for i, section in enumerate(sections):
        if i:
            st.divider()
        section(ctx)

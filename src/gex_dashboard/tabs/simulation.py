"""Simulation tab: Monte Carlo price paths whose volatility depends on GEX at the price."""

from __future__ import annotations

import streamlit as st

from gex_dashboard.charts.simulation import simulation_figure
from gex_dashboard.clock import simulation_window
from gex_dashboard.simulation import percentile_bands, simulate_paths, summarize_paths, time_labels
from gex_dashboard.ui.context import TabContext

SEED = 0
CAPTION = (
    "Each path takes one-minute steps with no drift. At every step its volatility is the base "
    "volatility × (1 − dampening × tanh(GEX at the path's price ÷ the largest |GEX|)), with GEX "
    "interpolated from the profile between strikes and taken as zero outside them. Volatility "
    "is therefore lower where net GEX is positive and higher where it is negative. Dampening is "
    "a free setting, not something estimated from data, and the profile is held fixed for the "
    "whole run. Bands show how the simulated prices spread out minute by minute; they describe "
    "this model, not a forecast. Paths use a fixed random seed, so the same inputs give the "
    "same chart."
)


def render(ctx: TabContext) -> None:
    view = ctx.view
    st.subheader("Price path simulation")
    st.caption(CAPTION)
    left, middle, right = st.columns(3)
    with left:
        sources = ["At-the-money IV", "Manual"] if view.atm_iv is not None else ["Manual"]
        source = st.radio("Base volatility", sources, horizontal=True, key="sim_vol_source")
    with middle:
        if source == "Manual":
            sigma = st.number_input(
                "Annualized volatility",
                min_value=0.01,
                max_value=2.0,
                value=0.20,
                step=0.01,
                key="sim_sigma",
            )
        else:
            sigma = float(view.atm_iv or 0.0)
            st.metric("Front-expiration ATM IV", f"{sigma:.1%}")
    with right:
        n_paths = st.selectbox("Paths", [100, 500, 1000], index=1, key="sim_paths")
    dampening = st.slider(
        "Dampening", min_value=0.0, max_value=1.0, value=0.4, step=0.05, key="sim_dampening"
    )

    start, n_steps = simulation_window(ctx.now)
    paths = simulate_paths(
        view.spot, view.gex_profile, sigma, n_steps, dampening=dampening, n_paths=n_paths, seed=SEED
    )
    st.caption(
        f"{n_paths} paths over {n_steps} trading minutes from {start:%a %b %d %H:%M} New York "
        "time to the 16:00 close."
    )
    st.plotly_chart(
        simulation_figure(
            percentile_bands(paths),
            time_labels(n_steps, start),
            view.spot,
            gamma_flip=view.gamma_flip,
            expected_move=view.expected_move,
            vega_peaks=view.vega_peaks,
        ),
        width="stretch",
    )
    summary = summarize_paths(paths)
    a, b = st.columns(2)
    a.metric("5th–95th percentile at the close", f"{summary.p5:,.2f} – {summary.p95:,.2f}")
    b.metric("Median at the close", f"{summary.median:,.2f}")

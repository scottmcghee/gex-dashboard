"""Exposure Heatmap tab: gamma, vanna or charm exposure by strike and expiration."""

from __future__ import annotations

from typing import Final

import streamlit as st

from gex_dashboard.charts.heatmap import strike_expiration_heatmap
from gex_dashboard.charts.style import GEX_UNIT
from gex_dashboard.exposures import Metric, exposure_matrix
from gex_dashboard.formatting import front_move_label
from gex_dashboard.providers.base import Capability
from gex_dashboard.ui.context import TabContext, require, require_several_expirations

# radio label -> (metric, unit, what one unit means)
METRICS: Final[dict[str, tuple[Metric, str, str]]] = {
    "Gamma": ("gamma", GEX_UNIT, "dollars of delta dealers would trade for a 1% spot move"),
    "Vanna": (
        "vanna",
        "$ delta per vol point",
        "dollars of dealer delta that change when implied volatility rises one point",
    ),
    "Charm": (
        "charm",
        "$ delta per trading day",
        "dollars of dealer delta that change over one trading day with spot and volatility "
        "unchanged",
    ),
}


def render(ctx: TabContext) -> None:
    st.subheader("Exposure by strike and expiration")
    if not require_several_expirations(ctx, "The exposure heatmap"):
        return
    choice = st.radio("Exposure", list(METRICS), horizontal=True, key="heatmap_metric")
    metric, unit, meaning = METRICS[choice]
    if metric != "gamma" and not require(ctx, Capability.SECOND_ORDER, f"{choice} exposure"):
        return
    st.caption(
        f"Each cell is the dealer {choice.lower()} exposure of one strike and expiration, calls "
        f"and puts summed: {meaning}. Blue cells are positive and red negative under the sign "
        "convention in **Assumptions**; the darker the cell, the larger the exposure. Up to the "
        "ten nearest expirations are shown. The horizontal lines mark spot and the front "
        "expiration's ±1σ expected move, which belongs to that expiration only."
    )
    view = ctx.view
    st.plotly_chart(
        strike_expiration_heatmap(
            exposure_matrix(view.exposures, metric),
            view.spot,
            title=f"Dealer {choice.lower()} exposure",
            value_label=choice,
            unit=unit,
            expected_move=view.expected_move,
            expected_move_label=front_move_label(view.front_expiration),
        ),
        width="stretch",
    )

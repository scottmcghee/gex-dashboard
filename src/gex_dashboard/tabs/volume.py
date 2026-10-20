"""Volume Spikes tab: contracts trading far more, for their open interest, than their neighbours."""

from __future__ import annotations

import streamlit as st

from gex_dashboard.charts.volume import volume_spikes_figure
from gex_dashboard.flow import (
    DEFAULT_SPIKE_THRESHOLD,
    MIN_SPIKE_VOLUME,
    NEIGHBOUR_STRIKES,
    volume_spikes,
)
from gex_dashboard.ui.context import TabContext

COLUMN_LABELS = {
    "strike": "Strike",
    "expiration": "Expiration",
    "right": "Right",
    "volume": "Volume",
    "open_interest": "Open interest",
    "vol_oi_ratio": "Volume / OI",
    "vs_neighbours": "× nearby strikes",
    "volume_buy": "Bought",
    "volume_sell": "Sold",
}


def render(ctx: TabContext) -> None:
    st.subheader("Unusual volume")
    threshold = st.slider(
        "Threshold (× the volume / OI of nearby strikes)",
        min_value=1.5,
        max_value=10.0,
        value=DEFAULT_SPIKE_THRESHOLD,
        step=0.5,
        key="volume_threshold",
    )
    st.caption(
        f"Contracts whose volume / OI (today's volume over open interest plus one) is at least "
        f"{threshold:g} times the median volume / OI of the {2 * NEIGHBOUR_STRIKES + 1} listed "
        f"strikes centred on them (itself and up to {NEIGHBOUR_STRIKES} either side, same "
        f"expiration and right, counting only contracts that traded), with at least "
        f"{MIN_SPIKE_VOLUME} contracts traded, most unusual first. "
        "Dividing by open interest compares a contract's trading with its existing positions, "
        "and the nearby median is robust to the spikes themselves and, for contracts at least "
        "five strikes inside the strike range, does not depend on it. When the data source "
        "reports customer buys and sells, the bars split each strike's flagged volume into "
        "contracts bought and sold."
    )
    spikes = volume_spikes(ctx.view.exposures, threshold)
    if spikes.empty:
        st.info(
            f"No contract's volume / OI is {threshold:g} times that of its nearby strikes "
            "(or nothing has traded yet)."
        )
        return
    st.plotly_chart(volume_spikes_figure(spikes), width="stretch")
    st.dataframe(
        spikes.rename(columns=COLUMN_LABELS),
        hide_index=True,
        column_config={
            "Strike": st.column_config.NumberColumn(format="%.2f"),
            "Volume / OI": st.column_config.NumberColumn(format="%.2f"),
            "× nearby strikes": st.column_config.NumberColumn(format="%.1f×"),
        },
    )

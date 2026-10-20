"""GEX Profile tab: dealer GEX by strike and a table of the computed levels."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from gex_dashboard.analysis import MarketView
from gex_dashboard.charts.profile import gex_profile_figure
from gex_dashboard.formatting import format_compact_dollars, format_from_spot
from gex_dashboard.ui.context import TabContext

TABLE_ROW_PX = 35  # Streamlit's dataframe row height

CAPTION = (
    "Bars are dealer gamma exposure (GEX) at each strike: the dollars of delta that dealers "
    "would need to trade for a 1% move in the underlying. Calls plot above zero and puts below "
    "it, under the sign convention in **Assumptions**; the line is their sum. Vertical lines mark "
    "spot, the gamma flip (the price at which net GEX, recomputed at hypothetical spot prices, "
    "turns from negative below to positive above; it need not be a strike), the vanna flip (the "
    "nearest strike where per-strike vanna exposure changes sign), "
    "the vega peaks (the strikes with the most vega above and below spot) and spot plus or minus "
    "the front expiration's one-standard-deviation expected move. Call and put walls are up "
    "to three strikes with the largest positive call GEX and the most negative put GEX, marked "
    "with triangles; hover a triangle for its rank and size. The strike axis is zoomed to the "
    "strikes that carry exposure, with every line in view; everything covers only the strikes "
    "within the sidebar's strike range."
)


def key_levels(view: MarketView) -> pd.DataFrame:
    """One row per computed level: what it is, its price, its size and distance from spot."""
    spot = view.spot
    rows: list[tuple[str, float, str]] = []
    rows += [
        (f"Call wall {i}", w.strike, format_compact_dollars(w.gex))
        for i, w in enumerate(view.walls.call, 1)
    ]
    if view.gamma_flip is not None:
        rows.append(("Gamma flip", view.gamma_flip, ""))
    if view.vanna_flip is not None:
        rows.append(("Vanna flip", view.vanna_flip, ""))
    if view.vega_peaks.upper is not None:
        rows.append(("Vega peak above spot", view.vega_peaks.upper, ""))
    if view.vega_peaks.lower is not None:
        rows.append(("Vega peak below spot", view.vega_peaks.lower, ""))
    if view.expected_move is not None:
        size = f"±{view.expected_move:,.2f}"
        rows.append(("Spot + expected move", spot + view.expected_move, size))
        rows.append(("Spot − expected move", spot - view.expected_move, size))
    rows += [
        (f"Put wall {i}", w.strike, format_compact_dollars(w.gex))
        for i, w in enumerate(view.walls.put, 1)
    ]
    return pd.DataFrame(
        {
            "Level": [r[0] for r in rows],
            "Price": [r[1] for r in rows],
            "GEX at strike / move": [r[2] for r in rows],
            "From spot": [format_from_spot(r[1], spot) for r in rows],
        }
    )


def render(ctx: TabContext) -> None:
    view = ctx.view
    st.subheader("Gamma exposure by strike")
    st.caption(CAPTION)
    st.plotly_chart(
        gex_profile_figure(
            view.gex_profile,
            view.spot,
            view.gamma_flip,
            view.walls,
            vanna_flip=view.vanna_flip,
            vega_peaks=view.vega_peaks,
            expected_move=view.expected_move,
        ),
        width="stretch",
    )
    st.markdown("#### Computed levels")
    table = key_levels(view)
    st.dataframe(
        table,
        hide_index=True,
        height=TABLE_ROW_PX * (len(table) + 1) + 3,  # every level visible, no inner scroll
        column_config={"Price": st.column_config.NumberColumn(format="%.2f")},
    )

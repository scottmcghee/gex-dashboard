"""GEX Ladder tab: the strikes near spot, classified and summarized."""

from __future__ import annotations

from typing import Final

import pandas as pd
import streamlit as st

from gex_dashboard.charts.ladder import ladder_figure, ladder_roles
from gex_dashboard.formatting import format_compact_dollars
from gex_dashboard.patterns import (
    Bias,
    PositiveCluster,
    SaturatedStrike,
    classify_ladder,
    detect_deep_itm_saturation,
    detect_positive_clusters,
    ladder_structure,
    plural,
)
from gex_dashboard.ui.context import BadgeColor, TabContext

BIAS_COLORS: Final[dict[Bias, BadgeColor]] = {
    "pinned": "orange",
    "upside-weighted": "blue",
    "downside-weighted": "red",
    "neutral": "gray",
}

# "Share of peak" is unsigned (absolute GEX over the peak node's), so its bar is neutral gray
# rather than the theme's accent color; the sign is in the Regime column.
TABLE_COLUMNS: Final = {
    "Strike": st.column_config.NumberColumn(format="%.2f"),
    "Share of peak": st.column_config.ProgressColumn(
        min_value=0.0,
        max_value=1.0,
        color="gray",
        help="Absolute GEX as a share of the peak node's; the sign is in the Regime column.",
    ),
}

EXPLAINER = """
The ladder keeps the strikes of the GEX profile that lie within the chosen distance of spot.
Every label is computed from that window alone, and all of it inherits the dealer sign
convention in **Assumptions**.

- **Bars.** Each bar is the strike's net dealer GEX, in dollars per 1% move. Strikes are listed
  from highest to lowest; the spot line crosses the ladder at the current price.
- **Regime.** A strike is *positive* (blue) when its GEX is above 5% of the peak node's absolute
  GEX, *negative* (red) when it is below minus that amount, and *neutral* (gray) in between.
- **Peak node.** The strike with the largest absolute GEX in the window.
- **Low-exposure gap.** A strike whose absolute GEX is under 10% of the peak node's. Gaps are
  drawn faded.
- **Floor and ceiling.** The positive strike with the most GEX below spot (floor) and above
  spot (ceiling).
- **Intermediate node.** A positive or negative strike between spot and the floor or ceiling
  that is neither the peak node nor a low-exposure gap.
- **Upside gamma stack.** At least one negative strike above spot, while positive strikes
  outnumber negative ones below spot.
- **Downside gamma stack.** Positive strikes outnumber negative ones above spot, and negative
  strikes outnumber positive ones below it.
- **Arrangement.** *Pinned* when the peak node is within 0.15% of spot. Otherwise a gamma stack
  decides it, wherever the peak node is: *upside-weighted* for an upside gamma stack and
  *downside-weighted* for a downside gamma stack. Without a stack, *upside-weighted* when at
  least two positive strikes sit below spot and positive strikes outnumber negative ones above
  it, *downside-weighted* when negative strikes outnumber positive ones on both sides, and
  otherwise *neutral*. The label names how the exposure is laid out; it is not a forecast.
- **Positive-gamma cluster.** Three or more neighbouring strikes (no listed strike between
  them), each positive and at least 50% of the peak node's size. It marks a local concentration
  of positive GEX; a broad positive region of smaller strikes is not a cluster.
- **Deep-ITM saturation.** A negative strike where the open-interest-weighted absolute delta of
  every contract at that strike (calls, puts and all expirations in view) is at least 0.85,
  with at least 100 contracts open. Most of that open interest is deep in the money, so its
  delta has little room left to grow. It is listed only when a strike qualifies.
"""


def _cluster_line(cluster: PositiveCluster) -> str:
    return (
        f"Positive-gamma cluster: {len(cluster.strikes)} strikes from {cluster.strikes[0]:,.2f} "
        f"to {cluster.strikes[-1]:,.2f}, largest at {cluster.center_strike:,.2f}, "
        f"{format_compact_dollars(cluster.total_gex)} in total."
    )


def _saturation_line(hit: SaturatedStrike) -> str:
    return (
        f"Deep-ITM saturation at {hit.strike:,.2f}: open-interest-weighted |delta| "
        f"{hit.saturation:.2f} across {hit.total_oi:,} contracts."
    )


def render(ctx: TabContext) -> None:
    view = ctx.view
    st.subheader("GEX ladder")
    pct = st.slider(
        "Strikes within (% of spot)", min_value=1, max_value=10, value=3, key="ladder_range"
    )
    ladder = classify_ladder(view.gex_profile, view.spot, pct / 100)
    structure = ladder_structure(ladder, view.spot)
    if structure is None:
        st.info(f"No strikes with open interest within {pct}% of spot.")
        return
    clusters = detect_positive_clusters(ladder, view.spot)
    saturated = detect_deep_itm_saturation(view.exposures, ladder)

    badges = st.container(horizontal=True)
    with badges:
        st.badge(
            f"Arrangement: {structure.bias}",
            color=BIAS_COLORS[structure.bias],
            help="How positive and negative strikes are laid out around spot; see "
            "**How the ladder is built** below.",
        )
        if clusters:
            st.badge(plural(len(clusters), "positive-gamma cluster"), color="blue")
        if saturated:
            st.badge(plural(len(saturated), "deep-ITM saturated strike"), color="violet")

    roles = ladder_roles(ladder, structure, clusters, saturated)
    st.plotly_chart(ladder_figure(ladder, view.spot, roles), width="stretch")

    st.markdown("#### What the ladder shows")
    lines = [
        *structure.interpretation,
        *map(_cluster_line, clusters),
        *map(_saturation_line, saturated),
    ]
    st.markdown("\n".join(f"- {line}" for line in lines))

    with st.expander("Ladder table"):
        st.dataframe(
            pd.DataFrame(
                {
                    "Strike": ladder["strike"],
                    "Net GEX": ladder["gex"].map(format_compact_dollars),
                    "Regime": ladder["regime"],
                    "Share of peak": ladder["magnitude"],
                    "Roles": roles,
                }
            ),
            hide_index=True,
            column_config=TABLE_COLUMNS,
        )
    with st.expander("How the ladder is built"):
        st.markdown(EXPLAINER)

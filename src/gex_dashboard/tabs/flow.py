"""Customer Flow by Expiration tab: what customers bought minus sold, per expiration."""

from __future__ import annotations

import streamlit as st

from gex_dashboard.charts.flow import FLOW_UNITS, flow_by_expiration_figure
from gex_dashboard.charts.heatmap import strike_expiration_heatmap
from gex_dashboard.flow import (
    FLOW_METRICS,
    FlowMetric,
    flow_by_expiration,
    flow_matrix,
    flow_summary,
)
from gex_dashboard.formatting import format_compact_dollars, front_move_label
from gex_dashboard.providers.base import Capability
from gex_dashboard.ui.context import TabContext, require, require_several_expirations


def render(ctx: TabContext) -> None:
    st.subheader("Customer flow by expiration")
    if not require(ctx, Capability.FLOW, "Customer flow"):
        return
    if not require_several_expirations(ctx, "Customer flow by expiration"):
        return
    metric: FlowMetric = st.radio(
        "Metric",
        FLOW_METRICS,
        format_func=lambda m: FLOW_UNITS[m][0],
        horizontal=True,
        key="flow_metric",
    )
    name, unit, dollars = FLOW_UNITS[metric]
    st.caption(
        f"{name} is what customers bought minus what they sold today, in {unit}. Positive "
        "means net buying. These figures are from the customer side and are not dealer-signed. "
        "Delta and gamma apply each contract's greek to its net contracts (bought puts count as "
        "negative delta). Up to the ten nearest expirations are shown. On the heatmap, the "
        "horizontal lines mark spot and the front expiration's ±1σ expected move."
    )
    view = ctx.view
    st.plotly_chart(
        flow_by_expiration_figure(flow_by_expiration(view.exposures, view.spot, metric), metric),
        width="stretch",
    )
    st.plotly_chart(
        strike_expiration_heatmap(
            flow_matrix(view.exposures, view.spot, metric),
            view.spot,
            title=f"Customer {name.lower()} by strike and expiration",
            value_label=name,
            unit=unit,
            dollars=dollars,
            expected_move=view.expected_move,
            expected_move_label=front_move_label(view.front_expiration),
        ),
        width="stretch",
    )
    st.markdown("#### Net flow per expiration, every metric")
    summary = flow_summary(view.exposures, view.spot)
    table = summary.copy()
    for m in FLOW_METRICS:
        fmt = format_compact_dollars if FLOW_UNITS[m][2] else "{:,.0f}".format
        table[m] = summary[m].map(fmt)
    labels: dict[str, str] = {"expiration": "Expiration"}
    labels.update({m: FLOW_UNITS[m][0] for m in FLOW_METRICS})
    st.dataframe(table.rename(columns=labels), hide_index=True)

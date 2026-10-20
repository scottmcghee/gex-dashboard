"""Customer net flow (bought minus sold) per expiration."""

from __future__ import annotations

from typing import Final

import pandas as pd
import plotly.graph_objects as go

from gex_dashboard.charts.style import (
    FIGURE_HEIGHT,
    NEGATIVE,
    POSITIVE,
    compact_dollar_ticks,
    dollar_hover,
    empty_figure,
)
from gex_dashboard.flow import FlowMetric

# metric -> (display name, unit, is dollars)
FLOW_UNITS: Final[dict[FlowMetric, tuple[str, str, bool]]] = {
    "gamma": ("Net gamma", "$ per 1% move", True),
    "delta": ("Net delta", "$ of underlying", True),
    "volume": ("Net volume", "contracts", False),
    "premium": ("Net premium", "$", True),
}


def flow_by_expiration_figure(frame: pd.DataFrame, metric: FlowMetric) -> go.Figure:
    """Grouped call and put bars of customer net ``metric`` per expiration.

    ``frame`` is ``flow.flow_by_expiration`` output.
    """
    name, unit, dollars = FLOW_UNITS[metric]
    title = f"Customer {name.lower()} by expiration"
    if frame.empty:
        return empty_figure(title, "No customer flow in the selected chain")
    labels = [pd.Timestamp(e).strftime("%b %d") for e in frame["expiration"]]
    fig = go.Figure()
    for column, side, color in (("calls", "Calls", POSITIVE), ("puts", "Puts", NEGATIVE)):
        values = frame[column]
        hover = dollar_hover(values) if dollars else [f"{v:,.0f}" for v in values]
        fig.add_trace(
            go.Bar(
                x=labels,
                y=values,
                name=side,
                marker_color=color,
                customdata=hover,
                hovertemplate=f"%{{x}}<br>{side}: %{{customdata}}<extra></extra>",
            )
        )
    yaxis: dict[str, object] = {"title": f"{name} ({unit})", "zeroline": True}
    if dollars:
        yaxis.update(compact_dollar_ticks([*frame["calls"], *frame["puts"]]))
    fig.update_layout(
        title=title,
        barmode="group",
        height=FIGURE_HEIGHT,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        xaxis={"title": "Expiration", "type": "category"},
        yaxis=yaxis,
    )
    return fig

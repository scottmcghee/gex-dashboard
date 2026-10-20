"""Unusual-volume contracts, split into customer buys and sells when available."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from gex_dashboard.charts.style import FIGURE_HEIGHT, NEGATIVE, POSITIVE, empty_figure

TITLE = "Contracts with unusual volume"


def volume_spikes_figure(spikes: pd.DataFrame) -> go.Figure:
    """Calls on top, puts below; bars are contracts traded today, summed per strike.

    ``spikes`` is ``flow.volume_spikes`` output. With ``volume_buy`` and
    ``volume_sell`` columns each bar is stacked into bought and sold contracts.
    """
    if spikes.empty:
        return empty_figure(TITLE, "No contract is above the volume threshold")
    has_split = {"volume_buy", "volume_sell"}.issubset(spikes.columns)
    fig = make_subplots(
        rows=2, cols=1, subplot_titles=("Calls", "Puts"), vertical_spacing=0.14, shared_xaxes=True
    )
    for row, right in ((1, "C"), (2, "P")):
        side = (
            spikes[spikes["right"] == right]
            .groupby("strike", as_index=False)
            .sum(numeric_only=True)
        )
        series: tuple[tuple[str, str, str], ...]
        if has_split:
            series = (("volume_buy", "Bought", POSITIVE), ("volume_sell", "Sold", NEGATIVE))
        else:
            series = (("volume", "Volume", POSITIVE),)
        for column, name, color in series:
            fig.add_trace(
                go.Bar(
                    x=side["strike"],
                    y=side[column],
                    name=name,
                    marker_color=color,
                    legendgroup=name,
                    showlegend=row == 1,
                    hovertemplate=f"Strike %{{x:,.2f}}<br>{name}: %{{y:,.0f}} contracts"
                    "<extra></extra>",
                ),
                row=row,
                col=1,
            )
    fig.update_layout(
        title=TITLE,
        barmode="stack",
        height=FIGURE_HEIGHT + 120,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.04, "x": 0},
    )
    fig.update_xaxes(title_text="Strike", row=2, col=1)
    fig.update_yaxes(title_text="Contracts traded today")
    return fig

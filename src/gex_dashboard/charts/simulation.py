"""Percentile bands of simulated price paths, with the current levels for reference."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import numpy.typing as npt
import plotly.graph_objects as go

from gex_dashboard.charts.style import (
    FIGURE_HEIGHT,
    POSITIVE,
    Level,
    add_levels,
    empty_figure,
    expected_move_levels,
)
from gex_dashboard.levels import VegaPeaks

TITLE = "Simulated price paths"
_OUTER_FILL = "rgba(42, 120, 214, 0.12)"
_INNER_FILL = "rgba(42, 120, 214, 0.28)"


def simulation_figure(
    bands: Mapping[int, npt.NDArray[np.float64]],
    labels: list[str],
    spot: float,
    gamma_flip: float | None = None,
    expected_move: float | None = None,
    vega_peaks: VegaPeaks | None = None,
) -> go.Figure:
    """The 5-95 and 25-75 percentile bands and the median, against time of day.

    ``bands`` is ``simulation.percentile_bands`` output (keys 5, 25, 50, 75, 95)
    and ``labels`` the matching ``simulation.time_labels``.
    """
    if not labels or any(p not in bands for p in (5, 25, 50, 75, 95)):
        return empty_figure(TITLE, "No simulation to show")
    fig = go.Figure()
    for low, high, fill, name in (
        (5, 95, _OUTER_FILL, "5th-95th"),
        (25, 75, _INNER_FILL, "25th-75th"),
    ):
        fig.add_trace(
            go.Scatter(
                x=labels,
                y=bands[high],
                mode="lines",
                line={"width": 0},
                showlegend=False,
                hoverinfo="skip",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=labels,
                y=bands[low],
                mode="lines",
                line={"width": 0},
                fill="tonexty",
                fillcolor=fill,
                name=f"{name} percentile",
                hovertemplate=f"%{{x}} {name}: %{{y:,.2f}}<extra></extra>",
            )
        )
    fig.add_trace(
        go.Scatter(
            x=labels,
            y=bands[50],
            mode="lines",
            line={"color": POSITIVE, "width": 2},
            name="Median",
            hovertemplate="%{x} median: %{y:,.2f}<extra></extra>",
        )
    )
    levels = [Level(spot, "Spot", "spot"), *expected_move_levels(spot, expected_move)]
    if gamma_flip is not None:
        levels.append(Level(gamma_flip, "Gamma flip", "gamma_flip"))
    if vega_peaks is not None:
        levels += [
            Level(v, "Vega peak", "vega_peak")
            for v in (vega_peaks.lower, vega_peaks.upper)
            if v is not None
        ]
    add_levels(fig, levels, "h")
    fig.update_layout(
        title=TITLE,
        height=FIGURE_HEIGHT,
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        xaxis={"title": "Time (New York)", "type": "category", "nticks": 14},
        yaxis_title="Price ($)",
    )
    return fig

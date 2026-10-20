"""Strike x expiration heatmaps for dealer exposures and customer flow."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from gex_dashboard.charts.style import (
    DIVERGING_SCALE,
    EXPECTED_MOVE_LABEL,
    FIGURE_HEIGHT,
    Level,
    add_levels,
    compact_dollar_ticks,
    empty_figure,
    expected_move_levels,
)
from gex_dashboard.formatting import format_compact_dollars


def strike_expiration_heatmap(
    matrix: pd.DataFrame,
    spot: float,
    title: str,
    value_label: str,
    unit: str,
    dollars: bool = True,
    expected_move: float | None = None,
    expected_move_label: str = EXPECTED_MOVE_LABEL,
) -> go.Figure:
    """A diverging heatmap of ``matrix`` (strike index x expiration columns).

    Zero sits at the neutral midpoint, so color shows sign and depth shows
    size. ``dollars`` formats the hover and color bar as compact dollars;
    otherwise as whole numbers (contracts). The matrix spans several
    expirations, so ``expected_move_label`` should say whose move is drawn.
    """
    if matrix.empty:
        return empty_figure(title, "No data for these strikes and expirations")
    labels = [pd.Timestamp(c).strftime("%b %d") for c in matrix.columns]
    z = matrix.to_numpy(dtype=float)
    if dollars:
        text = [[format_compact_dollars(v) for v in row] for row in z]
        colorbar = {"title": unit, **compact_dollar_ticks(z.ravel(), max_ticks=5)}
    else:
        text = [[f"{v:,.0f}" for v in row] for row in z]
        colorbar = {"title": unit}
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=labels,
            y=matrix.index.to_list(),
            customdata=text,
            colorscale=DIVERGING_SCALE,
            zmid=0,
            colorbar=colorbar,
            hovertemplate=(
                f"Strike %{{y:,.2f}}<br>Expiration %{{x}}<br>{value_label}: %{{customdata}}"
                "<extra></extra>"
            ),
        )
    )
    move = expected_move_levels(spot, expected_move, expected_move_label)
    add_levels(fig, [Level(spot, "Spot", "spot"), *move], "h")
    fig.update_layout(
        title=title,
        height=FIGURE_HEIGHT + 80,
        xaxis={"title": "Expiration", "type": "category"},
        yaxis_title="Strike",
    )
    return fig

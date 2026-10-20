"""Shared colors, dollar axes and reference lines for the chart builders.

Charts are drawn without a Plotly template so Streamlit's light or dark theme
applies. Colors come from one diverging pair (blue for positive dealer
exposure, red for negative) plus a few categorical hues for reference lines;
every line is also named with its value, so no level is identified by color alone.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

import numpy as np
import plotly.graph_objects as go

from gex_dashboard.formatting import format_compact_dollars

POSITIVE: Final = "#2a78d6"  # blue: positive dealer exposure, calls, customer buying
NEGATIVE: Final = "#e34948"  # red: negative dealer exposure, puts, customer selling
NEUTRAL: Final = "#898781"  # gray: neutral strikes, spot
NET_LINE: Final = "#6250d6"  # violet: net GEX line
DIVERGING_SCALE: Final = [[0.0, NEGATIVE], [0.5, "#d9d8d3"], [1.0, POSITIVE]]

# Reference-line colors by level. The gamma flip and the vega peaks often sit close together,
# so they differ in hue (orange, cyan) and in dash pattern.
LEVEL_COLORS: Final = {
    "spot": NEUTRAL,
    "gamma_flip": "#eb6834",  # orange
    "vanna_flip": "#1baf7a",  # green
    "vega_peak": "#0ea5c6",  # cyan
    "expected_move": "#eda100",  # yellow
}
LEVEL_DASHES: Final = {"spot": "solid", "vega_peak": "dot"}  # every other level: "dash"
EXPECTED_MOVE_LABEL: Final = "1σ move"
# Labels on horizontal lines sit on a light, semi-opaque box with dark text, so they stay
# readable over heatmap cells of any color and on either theme.
LABEL_BACKGROUND: Final = "rgba(255, 255, 255, 0.8)"
LABEL_TEXT: Final = "#1f1f1f"

GEX_UNIT: Final = "$ per 1% move"
FIGURE_HEIGHT: Final = 520
_NICE_STEPS: Final = (1.0, 2.0, 2.5, 5.0, 10.0)


def compact_dollar_ticks(values: Iterable[float], max_ticks: int = 6) -> dict[str, list[object]]:
    """``tickvals``/``ticktext`` for an axis spanning ``values``, labelled ``$1.2B`` style.

    Ticks fall on round steps (1, 2, 2.5 or 5 times a power of ten) and always
    include zero when the range crosses it.
    """
    arr = np.asarray([v for v in values if math.isfinite(v)], dtype=np.float64)
    if arr.size == 0:
        return {"tickvals": [], "ticktext": []}
    lo, hi = min(float(arr.min()), 0.0), max(float(arr.max()), 0.0)
    span = hi - lo
    if span == 0:
        return {"tickvals": [0.0], "ticktext": [format_compact_dollars(0.0)]}
    raw_step = span / max(max_ticks - 1, 1)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(s * magnitude for s in _NICE_STEPS if s * magnitude >= raw_step)
    first = math.floor(lo / step) * step
    count = math.ceil((hi - first) / step) + 1
    ticks = [round(first + i * step, 10) for i in range(count)]
    return {"tickvals": ticks, "ticktext": [format_compact_dollars(t) for t in ticks]}


def dollar_hover(values: Iterable[float]) -> list[str]:
    """Each value as compact dollars, for ``customdata`` in a hover template."""
    return [format_compact_dollars(float(v)) for v in values]


def empty_figure(title: str, message: str) -> go.Figure:
    """A titled figure with a centered note instead of data."""
    fig = go.Figure()
    fig.add_annotation(
        text=message, xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False, font={"size": 16}
    )
    fig.update_layout(
        title=title, height=FIGURE_HEIGHT, xaxis={"visible": False}, yaxis={"visible": False}
    )
    return fig


@dataclass(frozen=True)
class Level:
    """A price level to draw as a labelled reference line."""

    value: float
    label: str
    kind: str  # a key of LEVEL_COLORS


def expected_move_levels(
    spot: float, expected_move: float | None, label: str = EXPECTED_MOVE_LABEL
) -> list[Level]:
    """Spot plus and minus the one-sigma expected move (none when it is unknown).

    ``label`` names the move, e.g. which expiration it belongs to on a chart
    that spans several.
    """
    if expected_move is None:
        return []
    return [
        Level(spot + expected_move, f"+{label}", "expected_move"),
        Level(spot - expected_move, f"−{label}", "expected_move"),
    ]


def add_levels(fig: go.Figure, levels: Iterable[Level], orientation: str = "v") -> None:
    """Reference lines, each named with its value so that nearby levels stay readable.

    Vertical lines (``v``) are named in the legend, where close levels cannot
    overlap. Horizontal lines (``h``) are labelled on the plot, alternating
    between the left and right edges, on a semi-opaque box edged in the line's
    color (``LABEL_BACKGROUND``).
    """
    for i, level in enumerate(sorted(levels, key=lambda lv: lv.value)):
        line = {
            "color": LEVEL_COLORS[level.kind],
            "width": 1.5,
            "dash": LEVEL_DASHES.get(level.kind, "dash"),
        }
        text = f"{level.label} {level.value:,.2f}"
        if orientation == "v":
            fig.add_vline(x=level.value, line=line)
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", line=line, name=text))
        else:
            fig.add_hline(y=level.value, line=line)
            left = i % 2 == 0
            fig.add_annotation(
                y=level.value,
                yref="y",
                x=0.0 if left else 1.0,
                xref="paper",
                xanchor="left" if left else "right",
                yanchor="bottom",
                text=text,
                showarrow=False,
                font={"size": 11, "color": LABEL_TEXT},
                bgcolor=LABEL_BACKGROUND,
                bordercolor=line["color"],
                borderwidth=1,
                borderpad=2,
            )

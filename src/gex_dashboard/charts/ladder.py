"""The GEX ladder: one horizontal bar per strike near spot, labelled with its role."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import pandas as pd
import plotly.graph_objects as go

from gex_dashboard.charts.style import (
    GEX_UNIT,
    NEGATIVE,
    NEUTRAL,
    POSITIVE,
    Level,
    add_levels,
    compact_dollar_ticks,
    dollar_hover,
    empty_figure,
)
from gex_dashboard.patterns import LadderStructure, PositiveCluster, SaturatedStrike

TITLE = "GEX ladder"
REGIME_COLORS: Final = {"positive": POSITIVE, "negative": NEGATIVE, "neutral": NEUTRAL}
ROW_HEIGHT: Final = 22
LABEL_ROOM: Final = 0.5  # share of the bar span left free on the right for role labels
LABEL_PAD_LEFT: Final = 0.03


def ladder_roles(
    ladder: pd.DataFrame,
    structure: LadderStructure | None,
    clusters: Sequence[PositiveCluster] = (),
    saturated: Sequence[SaturatedStrike] = (),
) -> list[str]:
    """A comma-separated role label for each ladder row (empty when it has none).

    Roles: peak node, floor, ceiling, intermediate node, low-exposure gap,
    positive-gamma cluster and deep-ITM saturation.
    """
    intermediate = set(structure.intermediate_strikes) if structure else set()
    cluster_strikes = {k for c in clusters for k in c.strikes}
    saturated_strikes = {s.strike for s in saturated}
    roles = []
    for strike, is_peak, is_low in zip(
        ladder["strike"], ladder["is_peak_node"], ladder["is_low_exposure"], strict=True
    ):
        parts = []
        if is_peak:
            parts.append("peak node")
        if structure is not None and strike == structure.floor:
            parts.append("floor")
        if structure is not None and strike == structure.ceiling:
            parts.append("ceiling")
        if strike in intermediate:
            parts.append("intermediate node")
        if is_low:
            parts.append("low-exposure gap")
        if strike in cluster_strikes:
            parts.append("positive-gamma cluster")
        if strike in saturated_strikes:
            parts.append("deep-ITM saturation")
        roles.append(", ".join(parts))
    return roles


def ladder_figure(ladder: pd.DataFrame, spot: float, roles: Sequence[str]) -> go.Figure:
    """Net GEX per strike as horizontal bars, highest strike on top, colored by regime.

    ``ladder`` is ``patterns.classify_ladder`` output and ``roles`` the
    matching ``ladder_roles``. Low-exposure gaps are drawn faded.
    """
    if ladder.empty:
        return empty_figure(TITLE, "No strikes in the ladder window")
    colors = [REGIME_COLORS[r] for r in ladder["regime"]]
    opacity = [0.35 if low else 0.9 for low in ladder["is_low_exposure"]]
    fig = go.Figure(
        go.Bar(
            x=ladder["gex"],
            y=ladder["strike"],
            orientation="h",
            marker={"color": colors, "opacity": opacity},
            customdata=list(zip(dollar_hover(ladder["gex"]), ladder["regime"], roles, strict=True)),
            hovertemplate=(
                "Strike %{y:,.2f}<br>Net GEX: %{customdata[0]}<br>Regime: %{customdata[1]}"
                "<br>%{customdata[2]}<extra></extra>"
            ),
            showlegend=False,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=ladder["gex"].clip(lower=0.0),
            y=ladder["strike"],
            mode="text",
            text=[f"  {r}" for r in roles],
            textposition="middle right",
            hoverinfo="skip",
            showlegend=False,
            cliponaxis=False,
        )
    )
    add_levels(fig, [Level(spot, "Spot", "spot")], "h")
    fig.update_layout(
        title=TITLE,
        height=max(360, ROW_HEIGHT * len(ladder) + 140),
        bargap=0.2,
        xaxis={
            "title": f"Net dealer GEX ({GEX_UNIT})",
            "range": _x_range(ladder["gex"]),
            **compact_dollar_ticks(ladder["gex"]),
        },
        yaxis={"title": "Strike", "dtick": _strike_step(ladder["strike"])},
    )
    return fig


def _x_range(gex: pd.Series) -> list[float]:
    """The bars' extent plus room on the right for the role labels."""
    lo, hi = min(float(gex.min()), 0.0), max(float(gex.max()), 0.0)
    span = (hi - lo) or 1.0
    return [lo - LABEL_PAD_LEFT * span, hi + LABEL_ROOM * span]


def _strike_step(strikes: pd.Series) -> float | None:
    """The smallest gap between listed strikes, so every row gets a tick label."""
    gaps = strikes.sort_values().diff().dropna()
    gaps = gaps[gaps > 0]
    return float(gaps.min()) if not gaps.empty else None

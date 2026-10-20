"""GEX by strike: call and put bars, the net line and the computed levels."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from gex_dashboard.charts.style import (
    FIGURE_HEIGHT,
    GEX_UNIT,
    NEGATIVE,
    NET_LINE,
    POSITIVE,
    Level,
    add_levels,
    compact_dollar_ticks,
    dollar_hover,
    empty_figure,
    expected_move_levels,
)
from gex_dashboard.formatting import format_compact_dollars
from gex_dashboard.levels import GammaWalls, VegaPeaks

TITLE = "Dealer gamma exposure by strike"
ZOOM_FRACTION: Final = 0.02  # strikes under 2% of the largest call or put GEX are trimmed
ZOOM_PAD_STRIKES: Final = 3  # strike steps of margin either side of the zoomed range


def exposure_range(
    profile: pd.DataFrame, levels: Iterable[float], fraction: float = ZOOM_FRACTION
) -> tuple[float, float] | None:
    """An x range over the strikes that carry exposure, widened to show every level.

    A strike carries exposure when its call or put GEX is at least ``fraction``
    of the largest in ``profile``. The range is padded by ``ZOOM_PAD_STRIKES``
    strike steps and never extends past the listed strikes unless a level does.
    None when the profile has no exposure.
    """
    gross = profile[["call_gex", "put_gex"]].abs().max(axis=1)
    peak = float(gross.max()) if len(gross) else 0.0
    if peak <= 0:
        return None
    strikes = profile["strike"].to_numpy(dtype=float)
    carrying = strikes[(gross >= fraction * peak).to_numpy()]
    step = float(np.median(np.diff(strikes))) if len(strikes) > 1 else 1.0
    pad = ZOOM_PAD_STRIKES * step
    lo = max(float(carrying.min()) - pad, float(strikes.min()) - step / 2)
    hi = min(float(carrying.max()) + pad, float(strikes.max()) + step / 2)
    values = [v for v in levels if np.isfinite(v)]
    if values:
        lo = min(lo, min(values) - step / 2)
        hi = max(hi, max(values) + step / 2)
    return lo, hi


def gex_profile_figure(
    profile: pd.DataFrame,
    spot: float,
    gamma_flip: float | None,
    walls: GammaWalls,
    vanna_flip: float | None = None,
    vega_peaks: VegaPeaks | None = None,
    expected_move: float | None = None,
) -> go.Figure:
    """Stacked call (positive) and put (negative) GEX bars per strike with net GEX.

    ``profile`` is ``exposures.gex_by_strike`` output, in dollars per 1% move.
    The x axis is zoomed to the strikes that carry exposure (``exposure_range``),
    which matters for a single expiration, whose GEX sits close to spot; spot,
    every level line and the walls stay in view.
    """
    if profile.empty:
        return empty_figure(TITLE, "No open interest in the selected strikes")
    strikes = profile["strike"]
    fig = go.Figure()
    for column, name, color in (
        ("call_gex", "Call GEX", POSITIVE),
        ("put_gex", "Put GEX", NEGATIVE),
    ):
        fig.add_trace(
            go.Bar(
                x=strikes,
                y=profile[column],
                name=name,
                marker_color=color,
                customdata=dollar_hover(profile[column]),
                hovertemplate=f"Strike %{{x:,.2f}}<br>{name}: %{{customdata}}<extra></extra>",
            )
        )
    fig.add_trace(
        go.Scatter(
            x=strikes,
            y=profile["gex"],
            name="Net GEX",
            mode="lines",
            line={"color": NET_LINE, "width": 2},
            customdata=dollar_hover(profile["gex"]),
            hovertemplate="Strike %{x:,.2f}<br>Net GEX: %{customdata}<extra></extra>",
        )
    )

    levels = [Level(spot, "Spot", "spot"), *expected_move_levels(spot, expected_move)]
    if gamma_flip is not None:
        levels.append(Level(gamma_flip, "Gamma flip", "gamma_flip"))
    if vanna_flip is not None:
        levels.append(Level(vanna_flip, "Vanna flip", "vanna_flip"))
    if vega_peaks is not None:
        levels += [
            Level(v, "Vega peak", "vega_peak")
            for v in (vega_peaks.lower, vega_peaks.upper)
            if v is not None
        ]
    add_levels(fig, levels)

    # Walls are markers named in the legend and on hover: neighbouring walls are often a
    # strike or two apart, where text labels on the plot would overlap.
    for name, wall_list, symbol in (
        ("Call wall", walls.call, "triangle-down"),
        ("Put wall", walls.put, "triangle-up"),
    ):
        if not wall_list:
            continue
        fig.add_trace(
            go.Scatter(
                x=[w.strike for w in wall_list],
                y=[w.gex for w in wall_list],
                name=f"{name}s",
                mode="markers",
                marker={"symbol": symbol, "size": 11, "color": NET_LINE},
                customdata=[
                    f"{name} {i}: {w.strike:,.2f}, {format_compact_dollars(w.gex)}"
                    for i, w in enumerate(wall_list, 1)
                ],
                hovertemplate="%{customdata}<extra></extra>",
            )
        )

    wall_strikes = [w.strike for w in (*walls.call, *walls.put)]
    x_range = exposure_range(profile, [lv.value for lv in levels] + wall_strikes)
    values = [*profile["call_gex"], *profile["put_gex"], *profile["gex"]]
    fig.update_layout(
        title=TITLE,
        barmode="relative",
        bargap=0.15,
        height=FIGURE_HEIGHT + 140,
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "top", "y": -0.14, "x": 0},
        xaxis={"title": "Strike", "range": list(x_range) if x_range else None},
        yaxis={"title": f"Dealer GEX ({GEX_UNIT})", **compact_dollar_ticks(values)},
    )
    return fig

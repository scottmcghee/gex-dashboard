"""Chart builders: traces, axis titles, units and the empty-data fallbacks."""

from __future__ import annotations

import colorsys
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from gex_dashboard.analysis import build_market_view
from gex_dashboard.charts import style
from gex_dashboard.charts.flow import flow_by_expiration_figure
from gex_dashboard.charts.heatmap import strike_expiration_heatmap
from gex_dashboard.charts.ladder import REGIME_COLORS, ladder_figure, ladder_roles
from gex_dashboard.charts.profile import gex_profile_figure
from gex_dashboard.charts.simulation import simulation_figure
from gex_dashboard.charts.volume import volume_spikes_figure
from gex_dashboard.exposures import exposure_matrix
from gex_dashboard.flow import flow_by_expiration, volume_spikes
from gex_dashboard.levels import GammaWalls, VegaPeaks, Wall
from gex_dashboard.patterns import (
    PositiveCluster,
    SaturatedStrike,
    classify_ladder,
    ladder_structure,
)
from gex_dashboard.simulation import percentile_bands, simulate_paths, time_labels
from tests.unit.conftest import gex_profile


def is_empty(fig) -> bool:  # type: ignore[no-untyped-def]
    return not fig.data and len(fig.layout.annotations) == 1


def shape_values(fig, axis: str) -> list[float]:  # type: ignore[no-untyped-def]
    return sorted(s[f"{axis}0"] for s in fig.layout.shapes)


@pytest.fixture
def view(synthetic, clock):  # type: ignore[no-untyped-def]
    return build_market_view(synthetic, "SPY", clock)


# --------------------------------------------------------------------- style


def test_ticks_are_round_dollars_and_include_zero() -> None:
    ticks = style.compact_dollar_ticks([-1.3e9, 2.6e9])
    assert 0.0 in ticks["tickvals"]
    assert ticks["tickvals"][0] <= -1.3e9
    assert ticks["tickvals"][-1] >= 2.6e9
    steps = np.diff(ticks["tickvals"])
    assert np.allclose(steps, steps[0])
    assert steps[0] == 1e9
    assert ticks["ticktext"][ticks["tickvals"].index(1e9)] == "$1.0B"
    assert ticks["ticktext"][0].startswith("-$")


def test_ticks_for_positive_values_start_at_zero() -> None:
    ticks = style.compact_dollar_ticks([5e6, 12e6])
    assert ticks["tickvals"][0] == 0.0
    assert ticks["ticktext"][-1].endswith("M")


@pytest.mark.parametrize(
    ("values", "expected"),
    [([], []), ([float("nan")], []), ([0.0, 0.0], [0.0])],
)
def test_ticks_degenerate(values: list[float], expected: list[float]) -> None:
    assert style.compact_dollar_ticks(values)["tickvals"] == expected


def test_dollar_hover() -> None:
    assert style.dollar_hover([1500.0, -2.5e9]) == ["$1.5K", "-$2.5B"]


def test_expected_move_levels() -> None:
    assert style.expected_move_levels(100.0, None) == []
    up, down = style.expected_move_levels(100.0, 5.0)
    assert (up.value, up.label) == (105.0, "+1σ move")
    assert (down.value, down.label) == (95.0, "−1σ move")


def test_expected_move_levels_can_name_their_expiration() -> None:
    up, down = style.expected_move_levels(100.0, 5.0, "1σ move, Oct 19 expiration")
    assert (up.label, down.label) == ("+1σ move, Oct 19 expiration", "−1σ move, Oct 19 expiration")


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def test_gamma_flip_and_vega_peak_lines_are_distinct() -> None:
    """The two often sit close together: they differ in hue and in dash pattern."""
    flip, vega = (_rgb(style.LEVEL_COLORS[k]) for k in ("gamma_flip", "vega_peak"))
    hue_flip = colorsys.rgb_to_hsv(*(c / 255 for c in flip))[0]
    hue_vega = colorsys.rgb_to_hsv(*(c / 255 for c in vega))[0]
    distance = min(abs(hue_flip - hue_vega), 1 - abs(hue_flip - hue_vega))
    assert distance > 0.25  # more than 90 degrees apart on the color wheel
    fig = style.empty_figure("t", "m")
    flip_line = style.Level(99.0, "Gamma flip", "gamma_flip")
    style.add_levels(fig, [flip_line, style.Level(101.0, "Vega peak", "vega_peak")])
    assert fig.layout.shapes[0].line.dash != fig.layout.shapes[1].line.dash


def test_vertical_levels_are_named_in_the_legend() -> None:
    fig = style.empty_figure("t", "m")
    style.add_levels(
        fig, [style.Level(101.0, "Gamma flip", "gamma_flip"), style.Level(99.0, "Spot", "spot")]
    )
    assert [t.name for t in fig.data] == ["Spot 99.00", "Gamma flip 101.00"]
    assert [s.x0 for s in fig.layout.shapes] == [99.0, 101.0]
    assert fig.layout.shapes[0].line.dash == "solid"
    assert fig.layout.shapes[1].line.dash == fig.data[1].line.dash == "dash"
    assert fig.data[1].line.color == style.LEVEL_COLORS["gamma_flip"]


def test_horizontal_level_labels_alternate_sides() -> None:
    fig = style.empty_figure("t", "m")
    style.add_levels(fig, style.expected_move_levels(100.0, 5.0), "h")
    low, high = fig.layout.annotations[1:]
    assert (low.text, low.x, low.xanchor) == ("−1σ move 95.00", 0.0, "left")
    assert (high.text, high.x, high.xanchor) == ("+1σ move 105.00", 1.0, "right")
    assert [s.y0 for s in fig.layout.shapes] == [95.0, 105.0]


# ------------------------------------------------------------------- profile


def test_profile_traces_levels_and_units() -> None:
    profile = gex_profile([95.0, 100.0, 105.0], [-2e6, 1e6, 3e6])
    walls = GammaWalls(call=(Wall(105.0, 3e6),), put=(Wall(95.0, -2e6),))
    fig = gex_profile_figure(
        profile,
        spot=101.0,
        gamma_flip=98.0,
        walls=walls,
        vanna_flip=99.0,
        vega_peaks=VegaPeaks(upper=104.0, lower=96.0),
        expected_move=2.0,
    )
    assert [t.name for t in fig.data][:3] == ["Call GEX", "Put GEX", "Net GEX"]
    assert list(fig.data[1].y) == [-2e6, 0.0, 0.0]
    assert fig.data[0].customdata[2] == "$3.0M"
    assert fig.layout.yaxis.title.text == "Dealer GEX ($ per 1% move)"
    assert fig.layout.xaxis.title.text == "Strike"
    assert fig.layout.barmode == "relative"
    assert all(t.startswith(("$", "-$")) for t in fig.layout.yaxis.ticktext)
    assert shape_values(fig, "x") == [96.0, 98.0, 99.0, 99.0, 101.0, 103.0, 104.0]
    assert not fig.layout.annotations  # wall names are in the legend and hover, not on the plot
    call_walls, put_walls = fig.data[-2], fig.data[-1]
    assert (call_walls.name, list(call_walls.x)) == ("Call walls", [105.0])
    assert call_walls.customdata[0] == "Call wall 1: 105.00, $3.0M"
    assert (put_walls.name, list(put_walls.y)) == ("Put walls", [-2e6])
    assert [t.name for t in fig.data][3:-2] == [
        "Vega peak 96.00",
        "Gamma flip 98.00",
        "−1σ move 99.00",  # ties keep the order the levels were given
        "Vanna flip 99.00",
        "Spot 101.00",
        "+1σ move 103.00",
        "Vega peak 104.00",
    ]


def test_profile_without_optional_levels() -> None:
    fig = gex_profile_figure(gex_profile([100.0], [1e6]), 100.0, None, GammaWalls())
    assert shape_values(fig, "x") == [100.0]  # spot only
    assert "Put walls" not in [t.name for t in fig.data]


def test_profile_close_call_walls_do_not_overlap_as_text() -> None:
    walls = GammaWalls(call=(Wall(101.0, 3e6), Wall(102.0, 2.9e6), Wall(100.0, 2.8e6)))
    fig = gex_profile_figure(
        gex_profile([100.0, 101.0, 102.0], [2.8e6, 3e6, 2.9e6]), 100.5, None, walls
    )
    assert not fig.layout.annotations
    assert [c.split(":")[0] for c in fig.data[-1].customdata] == [
        "Call wall 1",
        "Call wall 2",
        "Call wall 3",
    ]


def test_single_expiration_profile_zooms_to_the_strikes_with_exposure(synthetic, clock) -> None:  # type: ignore[no-untyped-def]
    view = build_market_view(synthetic, "SPY", clock, [date(2026, 10, 19)], strike_range=0.10)
    fig = gex_profile_figure(
        view.gex_profile,
        view.spot,
        view.gamma_flip,
        view.walls,
        vanna_flip=view.vanna_flip,
        vega_peaks=view.vega_peaks,
        expected_move=view.expected_move,
    )
    lo, hi = fig.layout.xaxis.range
    strikes = view.gex_profile["strike"]
    assert hi - lo < 0.5 * (strikes.max() - strikes.min())  # the 0DTE GEX sits near spot
    gross = view.gex_profile[["call_gex", "put_gex"]].abs().max(axis=1)
    carrying = strikes[gross >= 0.02 * gross.max()]
    assert lo < carrying.min()
    assert carrying.max() < hi
    walls = [w.strike for w in (*view.walls.call, *view.walls.put)]
    assert all(lo < x < hi for x in [*shape_values(fig, "x"), *walls])


def test_profile_zoom_keeps_a_distant_level_in_view() -> None:
    profile = gex_profile(
        [float(k) for k in range(90, 111)], [0.0] * 9 + [1e6, 2e6, 1e6] + [0.0] * 9
    )
    fig = gex_profile_figure(profile, 100.0, 108.0, GammaWalls())
    lo, hi = fig.layout.xaxis.range
    assert lo == pytest.approx(96.0)  # three strikes below the lowest with exposure
    assert hi == pytest.approx(108.5)  # widened to the gamma flip line


def test_profile_without_exposure_is_not_zoomed() -> None:
    fig = gex_profile_figure(gex_profile([99.0, 100.0], [0.0, 0.0]), 100.0, None, GammaWalls())
    assert fig.layout.xaxis.range is None


def test_profile_empty() -> None:
    assert is_empty(gex_profile_figure(gex_profile([], []), 100.0, None, GammaWalls()))


def test_profile_from_a_market_view(view) -> None:  # type: ignore[no-untyped-def]
    fig = gex_profile_figure(view.gex_profile, view.spot, view.gamma_flip, view.walls)
    assert len(fig.data[0].x) == len(view.gex_profile)


# ------------------------------------------------------------------- heatmap


def test_heatmap_dollars(view) -> None:  # type: ignore[no-untyped-def]
    matrix = exposure_matrix(view.exposures, "gamma")
    fig = strike_expiration_heatmap(
        matrix, view.spot, "Dealer gamma exposure", "Gamma", "$ per 1% move", expected_move=3.0
    )
    heat = fig.data[0]
    assert heat.zmid == 0
    assert list(heat.x) == [pd.Timestamp(c).strftime("%b %d") for c in matrix.columns]
    assert heat.colorbar.title.text == "$ per 1% move"
    assert heat.customdata[0][0].startswith(("$", "-$"))
    assert fig.layout.xaxis.title.text == "Expiration"
    assert fig.layout.yaxis.title.text == "Strike"
    assert len(fig.layout.shapes) == 3  # spot and the expected move
    labels = [a.text for a in fig.layout.annotations]
    assert any(t.startswith("+1σ move ") for t in labels)  # the default label


def test_heatmap_names_the_expected_moves_expiration(view) -> None:  # type: ignore[no-untyped-def]
    fig = strike_expiration_heatmap(
        exposure_matrix(view.exposures, "gamma"),
        view.spot,
        "t",
        "Gamma",
        "$ per 1% move",
        expected_move=3.0,
        expected_move_label="1σ move, Oct 19 expiration",
    )
    labels = [a.text for a in fig.layout.annotations]
    assert any(t.startswith("+1σ move, Oct 19 expiration ") for t in labels)
    assert any(t.startswith("−1σ move, Oct 19 expiration ") for t in labels)


def test_heatmap_move_labels_sit_on_a_readable_box(view) -> None:  # type: ignore[no-untyped-def]
    matrix = exposure_matrix(view.exposures, "gamma")
    fig = strike_expiration_heatmap(
        matrix, view.spot, "GEX", "Net GEX", "$", expected_move=view.expected_move
    )
    labels = fig.layout.annotations
    assert [a.text.split(" ")[0] for a in labels] == ["−1σ", "Spot", "+1σ"]
    for label in labels:
        assert label.bgcolor == style.LABEL_BACKGROUND
        assert float(label.bgcolor.rsplit(",", 1)[1].rstrip(")")) >= 0.7  # semi-opaque
        assert label.font.color == style.LABEL_TEXT
        assert label.bordercolor in style.LEVEL_COLORS.values()


def test_heatmap_counts() -> None:
    matrix = pd.DataFrame({date(2026, 10, 23): [1200.0, -40.0]}, index=[99.0, 100.0])
    fig = strike_expiration_heatmap(matrix, 100.0, "t", "Net volume", "contracts", dollars=False)
    assert fig.data[0].customdata[0][0] == "1,200"
    assert fig.data[0].colorbar.tickvals is None


def test_heatmap_empty() -> None:
    assert is_empty(strike_expiration_heatmap(pd.DataFrame(), 100.0, "t", "v", "u"))


# -------------------------------------------------------------------- volume


def test_volume_split_into_bought_and_sold(view) -> None:  # type: ignore[no-untyped-def]
    spikes = volume_spikes(view.exposures, 2.0)
    fig = volume_spikes_figure(spikes)
    names = {t.name for t in fig.data}
    assert names == {"Bought", "Sold"}
    assert fig.layout.barmode == "stack"
    assert fig.layout.yaxis.title.text == "Contracts traded today"
    assert fig.layout.xaxis2.title.text == "Strike"
    call_total = sum(sum(t.y) for t in fig.data if t.yaxis == "y")
    assert call_total == spikes.loc[spikes["right"] == "C", "volume"].sum()


def test_volume_without_flow_columns() -> None:
    spikes = pd.DataFrame({"strike": [100.0, 100.0], "right": ["C", "P"], "volume": [500, 300]})
    fig = volume_spikes_figure(spikes)
    assert [t.name for t in fig.data] == ["Volume", "Volume"]


def test_volume_empty() -> None:
    assert is_empty(volume_spikes_figure(pd.DataFrame()))


# ---------------------------------------------------------------------- flow


def test_flow_dollar_metric(view) -> None:  # type: ignore[no-untyped-def]
    frame = flow_by_expiration(view.exposures, view.spot, "premium")
    fig = flow_by_expiration_figure(frame, "premium")
    assert [t.name for t in fig.data] == ["Calls", "Puts"]
    assert fig.layout.yaxis.title.text == "Net premium ($)"
    assert fig.layout.title.text == "Customer net premium by expiration"
    assert fig.layout.yaxis.ticktext


def test_flow_volume_metric_in_contracts(view) -> None:  # type: ignore[no-untyped-def]
    fig = flow_by_expiration_figure(
        flow_by_expiration(view.exposures, view.spot, "volume"), "volume"
    )
    assert fig.layout.yaxis.title.text == "Net volume (contracts)"
    assert fig.layout.yaxis.ticktext is None
    assert "$" not in fig.data[0].customdata[0]


def test_flow_empty() -> None:
    assert is_empty(flow_by_expiration_figure(pd.DataFrame(), "gamma"))


# ---------------------------------------------------------------- simulation


def test_simulation_bands_and_levels() -> None:
    profile = gex_profile([95.0, 100.0, 105.0], [-1e6, 2e6, 1e6])
    paths = simulate_paths(100.0, profile, 0.2, 30, n_paths=50, seed=0)
    labels = time_labels(30, datetime(2026, 10, 19, 15, 30))
    fig = simulation_figure(
        percentile_bands(paths),
        labels,
        100.0,
        gamma_flip=98.0,
        expected_move=1.5,
        vega_peaks=VegaPeaks(upper=105.0, lower=None),
    )
    names = [t.name for t in fig.data if t.showlegend is not False]
    assert names == ["5th-95th percentile", "25th-75th percentile", "Median"]
    assert fig.data[1].fill == "tonexty"
    assert list(fig.data[0].x) == labels
    assert fig.layout.yaxis.title.text == "Price ($)"
    assert fig.layout.xaxis.title.text == "Time (New York)"
    assert shape_values(fig, "y") == [98.0, 98.5, 100.0, 101.5, 105.0]


def test_simulation_needs_every_band() -> None:
    assert is_empty(simulation_figure({50: np.zeros(3)}, ["a", "b", "c"], 100.0))
    assert is_empty(simulation_figure({}, [], 100.0))


# -------------------------------------------------------------------- ladder


@pytest.fixture
def ladder() -> pd.DataFrame:
    profile = gex_profile(
        [97.0, 98.0, 99.0, 100.0, 101.0, 102.0, 103.0],
        [-5e6, -1e5, -2e6, 1e5, 3e6, 4e6, 1e7],
    )
    return classify_ladder(profile, 100.2, pct_range=0.05)


def test_ladder_roles(ladder: pd.DataFrame) -> None:
    structure = ladder_structure(ladder, 100.2)
    cluster = PositiveCluster([101.0, 102.0, 103.0], 103.0, 1.7e7, 2.0, 0.8)
    roles = dict(
        zip(
            ladder["strike"],
            ladder_roles(ladder, structure, [cluster], [SaturatedStrike(97.0, 0.9, 500, -450.0)]),
            strict=True,
        )
    )
    assert roles[103.0] == "peak node, ceiling, positive-gamma cluster"
    assert roles[101.0] == "intermediate node, positive-gamma cluster"
    assert roles[98.0] == "low-exposure gap"
    assert roles[97.0] == "deep-ITM saturation"


def test_ladder_roles_without_structure(ladder: pd.DataFrame) -> None:
    roles = ladder_roles(ladder, None)
    assert roles[0] == "peak node"


def test_ladder_figure(ladder: pd.DataFrame) -> None:
    roles = ladder_roles(ladder, ladder_structure(ladder, 100.2))
    fig = ladder_figure(ladder, 100.2, roles)
    bar = fig.data[0]
    assert bar.orientation == "h"
    assert list(bar.y) == list(ladder["strike"])
    assert list(bar.marker.color) == [REGIME_COLORS[r] for r in ladder["regime"]]
    faded = [o for o, low in zip(bar.marker.opacity, ladder["is_low_exposure"], strict=True) if low]
    assert faded
    assert set(faded) == {0.35}
    assert [c[2] for c in bar.customdata] == roles
    labels = fig.data[1]
    assert [t.strip() for t in labels.text] == roles
    assert min(labels.x) == 0.0  # roles of negative bars start at zero, right of the axis
    lo, hi = fig.layout.xaxis.range
    assert lo < ladder["gex"].min()
    assert hi > 1.4 * ladder["gex"].max()  # room for the role labels
    assert fig.layout.xaxis.title.text == "Net dealer GEX ($ per 1% move)"
    assert fig.layout.yaxis.dtick == 1.0
    assert shape_values(fig, "y") == [100.2]


def test_ladder_figure_single_strike_and_empty() -> None:
    single = classify_ladder(gex_profile([100.0], [1e6]), 100.0)
    assert ladder_figure(single, 100.0, [""]).layout.yaxis.dtick is None
    assert is_empty(ladder_figure(pd.DataFrame(), 100.0, []))

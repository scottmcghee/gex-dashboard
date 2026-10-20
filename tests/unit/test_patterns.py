"""GEX ladder classification, structure, positive-gamma clusters and deep-ITM saturation."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from gex_dashboard import exposures as ex
from gex_dashboard import patterns
from gex_dashboard.analysis import build_market_view
from gex_dashboard.clock import FixedClock
from gex_dashboard.providers.synthetic import SyntheticProvider
from tests.unit.conftest import SESSION_TIME, gex_profile, make_chain

BANNED = ("fade", "momentum", "sit flat", "magnet", "probab", "expect")


def ladder(strikes, gex, spot=100.0, **kwargs):
    return patterns.classify_ladder(gex_profile(strikes, gex), spot, **kwargs)


class TestClassifyLadder:
    def test_window_and_order(self):
        out = ladder([90, 98, 100, 102, 110], [1, 2, 3, 4, 5])
        assert list(out["strike"]) == [102, 100, 98]

    def test_peak_node_and_magnitude(self):
        out = ladder([98, 100, 102], [1e6, -4e6, 2e6])
        assert out.loc[out["is_peak_node"], "strike"].tolist() == [100]
        assert out.set_index("strike")["magnitude"].to_dict() == pytest.approx(
            {98: 0.25, 100: 1.0, 102: 0.5}
        )

    def test_regimes_with_neutral_band(self):
        out = ladder([98, 99, 100, 101], [1e6, -1e6, 1e4, 0.0]).set_index("strike")
        assert out["regime"].to_dict() == {
            98: "positive",
            99: "negative",
            100: "neutral",
            101: "neutral",
        }

    def test_spot_and_low_exposure_flags(self):
        out = ladder([99, 100, 101], [1e6, 5e4, 2e6], spot=100.4).set_index("strike")
        assert out["is_spot"].to_dict() == {99: False, 100: True, 101: False}
        assert out["is_low_exposure"].to_dict() == {99: False, 100: True, 101: False}

    def test_empty_inputs(self):
        assert patterns.classify_ladder(pd.DataFrame(), 100.0).empty
        assert ladder([50, 60], [1, 2]).empty

    def test_all_zero_gex(self):
        out = ladder([99, 100], [0.0, 0.0])
        assert (out["magnitude"] == 0.0).all()
        assert (out["regime"] == "neutral").all()


class TestLadderStructure:
    def test_floor_ceiling_and_intermediate_nodes(self):
        strikes = [97, 98, 99, 100, 101, 102, 103]
        gex = [5e6, -1e6, 2e6, 1e3, 3e6, -2e6, 6e6]
        s = patterns.ladder_structure(ladder(strikes, gex, spot=100.0), 100.0)
        assert s.floor == 97.0
        assert s.ceiling == 103.0
        assert s.intermediate_strikes == [98.0, 99.0, 101.0, 102.0]
        assert s.peak_strike == 103.0
        assert s.peak_position == "above"
        assert (s.positive_above, s.negative_above) == (2, 1)
        assert (s.positive_below, s.negative_below) == (2, 1)

    def test_pinned_when_peak_at_spot(self):
        s = patterns.ladder_structure(ladder([99, 100, 101], [1e6, 9e6, 1e6]), 100.05)
        assert s.peak_position == "at"
        assert s.bias == "pinned"

    def test_upside_gamma_stack(self):
        s = patterns.ladder_structure(
            ladder([97, 98, 99, 101, 102], [2e6, 3e6, -1e6, -2e6, -9e6]), 100.0
        )
        assert s.upside_gamma_stack
        assert not s.downside_gamma_stack
        assert s.bias == "upside-weighted"
        assert any(line.startswith("Upside gamma stack:") for line in s.interpretation)

    def test_downside_gamma_stack(self):
        s = patterns.ladder_structure(
            ladder([97, 98, 99, 101, 102], [-9e6, -3e6, 1e6, 2e6, 3e6]), 100.0
        )
        assert s.downside_gamma_stack
        assert s.bias == "downside-weighted"
        assert any(line.startswith("Downside gamma stack:") for line in s.interpretation)

    def test_stack_decides_the_arrangement_wherever_the_peak_is(self):
        # A downside stack whose peak node is above spot, and an upside stack peaking below.
        down = patterns.ladder_structure(
            ladder([97, 98, 99, 101, 102], [-1e6, -2e6, -1e6, 2e6, 9e6]), 100.0
        )
        assert down.downside_gamma_stack
        assert down.peak_position == "above"
        assert down.bias == "downside-weighted"
        up = patterns.ladder_structure(
            ladder([97, 98, 99, 101, 102], [9e6, 3e6, 1e6, -2e6, -1e6]), 100.0
        )
        assert up.upside_gamma_stack
        assert up.peak_position == "below"
        assert up.bias == "upside-weighted"

    def test_two_positive_below_with_positive_above_is_upside_weighted(self):
        s = patterns.ladder_structure(ladder([97, 98, 101, 102], [2e6, 3e6, 4e6, 1e6]), 100.0)
        assert s.two_or_more_positive_below
        assert s.bias == "upside-weighted"

    def test_negative_both_sides_is_downside_weighted(self):
        s = patterns.ladder_structure(
            ladder([97, 98, 99, 101, 102, 103], [-2e6, -3e6, 1e6, -4e6, -1e6, -9e6]), 100.0
        )
        assert s.bias == "downside-weighted"

    def test_no_directional_words(self):
        text = " ".join(
            " ".join(
                patterns.ladder_structure(ladder([97, 98, 99, 101, 102], gex), 100.0).interpretation
            )
            for gex in ([2e6, 3e6, -1e6, -2e6, -9e6], [-9e6, -3e6, 1e6, 2e6, 3e6])
        ).lower()
        assert "bullish" not in text
        assert "bearish" not in text
        assert "support" not in text

    def test_neutral(self):
        s = patterns.ladder_structure(ladder([98, 102], [2e6, 1e6]), 100.0)
        assert not s.upside_gamma_stack
        assert not s.downside_gamma_stack
        assert s.bias == "neutral"

    def test_one_negative_above_and_one_positive_below_is_an_upside_stack(self):
        s = patterns.ladder_structure(ladder([98, 102], [2e6, -1e6]), 100.0)
        assert s.upside_gamma_stack
        assert s.bias == "upside-weighted"

    def test_low_exposure_counts_and_text(self):
        s = patterns.ladder_structure(
            ladder([97, 98, 99, 101, 102, 103], [9e6, 1e4, 1e4, 1e4, 1e4, 5e6]), 100.0
        )
        assert (s.low_exposure_above, s.low_exposure_below) == (2, 2)
        assert any("Low-exposure gaps: 2 strikes above" in line for line in s.interpretation)

    def test_one_low_exposure_strike_is_singular(self):
        s = patterns.ladder_structure(
            ladder([97, 98, 99, 101, 102], [9e6, 1e4, 1e4, 1e4, 5e6]), 100.0
        )
        assert (s.low_exposure_above, s.low_exposure_below) == (1, 2)
        text = " ".join(s.interpretation)
        assert "1 strike above spot" in text
        assert "1 strikes" not in text

    @pytest.mark.parametrize(
        ("strikes", "gex", "counts", "sentence"),
        [
            # one gap above spot, none below
            ([98, 101, 102], [9e6, 1e4, 5e6], (1, 0), "1 strike above spot and 0 strikes below"),
            # none above, one below
            ([98, 99, 102], [9e6, 1e4, 5e6], (0, 1), "0 strikes above spot and 1 strike below"),
        ],
    )
    def test_a_single_gap_on_one_side_is_reported(self, strikes, gex, counts, sentence):
        s = patterns.ladder_structure(ladder(strikes, gex), 100.0)
        assert (s.low_exposure_above, s.low_exposure_below) == counts
        assert any(sentence in line for line in s.interpretation), s.interpretation

    def test_no_gaps_no_sentence(self):
        s = patterns.ladder_structure(ladder([98, 102], [9e6, 5e6]), 100.0)
        assert not any("Low-exposure" in line for line in s.interpretation)

    def test_one_intermediate_node_is_singular(self):
        s = patterns.ladder_structure(ladder([98, 101, 102], [9e6, 4e6, 6e6]), 100.0)
        assert s.intermediate_strikes == [101.0]
        assert any(line.startswith("Intermediate node 101.00: a ") for line in s.interpretation)

    @pytest.mark.parametrize(
        ("count", "expected"), [(0, "0 strikes"), (1, "1 strike"), (2, "2 strikes")]
    )
    def test_plural(self, count, expected):
        assert patterns.plural(count, "strike") == expected

    def test_interpretation_describes_without_advice(self):
        s = patterns.ladder_structure(
            ladder([97, 98, 99, 101, 102, 103], [5e6, -1e6, 2e6, 3e6, -2e6, 6e6]), 100.0
        )
        text = " ".join(s.interpretation)
        assert "Peak node 103.00" in text
        assert "Floor 97.00" in text
        assert not any(word in text.lower() for word in BANNED)

    def test_empty(self):
        assert patterns.ladder_structure(pd.DataFrame(), 100.0) is None


def stack_sentence(s: patterns.LadderStructure) -> str | None:
    """``"upside"`` / ``"downside"`` for the gamma stack the interpretation names, else None."""
    for side in ("upside", "downside"):
        if any(line.startswith(f"{side.capitalize()} gamma stack:") for line in s.interpretation):
            return side
    return None


def assert_badge_agrees(s: patterns.LadderStructure) -> None:
    side = stack_sentence(s)
    if side is not None and s.bias != "pinned":
        assert s.bias == f"{side}-weighted", (s.bias, s.interpretation)


class TestArrangementAgreesWithTheStack:
    def test_badge_agrees_with_the_stack_sentence(self):
        """SPY, all expirations, strike range 2%, ladder 10%: a downside stack peaking above."""
        clock = FixedClock(SESSION_TIME)
        view = build_market_view(SyntheticProvider(clock), "SPY", clock, None, 0.02)
        s = patterns.ladder_structure(
            patterns.classify_ladder(view.gex_profile, view.spot, 0.10), view.spot
        )
        assert s.peak_position == "above"
        assert stack_sentence(s) == "downside"
        assert s.bias == "downside-weighted"

    @pytest.mark.parametrize("symbol", ["SPY", "SPX", "QQQ"])
    def test_arrangement_never_contradicts_the_stack(self, symbol):
        """Synthetic snapshots across the session and strike ranges, at every ladder range."""
        stacks = 0
        for minutes in range(0, 390, 65):
            clock = FixedClock(SESSION_TIME.replace(hour=9, minute=30) + timedelta(minutes=minutes))
            provider = SyntheticProvider(clock)
            spot = provider.spot(symbol)
            for strike_range in (0.02, 0.05, 0.10, 0.25):
                chain = provider.chain(symbol, strike_range=strike_range)
                profile = ex.gex_by_strike(ex.contract_exposures(chain, spot))
                for pct in range(1, 11):
                    s = patterns.ladder_structure(
                        patterns.classify_ladder(profile, spot, pct / 100), spot
                    )
                    if s is not None:
                        stacks += stack_sentence(s) is not None
                        assert_badge_agrees(s)
        assert stacks > 0  # the sweep reaches the stack branches

    def test_arrangement_never_contradicts_the_stack_on_random_ladders(self):
        rng = np.random.default_rng(0)
        for _ in range(500):
            n = int(rng.integers(2, 25))
            strikes = sorted(rng.choice(np.arange(90.0, 110.5, 0.5), n, replace=False))
            gex = rng.normal(0, 1e6, n) * rng.choice([0.01, 1.0, 5.0], n)
            spot = float(rng.uniform(95, 105))
            s = patterns.ladder_structure(
                ladder(list(strikes), list(gex), spot=spot, pct_range=0.10), spot
            )
            if s is not None:
                assert_badge_agrees(s)


class TestPositiveClusters:
    def test_single_cluster(self):
        lad = ladder([99.8, 100.0, 100.2, 100.4, 101.5], [3e6, 5e6, 4e6, -1e6, 2e6])
        clusters = patterns.detect_positive_clusters(lad, 100.0)
        assert len(clusters) == 1
        c = clusters[0]
        assert c.strikes == [99.8, 100.0, 100.2]
        assert c.center_strike == 100.0
        assert c.total_gex == pytest.approx(12e6)
        assert c.width == pytest.approx(0.4)
        assert c.distance_to_spot == 0.0

    def test_strike_between_ends_a_run_sorted_by_distance(self):
        strikes = [97.0, 98.0, 99.0, 100.0, 101.0, 102.0, 103.0]
        lad = ladder(strikes, [1e6, 1e6, 1e6, 0.0, 1e6, 1e6, 1e6])
        clusters = patterns.detect_positive_clusters(lad, 100.2)
        assert [c.strikes for c in clusters] == [[101.0, 102.0, 103.0], [97.0, 98.0, 99.0]]

    def test_adjacency_follows_the_chain_grid(self):
        # SPX-like 5-point strikes are neighbours even though 5 points is ~0.9% of spot.
        lad = ladder([570.0, 575.0, 580.0, 585.0], [4e6, 5e6, 4e6, 1e5], spot=578.0)
        assert patterns.detect_positive_clusters(lad, 578.0)[0].strikes == [570.0, 575.0, 580.0]

    def test_broad_positive_region_on_a_fine_grid_is_not_one_cluster(self):
        """SPY's $1 grid: 16 positive strikes above spot are a broad region, not one cluster."""
        strikes = [float(k) for k in range(580, 600)]
        gex = [-2e6, -1e6, -1e6, -5e5] + [3e6] * 16  # each 3/8 of the peak
        gex[4 + 8 : 4 + 11] = [7e6, 8e6, 7.5e6]  # a local concentration at 592-594
        lad = ladder(strikes, gex, spot=583.5, pct_range=0.03)
        clusters = patterns.detect_positive_clusters(lad, 583.5)
        assert [c.strikes for c in clusters] == [[592.0, 593.0, 594.0]]

    def test_too_few_nodes(self):
        lad = ladder([100.0, 100.2], [1e6, 1e6])
        assert patterns.detect_positive_clusters(lad, 100.0) == []

    def test_negative_and_small_strikes_ignored(self):
        lad = ladder([99.8, 100.0, 100.2, 100.4], [1e6, -1e6, 4e5, 1e6])
        assert patterns.detect_positive_clusters(lad, 100.0) == []

    def test_bad_input(self):
        assert patterns.detect_positive_clusters(pd.DataFrame(), 100.0) == []


class TestDeepItmSaturation:
    @pytest.fixture
    def lad(self):
        return ladder([95, 100, 105], [-5e6, 1e6, -5e6], pct_range=0.10)

    def test_saturated_strike(self, lad):
        chain = make_chain(
            [
                {"strike": 105.0, "right": "P", "delta": -0.95, "open_interest": 900},
                {"strike": 105.0, "right": "C", "delta": 0.05, "open_interest": 100},
                {"strike": 95.0, "right": "C", "delta": 0.97, "open_interest": 1000},
                {"strike": 95.0, "right": "P", "delta": -0.03, "open_interest": 10},
                {"strike": 100.0, "right": "C", "delta": 0.99, "open_interest": 5000},
            ]
        )
        hits = patterns.detect_deep_itm_saturation(chain, lad)
        assert [h.strike for h in hits] == [95.0, 105.0]
        assert hits[1].saturation == pytest.approx((0.95 * 900 + 0.05 * 100) / 1000)
        assert hits[1].total_oi == 1000
        assert hits[1].net_delta_oi == pytest.approx(-0.95 * 900 + 0.05 * 100)

    def test_thresholds(self, lad):
        chain = make_chain(
            [
                {"strike": 105.0, "right": "P", "delta": -0.6, "open_interest": 900},
                {"strike": 95.0, "right": "C", "delta": 0.99, "open_interest": 50},
            ]
        )
        assert patterns.detect_deep_itm_saturation(chain, lad) == []

    def test_aggregates_expirations(self, lad):
        chain = make_chain(
            [
                {"strike": 105.0, "right": "P", "delta": -0.9, "open_interest": 100},
                {
                    "strike": 105.0,
                    "right": "P",
                    "delta": -0.9,
                    "open_interest": 100,
                    "expiration": pd.Timestamp("2026-11-20").date(),
                },
            ]
        )
        assert patterns.detect_deep_itm_saturation(chain, lad)[0].total_oi == 200

    def test_bad_input(self, lad):
        assert patterns.detect_deep_itm_saturation(pd.DataFrame(), lad) == []
        assert (
            patterns.detect_deep_itm_saturation(
                make_chain([{"strike": 1.0, "right": "C"}]), pd.DataFrame()
            )
            == []
        )
        positive_only = ladder([100], [1e6])
        chain = make_chain([{"strike": 100.0, "right": "C", "delta": 1.0, "open_interest": 500}])
        assert patterns.detect_deep_itm_saturation(chain, positive_only) == []

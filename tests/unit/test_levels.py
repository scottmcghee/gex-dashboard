"""Gamma flip, walls, vanna flip, vega peaks, expected move and the TradingView string."""

from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from gex_dashboard import exposures as ex
from gex_dashboard import levels
from gex_dashboard.clock import TRADING_DAYS_PER_YEAR
from gex_dashboard.levels import GammaWalls, VegaPeaks, Wall
from tests.unit.conftest import make_chain


def curve(prices: list[float], gex: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"price": prices, "gex": gex})


class TestGammaFlip:
    """``gamma_flip`` on hand-made curves: the side of spot follows the sign at spot."""

    def test_interpolates_the_crossing_below_spot_when_positive(self):
        # Curve at 100.5 is +2, so the flip is the crossing below: 99 + 1 * (1 / 2) = 99.5.
        flip = levels.gamma_flip(curve([98, 99, 100, 101], [-3, -1, 1, 3]), 100.5)
        assert flip == pytest.approx(99.5)

    def test_crossing_above_spot_when_negative(self):
        # Curve at 99.2 is -0.6, so the flip is the first crossing above spot.
        flip = levels.gamma_flip(curve([98, 99, 100, 101], [-3, -1, 1, 3]), 99.2)
        assert flip == pytest.approx(99.5)

    def test_picks_the_crossing_on_the_side_set_by_the_sign_at_spot(self):
        # Rising crossings at 91.0 and 105.0, a falling one at 100.0; spot 102 is negative.
        c = curve([90, 92, 98, 102, 104, 106], [-1, 1, 1, -1, -1, 1])
        assert levels.gamma_flip(c, 102.0) == pytest.approx(105.0)
        # At 95 the curve is positive: the nearest rising crossing below is 91.
        assert levels.gamma_flip(c, 95.0) == pytest.approx(91.0)

    def test_a_disagreeing_net_gex_is_logged_and_the_curve_decides(self, caplog):
        c = curve([98, 99, 100, 101], [-3, -1, 1, 3])
        with caplog.at_level("WARNING", logger="gex_dashboard.levels"):
            assert levels.gamma_flip(c, 100.5, net_gex=-1.0) == pytest.approx(99.5)
        assert "differ in sign" in caplog.text
        caplog.clear()
        with caplog.at_level("WARNING", logger="gex_dashboard.levels"):
            assert levels.gamma_flip(c, 100.5, net_gex=5.0) == pytest.approx(99.5)
        assert not caplog.text

    def test_curve_exactly_zero_at_spot_after_a_negative_point_flips_at_spot(self):
        # Zero at spot counts as "zero or positive", so the flip is the crossing at or
        # below spot: the negative-to-zero step ending at spot itself.
        c = curve([98, 99, 100, 101], [-2, -1, 0, 1])
        assert levels.curve_value_at(c, 100.0) == 0.0
        assert levels.gamma_flip(c, 100.0) == 100.0

    def test_curve_value_at(self):
        assert levels.curve_value_at(curve([98, 100], [-2, 2]), 99.5) == pytest.approx(1.0)
        assert levels.curve_value_at(pd.DataFrame(), 99.5) is None


class TestGammaFlipNote:
    def test_nothing_to_reprice(self):
        assert levels.gamma_flip_note(curve([95, 100, 105], [0, 0, 0]), 100.0) == (
            levels.NO_REPRICING_NOTE
        )
        assert levels.gamma_flip_note(pd.DataFrame(), 100.0) == levels.NO_REPRICING_NOTE

    def test_positive_at_spot_without_a_crossing_below(self):
        note = levels.gamma_flip_note(curve([90, 100, 110], [1, 2, 3]), 100.0)
        assert "zero or positive" in note
        assert "at or below spot down to -10%" in note

    def test_negative_at_spot_without_a_crossing_above(self):
        note = levels.gamma_flip_note(curve([90, 100, 110], [-1, -2, -3]), 100.0)
        assert "negative" in note
        assert "above spot up to +10%" in note

    @pytest.mark.parametrize("gex", [[1, 2, 3], [-1, -2, -3]])
    def test_one_sign_returns_none(self, gex):
        assert levels.gamma_flip(curve([95, 100, 105], gex), 100.0) is None

    def test_empty_returns_none(self):
        assert levels.gamma_flip(pd.DataFrame(), 100.0) is None

    def test_zero_at_a_grid_point(self):
        assert levels.gamma_flip(curve([98, 99, 100], [-1, 0, 2]), 99.5) == pytest.approx(99.0)


class TestGammaFlipFromAChain:
    """The flip of a repriced two-contract chain, against a closed form worked by hand.

    One call at 105 and one put at 95 with the same open interest, IV 20%,
    T = 5/252 (Monday open to Friday close) and rate r = 4%. Dealer GEX is
    ``OI * 100 * S^2 * 0.01 * (gamma_105(S) - gamma_95(S))``, and the two BSM
    gammas share the factor ``1 / (S sigma sqrt(T))``, so GEX is zero where
    ``pdf(d1_105) = pdf(d1_95)``, i.e. ``d1_105 = -d1_95``:
    ``ln(S/105) + ln(S/95) + 2 (r + sigma^2 / 2) T = 0``, so
    ``S = sqrt(105 * 95) * exp(-(r + sigma^2 / 2) T)``. Below that price the put's
    gamma is larger (negative GEX), above it the call's.
    """

    NOW = datetime.fromisoformat("2026-10-19T09:30:00-04:00")
    SIGMA, T, R = 0.20, 5 / 252, 0.04
    FLIP = math.sqrt(105 * 95) * math.exp(-(0.04 + 0.20**2 / 2) * (5 / 252))  # 99.7560...

    @pytest.fixture
    def chain(self):
        return make_chain(
            [
                {"strike": 105.0, "right": "C", "open_interest": 1000, "iv": self.SIGMA},
                {"strike": 95.0, "right": "P", "open_interest": 1000, "iv": self.SIGMA},
            ]
        )

    @pytest.mark.parametrize("spot", [99.0, 100.0, 101.5])
    def test_matches_the_closed_form(self, chain, spot):
        gex = ex.gex_curve(chain, spot, self.NOW, risk_free_rate=self.R)
        net_at_spot = float(np.interp(spot, gex["price"], gex["gex"]))
        flip = levels.gamma_flip(gex, spot)
        assert flip == pytest.approx(self.FLIP, abs=0.005)
        assert (net_at_spot < 0) == (spot < flip)

    def test_all_calls_has_no_flip(self, chain):
        calls = chain.assign(right="C")
        assert levels.gamma_flip(ex.gex_curve(calls, 100.0, self.NOW), 100.0) is None

    def test_is_a_price_not_a_strike(self, chain):
        flip = levels.gamma_flip(ex.gex_curve(chain, 100.0, self.NOW), 100.0)
        assert flip not in set(chain["strike"])


class TestGammaWalls:
    @pytest.fixture
    def profile(self):
        return pd.DataFrame(
            {
                "strike": [90.0, 95.0, 100.0, 105.0, 110.0],
                "call_gex": [1.0, 2.0, 5.0, 9.0, 3.0],
                "put_gex": [-8.0, -6.0, -4.0, -1.0, -0.5],
                "gex": [-7.0, -4.0, 1.0, 8.0, 2.5],
            }
        )

    def test_top_call_and_put_walls(self, profile):
        walls = levels.gamma_walls(profile, n_walls=2)
        assert walls.call == (Wall(105.0, 9.0), Wall(100.0, 5.0))
        assert walls.put == (Wall(90.0, -8.0), Wall(95.0, -6.0))

    def test_fewer_strikes_than_walls(self, profile):
        walls = levels.gamma_walls(profile.head(2), n_walls=3)
        assert len(walls.call) == 2

    def test_empty(self):
        assert levels.gamma_walls(pd.DataFrame()) == GammaWalls()

    def test_all_calls_chain_has_no_put_walls(self):
        chain = make_chain(
            [
                {"strike": k, "right": "C", "open_interest": 100, "gamma": 0.02}
                for k in (95.0, 100.0, 105.0)
            ]
        )
        profile = ex.gex_by_strike(ex.contract_exposures(chain, 100.0))
        walls = levels.gamma_walls(profile)
        assert len(walls.call) == 3
        assert walls.put == ()
        out = levels.tradingview_levels(100.0, walls=walls)
        assert "PW" not in out
        assert out.startswith("CW1=")

    def test_zero_oi_chain_has_no_walls(self):
        chain = make_chain(
            [
                {"strike": k, "right": r, "open_interest": 0, "gamma": 0.02}
                for k in (95.0, 100.0)
                for r in ("C", "P")
            ]
        )
        profile = ex.gex_by_strike(ex.contract_exposures(chain, 100.0))
        assert levels.gamma_walls(profile) == GammaWalls()
        assert levels.tradingview_levels(100.0, walls=levels.gamma_walls(profile)) == ""

    def test_zero_gex_strikes_are_never_walls(self, profile):
        profile.loc[profile["strike"] == 105.0, "call_gex"] = 0.0
        profile.loc[profile["strike"] != 105.0, "put_gex"] = 0.0
        walls = levels.gamma_walls(profile, n_walls=10)
        assert 105.0 not in [w.strike for w in walls.call]
        assert walls.put == (Wall(105.0, -1.0),)


class TestVannaFlip:
    def test_finds_sign_change(self):
        profile = pd.DataFrame({"strike": [95.0, 100.0, 105.0], "vex": [3.0, 1.0, -2.0]})
        assert levels.vanna_flip(profile, 100.0) == 105.0

    def test_picks_change_nearest_spot(self):
        profile = pd.DataFrame(
            {"strike": [90.0, 95.0, 100.0, 105.0, 110.0], "vex": [-1.0, 2.0, 3.0, -1.0, -2.0]}
        )
        assert levels.vanna_flip(profile, 103.0) == 105.0

    @pytest.mark.parametrize("vex", [[1.0, 2.0], [-1.0, -2.0]])
    def test_one_sign_returns_none(self, vex):
        profile = pd.DataFrame({"strike": [95.0, 100.0], "vex": vex})
        assert levels.vanna_flip(profile, 100.0) is None

    def test_empty_returns_none(self):
        assert levels.vanna_flip(pd.DataFrame(), 100.0) is None

    def test_zero_strike_at_the_crossing_does_not_hide_it(self):
        profile = pd.DataFrame({"strike": [95.0, 100.0, 105.0], "vex": [-1.0, 0.0, 1.0]})
        assert levels.vanna_flip(profile, 100.0) == 105.0

    def test_missing_strike_is_skipped(self):
        profile = pd.DataFrame({"strike": [95.0, 100.0, 105.0], "vex": [-1.0, np.nan, 1.0]})
        assert levels.vanna_flip(profile, 100.0) == 105.0


class TestVegaPeaks:
    def test_peaks_above_and_below(self):
        profile = pd.DataFrame(
            {"strike": [90.0, 95.0, 100.0, 105.0, 110.0], "vega_exposure": [5, 9, 50, 7, 3]}
        )
        # The strike at spot belongs to neither side.
        assert levels.vega_peaks(profile, 100.0) == VegaPeaks(upper=105.0, lower=95.0)

    def test_one_side_only(self):
        profile = pd.DataFrame({"strike": [105.0, 110.0], "vega_exposure": [1.0, 2.0]})
        assert levels.vega_peaks(profile, 100.0) == VegaPeaks(upper=110.0, lower=None)

    def test_empty(self):
        assert levels.vega_peaks(pd.DataFrame(), 100.0) == VegaPeaks()


class TestExpectedMove:
    @pytest.fixture
    def chain(self):
        return make_chain(
            [
                {"strike": 95.0, "right": "C", "iv": 0.30},
                {"strike": 100.0, "right": "C", "iv": 0.20},
                {"strike": 100.0, "right": "P", "iv": 0.24},
                {"strike": 105.0, "right": "P", "iv": 0.30},
            ]
        )

    def test_one_standard_deviation(self, chain):
        # 100 * 0.22 * sqrt(5 / 252) = 22 * 0.1408590... = 3.098899
        years = 5 / TRADING_DAYS_PER_YEAR
        assert levels.expected_move(chain, 100.0, years) == pytest.approx(3.098899, abs=1e-6)

    def test_one_year_move_is_spot_times_iv(self, chain):
        assert levels.expected_move(chain, 100.0, 1.0) == pytest.approx(22.0)

    def test_atm_iv_is_call_put_average_at_nearest_strike(self, chain):
        assert levels.atm_iv(chain, 101.0) == pytest.approx(0.22)

    def test_skips_rows_without_iv(self, chain):
        chain.loc[chain["strike"] == 100.0, "iv"] = [float("nan"), 0.0]
        assert levels.atm_iv(chain, 100.0) == pytest.approx(0.30)

    def test_scales_with_sqrt_time(self, chain):
        one = levels.expected_move(chain, 100.0, 1 / 252)
        four = levels.expected_move(chain, 100.0, 4 / 252)
        assert four == pytest.approx(2 * one)

    @pytest.mark.parametrize(("spot", "years"), [(0.0, 0.01), (100.0, 0.0)])
    def test_invalid_inputs_return_none(self, chain, spot, years):
        assert levels.expected_move(chain, spot, years) is None

    def test_no_valid_iv(self, chain):
        chain["iv"] = 0.0
        assert levels.expected_move(chain, 100.0, 0.01) is None
        assert levels.atm_iv(chain.drop(columns=["iv"]), 100.0) is None

    def test_expected_move_at_uses_one_expiration_and_the_clock(self, chain):
        other = chain.assign(expiration=date(2026, 11, 20), iv=0.9)
        both = pd.concat([chain, other])
        now = datetime.fromisoformat("2026-10-22T16:00:00-04:00")
        # Thursday close to Friday close is exactly one session.
        move = levels.expected_move_at(both, 100.0, date(2026, 10, 23), now)
        assert move == pytest.approx(levels.expected_move(chain, 100.0, 1 / 252))
        assert levels.expected_move_at(both.iloc[0:0], 100.0, date(2026, 10, 23), now) is None

    def test_spx_one_day(self):
        # 5800 * 0.15 / sqrt(252) = 870 / 15.874508 = 54.8048
        chain = make_chain([{"strike": 5800.0, "right": r, "iv": 0.15} for r in ("C", "P")])
        assert levels.expected_move(chain, 5800.0, 1 / 252) == pytest.approx(54.8048, abs=1e-4)


class TestTradingViewLevels:
    WALLS = GammaWalls(
        call=(Wall(5920, 1), Wall(5950, 1), Wall(6000, 1), Wall(6050, 1)),
        put=(Wall(5800, -1),),
    )

    def test_all_levels_in_order(self):
        out = levels.tradingview_levels(
            5870.0,
            gamma_flip=5870.0,
            walls=self.WALLS,
            expected_move=80.0,
            vega_peaks=VegaPeaks(upper=5910.0, lower=5830.0),
            vanna_flip=5840.0,
        )
        assert out == (
            "GF=5870|CW1=5920|CW2=5950|CW3=6000|PW1=5800|EU=5950|EL=5790|VPU=5910|VPL=5830|VF=5840"
        )

    def test_empty_when_nothing(self):
        assert levels.tradingview_levels(100.0) == ""

    def test_no_trailing_zeros(self):
        assert levels.tradingview_levels(100.0, gamma_flip=582.50) == "GF=582.5"
        assert levels.tradingview_levels(100.0, gamma_flip=582.257) == "GF=582.26"

    def test_expected_move_skipped_without_spot(self):
        assert levels.tradingview_levels(0.0, expected_move=5.0) == ""

    def test_partial_vega_peaks(self):
        assert levels.tradingview_levels(100.0, vega_peaks=VegaPeaks(lower=95.0)) == "VPL=95"

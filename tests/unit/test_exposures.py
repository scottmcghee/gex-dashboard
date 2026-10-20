"""Units and sign convention of the exposure conversions, pinned by hand-computed contracts."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from gex_dashboard import bsm
from gex_dashboard import exposures as ex
from gex_dashboard.clock import TRADING_DAYS_PER_YEAR, years_to_expiry
from tests.unit.conftest import make_chain

SPOT = 500.0


def _one_contract(right: str, **greeks: float) -> pd.DataFrame:
    return make_chain([{"strike": 500.0, "right": right, "open_interest": 1000, **greeks}])


class TestHandComputedContract:
    """One SPY-like contract: OI 1,000, spot $500."""

    def test_gamma_is_dollars_per_one_percent_move(self):
        # 0.05 * 1,000 contracts * 100 shares * 500^2 * 0.01 = $12.5M
        assert ex.dollar_gamma(0.05, 1000, SPOT) == pytest.approx(12_500_000.0)

    def test_delta_is_dollars_of_underlying(self):
        # 0.5 * 1,000 * 100 * 500 = $25M of stock
        assert ex.dollar_delta(0.5, 1000, SPOT) == pytest.approx(25_000_000.0)

    def test_vanna_is_dollar_delta_per_vol_point(self):
        # 0.01 delta per vol point * 1,000 * 100 * 500 = $500K
        assert ex.dollar_vanna(0.01, 1000, SPOT) == pytest.approx(500_000.0)

    def test_charm_is_dollar_delta_per_trading_day(self):
        assert ex.dollar_charm(-0.002, 1000, SPOT) == pytest.approx(-100_000.0)

    def test_vega_is_dollars_per_vol_point(self):
        assert ex.dollar_vega(0.3, 1000) == pytest.approx(30_000.0)

    def test_contract_exposures_applies_the_conventions(self):
        chain = _one_contract("C", delta=0.5, gamma=0.05, vanna=0.01, charm=-0.002, vega=0.3)
        row = ex.contract_exposures(chain, SPOT).iloc[0]
        assert row["dealer_sign"] == 1.0
        assert row["gamma_exposure"] == pytest.approx(12_500_000.0)
        assert row["delta_exposure"] == pytest.approx(25_000_000.0)
        assert row["vanna_exposure"] == pytest.approx(500_000.0)
        assert row["charm_exposure"] == pytest.approx(-100_000.0)
        assert row["vega_exposure"] == pytest.approx(30_000.0)


class TestMeaningAgainstBsm:
    """The dollar figures equal the dealer-delta change they claim to measure."""

    S, K, T, SIGMA, OI = 580.0, 585.0, 5 / 252, 0.16, 2000

    def _dollar_delta(self, s=S, t=T, sigma=SIGMA):
        return bsm.delta(s, self.K, t, sigma, True) * self.OI * 100 * s

    def test_gamma_matches_a_one_percent_move(self):
        # Change in share-delta over a 1% move, valued at spot. GEX is the linear
        # (first-order) estimate, so it agrees to within the curvature of gamma.
        move = bsm.delta(self.S * 1.005, self.K, self.T, self.SIGMA, True) - bsm.delta(
            self.S * 0.995, self.K, self.T, self.SIGMA, True
        )
        expected = move * self.OI * 100 * self.S
        gamma = float(bsm.gamma(self.S, self.K, self.T, self.SIGMA))
        assert ex.dollar_gamma(gamma, self.OI, self.S) == pytest.approx(expected, rel=1e-2)

    def test_vanna_matches_a_vol_point(self):
        move = self._dollar_delta(sigma=self.SIGMA + 0.005) - self._dollar_delta(
            sigma=self.SIGMA - 0.005
        )
        chain_vanna = float(bsm.vanna(self.S, self.K, self.T, self.SIGMA)) * 0.01
        assert ex.dollar_vanna(chain_vanna, self.OI, self.S) == pytest.approx(move, rel=1e-3)

    def test_charm_matches_one_trading_day(self):
        day = 1 / TRADING_DAYS_PER_YEAR
        move = self._dollar_delta(t=self.T - day / 2) - self._dollar_delta(t=self.T + day / 2)
        chain_charm = float(bsm.charm(self.S, self.K, self.T, self.SIGMA, True)) / 252
        assert ex.dollar_charm(chain_charm, self.OI, self.S) == pytest.approx(move, rel=1e-2)

    def test_gamma_scales_with_spot_squared(self):
        assert ex.dollar_gamma(0.01, 100, 200.0) == pytest.approx(
            4 * ex.dollar_gamma(0.01, 100, 100.0)
        )


class TestDealerSign:
    def test_single_constant(self):
        assert ex.DEALER_SIGN == {"C": 1.0, "P": -1.0}

    def test_puts_are_negative_calls_positive(self):
        chain = make_chain(
            [
                {"strike": 500.0, "right": "C", "open_interest": 100, "gamma": 0.02},
                {"strike": 500.0, "right": "P", "open_interest": 100, "gamma": 0.02},
            ]
        )
        out = ex.contract_exposures(chain, SPOT)
        call, put = out["gamma_exposure"]
        assert call > 0
        assert put == pytest.approx(-call)

    def test_vanna_and_charm_follow_the_sign_vega_does_not(self):
        chain = _one_contract("P", vanna=0.01, charm=0.001, vega=0.3, delta=-0.4)
        row = ex.contract_exposures(chain, SPOT).iloc[0]
        assert row["vanna_exposure"] < 0
        assert row["charm_exposure"] < 0
        assert row["vega_exposure"] > 0
        # Dealer short a put with delta -0.4 holds +0.4 delta per share.
        assert row["delta_exposure"] > 0


class TestProfiles:
    @pytest.fixture
    def chain(self):
        return make_chain(
            [
                {
                    "strike": 95.0,
                    "right": "P",
                    "open_interest": 200,
                    "gamma": 0.03,
                    "vanna": 0.02,
                    "charm": 0.001,
                    "vega": 0.1,
                    "volume": 7,
                },
                {
                    "strike": 95.0,
                    "right": "C",
                    "open_interest": 50,
                    "gamma": 0.03,
                    "vanna": 0.02,
                    "charm": 0.001,
                    "vega": 0.1,
                    "volume": 3,
                },
                {
                    "strike": 105.0,
                    "right": "C",
                    "open_interest": 300,
                    "gamma": 0.02,
                    "vanna": -0.01,
                    "charm": -0.002,
                    "vega": 0.2,
                    "volume": 5,
                },
                {
                    "strike": 105.0,
                    "right": "C",
                    "open_interest": 100,
                    "gamma": 0.02,
                    "vanna": -0.01,
                    "charm": -0.002,
                    "vega": 0.2,
                    "volume": 1,
                    "expiration": date(2026, 11, 20),
                },
            ]
        )

    def test_gex_by_strike_schema_and_split(self, chain):
        exp = ex.contract_exposures(chain, 100.0)
        profile = ex.gex_by_strike(exp)
        assert list(profile.columns) == list(ex.GEX_PROFILE_COLUMNS)
        low = profile.iloc[0]
        assert low["strike"] == 95.0
        assert low["call_gex"] == pytest.approx(ex.dollar_gamma(0.03, 50, 100.0))
        assert low["put_gex"] == pytest.approx(-ex.dollar_gamma(0.03, 200, 100.0))
        assert low["gex"] == pytest.approx(low["call_gex"] + low["put_gex"])
        assert low["open_interest"] == 250
        assert low["volume"] == 10

    def test_aggregates_across_expirations(self, chain):
        profile = ex.gex_by_strike(ex.contract_exposures(chain, 100.0))
        high = profile[profile["strike"] == 105.0].iloc[0]
        assert high["call_gex"] == pytest.approx(ex.dollar_gamma(0.02, 400, 100.0))

    def test_vanna_charm_vega_profiles(self, chain):
        exp = ex.contract_exposures(chain, 100.0)
        vex = ex.vanna_profile(exp)
        assert list(vex.columns) == ["strike", "vex"]
        assert vex["vex"].iloc[0] == pytest.approx(ex.dollar_vanna(0.02, 50 - 200, 100.0))
        charm = ex.charm_profile(exp)
        assert list(charm.columns) == ["strike", "charm_exposure"]
        vega = ex.vega_profile(exp)
        assert vega["vega_exposure"].iloc[0] == pytest.approx(ex.dollar_vega(0.1, 250))

    def test_net_exposure(self, chain):
        exp = ex.contract_exposures(chain, 100.0)
        assert ex.net_exposure(exp, "gamma") == pytest.approx(exp["gamma_exposure"].sum())

    def test_does_not_mutate_input(self, chain):
        before = chain.copy()
        ex.contract_exposures(chain, 100.0)
        pd.testing.assert_frame_equal(chain, before)

    def test_missing_second_order_columns_are_unavailable(self, chain):
        exp = ex.contract_exposures(chain.drop(columns=["vanna", "charm"]), 100.0)
        assert ex.net_exposure(exp, "vanna") is None
        assert ex.vanna_profile(exp).empty
        assert ex.exposure_matrix(exp, "charm").empty
        assert not ex.gex_by_strike(exp).empty

    def test_empty_chain(self):
        exp = ex.contract_exposures(make_chain([]).iloc[0:0], 100.0)
        assert exp.empty
        assert "gamma_exposure" in exp
        assert ex.gex_by_strike(exp).empty
        assert list(ex.gex_by_strike(exp).columns) == list(ex.GEX_PROFILE_COLUMNS)
        assert ex.net_exposure(exp, "gamma") is None


class TestExposureMatrix:
    def test_strike_by_expiration(self):
        exps = [date(2026, 10, 23), date(2026, 10, 30), date(2026, 11, 6)]
        chain = make_chain(
            [
                {"strike": k, "right": r, "expiration": e, "open_interest": 10, "gamma": 0.01}
                for e in exps
                for k in (95.0, 100.0)
                for r in ("C", "P")
            ]
            + [
                {
                    "strike": 90.0,
                    "right": "C",
                    "expiration": exps[0],
                    "open_interest": 5,
                    "gamma": 0.01,
                }
            ]
        )
        grid = ex.exposure_matrix(ex.contract_exposures(chain, 100.0), "gamma", max_expirations=2)
        assert list(grid.columns) == exps[:2]
        assert list(grid.index) == [90.0, 95.0, 100.0]
        assert grid.loc[95.0, exps[0]] == pytest.approx(0.0)  # call and put cancel
        assert grid.loc[90.0, exps[1]] == 0.0  # missing cell filled
        assert grid.loc[90.0, exps[0]] > 0


class TestGammaRatio:
    @pytest.mark.parametrize(
        ("call", "put", "expected"),
        [
            (1.0, -1.0, 0.5),
            (3.0, -1.0, 0.75),
            (1.0, -3.0, 0.25),
            (5.0, 0.0, 1.0),
            (0.0, -5.0, 0.0),
            (0.0, 0.0, None),
            (-3.0, 1.0, 0.75),
        ],
    )
    def test_values(self, call, put, expected):
        ratio = ex.gamma_ratio(call, put)
        assert ratio == (None if expected is None else pytest.approx(expected))


class TestMissingGreeks:
    """A NaN greek is unavailable for that contract, not zero."""

    @pytest.fixture
    def chain(self):
        return make_chain(
            [
                {"strike": 100.0, "right": "C", "open_interest": 100, "gamma": 0.02},
                {"strike": 100.0, "right": "P", "open_interest": 50, "gamma": np.nan},
                {"strike": 105.0, "right": "C", "open_interest": 10, "gamma": np.nan},
            ]
        )

    def test_partial_nan_gamma_is_left_out_of_sums(self, chain, caplog):
        exp = ex.contract_exposures(chain, 100.0)
        assert "2 of 3 contracts have no gamma" in caplog.text
        assert ex.has_metric(exp, "gamma")
        assert ex.net_exposure(exp, "gamma") == pytest.approx(ex.dollar_gamma(0.02, 100, 100.0))
        profile = ex.gex_by_strike(exp).set_index("strike")
        assert profile.loc[100.0, "gex"] == pytest.approx(ex.dollar_gamma(0.02, 100, 100.0))
        assert profile.loc[105.0, "gex"] == 0.0
        assert np.isfinite(profile["gex"]).all()

    def test_all_nan_is_unavailable(self, chain):
        exp = ex.contract_exposures(chain.assign(vanna=np.nan), 100.0)
        assert not ex.has_metric(exp, "vanna")
        assert ex.net_exposure(exp, "vanna") is None


class TestGexCurve:
    NOW = datetime.fromisoformat("2026-10-19T11:00:00-04:00")
    EXPIRY = date(2026, 10, 23)

    def _chain(self, spot: float) -> pd.DataFrame:
        """Contracts whose chain gamma is BSM at ``spot`` with the default rate."""
        t = years_to_expiry(self.NOW, self.EXPIRY)
        rows = []
        for k, right, oi, iv in [
            (95.0, "P", 900, 0.24),
            (100.0, "C", 400, 0.20),
            (100.0, "P", 300, 0.21),
            (105.0, "C", 700, 0.18),
        ]:
            gamma = float(bsm.gamma(spot, k, t, iv, r=bsm.DEFAULT_RISK_FREE_RATE))
            rows.append(
                {"strike": k, "right": right, "open_interest": oi, "iv": iv, "gamma": gamma}
            )
        return make_chain(rows)

    def test_at_spot_equals_the_chain_gex(self):
        chain = self._chain(100.0)
        out = ex.gex_curve(chain, 100.0, self.NOW, prices=np.array([100.0]))
        net = ex.net_exposure(ex.contract_exposures(chain, 100.0), "gamma")
        assert out["gex"].iloc[0] == pytest.approx(net, rel=1e-9)

    def test_reprices_at_other_prices(self):
        # Moving spot to 103 and repricing equals a chain priced at 103 from the start.
        out = ex.gex_curve(self._chain(100.0), 100.0, self.NOW, prices=np.array([103.0]))
        net = ex.net_exposure(ex.contract_exposures(self._chain(103.0), 103.0), "gamma")
        assert out["gex"].iloc[0] == pytest.approx(net, rel=1e-9)

    def test_default_grid_is_centred_on_spot(self):
        out = ex.gex_curve(self._chain(100.0), 100.0, self.NOW)
        assert list(out.columns) == list(ex.GEX_CURVE_COLUMNS)
        assert len(out) == ex.CURVE_POINTS
        assert out["price"].iloc[0] == pytest.approx(90.0)
        assert out["price"].iloc[-1] == pytest.approx(110.0)
        assert out["price"].iloc[ex.CURVE_POINTS // 2] == 100.0

    def test_skips_unpriceable_contracts(self):
        chain = self._chain(100.0)
        chain.loc[0, "iv"] = np.nan
        chain.loc[1, "open_interest"] = 0
        chain.loc[2, "expiration"] = date(2026, 10, 16)  # expired
        out = ex.gex_curve(chain, 100.0, self.NOW, prices=np.array([100.0]))
        only = ex.net_exposure(ex.contract_exposures(chain.iloc[[3]], 100.0), "gamma")
        assert out["gex"].iloc[0] == pytest.approx(only, rel=1e-9)

    def test_empty_inputs(self):
        assert ex.gex_curve(make_chain([]).iloc[0:0], 100.0, self.NOW).empty
        nothing = self._chain(100.0).assign(open_interest=0)
        assert (ex.gex_curve(nothing, 100.0, self.NOW)["gex"] == 0).all()


def test_dealer_sign_series():
    out = ex.dealer_sign(pd.Series(["C", "P", "C"]))
    assert np.array_equal(out.to_numpy(), [1.0, -1.0, 1.0])

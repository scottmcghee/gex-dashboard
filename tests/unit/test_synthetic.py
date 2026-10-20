"""Determinism, calendar and market shape of the synthetic provider."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from gex_dashboard import bsm
from gex_dashboard.analysis import build_market_view
from gex_dashboard.clock import FixedClock, years_to_expiry
from gex_dashboard.exposures import exposure_matrix
from gex_dashboard.providers.synthetic import (
    PROFILES,
    WEEKLY_COUNT,
    SyntheticProvider,
    _round_strike_bonus,
    expiration_schedule,
)


def at(text: str, seed: int = 0) -> SyntheticProvider:
    return SyntheticProvider(FixedClock(datetime.fromisoformat(text)), seed=seed)


class TestDeterminism:
    def test_same_instant_same_chain(self):
        a, b = at("2026-10-19T11:00"), at("2026-10-19T11:00")
        pd.testing.assert_frame_equal(a.chain("SPY"), b.chain("SPY"))
        assert a.spot("SPY") == b.spot("SPY")

    def test_seed_and_symbol_change_the_market(self):
        base = at("2026-10-19T11:00").chain("SPY")["open_interest"]
        other_seed = at("2026-10-19T11:00", seed=1).chain("SPY")["open_interest"]
        assert not base.equals(other_seed)
        assert at("2026-10-19T11:00").spot("QQQ") != at("2026-10-19T11:00").spot("SPY")

    def test_spot_moves_through_the_session(self):
        spots = {at(f"2026-10-19T{h}:00").spot("SPY") for h in (10, 11, 12, 13, 14, 15)}
        assert len(spots) == 6

    def test_spot_before_open_and_after_close(self):
        assert at("2026-10-19T06:00").spot("SPY") == at("2026-10-19T09:30").spot("SPY")
        assert at("2026-10-19T16:00").spot("SPY") == at("2026-10-19T20:00").spot("SPY")

    def test_weekend_holds_fridays_close(self):
        friday_close = at("2026-10-23T16:00").spot("SPX")
        assert at("2026-10-24T12:00").spot("SPX") == friday_close
        assert at("2026-10-25T23:00").spot("SPX") == friday_close

    def test_symbol_is_case_insensitive(self):
        p = at("2026-10-19T11:00")
        assert p.spot("spy") == p.spot("SPY")

    def test_unlisted_symbol_gets_a_seeded_profile(self):
        p = at("2026-10-19T11:00")
        assert p.spot("AAPL") == at("2026-10-19T11:00").spot("AAPL")
        assert not p.chain("AAPL").empty


class TestCalendar:
    def test_monday_session(self):
        exps = expiration_schedule(datetime.fromisoformat("2026-10-19T11:00-04:00"))
        assert exps[0] == date(2026, 10, 19)  # 0DTE
        assert date(2026, 10, 20) in exps  # next trading day
        assert date(2026, 10, 23) in exps  # weekly Friday
        assert date(2026, 11, 20) in exps  # monthly (third Friday)
        assert date(2027, 1, 15) in exps
        assert exps == sorted(set(exps))

    def test_0dte_drops_after_the_close(self):
        exps = expiration_schedule(datetime.fromisoformat("2026-10-19T16:00-04:00"))
        assert date(2026, 10, 19) not in exps
        assert exps[0] == date(2026, 10, 20)

    def test_friday_includes_itself_until_close(self):
        assert expiration_schedule(datetime.fromisoformat("2026-10-23T12:00-04:00"))[0] == date(
            2026, 10, 23
        )

    def test_weekend_starts_monday(self):
        assert expiration_schedule(datetime.fromisoformat("2026-10-24T12:00-04:00"))[0] == date(
            2026, 10, 26
        )

    def test_friday_after_the_close_lists_four_weeklies_like_saturday(self):
        friday_close = expiration_schedule(datetime.fromisoformat("2026-11-06T16:00-05:00"))
        saturday = expiration_schedule(datetime.fromisoformat("2026-11-07T12:00-05:00"))
        assert friday_close == saturday
        weeklies = [date(2026, 11, 13), date(2026, 11, 20), date(2026, 11, 27), date(2026, 12, 4)]
        assert len(weeklies) == WEEKLY_COUNT
        assert all(day in friday_close for day in weeklies)

    def test_december_rolls_the_year(self):
        exps = expiration_schedule(datetime.fromisoformat("2026-12-21T12:00-05:00"))
        assert date(2027, 1, 15) in exps
        assert date(2027, 3, 19) in exps


class TestGreekUnits:
    def test_columns_match_bsm_in_chain_units(self):
        clock = FixedClock(datetime.fromisoformat("2026-10-19T11:00"))
        p = SyntheticProvider(clock, risk_free_rate=0.04)
        exp = date(2026, 10, 23)
        row = p.chain("SPY", [exp]).iloc[0]
        spot, t = p.spot("SPY"), years_to_expiry(clock.now(), exp)
        is_call = row["right"] == "C"
        args = (spot, row["strike"], t, row["iv"])
        assert row["gamma"] == pytest.approx(bsm.gamma(*args, r=0.04))
        assert row["vega"] == pytest.approx(bsm.vega(*args, r=0.04) / 100)
        assert row["vanna"] == pytest.approx(bsm.vanna(*args, r=0.04) / 100)
        assert row["theta"] == pytest.approx(bsm.theta(*args, is_call, r=0.04) / 252)
        assert row["charm"] == pytest.approx(bsm.charm(*args, is_call, r=0.04) / 252)

    def test_smile_is_skewed(self):
        chain = at("2026-10-19T11:00").chain("SPY", [date(2026, 11, 20)])
        calls = chain[chain["right"] == "C"].sort_values("strike")
        assert calls["iv"].iloc[0] > calls["iv"].iloc[len(calls) // 2]


class TestFlow:
    def test_volume_accumulates_through_the_session(self):
        totals = [
            at(f"2026-10-19T{t}").chain("SPY")["volume"].sum()
            for t in ("09:00", "10:00", "13:00", "16:30")
        ]
        assert totals[0] == 0
        assert totals[0] < totals[1] < totals[2] < totals[3]

    def test_buy_plus_sell_is_volume(self):
        chain = at("2026-10-19T12:00").chain("SPY")
        assert (chain["volume_buy"] + chain["volume_sell"] == chain["volume"]).all()

    def test_0dte_flow_leans_to_buying(self):
        chain = at("2026-10-19T12:00").chain("SPY")
        zero = chain[chain["expiration"] == date(2026, 10, 19)]
        rest = chain[chain["expiration"] != date(2026, 10, 19)]
        assert zero["volume_buy"].sum() / zero["volume"].sum() > 0.55
        assert abs(rest["volume_buy"].sum() / rest["volume"].sum() - 0.5) < 0.03

    def test_some_contracts_spike(self):
        chain = at("2026-10-19T15:00").chain("SPY")
        assert (chain["volume"] > 2 * chain["volume"].mean()).sum() >= 3


SHAPE_CASES = [
    (symbol, when)
    for symbol in PROFILES
    for when in ("2026-10-19T10:00", "2026-10-19T13:00", "2026-10-22T11:30", "2026-10-24T12:00")
]


@pytest.mark.parametrize(("symbol", "when"), SHAPE_CASES)
class TestShape:
    def _view(self, symbol, when):
        clock = FixedClock(datetime.fromisoformat(when))
        return build_market_view(SyntheticProvider(clock), symbol, clock)

    def test_flip_lies_between_the_walls(self, symbol, when):
        view = self._view(symbol, when)
        assert view.gamma_flip is not None
        assert view.walls.put[0].strike < view.gamma_flip < view.walls.call[0].strike

    def test_put_oi_below_spot_call_oi_above(self, symbol, when):
        view = self._view(symbol, when)
        df = view.exposures
        below, above = df[df["strike"] < view.spot], df[df["strike"] > view.spot]

        def oi(side, right):
            return side.loc[side["right"] == right, "open_interest"].sum()

        assert oi(below, "P") > oi(below, "C")
        assert oi(above, "C") > oi(above, "P")

    def test_vanna_flip_exists(self, symbol, when):
        assert self._view(symbol, when).vanna_flip is not None


def test_regimes_differ_across_default_symbols():
    clock = FixedClock(datetime.fromisoformat("2026-10-19T11:00"))
    p = SyntheticProvider(clock)
    regimes = {build_market_view(p, s, clock).regime.key for s in ("SPY", "SPX", "QQQ")}
    assert len(regimes) > 1


def test_open_interest_round_strike_bonus():
    chain = at("2026-10-19T11:00").chain("SPY", [date(2026, 11, 20)])
    calls = chain[chain["right"] == "C"].set_index("strike")["open_interest"]
    round_mean = calls[np.isclose(calls.index % 10, 0)].mean()
    assert round_mean > calls.mean()


def test_round_strike_bonus_levels():
    strikes = np.array([570.0, 575.0, 576.0, 580.0])
    assert _round_strike_bonus(strikes, 1.0).tolist() == [2.0, 1.25, 1.0, 2.0]
    assert _round_strike_bonus(np.array([5800.0, 5825.0, 5805.0]), 5.0).tolist() == [2.0, 1.25, 1.0]


def test_0dte_open_interest_sits_near_the_money():
    """0DTE gamma must be visible next to the monthlies (see ``ZERO_DTE_CENTER_SCALE``)."""
    clock = FixedClock(datetime.fromisoformat("2026-10-19T10:30-04:00"))
    provider = SyntheticProvider(clock)
    for symbol in ("SPY", "SPX", "QQQ"):
        view = build_market_view(provider, symbol, clock)
        column_peaks = exposure_matrix(view.exposures, "gamma").abs().max()
        assert column_peaks[date(2026, 10, 19)] >= 0.25 * column_peaks.max(), symbol
        zero = view.exposures[view.exposures["expiration"] == date(2026, 10, 19)]
        distance = (np.log(zero["strike"] / view.spot).abs() * zero["open_interest"]).sum()
        assert distance / zero["open_interest"].sum() < 0.02, symbol

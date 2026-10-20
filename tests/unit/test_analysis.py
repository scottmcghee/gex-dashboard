"""The MarketView assembled from one chain snapshot."""

from __future__ import annotations

import time
from datetime import date, datetime

import numpy as np
import pytest

from gex_dashboard import clock as ck
from gex_dashboard import exposures as ex
from gex_dashboard import levels
from gex_dashboard.analysis import analyze_chain, build_market_view
from gex_dashboard.clock import FixedClock, years_to_expiry
from gex_dashboard.providers.base import FLOW_COLUMNS, SECOND_ORDER_COLUMNS
from gex_dashboard.providers.synthetic import SyntheticProvider
from tests.unit.conftest import SESSION_TIME


def test_full_view(synthetic, clock):
    view = build_market_view(synthetic, "SPY", clock)
    assert view.symbol == "SPY"
    assert view.as_of == clock.now()
    assert view.spot == synthetic.spot("SPY")
    assert view.expirations == tuple(synthetic.expirations("SPY"))
    assert view.front_expiration == date(2026, 10, 19)
    assert view.net_gex == pytest.approx(view.gex_profile["gex"].sum())
    assert view.net_gex == pytest.approx(view.exposures["gamma_exposure"].sum())
    assert 0 <= view.gamma_ratio <= 1
    assert view.alignment is not None
    assert view.hedging is not None
    assert view.hedging.charm_remaining == pytest.approx(view.net_charm * 300 / 390)
    assert view.net_delta_flow is not None
    assert view.vega_peaks.upper is not None
    assert view.tradingview_levels().startswith("GF=")
    assert view.gamma_flip is not None
    assert (view.net_gex < 0) == (view.spot < view.gamma_flip)


def test_expected_move_uses_front_expiration(synthetic, clock):
    view = build_market_view(synthetic, "SPY", clock)
    front = view.exposures[view.exposures["expiration"] == view.front_expiration]
    years = years_to_expiry(clock.now(), view.front_expiration)
    assert view.expected_move == pytest.approx(levels.expected_move(front, view.spot, years))
    assert view.atm_iv == pytest.approx(levels.atm_iv(front, view.spot))


def test_single_expiration(synthetic, clock):
    view = build_market_view(synthetic, "SPY", clock, [date(2026, 10, 23)], strike_range=0.05)
    assert view.expirations == (date(2026, 10, 23),)
    assert view.front_expiration == date(2026, 10, 23)


def test_basic_chain_without_optional_columns(synthetic):
    chain = synthetic.chain("SPY").drop(columns=[*SECOND_ORDER_COLUMNS, *FLOW_COLUMNS])
    view = analyze_chain("SPY", synthetic.spot("SPY"), chain, SESSION_TIME)
    assert view.has_data
    assert view.net_vanna is None
    assert view.vanna_flip is None
    assert view.alignment is None
    assert view.hedging is None
    assert view.net_delta_flow is None
    assert view.regime.key == ("positive_gamma" if view.net_gex >= 0 else "negative_gamma")


def test_empty_chain(synthetic):
    view = analyze_chain("SPY", 100.0, synthetic.chain("SPY", [date(1999, 1, 1)]), SESSION_TIME)
    assert not view.has_data
    assert view.net_gex == 0.0
    assert view.regime is None
    assert view.gamma_ratio is None
    assert view.alignment is None
    assert view.gamma_flip is None
    assert view.gex_curve.empty
    assert view.expected_move is None
    assert view.front_expiration is None
    assert view.tradingview_levels() == ""


def test_zero_open_interest_chain(synthetic):
    chain = synthetic.chain("SPY", strike_range=0.02).assign(open_interest=0)
    view = analyze_chain("SPY", synthetic.spot("SPY"), chain, SESSION_TIME)
    assert view.gamma_ratio is None
    assert view.walls.call == ()
    assert view.walls.put == ()
    assert view.gamma_flip is None
    assert "CW" not in view.tradingview_levels()


def test_partial_nan_gamma(synthetic):
    chain = synthetic.chain("SPY", strike_range=0.05)
    chain.loc[chain.index[::7], "gamma"] = np.nan
    view = analyze_chain("SPY", synthetic.spot("SPY"), chain, SESSION_TIME)
    assert np.isfinite(view.net_gex)
    assert np.isfinite(view.gex_profile["gex"]).all()
    assert view.gamma_flip is not None  # repriced from IV, so it does not need the chain gamma


class _Ticks:
    """A fake ``time.monotonic`` that returns the given readings in order, then the last."""

    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def test_one_clock_read_across_the_replay_wrap(monkeypatch):
    """A replay clock at 15:59:59 wraps to 09:30 a second later; the view must not mix them."""
    ticks = _Ticks(0.0, 0.0, 1.0, 2.0, 3.0)  # origin, then one second per read
    monkeypatch.setattr(ck._time, "monotonic", ticks)
    replay = ck.ReplayClock(datetime.fromisoformat("2026-10-19T15:59:59-04:00"))
    provider = SyntheticProvider(replay)
    view = build_market_view(provider, "SPY", replay)
    assert view.as_of == datetime.fromisoformat("2026-10-19T15:59:59-04:00")
    assert view.spot == provider.spot("SPY", view.as_of)
    assert view.spot != provider.spot("SPY", datetime.fromisoformat("2026-10-19T09:30-04:00"))
    # The 0DTE chain was priced at the as-of instant too: one second left to the close.
    front = view.exposures[view.exposures["expiration"] == date(2026, 10, 19)]
    reference = provider.chain("SPY", [date(2026, 10, 19)], now=view.as_of)
    assert front["gamma"].to_numpy() == pytest.approx(reference["gamma"].to_numpy())


def test_expirations_closed_at_now_are_dropped(synthetic):
    """A chain fetched at 15:59 and analysed at 17:00 describes only what is still open."""
    before = datetime.fromisoformat("2026-10-19T15:59:00-04:00")
    after = datetime.fromisoformat("2026-10-19T17:00:00-04:00")
    spot = synthetic.spot("SPY", before)
    chain = synthetic.chain("SPY", now=before)
    assert date(2026, 10, 19) in set(chain["expiration"])

    view = analyze_chain("SPY", spot, chain, after)
    assert date(2026, 10, 19) not in set(view.exposures["expiration"])
    assert view.expirations[0] == view.front_expiration == date(2026, 10, 20)
    rest = chain[chain["expiration"] != date(2026, 10, 19)]
    assert view.net_gex == pytest.approx(ex.contract_exposures(rest, spot)["gamma_exposure"].sum())
    assert view.expected_move is not None
    next_day = rest[rest["expiration"] == date(2026, 10, 20)]
    assert view.atm_iv == pytest.approx(levels.atm_iv(next_day, spot))
    # Net GEX, the walls and the repriced curve describe the same contracts.
    at_spot = levels.curve_value_at(view.gex_curve, spot)
    assert at_spot == pytest.approx(view.net_gex, rel=1e-3)  # greeks priced an hour apart
    assert {w.strike for w in view.walls.call} <= set(rest["strike"])


def test_only_expired_contracts_leave_an_empty_view(synthetic):
    before = datetime.fromisoformat("2026-10-19T15:59:00-04:00")
    chain = synthetic.chain("SPY", [date(2026, 10, 19)], now=before)
    view = analyze_chain("SPY", 580.0, chain, datetime.fromisoformat("2026-10-19T16:00-04:00"))
    assert not view.has_data
    assert view.front_expiration is None


def test_a_disagreeing_vendor_gamma_leaves_the_side_to_the_curve(synthetic, caplog):
    chain = synthetic.chain("SPY", now=SESSION_TIME)
    spot = synthetic.spot("SPY", SESSION_TIME)
    reference = analyze_chain("SPY", spot, chain, SESSION_TIME)
    curve_sign = np.sign(levels.curve_value_at(reference.gex_curve, spot))
    # Scale the chain's own gamma so that its net GEX has the other sign; IV is unchanged.
    calls, puts = chain["right"] == "C", chain["right"] == "P"
    scaled = chain.copy()
    scaled.loc[puts if curve_sign < 0 else calls, "gamma"] *= 0.0
    with caplog.at_level("WARNING", logger="gex_dashboard.levels"):
        view = analyze_chain("SPY", spot, scaled, SESSION_TIME)
    assert np.sign(view.net_gex) == -curve_sign
    assert "differ in sign" in caplog.text
    assert view.gamma_flip == pytest.approx(reference.gamma_flip)  # the curve decides the side


# Property: over many synthetic snapshots, the flip is a real rising zero of repriced net GEX.
PROPERTY_SEEDS = (0, 1, 2)
PROPERTY_TIMES = (
    "2026-10-19T08:00",
    "2026-10-19T09:31",
    "2026-10-19T11:00",
    "2026-10-19T15:55",
    "2026-10-19T18:00",
    "2026-10-23T15:59",
    "2026-10-24T12:00",
)
PROPERTY_SYMBOLS = ("SPY", "SPX", "QQQ", "IWM")


def test_gamma_flip_is_a_rising_zero_of_the_repriced_curve():
    found = 0
    for seed in PROPERTY_SEEDS:
        for when in PROPERTY_TIMES:
            fixed = FixedClock(datetime.fromisoformat(when))
            provider = SyntheticProvider(fixed, seed=seed)
            for symbol in PROPERTY_SYMBOLS:
                view = build_market_view(provider, symbol, fixed)
                case = (seed, when, symbol)
                # The repriced curve at spot and the chain's own greeks agree on the sign.
                at_spot = levels.curve_value_at(view.gex_curve, view.spot)
                assert np.sign(at_spot) == np.sign(view.net_gex), case
                if view.gamma_flip is None:
                    assert view.gamma_flip_note, case
                    continue
                found += 1
                # Reprice off the grid at the flip and one grid step (0.1% of spot) either side.
                step = view.spot * 0.001
                prices = np.array([view.gamma_flip - step, view.gamma_flip, view.gamma_flip + step])
                below, at, above = ex.gex_curve(
                    view.exposures, view.spot, view.as_of, prices=prices
                )["gex"]
                # A true rising zero lies within one grid step of the flip. (Minutes before a
                # 0DTE close the curve bends within a step, so GEX at the flip itself need
                # not be near zero; the grid step is the flip's resolution.)
                rises = (below < 0 <= at) or (at < 0 <= above)
                assert rises, (case, below, at, above)
                assert view.gamma_flip_note is None, case
    total = len(PROPERTY_SEEDS) * len(PROPERTY_TIMES) * len(PROPERTY_SYMBOLS)
    assert found >= 0.9 * total, f"a flip in only {found} of {total} snapshots"


def test_gamma_flip_is_cheap(synthetic):
    chain = synthetic.chain("SPY", now=SESSION_TIME)
    spot = synthetic.spot("SPY", SESSION_TIME)
    start = time.perf_counter()
    for _ in range(5):
        levels.gamma_flip(ex.gex_curve(chain, spot, SESSION_TIME), spot)
    # A loose bound so a busy CI runner does not fail it; measured runtimes are in REQUIREMENTS.md.
    assert (time.perf_counter() - start) / 5 < 0.5

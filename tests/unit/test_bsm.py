"""BSM greeks against central finite differences of the BSM price and delta."""

from __future__ import annotations

import numpy as np
import pytest

from gex_dashboard import bsm

H_S = 1e-3
H_SIGMA = 1e-5
H_T = 1e-6

CASES = [
    # s, k, t, sigma, r, q
    (100.0, 105.0, 0.1, 0.2, 0.0, 0.0),  # the reference case below, with r = 0
    (100.0, 105.0, 0.1, 0.2, 0.05, 0.0),
    (100.0, 90.0, 0.5, 0.35, 0.03, 0.02),
    (5800.0, 5750.0, 2 / 252, 0.15, 0.04, 0.0),
    (580.0, 600.0, 1.0, 0.18, 0.04, 0.015),
]
RIGHTS = [True, False]


def _fd(f, x, h):
    return (f(x + h) - f(x - h)) / (2 * h)


@pytest.mark.parametrize(("s", "k", "t", "sigma", "r", "q"), CASES)
@pytest.mark.parametrize("is_call", RIGHTS)
class TestFiniteDifferences:
    def test_delta(self, s, k, t, sigma, r, q, is_call):
        expected = _fd(lambda x: bsm.price(x, k, t, sigma, is_call, r, q), s, H_S)
        assert bsm.delta(s, k, t, sigma, is_call, r, q) == pytest.approx(expected, rel=1e-6)

    def test_gamma(self, s, k, t, sigma, r, q, is_call):
        expected = _fd(lambda x: bsm.delta(x, k, t, sigma, is_call, r, q), s, H_S)
        assert bsm.gamma(s, k, t, sigma, r, q) == pytest.approx(expected, rel=1e-5)

    def test_vega(self, s, k, t, sigma, r, q, is_call):
        expected = _fd(lambda v: bsm.price(s, k, t, v, is_call, r, q), sigma, H_SIGMA)
        assert bsm.vega(s, k, t, sigma, r, q) == pytest.approx(expected, rel=1e-5)

    def test_vanna(self, s, k, t, sigma, r, q, is_call):
        expected = _fd(lambda v: bsm.delta(s, k, t, v, is_call, r, q), sigma, H_SIGMA)
        assert bsm.vanna(s, k, t, sigma, r, q) == pytest.approx(expected, rel=1e-5)

    def test_theta(self, s, k, t, sigma, r, q, is_call):
        # Calendar time passing shortens time to expiry: theta = -dV/dT.
        expected = -_fd(lambda x: bsm.price(s, k, x, sigma, is_call, r, q), t, H_T)
        assert bsm.theta(s, k, t, sigma, is_call, r, q) == pytest.approx(expected, rel=1e-4)

    def test_charm(self, s, k, t, sigma, r, q, is_call):
        expected = -_fd(lambda x: bsm.delta(s, k, x, sigma, is_call, r, q), t, H_T)
        assert bsm.charm(s, k, t, sigma, is_call, r, q) == pytest.approx(expected, rel=1e-4)


class TestReferenceValues:
    """One worked case (S 100, K 105, T 0.1, sigma 20%, r 5%) checked by finite difference."""

    def test_vanna_matches_a_finite_difference_of_delta(self):
        """d(delta)/d(sigma) by central finite difference is 1.161 per 1.00 of volatility."""
        assert bsm.vanna(100.0, 105.0, 0.1, 0.2, r=0.05) == pytest.approx(1.161, abs=1e-3)

    def test_charm_matches_a_finite_difference_of_delta(self):
        """d(delta)/dt by central finite difference is -1.4145 per year, calls and puts alike."""
        for is_call in RIGHTS:
            assert bsm.charm(100.0, 105.0, 0.1, 0.2, is_call, r=0.05) == pytest.approx(
                -1.4145, abs=1e-4
            )

    def test_charm_is_equal_for_calls_and_puts_without_dividends(self):
        """With q = 0 put-call parity makes call and put charm identical."""
        call = bsm.charm(100.0, 100.0, 0.25, 0.2, True, r=0.05)
        put = bsm.charm(100.0, 100.0, 0.25, 0.2, False, r=0.05)
        assert call == pytest.approx(put)

    def test_charm_differs_with_a_dividend_yield(self):
        call = bsm.charm(100.0, 100.0, 0.25, 0.2, True, r=0.05, q=0.03)
        put = bsm.charm(100.0, 100.0, 0.25, 0.2, False, r=0.05, q=0.03)
        assert call - put == pytest.approx(0.03 * np.exp(-0.03 * 0.25))


class TestPriceAndShape:
    def test_put_call_parity(self):
        s, k, t, sigma, r, q = 100.0, 95.0, 0.4, 0.25, 0.03, 0.01
        call = bsm.price(s, k, t, sigma, True, r, q)
        put = bsm.price(s, k, t, sigma, False, r, q)
        assert call - put == pytest.approx(s * np.exp(-q * t) - k * np.exp(-r * t))

    def test_gamma_known_value(self):
        # ATM, T = 1, sigma = 0.2, r = 0: d1 = 0.1, gamma = pdf(0.1) / (100 * 0.2).
        expected = np.exp(-0.005) / np.sqrt(2 * np.pi) / 20.0
        assert bsm.gamma(100.0, 100.0, 1.0, 0.2) == pytest.approx(expected)

    def test_gamma_peaks_at_the_money(self):
        g = bsm.gamma(100.0, np.array([80.0, 100.0, 120.0]), 0.25, 0.2)
        assert g[1] > g[0]
        assert g[1] > g[2]

    def test_vanna_sign_by_moneyness(self):
        assert bsm.vanna(100.0, 130.0, 0.25, 0.2) > 0  # OTM call: d2 < 0
        assert bsm.vanna(100.0, 70.0, 0.25, 0.2) < 0  # ITM call: d2 > 0

    def test_vectorized_and_broadcast(self):
        k = np.array([95.0, 100.0, 105.0])
        is_call = np.array([True, False, True])
        assert bsm.price(100.0, k, 0.25, 0.2, is_call).shape == (3,)
        assert bsm.charm(100.0, k, 0.25, 0.2, is_call).shape == (3,)


class TestDegenerateInputs:
    @pytest.mark.parametrize(("t", "sigma"), [(0.0, 0.2), (-0.1, 0.2), (0.25, 0.0)])
    def test_greeks_are_zero(self, t, sigma):
        for value in (
            bsm.gamma(100.0, 100.0, t, sigma),
            bsm.vega(100.0, 100.0, t, sigma),
            bsm.vanna(100.0, 100.0, t, sigma),
            bsm.theta(100.0, 100.0, t, sigma, True),
            bsm.charm(100.0, 100.0, t, sigma, True),
        ):
            assert value == 0.0

    def test_expired_price_is_intrinsic(self):
        assert bsm.price(105.0, 100.0, 0.0, 0.2, True) == pytest.approx(5.0)
        assert bsm.price(105.0, 100.0, 0.0, 0.2, False) == 0.0
        assert bsm.price(95.0, 100.0, 0.0, 0.2, False) == pytest.approx(5.0)

    def test_expired_delta_is_payoff_slope(self):
        assert bsm.delta(105.0, 100.0, 0.0, 0.2, True) == 1.0
        assert bsm.delta(95.0, 100.0, 0.0, 0.2, True) == 0.0
        assert bsm.delta(95.0, 100.0, 0.0, 0.2, False) == -1.0
        assert bsm.delta(105.0, 100.0, 0.0, 0.2, False) == 0.0

    def test_mixed_valid_and_invalid_elements(self):
        g = bsm.gamma(100.0, 100.0, np.array([0.0, 0.25]), 0.2)
        assert g[0] == 0.0
        assert g[1] > 0

"""Black-Scholes-Merton prices and greeks, vectorized over numpy arrays.

Inputs broadcast against each other: spot ``s``, strike ``k``, time to expiry
``t`` in years, volatility ``sigma`` (annual, as a fraction), risk-free rate
``r`` and continuous dividend yield ``q`` (default 0).

Outputs are in **textbook units**, per share:

- ``price``: dollars.
- ``delta``: dV/dS. ``gamma``: d(delta)/dS.
- ``vega``: dV/d(sigma), per 1.00 of volatility (divide by 100 for a vol point).
- ``vanna``: d(delta)/d(sigma), per 1.00 of volatility.
- ``theta``: dV/dt per year of ``t`` as time to expiry shrinks.
- ``charm``: d(delta)/dt per year of ``t`` as time to expiry shrinks.

Where ``t <= 0`` or ``sigma <= 0`` (or a price is not positive) the greeks are
0, the price is intrinsic value and delta is the expiry payoff's slope.
Providers convert these to the per-vol-point / per-trading-day units of the
chain contract (see ``gex_dashboard.providers.base``).
"""

from __future__ import annotations

from typing import Final

import numpy as np
import numpy.typing as npt
from scipy.special import ndtr

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]
ArrayLike = npt.ArrayLike

_INV_SQRT_2PI: Final = 1.0 / np.sqrt(2.0 * np.pi)
DEFAULT_RISK_FREE_RATE: Final = 0.04  # GEX_RISK_FREE_RATE's default


def _cdf(x: FloatArray) -> FloatArray:
    return np.asarray(ndtr(x), dtype=np.float64)


def _pdf(x: FloatArray) -> FloatArray:
    return np.asarray(_INV_SQRT_2PI * np.exp(-0.5 * x * x), dtype=np.float64)


class _Inputs:
    """Broadcast inputs plus d1/d2, computed only where the model is defined."""

    def __init__(
        self,
        s: ArrayLike,
        k: ArrayLike,
        t: ArrayLike,
        sigma: ArrayLike,
        r: float,
        q: float,
        is_call: ArrayLike = True,
    ) -> None:
        arrays = np.broadcast_arrays(
            np.asarray(s, dtype=np.float64),
            np.asarray(k, dtype=np.float64),
            np.asarray(t, dtype=np.float64),
            np.asarray(sigma, dtype=np.float64),
            np.asarray(is_call, dtype=np.bool_),
        )
        self.s, self.k, self.t, self.sigma = (np.asarray(a, dtype=np.float64) for a in arrays[:4])
        self.is_call: BoolArray = np.asarray(arrays[4], dtype=np.bool_)
        self.r, self.q = r, q
        self.valid: BoolArray = (self.t > 0) & (self.sigma > 0) & (self.s > 0) & (self.k > 0)
        # Placeholders outside the valid region keep the formulas finite; callers mask them out.
        t_safe = np.where(self.valid, self.t, 1.0)
        sig_safe = np.where(self.valid, self.sigma, 1.0)
        s_safe = np.where(self.valid, self.s, 1.0)
        k_safe = np.where(self.valid, self.k, 1.0)
        self.sqrt_t = np.sqrt(t_safe)
        self.sig_sqrt_t = sig_safe * self.sqrt_t
        self.d1 = (np.log(s_safe / k_safe) + (r - q + 0.5 * sig_safe**2) * t_safe) / self.sig_sqrt_t
        self.d2 = self.d1 - self.sig_sqrt_t
        self.disc_q = np.exp(-q * t_safe)
        self.disc_r = np.exp(-r * t_safe)
        self.t_safe, self.sig_safe, self.s_safe, self.k_safe = t_safe, sig_safe, s_safe, k_safe

    def masked(self, values: FloatArray, fallback: FloatArray | float = 0.0) -> FloatArray:
        return np.asarray(np.where(self.valid, values, fallback), dtype=np.float64)


def price(
    s: ArrayLike,
    k: ArrayLike,
    t: ArrayLike,
    sigma: ArrayLike,
    is_call: ArrayLike,
    r: float = 0.0,
    q: float = 0.0,
) -> FloatArray:
    """Option value in dollars per share."""
    x = _Inputs(s, k, t, sigma, r, q, is_call)
    call = x.s_safe * x.disc_q * _cdf(x.d1) - x.k_safe * x.disc_r * _cdf(x.d2)
    put = x.k_safe * x.disc_r * _cdf(-x.d2) - x.s_safe * x.disc_q * _cdf(-x.d1)
    intrinsic = np.where(x.is_call, np.maximum(x.s - x.k, 0.0), np.maximum(x.k - x.s, 0.0))
    return x.masked(np.where(x.is_call, call, put), intrinsic)


def delta(
    s: ArrayLike,
    k: ArrayLike,
    t: ArrayLike,
    sigma: ArrayLike,
    is_call: ArrayLike,
    r: float = 0.0,
    q: float = 0.0,
) -> FloatArray:
    """dV/dS per share."""
    x = _Inputs(s, k, t, sigma, r, q, is_call)
    call = x.disc_q * _cdf(x.d1)
    put = -x.disc_q * _cdf(-x.d1)
    expired = np.where(x.is_call, (x.s > x.k).astype(float), -(x.s < x.k).astype(float))
    return x.masked(np.where(x.is_call, call, put), expired)


def gamma(
    s: ArrayLike, k: ArrayLike, t: ArrayLike, sigma: ArrayLike, r: float = 0.0, q: float = 0.0
) -> FloatArray:
    """d(delta)/dS per share; the same for calls and puts."""
    x = _Inputs(s, k, t, sigma, r, q)
    return x.masked(x.disc_q * _pdf(x.d1) / (x.s_safe * x.sig_sqrt_t))


def vega(
    s: ArrayLike, k: ArrayLike, t: ArrayLike, sigma: ArrayLike, r: float = 0.0, q: float = 0.0
) -> FloatArray:
    """dV/d(sigma) per 1.00 of volatility; the same for calls and puts."""
    x = _Inputs(s, k, t, sigma, r, q)
    return x.masked(x.s_safe * x.disc_q * _pdf(x.d1) * x.sqrt_t)


def vanna(
    s: ArrayLike, k: ArrayLike, t: ArrayLike, sigma: ArrayLike, r: float = 0.0, q: float = 0.0
) -> FloatArray:
    """d(delta)/d(sigma) per 1.00 of volatility; the same for calls and puts.

    ``-e^(-qT) * pdf(d1) * d2 / sigma``, with no further division by
    ``S * sqrt(T)``; the tests check it against a finite difference of delta
    in sigma.
    """
    x = _Inputs(s, k, t, sigma, r, q)
    return x.masked(-x.disc_q * _pdf(x.d1) * x.d2 / x.sig_safe)


def theta(
    s: ArrayLike,
    k: ArrayLike,
    t: ArrayLike,
    sigma: ArrayLike,
    is_call: ArrayLike,
    r: float = 0.0,
    q: float = 0.0,
) -> FloatArray:
    """dV/dt per year of ``t`` as time to expiry shrinks (usually negative)."""
    x = _Inputs(s, k, t, sigma, r, q, is_call)
    decay = -x.s_safe * x.disc_q * _pdf(x.d1) * x.sig_safe / (2.0 * x.sqrt_t)
    call = decay - r * x.k_safe * x.disc_r * _cdf(x.d2) + q * x.s_safe * x.disc_q * _cdf(x.d1)
    put = decay + r * x.k_safe * x.disc_r * _cdf(-x.d2) - q * x.s_safe * x.disc_q * _cdf(-x.d1)
    return x.masked(np.where(x.is_call, call, put))


def charm(
    s: ArrayLike,
    k: ArrayLike,
    t: ArrayLike,
    sigma: ArrayLike,
    is_call: ArrayLike,
    r: float = 0.0,
    q: float = 0.0,
) -> FloatArray:
    """d(delta)/dt per year of ``t`` as time to expiry shrinks.

    The shared term is ``-e^(-qT) pdf(d1) (2(r-q)T - d2 sigma sqrt(T)) / (2T sigma sqrt(T))``;
    calls add ``q e^(-qT) N(d1)`` and puts subtract ``q e^(-qT) N(-d1)``. With
    ``q = 0`` call and put charm are equal: the dividend terms use ``q``, not
    ``r``. The tests check it against a finite difference of delta in time.
    """
    x = _Inputs(s, k, t, sigma, r, q, is_call)
    common = (
        -x.disc_q
        * _pdf(x.d1)
        * (2.0 * (r - q) * x.t_safe - x.d2 * x.sig_sqrt_t)
        / (2.0 * x.t_safe * x.sig_sqrt_t)
    )
    call = common + q * x.disc_q * _cdf(x.d1)
    put = common - q * x.disc_q * _cdf(-x.d1)
    return x.masked(np.where(x.is_call, call, put))

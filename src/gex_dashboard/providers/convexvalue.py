"""ConvexValue adapter: maps the vendor's chain fields onto the canonical columns.

``convexlib`` is imported lazily, so the rest of the package works without
it. It is not on PyPI; install it with
``uv pip install "convexlib @ git+https://github.com/convexvalue/convexlib.git"``.
Credentials come only from ``CONVEX_USER`` and ``CONVEX_PASSWORD``.

Field mapping (one ``get_chain_as_rows`` call per chain):

| Vendor field | Canonical column | Note |
|---|---|---|
| ``bid_price`` / ``ask_price`` / ``price`` | ``bid`` / ``ask`` / ``last`` | |
| ``day_volume`` / ``oi`` | ``volume`` / ``open_interest`` | |
| ``volatility`` | ``iv`` | |
| ``delta`` / ``gamma`` | same names | per $1, as canonical |
| ``vega`` x 0.01 | ``vega`` | vendor: per 1.0 of volatility |
| ``theta`` / 252 | ``theta`` | vendor: per year |
| ``vannaxoi / oi`` x 0.01 | ``vanna`` | vendor: vanna x OI, per 1.0 of volatility |
| ``charmxoi / oi`` / 252 | ``charm`` | vendor: charm x OI, per year |
| ``volm_buy`` / ``volm_sell`` | ``volume_buy`` / ``volume_sell`` | |
| ``value_buy`` / ``value_sell`` | ``premium_buy`` / ``premium_sell`` | |

Checked against the live API (SPY, 2026-10-19): ``gxoi = gamma x oi`` with
per-share gamma, and likewise ``dxoi`` and ``vxoi``. Against Black-Scholes values
computed from the vendor's own IV (155 contracts, 2-6 weeks out), the vendor's
gamma and delta match the canonical units, its vega and vanna are per 1.0 of
volatility (ratio 1.01 and 0.98) and its theta and charm are per year (ratio
0.91 and 0.92, the gap being day-count and rate conventions). The scale
factors below convert them to the canonical per-vol-point and per-trading-day
units.

A field the vendor leaves empty stays NaN (counts become 0), and ``vanna`` and
``charm`` are NaN where open interest is 0, because the vendor only sends them
multiplied by open interest.

**Expirations.** ``get_chain_as_rows`` selects expirations by their position
in the vendor's current expiration list. The adapter caches that list per
symbol and New York market date (from its clock), so it is refetched at least
once a day, and it lists only expirations on or after today. After every
chain request it checks that the rows returned are for the expirations asked
for. If they are not, or if no rows come back (a shorter list leaves the
cached positions pointing past its end), the list has shifted, so it
refetches the list once and retries. Rows that still do not match raise
``ProviderError``; no rows after the refetch give an empty chain. Contracts
of an expiration that closed earlier today are dropped by
``analysis.analyze_chain``, not here.

ConvexValue is a live source: ``spot`` and ``chain`` return the latest data,
and the ``now`` they accept only sets the market date.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Final

import numpy as np
import pandas as pd

from gex_dashboard.clock import TRADING_DAYS_PER_YEAR, Clock, SystemClock, market_date
from gex_dashboard.providers.base import (
    DEFAULT_STRIKE_RANGE,
    INT_COLUMNS,
    Capability,
    ProviderError,
    chain_columns,
    empty_chain,
)

logger = logging.getLogger(__name__)

# Vendor greek units -> canonical units (see the module docstring).
VENDOR_PER_VOL_TO_PER_POINT: Final = 0.01
VENDOR_PER_YEAR_TO_PER_DAY: Final = 1.0 / TRADING_DAYS_PER_YEAR

EPOCH: Final = date(1970, 1, 1)
USER_ENV: Final = "CONVEX_USER"
PASSWORD_ENV: Final = "CONVEX_PASSWORD"
_AUTH_HINTS: Final = ("401", "403", "unauthorized", "login")

# Requested in this order. Each returned row is
# [option_symbol, expiration_epoch_days, strike, "call" or "put", *values].
CHAIN_PARAMS: Final = (
    "bid_price",
    "ask_price",
    "price",
    "day_volume",
    "oi",
    "volatility",
    "delta",
    "gamma",
    "vega",
    "theta",
    "vannaxoi",
    "charmxoi",
    "volm_buy",
    "volm_sell",
    "value_buy",
    "value_sell",
)
_RENAME: Final = {
    "bid_price": "bid",
    "ask_price": "ask",
    "price": "last",
    "day_volume": "volume",
    "oi": "open_interest",
    "volatility": "iv",
    "volm_buy": "volume_buy",
    "volm_sell": "volume_sell",
    "value_buy": "premium_buy",
    "value_sell": "premium_sell",
}


def epoch_days_to_date(epoch_days: int) -> date:
    """The vendor's expiration format: days since 1970-01-01."""
    return EPOCH + timedelta(days=int(epoch_days))


def _load_api_class() -> Any:
    try:
        module = importlib.import_module("convexlib.api")
    except ImportError as err:
        raise ProviderError(
            "The ConvexValue provider needs convexlib: "
            'uv pip install "convexlib @ git+https://github.com/convexvalue/convexlib.git"'
        ) from err
    return module.ConvexApi


def _is_auth_error(err: Exception) -> bool:
    text = str(err).lower()
    return any(hint in text for hint in _AUTH_HINTS)


class ConvexValueProvider:
    """Live chains from ConvexValue (needs ``convexlib`` and credentials)."""

    name = "convexvalue"
    capabilities = Capability.MULTI_EXPIRATION | Capability.SECOND_ORDER | Capability.FLOW

    def __init__(self, user: str, password: str, clock: Clock | None = None) -> None:
        if not user or not password:
            raise ProviderError(f"ConvexValue needs {USER_ENV} and {PASSWORD_ENV} to be set")
        self._api_class = _load_api_class()
        self._user = user
        self._password = password
        self._clock: Clock = clock or SystemClock()
        self._api = self._login()
        # (symbol, market date) -> the vendor's expiration list, in its own order
        self._expiration_cache: dict[tuple[str, date], list[int]] = {}

    def _login(self) -> Any:
        try:
            return self._api_class(self._user, self._password)
        except Exception as err:  # convexlib does not document its exception types
            raise ProviderError(f"ConvexValue login failed: {err}") from err

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Call an API method, logging in again once if the session expired."""
        fn: Callable[..., Any] = getattr(self._api, method)
        try:
            return fn(*args, **kwargs)
        except Exception as err:  # convexlib does not document its exception types
            if not _is_auth_error(err):
                raise ProviderError(f"ConvexValue {method} failed: {err}") from err
            logger.warning("ConvexValue session expired; logging in again")
        self._api = self._login()
        self._expiration_cache.clear()
        try:
            return getattr(self._api, method)(*args, **kwargs)
        except Exception as err:  # convexlib does not document its exception types
            raise ProviderError(f"ConvexValue {method} failed after re-login: {err}") from err

    # ---------------------------------------------------------------- protocol

    def spot(self, symbol: str, now: datetime | None = None) -> float:
        result = self._call("get_und", [symbol], ["price"])
        try:
            return float(result["data"][0][0][1])
        except (KeyError, IndexError, TypeError, ValueError) as err:
            raise ProviderError(f"Unexpected ConvexValue price payload for {symbol}") from err

    def _raw_expirations(self, symbol: str, today: date, refresh: bool = False) -> list[int]:
        """The vendor's expiration list (epoch days) in its own order, cached per day."""
        key = (symbol, today)
        if refresh or key not in self._expiration_cache:
            result = self._call("get_chain", symbol, ["oi"])
            try:
                chain = result["data"][0]["chain"]
                raw = [int(row[0]) for row in chain]
            except (KeyError, IndexError, TypeError, ValueError) as err:
                raise ProviderError(
                    f"Unexpected ConvexValue expirations payload for {symbol}"
                ) from err
            self._expiration_cache = {
                k: v for k, v in self._expiration_cache.items() if k[1] == today
            }
            self._expiration_cache[key] = raw
        return self._expiration_cache[key]

    def _today(self, now: datetime | None) -> date:
        return market_date(now or self._clock.now())

    def expirations(self, symbol: str, now: datetime | None = None) -> list[date]:
        today = self._today(now)
        listed = {epoch_days_to_date(e) for e in self._raw_expirations(symbol, today)}
        return sorted(e for e in listed if e >= today)

    def chain(
        self,
        symbol: str,
        expirations: Sequence[date] | None = None,
        strike_range: float = DEFAULT_STRIKE_RANGE,
        now: datetime | None = None,
    ) -> pd.DataFrame:
        if expirations is None:
            rows = self._call(
                "get_chain_as_rows", symbol, list(CHAIN_PARAMS), exps=None, rng=strike_range
            )
            return self._to_canonical(rows or [])
        today = self._today(now)
        wanted = {e for e in expirations if e >= today}
        for attempt in range(2):
            raw = self._raw_expirations(symbol, today, refresh=attempt > 0)
            positions = [
                i for i, epoch_days in enumerate(raw) if epoch_days_to_date(epoch_days) in wanted
            ]
            if not positions:
                return empty_chain(self.capabilities)
            rows = self._call(
                "get_chain_as_rows", symbol, list(CHAIN_PARAMS), exps=positions, rng=strike_range
            )
            df = self._to_canonical(rows or [])
            if df.empty and attempt == 0:
                # The cached positions may point past the end of a list that has shrunk.
                logger.warning("ConvexValue sent no rows for %s; refetching expirations", symbol)
                continue
            if set(df["expiration"]) <= wanted:
                return df
            logger.warning("ConvexValue expiration list for %s changed; refetching it", symbol)
        raise ProviderError(
            f"ConvexValue returned other expirations than the ones requested for {symbol}"
        )

    def _to_canonical(self, rows: list[list[Any]]) -> pd.DataFrame:
        if not rows:
            return empty_chain(self.capabilities)
        try:
            raw = pd.DataFrame(
                [row[1:4] + list(row[4 : 4 + len(CHAIN_PARAMS)]) for row in rows],
                columns=["expiration_epoch", "strike", "side", *CHAIN_PARAMS],
            )
        except (TypeError, ValueError) as err:
            raise ProviderError("Unexpected ConvexValue chain payload") from err
        # Missing values stay NaN so that an absent greek is not mistaken for a zero one.
        numeric = raw[list(CHAIN_PARAMS)].apply(pd.to_numeric, errors="coerce")
        df = numeric.rename(columns=_RENAME)
        df["expiration"] = [epoch_days_to_date(e) for e in raw["expiration_epoch"]]
        df["strike"] = pd.to_numeric(raw["strike"], errors="coerce")
        df["right"] = np.where(raw["side"].astype(str).str.lower() == "call", "C", "P")
        oi = df["open_interest"]
        safe_oi = oi.where(oi > 0)
        df["vega"] = df["vega"] * VENDOR_PER_VOL_TO_PER_POINT
        df["theta"] = df["theta"] * VENDOR_PER_YEAR_TO_PER_DAY
        df["vanna"] = df["vannaxoi"] / safe_oi * VENDOR_PER_VOL_TO_PER_POINT
        df["charm"] = df["charmxoi"] / safe_oi * VENDOR_PER_YEAR_TO_PER_DAY
        for col in INT_COLUMNS:
            df[col] = df[col].fillna(0.0).round().astype(np.int64)
        out: pd.DataFrame = df.dropna(subset=["strike"])[chain_columns(self.capabilities)]
        return out.reset_index(drop=True)

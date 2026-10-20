"""An in-memory stand-in for ``convexlib``, injected through ``sys.modules``.

``FakeConvexApi`` serves vendor-shaped payloads built from a synthetic chain,
so the adapter can be checked end to end without any network access.
"""

from __future__ import annotations

import sys
import types
from datetime import date
from typing import Any, ClassVar

import pandas as pd
import pytest

EPOCH = date(1970, 1, 1)


class FakeConvexApi:
    """Mimics the four ``ConvexApi`` methods the adapter uses."""

    chains: ClassVar[dict[str, pd.DataFrame]] = {}
    spots: ClassVar[dict[str, float]] = {}
    logins: ClassVar[list[tuple[str, str]]] = []
    calls: ClassVar[list[tuple[str, dict[str, Any]]]] = []
    fail_next: ClassVar[list[Exception]] = []
    fail_login: ClassVar[Exception | None] = None

    def __init__(self, email: str, password: str) -> None:
        if FakeConvexApi.fail_login is not None:
            raise FakeConvexApi.fail_login
        FakeConvexApi.logins.append((email, password))

    @classmethod
    def reset(cls) -> None:
        cls.chains, cls.spots, cls.logins, cls.calls, cls.fail_next = {}, {}, [], [], []
        cls.fail_login = None

    def _maybe_fail(self, method: str, **kwargs: Any) -> None:
        FakeConvexApi.calls.append((method, kwargs))
        if FakeConvexApi.fail_next:
            raise FakeConvexApi.fail_next.pop(0)

    def get_und(self, symbols: list[str], params: list[str]) -> dict[str, Any]:
        self._maybe_fail("get_und")
        return {"data": [[[s, self.spots[s]] for s in symbols]]}

    def _epochs(self, symbol: str) -> list[int]:
        return sorted({(e - EPOCH).days for e in self.chains[symbol]["expiration"]})

    def get_chain(self, symbol: str, params: list[str]) -> dict[str, Any]:
        self._maybe_fail("get_chain")
        return {"data": [{"chain": [[epoch, []] for epoch in self._epochs(symbol)]}]}

    def get_chain_as_rows(
        self,
        symbol: str,
        params: list[str],
        exps: list[int] | None = None,
        rng: float | None = None,
    ) -> list[list[Any]]:
        self._maybe_fail("get_chain_as_rows", exps=exps, rng=rng)
        df = self.chains[symbol]
        epochs = self._epochs(symbol)
        if exps is not None:
            wanted = {epochs[i] for i in exps if i < len(epochs)}  # past the end: no rows
            df = df[[(e - EPOCH).days in wanted for e in df["expiration"]]]
        if rng is not None:
            spot = self.spots[symbol]
            df = df[(df["strike"] / spot - 1).abs() <= rng]
        vendor = {
            "bid_price": df["bid"],
            "ask_price": df["ask"],
            "price": df["last"],
            "day_volume": df["volume"],
            "oi": df["open_interest"],
            "volatility": df["iv"],
            "delta": df["delta"],
            "gamma": df["gamma"],
            # The vendor's units: vega and vanna per 1.0 of volatility, theta and charm per year.
            "vega": df["vega"] * 100,
            "theta": df["theta"] * 252,
            "vannaxoi": df["vanna"] * 100 * df["open_interest"],
            "charmxoi": df["charm"] * 252 * df["open_interest"],
            "volm_buy": df["volume_buy"],
            "volm_sell": df["volume_sell"],
            "value_buy": df["premium_buy"],
            "value_sell": df["premium_sell"],
        }
        rows = []
        for i, (_, row) in enumerate(df.iterrows()):
            side = "call" if row["right"] == "C" else "put"
            epoch = (row["expiration"] - EPOCH).days
            values = [vendor[p].iloc[i] for p in params]
            rows.append(
                [f"{symbol}{epoch}{side[0]}{row['strike']}", epoch, row["strike"], side, *values]
            )
        return rows


def install(monkeypatch: pytest.MonkeyPatch) -> type[FakeConvexApi]:
    """Put the fake ``convexlib`` and ``convexlib.api`` into ``sys.modules``."""
    FakeConvexApi.reset()
    package = types.ModuleType("convexlib")
    api = types.ModuleType("convexlib.api")
    api.ConvexApi = FakeConvexApi  # type: ignore[attr-defined]
    package.api = api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "convexlib", package)
    monkeypatch.setitem(sys.modules, "convexlib.api", api)
    return FakeConvexApi

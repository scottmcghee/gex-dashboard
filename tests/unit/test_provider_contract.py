"""The contract every ``ChainProvider`` must meet, run against each registered provider.

The ConvexValue provider runs against an in-memory fake ``convexlib``; no test
makes a network call.
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from gex_dashboard.analysis import build_market_view
from gex_dashboard.clock import market_date
from gex_dashboard.providers import PROVIDERS
from gex_dashboard.providers.base import (
    FLOW_COLUMNS,
    INT_COLUMNS,
    SECOND_ORDER_COLUMNS,
    Capability,
    ChainProvider,
    chain_columns,
)
from gex_dashboard.providers.convexvalue import ConvexValueProvider
from tests.unit import fake_convexlib

SYMBOL = "SPY"


@pytest.fixture(params=sorted(PROVIDERS))
def provider(request, monkeypatch, synthetic, clock) -> ChainProvider:
    if request.param == "synthetic":
        return synthetic
    fake = fake_convexlib.install(monkeypatch)
    fake.spots[SYMBOL] = synthetic.spot(SYMBOL)
    fake.chains[SYMBOL] = synthetic.chain(SYMBOL, strike_range=0.12)
    return ConvexValueProvider("user@example.com", "pw", clock)


def test_registry_covers_every_provider():
    assert set(PROVIDERS) == {"synthetic", "convexvalue"}


def test_satisfies_protocol(provider):
    assert isinstance(provider, ChainProvider)
    assert isinstance(provider.name, str)
    assert isinstance(provider.capabilities, Capability)


def test_spot_is_positive_float(provider):
    spot = provider.spot(SYMBOL)
    assert isinstance(spot, float)
    assert spot > 0


def test_expirations_sorted_unique_dates(provider):
    exps = provider.expirations(SYMBOL)
    assert exps
    assert all(isinstance(e, date) for e in exps)
    assert exps == sorted(set(exps))


def test_chain_has_canonical_columns_and_types(provider):
    chain = provider.chain(SYMBOL)
    assert not chain.empty
    assert set(chain_columns(provider.capabilities)).issubset(chain.columns)
    assert chain["right"].isin(["C", "P"]).all()
    assert all(isinstance(e, date) for e in chain["expiration"])
    for col in INT_COLUMNS:
        if col in chain:
            assert pd.api.types.is_integer_dtype(chain[col]), col
            assert (chain[col] >= 0).all(), col
    # Greeks may be NaN where the source has none; vanna and charm are, for instance,
    # where open interest is 0 in a vendor that sends them multiplied by OI.
    unknown_at_zero_oi = [c for c in SECOND_ORDER_COLUMNS if c in chain]
    numeric = chain.drop(columns=["expiration", "right", *unknown_at_zero_oi])
    assert np.isfinite(numeric.to_numpy(dtype=float)).all()
    held = chain[chain["open_interest"] > 0]
    assert np.isfinite(held[unknown_at_zero_oi].to_numpy(dtype=float)).all()
    assert (chain["iv"] > 0).all()
    calls, puts = chain[chain["right"] == "C"], chain[chain["right"] == "P"]
    assert (calls["delta"] >= 0).all()
    assert (puts["delta"] <= 0).all()
    assert (chain["gamma"] >= 0).all()


def test_chain_respects_strike_range(provider):
    spot = provider.spot(SYMBOL)
    narrow = provider.chain(SYMBOL, strike_range=0.02)
    assert not narrow.empty
    assert ((narrow["strike"] / spot - 1).abs() <= 0.02 + 1e-9).all()
    assert len(narrow) < len(provider.chain(SYMBOL, strike_range=0.10))


def test_chain_filters_expirations(provider):
    exps = provider.expirations(SYMBOL)
    one = provider.chain(SYMBOL, [exps[1]])
    assert set(one["expiration"]) == {exps[1]}


def test_unknown_expiration_gives_empty_canonical_chain(provider):
    chain = provider.chain(SYMBOL, [date(1999, 1, 1)])
    assert chain.empty
    assert set(chain_columns(provider.capabilities)).issubset(chain.columns)


def test_multi_expiration_returns_all(provider):
    if Capability.MULTI_EXPIRATION not in provider.capabilities:
        pytest.skip("single-expiration provider")
    chain = provider.chain(SYMBOL)
    assert set(chain["expiration"]) == set(provider.expirations(SYMBOL))


def test_flow_columns_non_negative(provider):
    if Capability.FLOW not in provider.capabilities:
        pytest.skip("no flow")
    chain = provider.chain(SYMBOL)
    for col in FLOW_COLUMNS:
        assert (chain[col] >= 0).all(), col


def test_accepts_an_explicit_now(provider, clock):
    later = datetime.fromisoformat("2026-10-21T14:00:00-04:00")
    exps = provider.expirations(SYMBOL, later)
    assert exps
    assert min(exps) >= market_date(later)
    assert provider.spot(SYMBOL, later) > 0
    chain = provider.chain(SYMBOL, exps[:1], now=later)
    assert set(chain["expiration"]) == {exps[0]}


def test_feeds_the_core(provider, clock):
    view = build_market_view(provider, SYMBOL, clock)
    assert view.has_data
    assert view.gamma_flip is not None
    assert (view.net_gex < 0) == (view.spot < view.gamma_flip)
    assert view.walls.call
    assert view.walls.put
    assert view.expected_move is not None
    if Capability.SECOND_ORDER in provider.capabilities:
        assert view.net_vanna is not None
        assert view.vanna_flip is not None

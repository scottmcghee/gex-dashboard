"""ConvexValue adapter details: field mapping, credentials, errors and re-login."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from gex_dashboard import exposures as ex
from gex_dashboard.providers.base import ProviderError
from gex_dashboard.providers.convexvalue import (
    CHAIN_PARAMS,
    ConvexValueProvider,
    epoch_days_to_date,
)
from tests.unit import fake_convexlib
from tests.unit.conftest import SESSION_TIME, make_chain

EXP = date(2026, 10, 23)
NEXT = date(2026, 11, 20)


@pytest.fixture
def fake(monkeypatch):
    fake = fake_convexlib.install(monkeypatch)
    fake.spots["SPY"] = 100.0
    fake.chains["SPY"] = make_chain(
        [
            {
                "strike": 100.0,
                "right": "C",
                "open_interest": 200,
                "vanna": 0.01,
                "charm": -0.002,
                "volume": 30,
                "volume_buy": 20,
                "volume_sell": 10,
                "premium_buy": 500.0,
                "premium_sell": 250.0,
                "gamma": 0.05,
                "delta": 0.5,
            },
            {"strike": 99.0, "right": "P", "open_interest": 0, "vanna": 0.0, "charm": 0.0},
            {"strike": 101.0, "right": "C", "open_interest": 5, "expiration": NEXT},
        ]
    )
    return fake


class MovableClock:
    """A clock the test can move between calls."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


@pytest.fixture
def clock():
    return MovableClock(SESSION_TIME)


@pytest.fixture
def provider(fake, clock):
    return ConvexValueProvider("user@example.com", "pw", clock)


def test_epoch_days():
    assert epoch_days_to_date(0) == date(1970, 1, 1)
    assert epoch_days_to_date((EXP - date(1970, 1, 1)).days) == EXP


class TestMapping:
    def test_round_trip(self, provider):
        chain = provider.chain("SPY", [EXP])
        row = chain[chain["strike"] == 100.0].iloc[0]
        assert row["right"] == "C"
        assert row["expiration"] == EXP
        assert row["open_interest"] == 200
        assert row["vanna"] == pytest.approx(0.01)  # vannaxoi / oi
        assert row["charm"] == pytest.approx(-0.002)
        assert row["volume_buy"] == 20
        assert row["premium_sell"] == 250.0
        assert row["gamma"] == 0.05

    def test_vendor_units_converted(self, provider, fake, monkeypatch):
        """Vendor vega/vanna are per 1.0 vol and theta/charm per year (checked on the live API)."""
        vendor = dict.fromkeys(CHAIN_PARAMS, 0.0)
        vendor.update(oi=200, vega=0.5, theta=-25.2, vannaxoi=100.0, charmxoi=-50.4)
        rows = [["SPY", 20384, 100.0, "call", *(vendor[p] for p in CHAIN_PARAMS)]]
        monkeypatch.setattr(fake, "get_chain_as_rows", lambda self, *a, **k: rows)
        row = provider.chain("SPY").iloc[0]
        assert row["vega"] == pytest.approx(0.005)  # per vol point
        assert row["theta"] == pytest.approx(-0.1)  # per trading day
        assert row["vanna"] == pytest.approx(0.005)  # 100 / 200 per 1.0 vol -> per point
        assert row["charm"] == pytest.approx(-0.001)  # -50.4 / 200 per year -> per day

    def test_zero_oi_leaves_second_order_unknown(self, provider):
        """The vendor sends vanna and charm times OI, so at OI 0 they are unknown, not 0."""
        put = provider.chain("SPY", [EXP]).set_index("strike").loc[99.0]
        assert put["right"] == "P"
        assert np.isnan(put["vanna"])
        assert np.isnan(put["charm"])

    def test_requests_expiration_indices_and_range(self, provider, fake):
        provider.chain("SPY", [NEXT], strike_range=0.05)
        method, kwargs = fake.calls[-1]
        assert method == "get_chain_as_rows"
        assert kwargs == {"exps": [1], "rng": 0.05}

    def test_all_expirations(self, provider, fake):
        provider.chain("SPY")
        assert fake.calls[-1][1]["exps"] is None

    def test_missing_values_stay_missing(self, provider, fake, monkeypatch):
        rows = [["SPY", 20384, 100.0, "call", *([None] * len(CHAIN_PARAMS))]]
        monkeypatch.setattr(fake, "get_chain_as_rows", lambda self, *a, **k: rows)
        row = provider.chain("SPY").iloc[0]
        assert row["open_interest"] == 0  # counts are 0 when missing
        assert row["volume_buy"] == 0
        for col in ("gamma", "delta", "vega", "theta", "iv", "vanna", "charm", "bid"):
            assert np.isnan(row[col]), col

    def test_missing_greek_is_not_a_zero_exposure(self, provider, fake, monkeypatch):
        vendor = dict.fromkeys(CHAIN_PARAMS, 1.0)
        vendor.update(oi=100, gamma=None, vannaxoi=None)
        rows = [["SPY", 20384, 100.0, "call", *(vendor[p] for p in CHAIN_PARAMS)]]
        monkeypatch.setattr(fake, "get_chain_as_rows", lambda self, *a, **k: rows)
        exp = ex.contract_exposures(provider.chain("SPY"), 100.0)
        assert not ex.has_metric(exp, "gamma")
        assert not ex.has_metric(exp, "vanna")
        assert ex.has_metric(exp, "charm")

    def test_empty_rows(self, provider, fake, monkeypatch):
        monkeypatch.setattr(fake, "get_chain_as_rows", lambda self, *a, **k: [])
        assert provider.chain("SPY").empty

    def test_malformed_rows(self, provider, fake, monkeypatch):
        monkeypatch.setattr(fake, "get_chain_as_rows", lambda self, *a, **k: [["x"]])
        with pytest.raises(ProviderError, match="chain payload"):
            provider.chain("SPY")


def expiration_requests(fake) -> int:
    return sum(1 for method, _ in fake.calls if method == "get_chain")


class TestExpirationCache:
    """The vendor selects expirations by position, so a stale list must never be used."""

    def test_cached_within_a_day(self, provider, fake):
        provider.expirations("SPY")
        provider.expirations("SPY")
        assert expiration_requests(fake) == 1

    def test_refetched_on_a_new_market_date(self, provider, fake, clock):
        provider.expirations("SPY")
        clock.at = SESSION_TIME + timedelta(days=1)
        provider.expirations("SPY")
        assert expiration_requests(fake) == 2
        provider.expirations("SPY", now=SESSION_TIME + timedelta(days=2))
        assert expiration_requests(fake) == 3

    def test_drops_expirations_before_today(self, provider, fake):
        past = date(2026, 10, 16)
        fake.chains["SPY"] = pd.concat(
            [fake.chains["SPY"], make_chain([{"strike": 100.0, "right": "C", "expiration": past}])]
        )
        assert provider.expirations("SPY") == [EXP, NEXT]
        assert provider.chain("SPY", [past]).empty
        # Positions still count the vendor's own list, past date included.
        provider.chain("SPY", [NEXT])
        assert fake.calls[-1][1]["exps"] == [2]

    def test_shifted_list_is_refetched_and_retried(self, provider, fake):
        provider.expirations("SPY")  # caches [EXP, NEXT]
        earlier = date(2026, 10, 21)
        fake.chains["SPY"] = pd.concat(
            [
                fake.chains["SPY"],
                make_chain([{"strike": 100.0, "right": "C", "expiration": earlier}]),
            ]
        )  # the vendor's list is now [earlier, EXP, NEXT]: position 1 is EXP, not NEXT
        chain = provider.chain("SPY", [NEXT])
        assert set(chain["expiration"]) == {NEXT}
        assert expiration_requests(fake) == 2
        assert [k["exps"] for m, k in fake.calls if m == "get_chain_as_rows"] == [[1], [2]]

    def test_no_rows_after_the_list_shrinks_refetches(self, provider, fake):
        provider.expirations("SPY")  # caches [EXP, NEXT]: NEXT is position 1
        chain = fake.chains["SPY"]
        fake.chains["SPY"] = chain[chain["expiration"] != EXP]  # the list is now [NEXT]
        result = provider.chain("SPY", [NEXT])
        assert set(result["expiration"]) == {NEXT}
        assert expiration_requests(fake) == 2
        assert [k["exps"] for m, k in fake.calls if m == "get_chain_as_rows"] == [[1], [0]]

    def test_no_rows_after_the_refetch_is_an_empty_chain(self, provider, fake, monkeypatch):
        monkeypatch.setattr(fake, "get_chain_as_rows", lambda self, *a, **k: [])
        assert provider.chain("SPY", [NEXT]).empty
        assert expiration_requests(fake) == 2

    def test_still_wrong_after_refetch_raises(self, provider, fake, monkeypatch):
        epoch = (EXP - date(1970, 1, 1)).days
        rows = [["SPY", epoch, 100.0, "call", *([1.0] * len(CHAIN_PARAMS))]]
        monkeypatch.setattr(fake, "get_chain_as_rows", lambda self, *a, **k: rows)
        with pytest.raises(ProviderError, match="other expirations"):
            provider.chain("SPY", [NEXT])
        assert expiration_requests(fake) == 2

    def test_now_sets_the_market_date(self, provider):
        later = datetime.fromisoformat("2026-10-26T10:00:00-04:00")
        assert provider.expirations("SPY", now=later) == [NEXT]
        assert provider.chain("SPY", [EXP], now=later).empty


class TestStartup:
    def test_missing_credentials(self, fake):
        with pytest.raises(ProviderError, match="CONVEX_USER"):
            ConvexValueProvider("", "")

    def test_missing_package(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "convexlib", None)
        monkeypatch.setitem(sys.modules, "convexlib.api", None)
        with pytest.raises(ProviderError, match="needs convexlib"):
            ConvexValueProvider("u", "p")

    def test_login_failure(self, fake):
        fake.fail_login = ConnectionError("refused")
        with pytest.raises(ProviderError, match="login failed"):
            ConvexValueProvider("u", "p")

    def test_logs_in_with_given_credentials(self, provider, fake):
        assert fake.logins == [("user@example.com", "pw")]


class TestErrors:
    def test_relogin_once_on_auth_error(self, provider, fake):
        fake.fail_next.append(RuntimeError("HTTP 401 Unauthorized"))
        assert provider.spot("SPY") == 100.0
        assert len(fake.logins) == 2

    def test_second_auth_failure_raises(self, provider, fake):
        fake.fail_next += [RuntimeError("401"), RuntimeError("401")]
        with pytest.raises(ProviderError, match="after re-login"):
            provider.spot("SPY")

    def test_other_errors_raise_without_relogin(self, provider, fake):
        fake.fail_next.append(TimeoutError("slow"))
        with pytest.raises(ProviderError, match="get_und failed"):
            provider.spot("SPY")
        assert len(fake.logins) == 1

    def test_bad_price_payload(self, provider, fake, monkeypatch):
        monkeypatch.setattr(fake, "get_und", lambda self, *a: {"data": []})
        with pytest.raises(ProviderError, match="price payload"):
            provider.spot("SPY")

    def test_bad_expirations_payload(self, provider, fake, monkeypatch):
        monkeypatch.setattr(fake, "get_chain", lambda self, *a: {"data": [{}]})
        with pytest.raises(ProviderError, match="expirations payload"):
            provider.expirations("SPY")

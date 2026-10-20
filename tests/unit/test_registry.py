"""Provider lookup by name."""

from __future__ import annotations

import pytest

from gex_dashboard.config import Settings
from gex_dashboard.providers import available_providers, create_provider
from gex_dashboard.providers.base import ProviderError, empty_chain
from gex_dashboard.providers.convexvalue import ConvexValueProvider
from gex_dashboard.providers.synthetic import SyntheticProvider
from tests.unit import fake_convexlib


def test_names():
    assert available_providers() == ["convexvalue", "synthetic"]


def test_default_is_synthetic(clock, monkeypatch):
    monkeypatch.setenv("GEX_SYNTHETIC_SEED", "7")
    provider = create_provider(Settings(), clock)
    assert isinstance(provider, SyntheticProvider)
    assert provider.seed == 7


def test_unknown_name(clock):
    with pytest.raises(ProviderError, match="Unknown provider 'nope'"):
        create_provider(Settings(), clock, name="nope")


def test_convexvalue_reads_credentials_from_env(clock, monkeypatch):
    fake = fake_convexlib.install(monkeypatch)
    monkeypatch.setenv("CONVEX_USER", "env-user")
    monkeypatch.setenv("CONVEX_PASSWORD", "env-pass")
    provider = create_provider(Settings(), clock, name=" ConvexValue ")
    assert isinstance(provider, ConvexValueProvider)
    assert fake.logins == [("env-user", "env-pass")]


def test_convexvalue_without_credentials(clock, monkeypatch):
    fake_convexlib.install(monkeypatch)
    with pytest.raises(ProviderError):
        create_provider(Settings(), clock, name="convexvalue")


def test_empty_chain_has_no_rows():
    assert empty_chain().empty

"""Page-test settings: a fixed clock, a clean environment and empty Streamlit caches."""

from __future__ import annotations

import pytest
import streamlit as st

from gex_dashboard.config import get_settings
from tests.unit.conftest import _SETTINGS_ENV

# Monday 2026-10-19, mid-session (New York time).
IN_SESSION = "2026-10-19T11:00:00-04:00"


@pytest.fixture(autouse=True)
def app_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Synthetic provider on a fixed in-session clock; tests override with ``setenv``."""
    for name in _SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEX_CLOCK", "fixed")
    monkeypatch.setenv("GEX_CLOCK_START", IN_SESSION)
    get_settings.cache_clear()
    st.cache_data.clear()
    st.cache_resource.clear()
    yield monkeypatch
    get_settings.cache_clear()
    st.cache_data.clear()
    st.cache_resource.clear()

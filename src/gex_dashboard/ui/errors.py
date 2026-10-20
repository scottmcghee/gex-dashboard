"""The app's provider error boundary: a message instead of a traceback."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import streamlit as st

from gex_dashboard.providers.base import ProviderError


@contextmanager
def provider_errors(provider_name: str) -> Iterator[None]:
    """Show what failed when the data source cannot start or return data, then stop the run."""
    try:
        yield
    except ProviderError as err:
        st.error(
            f"The data source ({provider_name}) could not provide data: {err}. "
            "Check GEX_PROVIDER and the provider's settings, then use **Refresh data**."
        )
        st.stop()

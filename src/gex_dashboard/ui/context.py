"""What every tab receives, and the message a tab shows when a feature is unavailable."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

import streamlit as st

from gex_dashboard.analysis import MarketView
from gex_dashboard.providers.base import Capability

BadgeColor = Literal["red", "orange", "yellow", "blue", "green", "violet", "gray"]

CAPABILITY_NAMES: Final = {
    Capability.MULTI_EXPIRATION: "a chain across several expirations",
    Capability.SECOND_ORDER: "vanna and charm for each contract",
    Capability.FLOW: "customer buy/sell volume for each contract",
}


@dataclass(frozen=True)
class TabContext:
    """The one market snapshot of this run, shared by every tab."""

    view: MarketView
    capabilities: Capability
    provider_label: str
    now: datetime


def require(ctx: TabContext, capability: Capability, feature: str) -> bool:
    """True if the provider has ``capability``; otherwise explain why ``feature`` is missing."""
    if capability in ctx.capabilities:
        return True
    st.info(
        f"{feature} needs {CAPABILITY_NAMES[capability]}, which the current data source "
        f"({ctx.provider_label}) does not supply."
    )
    return False


def require_several_expirations(ctx: TabContext, feature: str) -> bool:
    """True if the view spans more than one expiration; otherwise say how to get that."""
    if not require(ctx, Capability.MULTI_EXPIRATION, feature):
        return False
    if len(ctx.view.expirations) > 1:
        return True
    st.info(f"{feature} compares expirations: choose **All expirations** in the sidebar.")
    return False

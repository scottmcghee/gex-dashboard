"""Chain providers, looked up by name (``GEX_PROVIDER``)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from gex_dashboard.clock import Clock
from gex_dashboard.config import Settings
from gex_dashboard.providers.base import Capability, ChainProvider, ProviderError
from gex_dashboard.providers.synthetic import SyntheticProvider

ProviderFactory = Callable[[Settings, Clock], ChainProvider]


def _synthetic(settings: Settings, clock: Clock) -> ChainProvider:
    return SyntheticProvider(
        clock, seed=settings.synthetic_seed, risk_free_rate=settings.risk_free_rate
    )


def _convexvalue(settings: Settings, clock: Clock) -> ChainProvider:
    from gex_dashboard.providers.convexvalue import ConvexValueProvider

    return ConvexValueProvider(settings.convex_user, settings.convex_password, clock)


PROVIDERS: Final[dict[str, ProviderFactory]] = {
    "synthetic": _synthetic,
    "convexvalue": _convexvalue,
}


def available_providers() -> list[str]:
    """Registered provider names."""
    return sorted(PROVIDERS)


def create_provider(settings: Settings, clock: Clock, name: str | None = None) -> ChainProvider:
    """Build the provider called ``name`` (default ``settings.provider``).

    Raises ``ProviderError`` for an unknown name or a provider that cannot start.
    """
    key = (name or settings.provider).strip().lower()
    try:
        factory = PROVIDERS[key]
    except KeyError:
        raise ProviderError(
            f"Unknown provider {key!r}; choose one of {', '.join(available_providers())}"
        ) from None
    return factory(settings, clock)


__all__ = [
    "PROVIDERS",
    "Capability",
    "ChainProvider",
    "ProviderError",
    "available_providers",
    "create_provider",
]

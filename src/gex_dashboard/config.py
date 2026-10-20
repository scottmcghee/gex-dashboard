"""Runtime settings, read only from environment variables.

Nothing secret has a default: the ConvexValue credentials are empty unless
``CONVEX_USER`` and ``CONVEX_PASSWORD`` are set, and the password is kept out
of ``repr``.

``GEX_CLOCK_START`` is an ISO 8601 datetime such as ``2026-10-19T11:00`` or
``2026-10-19T11:00:00-04:00``; without an offset it is read as New York time.
A replay clock started outside a session rolls forward to the next 09:30 open
(see ``gex_dashboard.clock.ReplayClock``).

| Variable | Default | Meaning |
|---|---|---|
| ``GEX_PROVIDER`` | ``synthetic`` | Chain provider name (see ``gex_dashboard.providers``) |
| ``GEX_SYMBOLS`` | ``SPY,SPX,QQQ`` | Comma-separated symbols offered in the UI |
| ``GEX_CLOCK`` | ``replay`` for synthetic, else ``system`` | ``system``, ``fixed`` or ``replay`` |
| ``GEX_CLOCK_START`` | 09:30 New York, latest weekday | ISO datetime for ``fixed``/``replay`` |
| ``GEX_CLOCK_SPEED`` | ``1`` | Replay speed multiplier |
| ``GEX_STRIKE_RANGE`` | ``0.10`` | Strikes kept, as a fraction of spot either side (0.02-0.25) |
| ``GEX_RISK_FREE_RATE`` | ``0.04`` | Rate for the synthetic chain and gamma repricing |
| ``GEX_SYNTHETIC_SEED`` | ``0`` | Seed for the synthetic market |
| ``CONVEX_USER`` / ``CONVEX_PASSWORD`` | empty | ConvexValue login |
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal, cast

from gex_dashboard.bsm import DEFAULT_RISK_FREE_RATE
from gex_dashboard.clock import (
    Clock,
    FixedClock,
    ReplayClock,
    SystemClock,
    default_replay_start,
    market_date,
)

ClockMode = Literal["system", "fixed", "replay"]
CLOCK_MODES: Final = ("system", "fixed", "replay")
DEFAULT_PROVIDER: Final = "synthetic"
DEFAULT_SYMBOLS: Final = ("SPY", "SPX", "QQQ")
# The sidebar slider's bounds, so the configured default is always a value it can show.
STRIKE_RANGE_MIN: Final = 0.02
STRIKE_RANGE_MAX: Final = 0.25


def _env(name: str, default: str) -> str:
    """``name`` from the environment; unset or empty means ``default``."""
    return os.getenv(name) or default


def _symbols() -> tuple[str, ...]:
    raw = _env("GEX_SYMBOLS", ",".join(DEFAULT_SYMBOLS))
    symbols = tuple(s.strip().upper() for s in raw.split(",") if s.strip())
    return symbols or DEFAULT_SYMBOLS


def _provider() -> str:
    return _env("GEX_PROVIDER", DEFAULT_PROVIDER).strip().lower()


def _clock_mode() -> ClockMode:
    default = "replay" if _provider() == DEFAULT_PROVIDER else "system"
    mode = _env("GEX_CLOCK", default).strip().lower()
    if mode not in CLOCK_MODES:
        raise ValueError(f"GEX_CLOCK must be one of {', '.join(CLOCK_MODES)}, got {mode!r}")
    return cast(ClockMode, mode)


def _clock_start() -> datetime | None:
    raw = os.getenv("GEX_CLOCK_START", "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError(
            f"GEX_CLOCK_START must be an ISO datetime such as 2026-10-19T11:00, got {raw!r}"
        ) from None


def _positive_float(name: str, default: str) -> float:
    value = float(_env(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def _strike_range() -> float:
    value = float(_env("GEX_STRIKE_RANGE", "0.10"))
    if not STRIKE_RANGE_MIN <= value <= STRIKE_RANGE_MAX:
        raise ValueError(
            f"GEX_STRIKE_RANGE must be between {STRIKE_RANGE_MIN} and {STRIKE_RANGE_MAX} "
            f"(a fraction of spot), got {value}"
        )
    return value


@dataclass(frozen=True)
class Settings:
    provider: str = field(default_factory=_provider)
    symbols: tuple[str, ...] = field(default_factory=_symbols)
    clock_mode: ClockMode = field(default_factory=_clock_mode)
    clock_start: datetime | None = field(default_factory=_clock_start)
    clock_speed: float = field(default_factory=lambda: _positive_float("GEX_CLOCK_SPEED", "1"))
    strike_range: float = field(default_factory=_strike_range)
    risk_free_rate: float = field(
        default_factory=lambda: float(_env("GEX_RISK_FREE_RATE", str(DEFAULT_RISK_FREE_RATE)))
    )
    synthetic_seed: int = field(default_factory=lambda: int(_env("GEX_SYNTHETIC_SEED", "0")))
    convex_user: str = field(default_factory=lambda: os.getenv("CONVEX_USER", ""))
    convex_password: str = field(
        default_factory=lambda: os.getenv("CONVEX_PASSWORD", ""), repr=False
    )


@functools.cache
def get_settings() -> Settings:
    """Settings read once per process; tests call ``get_settings.cache_clear()``."""
    return Settings()


def make_clock(settings: Settings) -> Clock:
    """The clock ``settings.clock_mode`` asks for."""
    if settings.clock_mode == "system":
        return SystemClock()
    start = settings.clock_start or default_replay_start(market_date(SystemClock().now()))
    if settings.clock_mode == "fixed":
        return FixedClock(start)
    return ReplayClock(start, speed=settings.clock_speed)

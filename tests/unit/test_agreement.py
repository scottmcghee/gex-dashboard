"""The per-symbol regimes behind the index-agreement badge."""

from __future__ import annotations

from datetime import date

from gex_dashboard.analysis import MarketView, build_market_view
from gex_dashboard.providers.base import ProviderError
from gex_dashboard.ui.agreement import scope_regimes

SYMBOLS = ("SPY", "SPX", "QQQ")


class Loads:
    """A ``load`` callable that builds real views and records what it was asked for."""

    def __init__(self, provider, clock) -> None:  # type: ignore[no-untyped-def]
        self.provider, self.clock = provider, clock
        self.calls: list[tuple[str, tuple[date, ...] | None]] = []

    def __call__(self, symbol: str, expirations: tuple[date, ...] | None) -> MarketView:
        self.calls.append((symbol, expirations))
        chosen = list(expirations) if expirations is not None else None
        return build_market_view(self.provider, symbol, self.clock, chosen)


def test_all_expirations_reuses_the_main_view(synthetic, clock) -> None:  # type: ignore[no-untyped-def]
    view = build_market_view(synthetic, "SPY", clock)
    load = Loads(synthetic, clock)
    out = scope_regimes(SYMBOLS, view, True, synthetic.expirations, load)
    assert load.calls == [("SPX", None), ("QQQ", None)]  # no second SPY view
    assert out.regimes["SPY"] == view.regime
    assert out.fronts == {}


def test_front_scope_reuses_the_main_view_when_it_is_the_front(synthetic, clock) -> None:  # type: ignore[no-untyped-def]
    front = synthetic.expirations("SPY")[0]
    view = build_market_view(synthetic, "SPY", clock, [front])
    load = Loads(synthetic, clock)
    out = scope_regimes(SYMBOLS, view, False, synthetic.expirations, load)
    assert [sym for sym, _ in load.calls] == ["SPX", "QQQ"]
    assert out.fronts == dict.fromkeys(SYMBOLS, front)


def test_front_scope_loads_the_front_when_another_expiration_is_shown(synthetic, clock) -> None:  # type: ignore[no-untyped-def]
    exps = synthetic.expirations("SPY")
    view = build_market_view(synthetic, "SPY", clock, [exps[2]])
    load = Loads(synthetic, clock)
    scope_regimes(SYMBOLS, view, False, synthetic.expirations, load)
    assert ("SPY", (exps[0],)) in load.calls


def test_failures_and_missing_expirations_are_unavailable(synthetic, clock) -> None:  # type: ignore[no-untyped-def]
    view = build_market_view(synthetic, "SPY", clock, synthetic.expirations("SPY")[:1])

    def expirations(symbol: str) -> list[date]:
        if symbol == "QQQ":
            raise ProviderError("timed out")
        return [] if symbol == "SPX" else synthetic.expirations(symbol)

    out = scope_regimes(SYMBOLS, view, False, expirations, Loads(synthetic, clock))
    assert out.regimes["SPX"] is None
    assert out.regimes["QQQ"] is None
    assert out.regimes["SPY"] == view.regime

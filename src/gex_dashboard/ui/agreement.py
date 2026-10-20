"""The views behind the index-agreement badge: one gamma regime per configured symbol."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date

from gex_dashboard.analysis import MarketView
from gex_dashboard.profiles import GammaRegime
from gex_dashboard.providers.base import ProviderError

ViewLoader = Callable[[str, tuple[date, ...] | None], MarketView]


@dataclass(frozen=True)
class ScopeRegimes:
    """Each symbol's regime (None when unavailable) and, for a front scope, its front date."""

    regimes: dict[str, GammaRegime | None]
    fronts: dict[str, date] = field(default_factory=dict)


def scope_regimes(
    symbols: Sequence[str],
    view: MarketView,
    all_expirations: bool,
    expirations: Callable[[str], list[date]],
    load: ViewLoader,
) -> ScopeRegimes:
    """Every symbol's gamma regime in the main view's scope, reusing the main view.

    With ``all_expirations`` each symbol's whole chain is used; otherwise its
    front (first open) expiration from ``expirations``. The main view stands
    in for its own symbol whenever it covers that scope, so ``load`` (a view
    for a symbol and expirations, None meaning all) runs only for the others.
    A symbol whose provider call fails is unavailable rather than an error.
    """
    regimes: dict[str, GammaRegime | None] = {}
    fronts: dict[str, date] = {}
    for sym in symbols:
        try:
            scope: tuple[date, ...] | None = None
            if not all_expirations:
                exps = expirations(sym)
                if not exps:
                    regimes[sym] = None
                    continue
                fronts[sym] = exps[0]
                scope = (exps[0],)
            reuse = sym == view.symbol and (all_expirations or view.expirations == scope)
            regimes[sym] = (view if reuse else load(sym, scope)).regime
        except ProviderError:
            regimes[sym] = None
    return ScopeRegimes(regimes, fronts)

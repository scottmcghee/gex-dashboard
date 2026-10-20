"""The whole app, run with Streamlit's AppTest against the synthetic and fake providers.

The app reads its provider from ``GEX_PROVIDER`` through the registry in
``gex_dashboard.providers``, so tests register extra providers there: one
without optional capabilities and two that fail.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import ClassVar

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from gex_dashboard import patterns
from gex_dashboard.clock import FixedClock
from gex_dashboard.providers import PROVIDERS
from gex_dashboard.providers.base import (
    REQUIRED_COLUMNS,
    Capability,
    ChainProvider,
    ProviderError,
    empty_chain,
)
from gex_dashboard.providers.synthetic import SyntheticProvider
from gex_dashboard.tabs.ladder import EXPLAINER, TABLE_COLUMNS
from tests.pages.conftest import IN_SESSION
from tests.unit import fake_convexlib

APP = str(Path(__file__).parents[2] / "src" / "gex_dashboard" / "app.py")
TAB_TITLES = [
    "GEX Profile",
    "Volume Spikes",
    "Exposure Heatmap",
    "Live Levels",
    "Customer Flow by Expiration",
    "Simulation",
    "GEX Ladder",
]
TIMEOUT = 60


def run(at: AppTest | None = None) -> AppTest:
    """Run (or rerun) the app and fail on any exception or error it showed."""
    at = at or AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    return at


def by_label(widgets: Sequence, label: str):  # type: ignore[no-untyped-def]
    return next(w for w in widgets if w.label == label)


def infos(at: AppTest) -> str:
    return "\n".join(i.value for i in at.info)


def metric(at: AppTest, label: str) -> str:
    return str(by_label(at.metric, label).value)


def metric_help(at: AppTest, label: str) -> str:
    return str(by_label(at.metric, label).proto.help)


def badge(at: AppTest, text: str):  # type: ignore[no-untyped-def]
    """The badge whose label contains ``text`` (badges render as markdown)."""
    return next(m for m in at.markdown if "badge[" in m.value and text in m.value)


# ----------------------------------------------------------------- synthetic


@pytest.mark.parametrize(
    ("now", "traded"),
    [
        (IN_SESSION, True),
        ("2026-10-19T08:00:00-04:00", False),  # before the open: nothing traded yet
        ("2026-10-19T18:30:00-04:00", True),  # after the close
        ("2026-10-24T12:00:00-04:00", True),  # Saturday: Friday's session
    ],
)
def test_every_tab_renders(app_env: pytest.MonkeyPatch, now: str, traded: bool) -> None:
    app_env.setenv("GEX_CLOCK_START", now)
    at = run()
    assert [t.label for t in at.tabs] == TAB_TITLES
    assert at.title[0].value == "SPY dealer exposure"
    # profile, volume, heatmap, flow bars, flow heatmap, simulation, ladder
    assert len(at.get("plotly_chart")) == (7 if traded else 6)
    if traded:
        assert not at.info  # the synthetic market supports every feature
    else:
        assert infos(at) == (
            "No contract's volume / OI is 3 times that of its nearby strikes "
            "(or nothing has traded yet)."
        )
    assert metric(at, "Net GEX").startswith(("$", "-$"))
    assert metric(at, "Vanna flip") != "n/a"
    assert any("Data: synthetic demo market" in c.value for c in at.caption)


def test_simulation_shows_no_share_of_paths(app_env: pytest.MonkeyPatch) -> None:
    at = run()
    labels = [m.label for m in at.metric]
    assert "Median at the close" in labels
    assert not any("above spot" in label for label in labels)


def test_levels_tab_describes_the_flip_as_a_price() -> None:
    at = run()
    captions = " ".join(c.value for c in at.caption)
    assert "The price at which net dealer GEX" in captions
    assert "best separates" not in captions
    assert "√(2T/π)" not in captions
    assert "spot × IV × √T" in captions


def test_simulation_starts_at_the_next_open_outside_the_session(
    app_env: pytest.MonkeyPatch,
) -> None:
    app_env.setenv("GEX_CLOCK_START", "2026-10-19T18:30:00-04:00")
    at = run()
    assert any("390 trading minutes from Tue Oct 20 09:30" in c.value for c in at.caption)


def test_badges_and_assumptions(app_env: pytest.MonkeyPatch) -> None:
    at = run()
    badges = [m.value for m in at.markdown if "badge" in m.value]
    assert any("Index regime agreement" in b for b in badges)
    assumptions = next(e for e in at.expander if e.label == "Assumptions")
    text = assumptions.markdown[0].value
    assert "customers sell calls and buy puts" in text
    assert "open interest" in text
    assert "covers only the strikes within ±10% of spot" in text
    for label in ("Net GEX", "Gamma flip"):
        assert "only the strikes within ±10% of spot" in metric_help(at, label), label
    at.slider(key="strike_range").set_value(5)
    run(at)
    assumptions = next(e for e in at.expander if e.label == "Assumptions")
    assert "within ±5% of spot" in assumptions.markdown[0].value
    assert "only the strikes within ±5% of spot" in metric_help(at, "Net GEX")


def test_every_symbol_renders() -> None:
    at = run()
    for symbol in ("SPX", "QQQ"):
        at.selectbox(key="symbol").set_value(symbol)
        run(at)
        assert at.title[0].value == f"{symbol} dealer exposure"


def test_single_expiration_explains_the_cross_expiration_tabs() -> None:
    at = run()
    box = at.selectbox(key="expiration_SPY")
    assert box.value == "All expirations"
    box.set_value(date(2026, 10, 19))  # the synthetic 0DTE expiration
    run(at)
    text = infos(at)
    assert "The exposure heatmap compares expirations" in text
    assert "Customer flow by expiration compares expirations" in text


def test_tab_controls() -> None:
    at = run()
    for choice in ("Vanna", "Charm"):
        at.radio(key="heatmap_metric").set_value(choice)
        run(at)
        assert any(f"dealer {choice.lower()} exposure" in c.value for c in at.caption)
    for flow_metric in ("delta", "volume", "premium"):
        at.radio(key="flow_metric").set_value(flow_metric)
        run(at)
    at.radio(key="sim_vol_source").set_value("Manual")
    at.selectbox(key="sim_paths").set_value(100)
    run(at)
    assert by_label(at.number_input, "Annualized volatility").value == 0.20
    at.slider(key="ladder_range").set_value(10)
    at.slider(key="volume_threshold").set_value(10.0)
    at.slider(key="strike_range").set_value(5)
    run(at)


def test_refresh_reloads() -> None:
    at = run()
    at.button[0].click()
    run(at)
    assert at.title[0].value == "SPY dealer exposure"


def test_ladder_shows_structure_and_explainer() -> None:
    at = run()
    ladder = next(e for e in at.expander if e.label == "How the ladder is built")
    assert "Peak node" in ladder.markdown[0].value
    assert any("Peak node" in m.value and m.value.startswith("- ") for m in at.markdown)


def test_ladder_explainer_matches_the_core_defaults() -> None:
    """The thresholds quoted in the explainer are the ones the core uses."""

    def default(fn: object, name: str) -> float:
        return float(inspect.signature(fn).parameters[name].default)  # type: ignore[arg-type]

    text = " ".join(EXPLAINER.split())
    assert f"above {default(patterns.classify_ladder, 'neutral_pct'):.0%} of the peak" in text
    assert f"under {default(patterns.classify_ladder, 'low_exposure_pct'):.0%} of the peak" in text
    assert f"within {patterns.AT_SPOT_PCT}% of spot" in text
    clusters = patterns.detect_positive_clusters
    assert f"at least {default(clusters, 'magnitude_threshold'):.0%} of the peak" in text
    assert int(default(clusters, "min_nodes")) == 3
    assert "Three or more neighbouring strikes (no listed strike between them)" in text
    for word in ("bullish", "bearish", "support"):
        assert word not in text.lower()
    assert (
        "Otherwise a gamma stack decides it, wherever the peak node is: *upside-weighted* for an "
        "upside gamma stack and *downside-weighted* for a downside gamma stack. Without a stack,"
    ) in text
    saturation = patterns.detect_deep_itm_saturation
    assert f"at least {default(saturation, 'saturation_threshold')}" in text
    assert f"at least {int(default(saturation, 'min_oi'))} contracts" in text


def test_ladder_badge_agrees_with_the_stack_sentence() -> None:
    """SPY, all expirations, strike range 2%, ladder 10%: the stack names the arrangement."""
    at = run()
    at.slider(key="strike_range").set_value(2)
    at.slider(key="ladder_range").set_value(10)
    run(at)
    bullets = next(m.value for m in at.markdown if m.value.startswith("- Peak node"))
    assert "- Downside gamma stack:" in bullets
    assert badge(at, "Arrangement").value.startswith(":red-badge[Arrangement: downside-weighted]")


class ChainCountingProvider(SyntheticProvider):
    """The synthetic market, recording every chain request as (symbol, expirations)."""

    name = "counting"
    calls: ClassVar[list[tuple[str, tuple[date, ...] | None]]] = []

    def chain(
        self,
        symbol: str,
        expirations: Sequence[date] | None = None,
        strike_range: float = 0.1,
        now: datetime | None = None,
    ) -> pd.DataFrame:
        self.calls.append((symbol, tuple(expirations) if expirations is not None else None))
        return super().chain(symbol, expirations, strike_range, now)


@pytest.mark.parametrize("front_only", [False, True])
def test_agreement_badge_reuses_the_main_view(
    app_env: pytest.MonkeyPatch, front_only: bool
) -> None:
    """One chain request per symbol: the badge adds no view load for the main symbol.

    ``test_agreement`` checks the reuse itself, where no cache stands in for it.
    """
    ChainCountingProvider.calls = []
    app_env.setitem(PROVIDERS, "counting", lambda settings, clock: ChainCountingProvider(clock))
    app_env.setenv("GEX_PROVIDER", "counting")
    at = run()
    if front_only:
        ChainCountingProvider.calls = []
        at.selectbox(key="expiration_SPY").set_value(date(2026, 10, 19))  # the front
        run(at)
    assert badge(at, "Index regime")
    assert sorted(sym for sym, _ in ChainCountingProvider.calls) == ["QQQ", "SPX", "SPY"]


class FrontCallsProvider(SyntheticProvider):
    """The synthetic market with the front expiration's puts removed.

    On the fixed clock SPY is in negative gamma across all expirations, while
    its front (0DTE) expiration alone, holding only calls, is in positive gamma.
    """

    name = "front_calls"

    def chain(
        self,
        symbol: str,
        expirations: Sequence[date] | None = None,
        strike_range: float = 0.1,
        now: datetime | None = None,
    ) -> pd.DataFrame:
        chain = super().chain(symbol, expirations, strike_range, now)
        front = self.expirations(symbol, now)[0]
        drop = (chain["expiration"] == front) & (chain["right"] == "P")
        return chain[~drop].reset_index(drop=True)


def test_index_agreement_follows_the_expiration_scope(app_env: pytest.MonkeyPatch) -> None:
    app_env.setitem(PROVIDERS, "front_calls", lambda settings, clock: FrontCallsProvider(clock))
    app_env.setenv("GEX_PROVIDER", "front_calls")
    at = run()
    agreement = badge(at, "Index regime").proto.help
    assert "Scope: all expirations of each symbol" in agreement
    assert "SPY: Negative gamma" in agreement
    assert "Negative gamma" in badge(at, "Negative gamma").value

    at.selectbox(key="expiration_SPY").set_value(date(2026, 10, 19))  # the 0DTE expiration
    run(at)
    assert badge(at, "Positive gamma")  # the main regime badge for SPY's 0DTE chain
    agreement = badge(at, "Index regime").proto.help
    assert "SPY: Positive gamma" in agreement  # the same regime the main badge shows
    assert "front expiration (SPY Oct 19, SPX Oct 19, QQQ Oct 19)" in agreement


def test_ladder_share_of_peak_bar_is_neutral() -> None:
    """The share is unsigned, so its bar is gray rather than the theme's accent color."""
    assert TABLE_COLUMNS["Share of peak"]["type_config"]["color"] == "gray"


# ------------------------------------------------------- reduced capabilities


class LimitedProvider:
    """The synthetic market without MULTI_EXPIRATION, SECOND_ORDER or FLOW."""

    name = "limited"
    capabilities = Capability.NONE

    def __init__(self, clock: FixedClock) -> None:
        self._inner = SyntheticProvider(clock)

    def spot(self, symbol: str, now: datetime | None = None) -> float:
        return self._inner.spot(symbol, now)

    def expirations(self, symbol: str, now: datetime | None = None) -> list[date]:
        return self._inner.expirations(symbol, now)

    def chain(
        self,
        symbol: str,
        expirations: Sequence[date] | None = None,
        strike_range: float = 0.1,
        now: datetime | None = None,
    ) -> pd.DataFrame:
        wanted = expirations if expirations is not None else self.expirations(symbol, now)[:1]
        return self._inner.chain(symbol, wanted, strike_range, now)[list(REQUIRED_COLUMNS)]


def test_gated_tabs_explain_missing_capabilities(app_env: pytest.MonkeyPatch) -> None:
    app_env.setitem(PROVIDERS, "limited", lambda settings, clock: LimitedProvider(clock))
    app_env.setenv("GEX_PROVIDER", "limited")
    at = run()
    assert "All expirations" not in at.selectbox(key="expiration_SPY").options
    text = infos(at)
    assert "The exposure heatmap needs a chain across several expirations" in text
    assert "Customer flow needs customer buy/sell volume" in text
    assert "The vanna flip needs vanna and charm" in text
    assert "The vanna and charm sensitivities needs vanna and charm" in text
    assert "(limited)" in text
    for label in ("Vanna flip", "Customer net delta", "GEX / vanna"):
        assert metric(at, label) == "n/a"
    assert len(at.get("plotly_chart")) == 4  # profile, volume, simulation, ladder
    regime = next(m for m in at.markdown if "badge[Negative gamma]" in m.value)
    assert regime.proto.help.endswith(
        "Net vanna exposure is not available, so this regime is not refined by it."
    )


class AllCallsProvider(LimitedProvider):
    """Only calls: no put GEX, so no put walls and no gamma flip."""

    name = "calls"

    def chain(
        self,
        symbol: str,
        expirations: Sequence[date] | None = None,
        strike_range: float = 0.1,
        now: datetime | None = None,
    ) -> pd.DataFrame:
        chain = super().chain(symbol, expirations, strike_range, now)
        return chain[chain["right"] == "C"].reset_index(drop=True)


def test_all_calls_chain_has_no_put_walls(app_env: pytest.MonkeyPatch) -> None:
    app_env.setitem(PROVIDERS, "calls", lambda settings, clock: AllCallsProvider(clock))
    app_env.setenv("GEX_PROVIDER", "calls")
    at = run()
    assert metric(at, "Top put wall") == "n/a"
    assert metric(at, "Gamma flip") == "n/a"
    assert "does not turn from negative to positive anywhere at or below spot down to -10%" in (
        metric_help(at, "Gamma flip")
    )
    assert any("does not turn from negative to positive" in m.value for m in at.markdown)
    assert metric(at, "Gamma ratio") == "100% calls"
    assert any("None: no strike has put GEX." in m.value for m in at.markdown)
    assert not any("PW1=" in c.value for c in at.code)


class ZeroOpenInterestProvider(LimitedProvider):
    """Contracts listed but none open: GEX is zero everywhere."""

    name = "zero_oi"

    def chain(
        self,
        symbol: str,
        expirations: Sequence[date] | None = None,
        strike_range: float = 0.1,
        now: datetime | None = None,
    ) -> pd.DataFrame:
        return super().chain(symbol, expirations, strike_range, now).assign(open_interest=0)


def test_zero_open_interest_shows_n_a(app_env: pytest.MonkeyPatch) -> None:
    app_env.setitem(PROVIDERS, "zero_oi", lambda settings, clock: ZeroOpenInterestProvider(clock))
    app_env.setenv("GEX_PROVIDER", "zero_oi")
    at = run()
    for label in ("Gamma ratio", "Top call wall", "Top put wall", "Gamma flip"):
        assert metric(at, label) == "n/a", label
    assert "No usable implied volatility to reprice" in metric_help(at, "Gamma flip")
    assert any("the chain has no GEX" in m.value for m in at.markdown)


# ------------------------------------------------------------------ failures


def _failing_factory(settings: object, clock: object) -> ChainProvider:
    raise ProviderError("login refused")


class BrokenChainProvider(LimitedProvider):
    name = "broken"

    def chain(
        self,
        symbol: str,
        expirations: Sequence[date] | None = None,
        strike_range: float = 0.1,
        now: datetime | None = None,
    ) -> pd.DataFrame:
        raise ProviderError("chain request timed out")


class EmptyProvider(LimitedProvider):
    name = "empty"

    def chain(
        self,
        symbol: str,
        expirations: Sequence[date] | None = None,
        strike_range: float = 0.1,
        now: datetime | None = None,
    ) -> pd.DataFrame:
        return empty_chain()


def _error_run(app_env: pytest.MonkeyPatch, name: str, factory: object) -> AppTest:
    app_env.setitem(PROVIDERS, name, factory)
    app_env.setenv("GEX_PROVIDER", name)
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def test_provider_that_cannot_start_shows_an_error(app_env: pytest.MonkeyPatch) -> None:
    at = _error_run(app_env, "failing", _failing_factory)
    assert len(at.error) == 1
    assert "login refused" in at.error[0].value
    assert not at.tabs


def test_chain_failure_shows_an_error(app_env: pytest.MonkeyPatch) -> None:
    at = _error_run(app_env, "broken", lambda s, clock: BrokenChainProvider(clock))
    assert "chain request timed out" in at.error[0].value
    assert not at.tabs


def test_unknown_provider_shows_an_error(app_env: pytest.MonkeyPatch) -> None:
    app_env.setenv("GEX_PROVIDER", "nosuch")
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    assert "Unknown provider 'nosuch'" in at.error[0].value


def test_empty_chain_shows_a_warning(app_env: pytest.MonkeyPatch) -> None:
    at = _error_run(app_env, "empty", lambda s, clock: EmptyProvider(clock))
    assert "No open interest for SPY" in at.warning[0].value
    assert not at.tabs


# --------------------------------------------------------------- ConvexValue


def test_convexvalue_renders_with_the_fake_api(app_env: pytest.MonkeyPatch) -> None:
    fake = fake_convexlib.install(app_env)
    synthetic = SyntheticProvider(FixedClock(datetime.fromisoformat(IN_SESSION)))
    for symbol in ("SPY", "SPX", "QQQ"):
        fake.spots[symbol] = synthetic.spot(symbol)
        fake.chains[symbol] = synthetic.chain(symbol, strike_range=0.25)
    app_env.setenv("GEX_PROVIDER", "convexvalue")
    app_env.setenv("CONVEX_USER", "demo@example.com")
    app_env.setenv("CONVEX_PASSWORD", "not-a-secret")
    at = run()
    assert [t.label for t in at.tabs] == TAB_TITLES
    assert len(at.get("plotly_chart")) == 7
    assert any("Data: ConvexValue API" in c.value for c in at.caption)
    assert fake.logins == [("demo@example.com", "not-a-secret")]

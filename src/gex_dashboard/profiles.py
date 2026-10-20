"""Labels that summarize the sign of the net dealer exposures.

All inputs use the dollar conventions and dealer sign convention of
``gex_dashboard.exposures``. The labels say which signs were observed; they
are descriptions of the computed exposures, not forecasts. Zero counts as
positive.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

AgreementStatus = Literal["aligned", "partial", "divergent", "unavailable"]
HedgeAlignment = Literal["same_sign", "mixed", "neutral"]
HedgeDriver = Literal["gamma", "vanna", "charm"]


@dataclass(frozen=True)
class GammaRegime:
    key: str
    label: str
    description: str


_GAMMA_HEDGING: Final = {
    "+": "Under the assumed dealer positioning, delta hedging sells as spot rises and buys as "
    "it falls.",
    "-": "Under the assumed dealer positioning, delta hedging buys as spot rises and sells as "
    "it falls.",
}
_VANNA_HEDGING: Final = {
    "+": "A rise in implied volatility raises dealer delta, which hedging offsets by selling.",
    "-": "A rise in implied volatility lowers dealer delta, which hedging offsets by buying.",
}

POSITIVE_GAMMA: Final = GammaRegime(
    "positive_gamma",
    "Positive gamma",
    f"Net dealer GEX is positive. {_GAMMA_HEDGING['+']}",
)
NEGATIVE_GAMMA: Final = GammaRegime(
    "negative_gamma",
    "Negative gamma",
    f"Net dealer GEX is negative. {_GAMMA_HEDGING['-']}",
)
NEGATIVE_GAMMA_VANNA_ALIGNED: Final = GammaRegime(
    "negative_gamma_vanna_aligned",
    "Negative gamma, vanna aligned",
    "Net dealer GEX and net dealer vanna exposure are both negative. "
    f"{_GAMMA_HEDGING['-']} {_VANNA_HEDGING['-']}",
)
NEGATIVE_GAMMA_VANNA_OPPOSED: Final = GammaRegime(
    "negative_gamma_vanna_opposed",
    "Negative gamma, vanna opposed",
    "Net dealer GEX is negative while net dealer vanna exposure is zero or positive. "
    f"{_GAMMA_HEDGING['-']} {_VANNA_HEDGING['+']}",
)


def classify_gamma_regime(net_gex: float, net_vanna: float | None = None) -> GammaRegime:
    """Regime from the sign of net GEX, refined by net vanna when GEX is negative.

    Without net vanna (``None``) a negative regime is the unrefined
    ``NEGATIVE_GAMMA``; the UI says that vanna is unavailable.
    """
    if net_gex >= 0:
        return POSITIVE_GAMMA
    if net_vanna is None:
        return NEGATIVE_GAMMA
    return NEGATIVE_GAMMA_VANNA_ALIGNED if net_vanna < 0 else NEGATIVE_GAMMA_VANNA_OPPOSED


@dataclass(frozen=True)
class ExposureAlignment:
    state: str
    label: str
    description: str


def classify_gex_vex_alignment(net_gex: float, net_vanna: float) -> ExposureAlignment:
    """One of four states from the signs of net GEX and net vanna exposure."""
    gex_sign = "+" if net_gex >= 0 else "-"
    vex_sign = "+" if net_vanna >= 0 else "-"
    words = {"+": "positive", "-": "negative"}
    if gex_sign == vex_sign:
        state = f"aligned_{words[gex_sign]}"
        description = f"Net GEX and net vanna exposure are both {words[gex_sign]}."
    else:
        state = f"opposed_gex_{words[gex_sign]}"
        description = f"Net GEX is {words[gex_sign]} and net vanna exposure is {words[vex_sign]}."
    return ExposureAlignment(state, f"{gex_sign}GEX / {vex_sign}VEX", description)


@dataclass(frozen=True)
class IndexAgreement:
    status: AgreementStatus
    shared_regime: str | None
    breakdown: dict[str, str | None]
    label: str
    description: str


def classify_index_agreement(regimes: Mapping[str, GammaRegime | None]) -> IndexAgreement:
    """Whether several underlyings (e.g. SPX, SPY, QQQ) share a gamma regime.

    ``aligned``: every symbol has the same regime. ``partial``: a strict
    majority does. ``divergent``: no regime has a majority. ``unavailable``:
    fewer than two symbols, or any regime missing.
    """
    breakdown = {sym: (r.label if r else None) for sym, r in regimes.items()}
    names = ", ".join(regimes)
    if len(regimes) < 2 or any(r is None for r in regimes.values()):
        return IndexAgreement(
            "unavailable",
            None,
            breakdown,
            "Agreement: n/a",
            f"Needs a regime for each of {names}.",
        )
    counts = Counter(r.label for r in regimes.values() if r is not None)
    top, n_top = counts.most_common(1)[0]
    total = len(regimes)
    if n_top == total:
        return IndexAgreement(
            "aligned",
            top,
            breakdown,
            f"Agreement: all {top.lower()}",
            f"{names} are all in the {top.lower()} regime.",
        )
    if n_top * 2 > total:
        return IndexAgreement(
            "partial",
            top,
            breakdown,
            f"Agreement: {n_top}/{total} {top.lower()}",
            f"{n_top} of {total} symbols are in the {top.lower()} regime.",
        )
    return IndexAgreement(
        "divergent",
        None,
        breakdown,
        "Agreement: divergent",
        f"No regime is shared by a majority of {names}.",
    )


@dataclass(frozen=True)
class HedgingImpact:
    """Dealer dollar-delta sensitivities to three drivers.

    ``gamma``: per 1% spot move. ``vanna``: per 1 vol-point IV rise. ``charm``:
    per trading day. ``charm_remaining``: ``charm`` scaled by the share of
    today's session left.
    """

    gamma: float
    vanna: float
    charm: float
    charm_remaining: float
    alignment: HedgeAlignment
    dominant: HedgeDriver


def hedging_impact(
    net_gex: float, net_vanna: float, net_charm: float, session_fraction_remaining: float
) -> HedgingImpact:
    """Compare the three dealer delta sensitivities, all in dollars of delta.

    ``alignment`` is ``same_sign`` when every non-zero component has the same
    sign (zeros are ignored), ``mixed`` when at least one is positive and one
    negative, and ``neutral`` when all are zero. ``dominant`` is
    the largest in absolute value at the unit shocks above (1% move, 1 vol
    point, 1 trading day); a different shock size changes that ranking.
    """
    fraction = min(max(session_fraction_remaining, 0.0), 1.0)
    components: dict[HedgeDriver, float] = {
        "gamma": net_gex,
        "vanna": net_vanna,
        "charm": net_charm,
    }
    signs = {(v > 0) - (v < 0) for v in components.values()} - {0}
    alignment: HedgeAlignment
    if not signs:
        alignment = "neutral"
    elif len(signs) == 1:
        alignment = "same_sign"
    else:
        alignment = "mixed"
    dominant = max(components, key=lambda k: abs(components[k]))
    return HedgingImpact(
        gamma=net_gex,
        vanna=net_vanna,
        charm=net_charm,
        charm_remaining=net_charm * fraction,
        alignment=alignment,
        dominant=dominant,
    )

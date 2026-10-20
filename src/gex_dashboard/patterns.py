"""The GEX ladder: per-strike classification and the structure around spot.

Vocabulary (all computed from one GEX-by-strike profile):

- **Regime** of a strike: ``positive`` / ``negative`` when its GEX exceeds
  ``neutral_pct`` of the peak node's absolute GEX, else ``neutral``.
- **Peak node**: the strike with the largest absolute GEX in the window.
- **Low-exposure gap**: a strike whose absolute GEX is under
  ``low_exposure_pct`` of the peak node's.
- **Floor / ceiling**: the largest positive-GEX strike below / above spot.
- **Intermediate nodes**: non-neutral strikes (not the peak node and not a
  low-exposure gap) between spot and the floor or ceiling.
- **Upside gamma stack**: negative strikes above spot while positive strikes
  outnumber negative ones below it. **Downside gamma stack**: positive strikes
  outnumber negative ones above spot and negative outnumber positive below.
- **Positive-gamma cluster**: at least ``min_nodes`` neighbouring strikes (no
  listed strike between them), each positive and at least
  ``magnitude_threshold`` of the peak node's absolute GEX: a local
  concentration, not a broad positive region.
- **Deep-ITM saturation**: a negative-regime strike whose open interest is
  mostly deep in the money (OI-weighted ``|delta|`` near 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

import pandas as pd

from gex_dashboard.levels import strike_with_max

AT_SPOT_PCT: Final = 0.15  # peak node within 0.15% of spot counts as "at" spot
Bias = Literal["pinned", "upside-weighted", "downside-weighted", "neutral"]
PeakPosition = Literal["at", "above", "below"]


def classify_ladder(
    profile: pd.DataFrame,
    spot: float,
    pct_range: float = 0.03,
    neutral_pct: float = 0.05,
    low_exposure_pct: float = 0.10,
) -> pd.DataFrame:
    """Label each strike within ``pct_range`` of spot.

    Adds ``regime``, ``is_peak_node``, ``is_spot`` (the strike nearest spot),
    ``magnitude`` (absolute GEX over the window's largest, 0-1) and
    ``is_low_exposure``. Sorted by strike, highest first. ``profile`` is
    ``exposures.gex_by_strike`` output.
    """
    if profile.empty or "gex" not in profile:
        return pd.DataFrame()
    window = (profile["strike"] >= spot * (1 - pct_range)) & (
        profile["strike"] <= spot * (1 + pct_range)
    )
    df = profile[window].reset_index(drop=True)
    if df.empty:
        return pd.DataFrame()

    abs_gex = df["gex"].abs()
    peak_idx = abs_gex.idxmax()
    peak_abs = float(abs_gex.max())
    threshold = peak_abs * neutral_pct
    df["is_peak_node"] = df.index == peak_idx
    df["regime"] = "neutral"
    df.loc[df["gex"] > threshold, "regime"] = "positive"
    df.loc[df["gex"] < -threshold, "regime"] = "negative"
    df["magnitude"] = abs_gex / peak_abs if peak_abs > 0 else 0.0
    df["is_spot"] = df.index == (df["strike"] - spot).abs().idxmin()
    df["is_low_exposure"] = df["magnitude"] < low_exposure_pct
    return df.sort_values("strike", ascending=False).reset_index(drop=True)


@dataclass(frozen=True)
class LadderStructure:
    peak_strike: float
    peak_gex: float
    peak_regime: str
    peak_position: PeakPosition
    positive_above: int
    negative_above: int
    positive_below: int
    negative_below: int
    low_exposure_above: int
    low_exposure_below: int
    floor: float | None
    ceiling: float | None
    intermediate_strikes: list[float] = field(default_factory=list)
    two_or_more_positive_below: bool = False
    upside_gamma_stack: bool = False
    downside_gamma_stack: bool = False
    bias: Bias = "neutral"
    interpretation: list[str] = field(default_factory=list)


def _largest_positive(side: pd.DataFrame) -> float | None:
    positive = side[side["regime"] == "positive"]
    return strike_with_max(positive, "gex")


def ladder_structure(ladder: pd.DataFrame, spot: float) -> LadderStructure | None:
    """Summarize a ``classify_ladder`` result around spot; None for an empty ladder.

    ``bias`` is ``pinned`` when the peak node is at spot. Otherwise a gamma
    stack decides it, wherever the peak node is: ``upside-weighted`` for an
    upside stack, ``downside-weighted`` for a downside stack. Without a stack,
    ``upside-weighted`` when at least two positive strikes are below spot and
    more positive than negative above; ``downside-weighted`` when negative
    strikes outnumber positive on both sides; otherwise ``neutral``. These are
    labels for the arrangement of exposure, not forecasts.
    """
    if ladder.empty:
        return None
    peak = ladder[ladder["is_peak_node"]].iloc[0]
    peak_strike, peak_gex = float(peak["strike"]), float(peak["gex"])
    above = ladder[ladder["strike"] > spot]
    below = ladder[ladder["strike"] < spot]
    pos_above = int((above["regime"] == "positive").sum())
    neg_above = int((above["regime"] == "negative").sum())
    pos_below = int((below["regime"] == "positive").sum())
    neg_below = int((below["regime"] == "negative").sum())

    distance_pct = (peak_strike - spot) / spot * 100
    position: PeakPosition
    if abs(distance_pct) < AT_SPOT_PCT:
        position = "at"
    else:
        position = "above" if peak_strike > spot else "below"

    floor, ceiling = _largest_positive(below), _largest_positive(above)
    nodes = ladder[
        (ladder["regime"] != "neutral") & ~ladder["is_peak_node"] & ~ladder["is_low_exposure"]
    ]
    intermediate = sorted(
        float(s)
        for s in nodes["strike"]
        if (floor is not None and floor < s < spot) or (ceiling is not None and spot < s < ceiling)
    )

    two_positive_below = pos_below >= 2
    upside_stack = neg_above > 0 and pos_below > neg_below
    downside_stack = pos_above > neg_above and neg_below > pos_below
    bias: Bias
    if position == "at":
        bias = "pinned"
    elif upside_stack:
        bias = "upside-weighted"
    elif downside_stack:
        bias = "downside-weighted"
    elif two_positive_below and pos_above > neg_above:
        bias = "upside-weighted"
    elif neg_below > pos_below and neg_above > pos_above:
        bias = "downside-weighted"
    else:
        bias = "neutral"

    low_above = int(above["is_low_exposure"].sum())
    low_below = int(below["is_low_exposure"].sum())
    interpretation = _describe(
        peak_strike=peak_strike,
        peak_regime=str(peak["regime"]),
        peak_position=position,
        distance_pct=distance_pct,
        counts=(pos_above, neg_above, pos_below, neg_below),
        floor=floor,
        ceiling=ceiling,
        intermediate=intermediate,
        upside_stack=upside_stack,
        downside_stack=downside_stack,
        low_exposure=(low_above, low_below),
    )
    return LadderStructure(
        peak_strike=peak_strike,
        peak_gex=peak_gex,
        peak_regime=str(peak["regime"]),
        peak_position=position,
        positive_above=pos_above,
        negative_above=neg_above,
        positive_below=pos_below,
        negative_below=neg_below,
        low_exposure_above=low_above,
        low_exposure_below=low_below,
        floor=floor,
        ceiling=ceiling,
        intermediate_strikes=intermediate,
        two_or_more_positive_below=two_positive_below,
        upside_gamma_stack=upside_stack,
        downside_gamma_stack=downside_stack,
        bias=bias,
        interpretation=interpretation,
    )


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """``"1 strike"``, ``"2 strikes"``: the count with the noun in the matching number."""
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count} {word}"


def _describe(
    *,
    peak_strike: float,
    peak_regime: str,
    peak_position: PeakPosition,
    distance_pct: float,
    counts: tuple[int, int, int, int],
    floor: float | None,
    ceiling: float | None,
    intermediate: list[float],
    upside_stack: bool,
    downside_stack: bool,
    low_exposure: tuple[int, int],
) -> list[str]:
    """Plain statements of what ``ladder_structure`` found.

    ``counts`` is (positive above, negative above, positive below, negative
    below); ``low_exposure`` is (gaps above, gaps below).
    """
    pos_above, neg_above, pos_below, neg_below = counts
    low_above, low_below = low_exposure
    where = "at spot" if peak_position == "at" else f"{abs(distance_pct):.2f}% {peak_position} spot"
    lines = [
        f"Peak node {peak_strike:,.2f} ({peak_regime} GEX) is {where}: "
        "the largest absolute GEX in the window."
    ]
    if floor is not None:
        lines.append(f"Floor {floor:,.2f}: the largest positive-GEX strike below spot.")
    if ceiling is not None:
        lines.append(f"Ceiling {ceiling:,.2f}: the largest positive-GEX strike above spot.")
    if intermediate:
        joined = ", ".join(f"{k:,.2f}" for k in intermediate)
        if len(intermediate) == 1:
            lines.append(
                f"Intermediate node {joined}: a non-neutral strike between spot and the floor "
                "or ceiling."
            )
        else:
            lines.append(
                f"Intermediate nodes {joined}: non-neutral strikes between spot and the floor "
                "or ceiling."
            )
    lines.append(
        f"Above spot: {pos_above} positive, {neg_above} negative. "
        f"Below spot: {pos_below} positive, {neg_below} negative."
    )
    if upside_stack:
        lines.append(
            "Upside gamma stack: negative-GEX strikes above spot, with positive "
            "strikes outnumbering negative ones below."
        )
    elif downside_stack:
        lines.append(
            "Downside gamma stack: positive strikes outnumber negative ones above "
            "spot, and negative outnumber positive below."
        )
    if low_above or low_below:
        lines.append(
            f"Low-exposure gaps: {plural(low_above, 'strike')} above spot and "
            f"{plural(low_below, 'strike')} below fall under the low-exposure "
            "threshold."
        )
    return lines


@dataclass(frozen=True)
class PositiveCluster:
    strikes: list[float]
    center_strike: float  # the member with the largest |GEX|
    total_gex: float
    width: float
    distance_to_spot: float


def detect_positive_clusters(
    ladder: pd.DataFrame,
    spot: float,
    min_nodes: int = 3,
    magnitude_threshold: float = 0.50,
) -> list[PositiveCluster]:
    """Runs of at least ``min_nodes`` neighbouring positive strikes, each a large node.

    Neighbouring means consecutive in the ladder's strike list, so adjacency
    follows the chain's own strike grid. Every member must be in the positive
    regime with at least ``magnitude_threshold`` of the peak node's absolute
    GEX; a strike below that ends the run. Nearest cluster to spot first.
    """
    if ladder.empty or not {"strike", "gex", "regime", "magnitude"}.issubset(ladder.columns):
        return []
    rows = ladder.sort_values("strike")
    member = (rows["regime"] == "positive") & (rows["magnitude"] >= magnitude_threshold)
    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for strike, gex, is_member in zip(rows["strike"], rows["gex"], member, strict=True):
        if is_member:
            current.append((float(strike), float(gex)))
        elif current:
            runs.append(current)
            current = []
    runs.append(current)

    clusters = [
        PositiveCluster(
            strikes=[k for k, _ in run],
            center_strike=max(run, key=lambda m: abs(m[1]))[0],
            total_gex=sum(g for _, g in run),
            width=run[-1][0] - run[0][0],
            distance_to_spot=min(abs(k - spot) for k, _ in run),
        )
        for run in runs
        if len(run) >= min_nodes
    ]
    return sorted(clusters, key=lambda c: c.distance_to_spot)


@dataclass(frozen=True)
class SaturatedStrike:
    strike: float
    saturation: float  # OI-weighted |delta|, 0-1
    total_oi: int
    net_delta_oi: float  # sum of delta x OI (per share, customer-agnostic)


def detect_deep_itm_saturation(
    chain: pd.DataFrame,
    ladder: pd.DataFrame,
    saturation_threshold: float = 0.85,
    min_oi: int = 100,
) -> list[SaturatedStrike]:
    """Negative-regime strikes whose OI-weighted ``|delta|`` is at least the threshold.

    Aggregates calls, puts and every expiration in ``chain`` at each strike.
    A value near 1 means nearly all open interest there is deep in the money,
    so its delta (and the hedge against it) can barely grow further. Highest
    saturation first.
    """
    if chain.empty or not {"strike", "right", "delta", "open_interest"}.issubset(chain.columns):
        return []
    if ladder.empty or not {"strike", "regime"}.issubset(ladder.columns):
        return []
    negative = set(ladder.loc[ladder["regime"] == "negative", "strike"])
    df = chain[chain["strike"].isin(negative)].dropna(subset=["delta", "open_interest"])
    if df.empty:
        return []
    oi = df["open_interest"].astype(float)
    agg = (
        df.assign(abs_doi=df["delta"].abs() * oi, doi=df["delta"] * oi, oi=oi)
        .groupby("strike")
        .agg(abs_doi=("abs_doi", "sum"), doi=("doi", "sum"), oi=("oi", "sum"))
        .reset_index()
    )
    agg = agg[agg["oi"] >= min_oi]
    agg = agg.assign(saturation=agg["abs_doi"] / agg["oi"])
    hits = agg[agg["saturation"] >= saturation_threshold].sort_values("saturation", ascending=False)
    return [
        SaturatedStrike(float(k), float(sat), int(n), float(d))
        for k, sat, n, d in zip(
            hits["strike"], hits["saturation"], hits["oi"], hits["doi"], strict=True
        )
    ]

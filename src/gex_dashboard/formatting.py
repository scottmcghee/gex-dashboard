"""Small display helpers shared by the UI."""

from __future__ import annotations

from datetime import date


def format_expiration_display(expiration: date, today: date) -> str:
    """``Oct 23, 2026 (4 DTE)``: the date plus calendar days to expiration."""
    return f"{expiration.strftime('%b %d, %Y')} ({(expiration - today).days} DTE)"


def format_compact_dollars(value: float) -> str:
    """``-$1.2B``, ``$3.4M``, ``$5.6K`` or ``$7.89``."""
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= threshold:
            return f"{sign}${magnitude / threshold:,.1f}{suffix}"
    return f"{sign}${magnitude:,.2f}"


def format_from_spot(level: float, spot: float) -> str:
    """``+1.23%``: how far ``level`` is from ``spot``, as a signed share of spot."""
    return f"{(level - spot) / spot:+.2%}"


def front_move_label(front: date | None) -> str:
    """``1σ move, Oct 19 expiration``: names the expiration whose expected move is drawn."""
    return "1σ move" if front is None else f"1σ move, {front:%b %d} expiration"

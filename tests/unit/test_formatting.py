"""Display helpers."""

from __future__ import annotations

from datetime import date

import pytest

from gex_dashboard.formatting import (
    format_compact_dollars,
    format_expiration_display,
    format_from_spot,
    front_move_label,
)


def test_expiration_display():
    assert format_expiration_display(date(2026, 10, 23), date(2026, 10, 19)) == (
        "Oct 23, 2026 (4 DTE)"
    )
    assert format_expiration_display(date(2026, 10, 19), date(2026, 10, 19)).endswith("(0 DTE)")


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (2.5e9, "$2.5B"),
        (-3.44e6, "-$3.4M"),
        (5_600.0, "$5.6K"),
        (7.891, "$7.89"),
        (0.0, "$0.00"),
    ],
)
def test_compact_dollars(value, text):
    assert format_compact_dollars(value) == text


def test_from_spot():
    assert format_from_spot(101.5, 100.0) == "+1.50%"
    assert format_from_spot(97.0, 100.0) == "-3.00%"
    assert format_from_spot(100.0, 100.0) == "+0.00%"


def test_front_move_label():
    assert front_move_label(date(2026, 10, 19)) == "1σ move, Oct 19 expiration"
    assert front_move_label(None) == "1σ move"

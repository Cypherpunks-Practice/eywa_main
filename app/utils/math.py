from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal


def raw_amount_to_decimal(raw_value: int, decimals: int) -> Decimal:
    """Convert an integer token amount to a decimal value."""

    if raw_value <= 0:
        return Decimal()
    return Decimal(raw_value) / (Decimal(10) ** decimals)


def median_decimal(values: Sequence[Decimal]) -> Decimal:
    """Return the median of a non-empty Decimal sequence."""

    if not values:
        raise ValueError("values must not be empty")

    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")

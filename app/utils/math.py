from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

# Предел величины для колонок ClickHouse типа Decimal(38, 18): целая часть строго
# меньше 10^20 (38 значащих цифр минус 18 после точки). Значения за границей
# ClickHouse отвергает с `Decimal convert overflow`, роняя запись всего блока.
DB_DECIMAL_38_18_MAX = Decimal(10) ** 20


def raw_amount_to_decimal(raw_value: int, decimals: int) -> Decimal:
    """Convert an integer token amount to a decimal value."""

    if raw_value <= 0:
        return Decimal()
    return Decimal(raw_value) / (Decimal(10) ** decimals)


def fits_db_decimal(value: Decimal) -> bool:
    """Влезает ли значение в колонку `Decimal(38, 18)` без переполнения."""

    return value.is_finite() and abs(value) < DB_DECIMAL_38_18_MAX


def median_decimal(values: Sequence[Decimal]) -> Decimal:
    """Return the median of a non-empty Decimal sequence."""

    if not values:
        raise ValueError("values must not be empty")

    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")

"""Decimal helpers for crypto trading."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN


def parse_decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if not text:
        return default
    try:
        return Decimal(text)
    except InvalidOperation:
        return default


def floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        return value
    units = (value / increment).to_integral_value(rounding=ROUND_DOWN)
    return units * increment


def format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def decimal_to_sdk_float(value: Decimal) -> float:
    return float(format_decimal(value))


def validate_positive(value: Decimal) -> bool:
    return value > 0


def validate_minimum(value: Decimal, minimum: Decimal | None) -> bool:
    if minimum is None:
        return value > 0
    return value >= minimum

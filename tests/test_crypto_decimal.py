"""Crypto decimal helper tests."""

from decimal import Decimal

import pytest

from core.crypto_decimal import floor_to_increment, format_decimal, parse_decimal, validate_minimum


def test_floor_to_increment() -> None:
    assert floor_to_increment(Decimal("1.23456789"), Decimal("0.00000001")) == Decimal("1.23456789")


def test_never_rounds_buy_upward() -> None:
    value = Decimal("1.000000019")
    inc = Decimal("0.00000001")
    assert floor_to_increment(value, inc) == Decimal("1.00000001")


def test_eight_decimal_quantities() -> None:
    assert format_decimal(Decimal("0.00000001")) == "0.00000001"


def test_rejects_negative() -> None:
    assert not validate_minimum(Decimal("-1"), Decimal("0.0001"))


def test_minimum_order_size() -> None:
    assert validate_minimum(Decimal("10"), Decimal("5"))


def test_no_float_drift() -> None:
    value = parse_decimal("0.1") + parse_decimal("0.2")
    assert value == Decimal("0.3")

"""Tests for performance metrics."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from backtesting import metrics
from core.models import Trade


def _trade(side: str, gross_value: float, commission: float = 0.0) -> Trade:
    return Trade(
        timestamp=datetime(2024, 1, 1),
        symbol="TEST",
        side=side,  # type: ignore[arg-type]
        quantity=1,
        execution_price=gross_value,
        gross_value=gross_value,
        commission=commission,
        cash_after_trade=0.0,
        position_after_trade=1 if side == "BUY" else 0,
        reason="test",
    )


def test_positive_total_return() -> None:
    assert metrics.total_return_percent(10_000.0, 11_000.0) == pytest.approx(10.0)


def test_negative_total_return() -> None:
    assert metrics.total_return_percent(10_000.0, 9_000.0) == pytest.approx(-10.0)


def test_maximum_drawdown() -> None:
    equity = pd.Series([100.0, 110.0, 90.0, 95.0])
    assert metrics.maximum_drawdown_percent(equity) == pytest.approx(18.181818, rel=1e-3)


def test_zero_volatility_sharpe_ratio() -> None:
    daily_returns = pd.Series([0.0, 0.0, 0.0, 0.0])
    assert metrics.sharpe_ratio(daily_returns) == 0.0


def test_completed_trade_counting() -> None:
    trades = [_trade("BUY", 100.0), _trade("SELL", 110.0), _trade("BUY", 100.0)]
    assert metrics.count_completed_trades(trades) == 1


def test_winning_trade_counting() -> None:
    trades = [_trade("BUY", 100.0, 1.0), _trade("SELL", 110.0, 1.0)]
    assert metrics.count_winning_trades(trades) == 1
    assert metrics.count_losing_trades(trades) == 0


def test_losing_trade_counting() -> None:
    trades = [_trade("BUY", 100.0, 1.0), _trade("SELL", 90.0, 1.0)]
    assert metrics.count_losing_trades(trades) == 1
    assert metrics.count_winning_trades(trades) == 0


def test_open_trade_does_not_count_as_completed() -> None:
    trades = [_trade("BUY", 100.0)]
    assert metrics.count_completed_trades(trades) == 0
    assert metrics.count_winning_trades(trades) == 0
    assert metrics.count_losing_trades(trades) == 0


def test_buy_and_hold_return() -> None:
    closes = pd.Series([100.0, 110.0, 120.0])
    assert metrics.buy_and_hold_return_percent(closes) == pytest.approx(20.0)

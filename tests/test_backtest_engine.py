"""Tests for the backtesting engine."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from backtesting.engine import BacktestEngine
from core.models import BacktestConfiguration, SignalType
from strategies.base_strategy import BaseStrategy


class ScriptedStrategy(BaseStrategy):
    """Strategy that injects predetermined signals for deterministic tests."""

    def __init__(self, signals: list[str], name: str = "Scripted Strategy") -> None:
        self._signals = signals
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        return data.copy()

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        result = data.copy()
        result["SMA_Short"] = result["Close"]
        result["SMA_Long"] = result["Close"]
        result["Position"] = 0
        result["PositionChange"] = 0
        result["Signal"] = self._signals[: len(result)]
        return result


def _make_ohlcv(rows: list[dict[str, float]]) -> pd.DataFrame:
    index = pd.date_range(datetime(2024, 1, 1), periods=len(rows), freq="D")
    return pd.DataFrame(rows, index=index)


def _configuration(**overrides) -> BacktestConfiguration:
    defaults = {
        "symbol": "TEST",
        "start_date": datetime(2024, 1, 1).date(),
        "end_date": datetime(2024, 1, 10).date(),
        "starting_capital": 10_000.0,
        "allocation": 10_000.0,
        "commission": 0.0,
        "slippage_percent": 0.0,
        "cash_reserve_percent": 0.0,
    }
    defaults.update(overrides)
    return BacktestConfiguration(**defaults)


def test_signal_executes_on_next_day_open() -> None:
    data = _make_ohlcv(
        [
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 20, "High": 20, "Low": 20, "Close": 20, "Volume": 1000},
            {"Open": 20, "High": 20, "Low": 20, "Close": 20, "Volume": 1000},
        ]
    )
    signals = [SignalType.HOLD.value, SignalType.BUY.value, SignalType.HOLD.value]
    engine = BacktestEngine(ScriptedStrategy(signals), _configuration(), data)
    result = engine.run()
    assert len(result.trades) == 1
    assert result.trades[0].execution_price == 20.0
    assert result.trades[0].timestamp == data.index[2]


def test_no_lookahead_execution() -> None:
    data = _make_ohlcv(
        [
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 15, "High": 15, "Low": 15, "Close": 15, "Volume": 1000},
        ]
    )
    signals = [SignalType.BUY.value, SignalType.HOLD.value]
    engine = BacktestEngine(ScriptedStrategy(signals), _configuration(), data)
    result = engine.run()
    assert len(result.trades) == 1
    assert result.trades[0].execution_price == 15.0
    assert result.trades[0].execution_price != 10.0


def test_whole_share_quantity_calculation() -> None:
    data = _make_ohlcv(
        [
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 300, "High": 300, "Low": 300, "Close": 300, "Volume": 1000},
            {"Open": 300, "High": 300, "Low": 300, "Close": 300, "Volume": 1000},
        ]
    )
    signals = [SignalType.HOLD.value, SignalType.BUY.value, SignalType.HOLD.value]
    engine = BacktestEngine(
        ScriptedStrategy(signals),
        _configuration(starting_capital=1000.0, allocation=1000.0),
        data,
    )
    result = engine.run()
    assert result.trades[0].quantity == 3


def test_cash_reserve_reduces_purchases() -> None:
    data = _make_ohlcv(
        [
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 100, "High": 100, "Low": 100, "Close": 100, "Volume": 1000},
            {"Open": 100, "High": 100, "Low": 100, "Close": 100, "Volume": 1000},
        ]
    )
    signals = [SignalType.HOLD.value, SignalType.BUY.value, SignalType.HOLD.value]
    engine = BacktestEngine(
        ScriptedStrategy(signals),
        _configuration(starting_capital=1000.0, allocation=1000.0, cash_reserve_percent=0.10),
        data,
    )
    result = engine.run()
    assert result.trades[0].quantity == 9


def test_slippage_direction() -> None:
    data = _make_ohlcv(
        [
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 100, "High": 100, "Low": 100, "Close": 100, "Volume": 1000},
            {"Open": 100, "High": 100, "Low": 100, "Close": 100, "Volume": 1000},
            {"Open": 110, "High": 110, "Low": 110, "Close": 110, "Volume": 1000},
            {"Open": 120, "High": 120, "Low": 120, "Close": 120, "Volume": 1000},
        ]
    )
    signals = [
        SignalType.HOLD.value,
        SignalType.BUY.value,
        SignalType.HOLD.value,
        SignalType.SELL.value,
        SignalType.HOLD.value,
    ]
    engine = BacktestEngine(
        ScriptedStrategy(signals),
        _configuration(slippage_percent=0.01),
        data,
    )
    result = engine.run()
    assert result.trades[0].execution_price == pytest.approx(101.0)
    assert result.trades[1].execution_price == pytest.approx(118.8)


def test_commission_is_charged() -> None:
    data = _make_ohlcv(
        [
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
        ]
    )
    signals = [SignalType.HOLD.value, SignalType.BUY.value, SignalType.HOLD.value]
    engine = BacktestEngine(
        ScriptedStrategy(signals),
        _configuration(starting_capital=1_005.0, allocation=1_005.0, commission=5.0),
        data,
    )
    result = engine.run()
    assert len(result.trades) == 1
    assert result.trades[0].commission == 5.0


def test_no_duplicate_purchases() -> None:
    data = _make_ohlcv(
        [
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
        ]
    )
    signals = [
        SignalType.HOLD.value,
        SignalType.BUY.value,
        SignalType.BUY.value,
        SignalType.HOLD.value,
    ]
    engine = BacktestEngine(ScriptedStrategy(signals), _configuration(), data)
    result = engine.run()
    assert len(result.trades) == 1


def test_full_position_liquidation() -> None:
    data = _make_ohlcv(
        [
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 12, "High": 12, "Low": 12, "Close": 12, "Volume": 1000},
        ]
    )
    signals = [
        SignalType.HOLD.value,
        SignalType.BUY.value,
        SignalType.SELL.value,
        SignalType.HOLD.value,
    ]
    engine = BacktestEngine(ScriptedStrategy(signals), _configuration(), data)
    result = engine.run()
    assert len(result.trades) == 2
    assert result.trades[1].position_after_trade == 0


def test_no_execution_for_final_row_signal() -> None:
    data = _make_ohlcv(
        [
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
        ]
    )
    signals = [SignalType.HOLD.value, SignalType.BUY.value]
    engine = BacktestEngine(ScriptedStrategy(signals), _configuration(), data)
    result = engine.run()
    assert result.trades == []


def test_final_portfolio_includes_unallocated_capital() -> None:
    data = _make_ohlcv(
        [
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
            {"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000},
        ]
    )
    signals = [SignalType.HOLD.value, SignalType.HOLD.value]
    engine = BacktestEngine(
        ScriptedStrategy(signals),
        _configuration(starting_capital=10_000.0, allocation=6_000.0),
        data,
    )
    result = engine.run()
    assert result.final_value == pytest.approx(10_000.0)

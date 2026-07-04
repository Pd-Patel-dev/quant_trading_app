"""Tests for the moving-average crossover strategy."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from core.exceptions import StrategyError
from core.models import SignalType
from strategies.moving_average import MovingAverageCrossoverStrategy


def _make_price_series(closes: list[float], start: datetime | None = None) -> pd.DataFrame:
    start = start or datetime(2020, 1, 1)
    index = pd.date_range(start=start, periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=index,
    )


def test_invalid_short_window_raises_error() -> None:
    with pytest.raises(StrategyError):
        MovingAverageCrossoverStrategy(short_window=1, long_window=5)


def test_invalid_long_window_raises_error() -> None:
    with pytest.raises(StrategyError):
        MovingAverageCrossoverStrategy(short_window=50, long_window=50)


def test_indicators_have_expected_columns() -> None:
    data = _make_price_series([float(i) for i in range(1, 30)])
    strategy = MovingAverageCrossoverStrategy(short_window=3, long_window=5)
    result = strategy.calculate_indicators(data)
    assert "SMA_Short" in result.columns
    assert "SMA_Long" in result.columns


def test_no_signal_before_both_averages_available() -> None:
    data = _make_price_series([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0])
    strategy = MovingAverageCrossoverStrategy(short_window=3, long_window=5)
    result = strategy.generate_signals(data)
    early = result.iloc[:4]
    assert (early["Signal"] == SignalType.HOLD.value).all()
    assert (early["Position"] == 0).all()


def test_correct_buy_crossover() -> None:
    # Decline then rise to force a golden cross with short=2, long=3
    closes = [10.0, 9.0, 8.0, 9.0, 11.0, 13.0, 15.0, 17.0]
    data = _make_price_series(closes)
    strategy = MovingAverageCrossoverStrategy(short_window=2, long_window=3)
    result = strategy.generate_signals(data)

    buy_rows = result[result["Signal"] == SignalType.BUY.value]
    assert not buy_rows.empty
    assert buy_rows.index[0] == result.index[4]


def test_correct_sell_crossover() -> None:
    closes = [10.0, 12.0, 14.0, 16.0, 18.0, 16.0, 14.0, 12.0, 10.0]
    data = _make_price_series(closes)
    strategy = MovingAverageCrossoverStrategy(short_window=2, long_window=3)
    result = strategy.generate_signals(data)

    sell_rows = result[result["Signal"] == SignalType.SELL.value]
    assert not sell_rows.empty


def test_no_repeated_buy_while_above_long_average() -> None:
    closes = [10.0, 9.0, 8.0, 9.0, 11.0, 13.0, 15.0, 17.0, 19.0, 21.0, 23.0]
    data = _make_price_series(closes)
    strategy = MovingAverageCrossoverStrategy(short_window=2, long_window=3)
    result = strategy.generate_signals(data)
    buy_count = (result["Signal"] == SignalType.BUY.value).sum()
    assert buy_count == 1


def test_input_dataframe_is_not_mutated() -> None:
    data = _make_price_series([float(i) for i in range(1, 20)])
    original_columns = list(data.columns)
    strategy = MovingAverageCrossoverStrategy(short_window=3, long_window=5)
    strategy.generate_signals(data)
    assert list(data.columns) == original_columns
    assert "Signal" not in data.columns

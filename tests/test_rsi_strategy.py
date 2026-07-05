"""RSI indicator and strategy tests."""

import pandas as pd
import pytest

from core.exceptions import StrategyError
from core.models import SignalType
from strategies.indicators.rsi import calculate_rsi
from strategies.rsi_mean_reversion import RSIMeanReversionStrategy


def _closes(values: list[float]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(values), freq="D")
    return pd.DataFrame(
        {"Open": values, "High": values, "Low": values, "Close": values, "Volume": [1000] * len(values)},
        index=index,
    )


def test_rsi_between_zero_and_one_hundred() -> None:
    data = _closes([100 + i for i in range(50)])
    rsi = calculate_rsi(data["Close"], 14)
    valid = rsi.dropna()
    assert valid.min() >= 0
    assert valid.max() <= 100


def test_rising_prices_high_rsi() -> None:
    data = _closes([100 + i * 2 for i in range(40)])
    rsi = calculate_rsi(data["Close"], 14).dropna()
    assert float(rsi.iloc[-1]) > 50


def test_falling_prices_low_rsi() -> None:
    data = _closes([200 - i * 2 for i in range(40)])
    rsi = calculate_rsi(data["Close"], 14).dropna()
    assert float(rsi.iloc[-1]) < 50


def test_constant_prices_handled() -> None:
    data = _closes([100.0] * 30)
    rsi = calculate_rsi(data["Close"], 14)
    assert rsi.isna().any()


def test_original_dataframe_unchanged() -> None:
    data = _closes([100 + i for i in range(30)])
    original = data.copy()
    calculate_rsi(data["Close"], 14)
    pd.testing.assert_frame_equal(data, original)


def test_buy_recovery_crossover() -> None:
    values = [100.0] * 20
    values.extend([99 - i * 2 for i in range(10)])
    values.extend([80 + i * 3 for i in range(10)])
    data = _closes(values)
    strategy = RSIMeanReversionStrategy(rsi_period=5, oversold_threshold=30, exit_threshold=55, overbought_threshold=70)
    processed = strategy.generate_signals(data)
    buys = processed[processed["Signal"] == SignalType.BUY.value]
    assert not buys.empty


def test_no_repeated_buy_signals() -> None:
    values = [100 + i * 0.5 for i in range(60)]
    data = _closes(values)
    strategy = RSIMeanReversionStrategy()
    processed = strategy.generate_signals(data)
    buy_rows = processed[processed["Signal"] == SignalType.BUY.value]
    assert len(buy_rows) <= processed["Position"].max() + 1


def test_long_only_positions() -> None:
    data = _closes([100 + (i % 5) for i in range(80)])
    strategy = RSIMeanReversionStrategy()
    processed = strategy.generate_signals(data)
    assert set(processed["Position"].unique()).issubset({0, 1})


def test_invalid_thresholds_rejected() -> None:
    with pytest.raises(StrategyError):
        RSIMeanReversionStrategy(exit_threshold=25.0)


def test_deterministic_calculation() -> None:
    data = _closes([100 + i * 0.3 for i in range(80)])
    s1 = RSIMeanReversionStrategy().generate_signals(data)
    s2 = RSIMeanReversionStrategy().generate_signals(data)
    pd.testing.assert_frame_equal(s1, s2)


def test_signal_explanation_present() -> None:
    data = _closes([100 - i for i in range(30)] + [70 + i for i in range(30)])
    strategy = RSIMeanReversionStrategy(rsi_period=5)
    evaluation = strategy.get_current_evaluation(data)
    assert evaluation.explanation

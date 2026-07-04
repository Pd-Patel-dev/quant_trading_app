"""Moving-average crossover strategy."""

from __future__ import annotations

import pandas as pd

from core.exceptions import StrategyError
from core.models import SignalType
from strategies.base_strategy import BaseStrategy


class MovingAverageCrossoverStrategy(BaseStrategy):
    """Golden-cross / death-cross trend-following strategy."""

    def __init__(self, short_window: int = 50, long_window: int = 200) -> None:
        if not isinstance(short_window, int) or not isinstance(long_window, int):
            raise StrategyError("Moving-average windows must be integers.")
        if short_window < 2:
            raise StrategyError("Short window must be at least 2.")
        if long_window <= short_window:
            raise StrategyError("Long window must be greater than the short window.")
        self._short_window = short_window
        self._long_window = long_window

    @property
    def name(self) -> str:
        return f"Moving Average Crossover ({self._short_window}/{self._long_window})"

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        self.validate_input_data(data)
        result = data.copy()
        result["SMA_Short"] = result["Close"].rolling(self._short_window).mean()
        result["SMA_Long"] = result["Close"].rolling(self._long_window).mean()
        return result

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        self.validate_input_data(data)
        result = self.calculate_indicators(data)

        short_sma = result["SMA_Short"]
        long_sma = result["SMA_Long"]
        prev_short = short_sma.shift(1)
        prev_long = long_sma.shift(1)

        both_valid = short_sma.notna() & long_sma.notna() & prev_short.notna() & prev_long.notna()

        position = (short_sma > long_sma).astype(int)
        position = position.where(both_valid, 0).astype(int)
        result["Position"] = position
        result["PositionChange"] = result["Position"].diff().fillna(0).astype(int)

        buy_cross = both_valid & (prev_short <= prev_long) & (short_sma > long_sma)
        sell_cross = both_valid & (prev_short >= prev_long) & (short_sma < long_sma)

        signals = pd.Series(SignalType.HOLD.value, index=result.index, dtype=object)
        signals.loc[buy_cross] = SignalType.BUY.value
        signals.loc[sell_cross] = SignalType.SELL.value
        result["Signal"] = signals

        return result

"""Moving-average crossover strategy."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.exceptions import StrategyError
from core.models import SignalType
from strategies.base_strategy import BaseStrategy
from strategies.metadata import (
    ParameterType,
    StrategyCategory,
    StrategyMetadata,
    StrategyParameterDefinition,
)


class MovingAverageCrossoverStrategy(BaseStrategy):
    """Golden-cross / death-cross trend-following strategy."""

    STRATEGY_TYPE = "moving_average_crossover"

    def __init__(self, short_window: int = 50, long_window: int = 200) -> None:
        self._short_window = short_window
        self._long_window = long_window
        self.validate_parameters()

    @property
    def name(self) -> str:
        return f"Moving Average Crossover ({self._short_window}/{self._long_window})"

    @property
    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            strategy_type=self.STRATEGY_TYPE,
            display_name="Moving Average Crossover",
            description=(
                "Trend-following strategy that buys when the short moving average crosses above "
                "the long moving average and sells on the reverse crossover."
            ),
            category=StrategyCategory.TREND_FOLLOWING,
            version="1.0",
            minimum_history_bars=self._long_window + 1,
            supported_timeframes=("1Day",),
            supports_backtesting=True,
            supports_manual_paper_trading=True,
            supports_automated_paper_trading=True,
            default_parameters={"short_window": 50, "long_window": 200},
            parameter_definitions=(
                StrategyParameterDefinition(
                    name="short_window",
                    display_name="Short MA Window",
                    description="Number of days for the short simple moving average.",
                    parameter_type=ParameterType.INTEGER,
                    default_value=50,
                    minimum_value=2,
                    maximum_value=500,
                    step=1,
                ),
                StrategyParameterDefinition(
                    name="long_window",
                    display_name="Long MA Window",
                    description="Number of days for the long simple moving average.",
                    parameter_type=ParameterType.INTEGER,
                    default_value=200,
                    minimum_value=3,
                    maximum_value=1000,
                    step=1,
                ),
            ),
            risk_notes=(
                "Trend-following strategies may underperform in sideways markets and can "
                "experience extended drawdowns during reversals."
            ),
        )

    def validate_parameters(self) -> None:
        if not isinstance(self._short_window, int) or not isinstance(self._long_window, int):
            raise StrategyError("Moving-average windows must be integers.")
        if self._short_window < 2:
            raise StrategyError("Short window must be at least 2.")
        if self._long_window <= self._short_window:
            raise StrategyError("Long window must be greater than the short window.")

    @classmethod
    def from_parameters(cls, parameters: dict[str, Any]) -> MovingAverageCrossoverStrategy:
        return cls(
            short_window=int(parameters["short_window"]),
            long_window=int(parameters["long_window"]),
        )

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
        reasons = pd.Series(None, index=result.index, dtype=object)

        signals.loc[buy_cross] = SignalType.BUY.value
        reasons.loc[buy_cross] = (
            f"The {self._short_window}-day moving average crossed above "
            f"the {self._long_window}-day moving average."
        )
        signals.loc[sell_cross] = SignalType.SELL.value
        reasons.loc[sell_cross] = (
            f"The {self._short_window}-day moving average crossed below "
            f"the {self._long_window}-day moving average."
        )

        result["Signal"] = signals
        result["SignalReason"] = reasons
        return result

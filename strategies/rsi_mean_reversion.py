"""RSI mean-reversion strategy (long-only)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.exceptions import StrategyError
from core.models import SignalType
from strategies.base_strategy import BaseStrategy
from strategies.indicators.rsi import calculate_rsi
from strategies.metadata import (
    ParameterType,
    StrategyCategory,
    StrategyMetadata,
    StrategyParameterDefinition,
)


class RSIMeanReversionStrategy(BaseStrategy):
    """Long-only RSI recovery entry with threshold-based exit."""

    STRATEGY_TYPE = "rsi_mean_reversion"

    def __init__(
        self,
        rsi_period: int = 14,
        oversold_threshold: float = 30.0,
        exit_threshold: float = 55.0,
        overbought_threshold: float = 70.0,
    ) -> None:
        self._rsi_period = rsi_period
        self._oversold = oversold_threshold
        self._exit = exit_threshold
        self._overbought = overbought_threshold
        self.validate_parameters()

    @property
    def name(self) -> str:
        return (
            f"RSI Mean Reversion ({self._rsi_period}, "
            f"os={self._oversold}, exit={self._exit})"
        )

    @property
    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            strategy_type=self.STRATEGY_TYPE,
            display_name="RSI Mean Reversion",
            description=(
                "Long-only mean-reversion strategy. Buys when RSI recovers upward after "
                "being oversold. Sells when RSI rises to the exit threshold. "
                "The overbought threshold is shown for research only and does not create shorts."
            ),
            category=StrategyCategory.MEAN_REVERSION,
            version="1.0",
            minimum_history_bars=self._rsi_period + 2,
            supported_timeframes=("1Day",),
            supports_backtesting=True,
            supports_manual_paper_trading=True,
            supports_automated_paper_trading=False,
            default_parameters={
                "rsi_period": 14,
                "oversold_threshold": 30.0,
                "exit_threshold": 55.0,
                "overbought_threshold": 70.0,
            },
            parameter_definitions=(
                StrategyParameterDefinition(
                    name="rsi_period",
                    display_name="RSI Period",
                    description="Lookback period for Wilder-style RSI calculation.",
                    parameter_type=ParameterType.INTEGER,
                    default_value=14,
                    minimum_value=2,
                    maximum_value=100,
                    step=1,
                ),
                StrategyParameterDefinition(
                    name="oversold_threshold",
                    display_name="Oversold Threshold",
                    description="RSI level considered oversold for recovery entry.",
                    parameter_type=ParameterType.FLOAT,
                    default_value=30.0,
                    minimum_value=1.0,
                    maximum_value=49.0,
                    step=0.5,
                ),
                StrategyParameterDefinition(
                    name="exit_threshold",
                    display_name="Exit Threshold",
                    description="RSI level that triggers a full exit from a long position.",
                    parameter_type=ParameterType.FLOAT,
                    default_value=55.0,
                    minimum_value=2.0,
                    maximum_value=98.0,
                    step=0.5,
                ),
                StrategyParameterDefinition(
                    name="overbought_threshold",
                    display_name="Overbought Threshold (research only)",
                    description="Displayed on charts; does not trigger short orders.",
                    parameter_type=ParameterType.FLOAT,
                    default_value=70.0,
                    minimum_value=51.0,
                    maximum_value=99.0,
                    step=0.5,
                ),
            ),
            risk_notes=(
                "Mean-reversion strategies can fail during strong trends. RSI recovery entries "
                "may enter before a sustained rebound. This strategy is long-only."
            ),
        )

    def validate_parameters(self) -> None:
        if not isinstance(self._rsi_period, int) or self._rsi_period < 2:
            raise StrategyError("RSI period must be an integer of at least 2.")
        if not (1 <= self._oversold <= 49):
            raise StrategyError("Oversold threshold must be between 1 and 49.")
        if not (51 <= self._overbought <= 99):
            raise StrategyError("Overbought threshold must be between 51 and 99.")
        if self._exit <= self._oversold:
            raise StrategyError("Exit threshold must be greater than the oversold threshold.")
        if self._exit >= self._overbought:
            raise StrategyError("Exit threshold must be below the overbought threshold.")

    @classmethod
    def from_parameters(cls, parameters: dict[str, Any]) -> RSIMeanReversionStrategy:
        return cls(
            rsi_period=int(parameters["rsi_period"]),
            oversold_threshold=float(parameters["oversold_threshold"]),
            exit_threshold=float(parameters["exit_threshold"]),
            overbought_threshold=float(parameters["overbought_threshold"]),
        )

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        self.validate_input_data(data)
        result = data.copy()
        result["RSI"] = calculate_rsi(result["Close"], self._rsi_period)
        return result

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        self.validate_input_data(data)
        result = self.calculate_indicators(data)
        rsi = result["RSI"]
        prev_rsi = rsi.shift(1)
        valid = rsi.notna() & prev_rsi.notna()

        position = pd.Series(0, index=result.index, dtype=int)
        signals = pd.Series(SignalType.HOLD.value, index=result.index, dtype=object)
        reasons = pd.Series(None, index=result.index, dtype=object)

        for i in range(len(result)):
            if not valid.iloc[i]:
                continue
            cur_rsi = float(rsi.iloc[i])
            prev = float(prev_rsi.iloc[i])
            prev_pos = int(position.iloc[i - 1]) if i > 0 else 0

            buy_signal = prev <= self._oversold and cur_rsi > self._oversold and prev_pos == 0
            sell_signal = prev < self._exit and cur_rsi >= self._exit and prev_pos == 1

            if buy_signal:
                position.iloc[i] = 1
                signals.iloc[i] = SignalType.BUY.value
                reasons.iloc[i] = (
                    f"RSI recovered from {prev:.1f} to {cur_rsi:.1f} and crossed above "
                    f"the oversold threshold of {self._oversold:.1f}."
                )
            elif sell_signal:
                position.iloc[i] = 0
                signals.iloc[i] = SignalType.SELL.value
                reasons.iloc[i] = (
                    f"RSI rose from {prev:.1f} to {cur_rsi:.1f} and reached the "
                    f"exit threshold of {self._exit:.1f}."
                )
            else:
                position.iloc[i] = prev_pos

        result["Position"] = position.astype(int)
        result["PositionChange"] = result["Position"].diff().fillna(0).astype(int)
        result["Signal"] = signals
        result["SignalReason"] = reasons
        return result

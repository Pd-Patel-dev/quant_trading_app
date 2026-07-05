"""Crypto daily EMA trend-following strategy."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pandas as pd

from core.exceptions import StrategyError
from core.models import SignalType, to_decimal
from risk.position_sizing import FixedRiskPositionSizer
from risk.risk_overlay import StrategyRiskOverlay
from risk.stop_loss import PercentageStopLoss
from strategies.base_strategy import BaseStrategy
from strategies.metadata import (
    ParameterType,
    StrategyCategory,
    StrategyMetadata,
    StrategyParameterDefinition,
)

SUPPORTED_SYMBOLS = ("BTC/USD", "ETH/USD")
MINIMUM_HISTORY_BARS = 250

SIGNAL_REASON_BUY = "EMA_BULLISH_CROSS_WITH_LONG_TERM_FILTER"
SIGNAL_REASON_SELL = "EMA_BEARISH_CROSS"
SIGNAL_REASON_STOP = "STOP_LOSS"
SIGNAL_REASON_HOLD = "HOLD"


class CryptoEMATrendFollowingStrategy(BaseStrategy):
    """Long-only crypto EMA crossover with long-term filter and risk overlay."""

    STRATEGY_TYPE = "crypto_ema_trend_following"

    def __init__(
        self,
        fast_ema_period: int = 20,
        medium_ema_period: int = 50,
        long_ema_period: int = 200,
        stop_loss_percent: Decimal | float = Decimal("0.08"),
        risk_per_trade_percent: Decimal | float = Decimal("0.01"),
        symbol: str = "BTC/USD",
    ) -> None:
        self._fast = fast_ema_period
        self._medium = medium_ema_period
        self._long = long_ema_period
        self._stop_loss_percent = to_decimal(stop_loss_percent)
        self._risk_per_trade_percent = to_decimal(risk_per_trade_percent)
        self._symbol = symbol.upper().replace("-", "/")
        self.validate_parameters()

    @property
    def name(self) -> str:
        return (
            f"Crypto Daily EMA Trend Following "
            f"({self._fast}/{self._medium}/{self._long})"
        )

    @property
    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            strategy_type=self.STRATEGY_TYPE,
            display_name="Crypto Daily EMA Trend Following",
            description=(
                "A long-only cryptocurrency trend-following strategy that buys after the "
                "20-day EMA crosses above the 50-day EMA while price is above the 200-day "
                "EMA. It exits after a bearish EMA crossover or an 8% close-based stop-loss."
            ),
            category=StrategyCategory.TREND_FOLLOWING,
            version="1.0",
            minimum_history_bars=MINIMUM_HISTORY_BARS,
            supported_timeframes=("Daily", "1Day"),
            supports_backtesting=True,
            supports_manual_paper_trading=True,
            supports_automated_paper_trading=True,
            default_parameters={
                "fast_ema_period": 20,
                "medium_ema_period": 50,
                "long_ema_period": 200,
                "stop_loss_percent": 0.08,
                "risk_per_trade_percent": 0.01,
            },
            parameter_definitions=(
                StrategyParameterDefinition(
                    name="fast_ema_period",
                    display_name="Fast EMA Period",
                    description="Fast exponential moving average period (days).",
                    parameter_type=ParameterType.INTEGER,
                    default_value=20,
                    minimum_value=2,
                    maximum_value=100,
                    step=1,
                ),
                StrategyParameterDefinition(
                    name="medium_ema_period",
                    display_name="Medium EMA Period",
                    description="Medium EMA period; must exceed fast EMA.",
                    parameter_type=ParameterType.INTEGER,
                    default_value=50,
                    minimum_value=3,
                    maximum_value=300,
                    step=1,
                ),
                StrategyParameterDefinition(
                    name="long_ema_period",
                    display_name="Long EMA Period",
                    description="Long-term EMA filter period.",
                    parameter_type=ParameterType.INTEGER,
                    default_value=200,
                    minimum_value=4,
                    maximum_value=500,
                    step=1,
                ),
                StrategyParameterDefinition(
                    name="stop_loss_percent",
                    display_name="Stop-Loss %",
                    description="Daily close-based stop from actual entry price (e.g. 0.08 = 8%).",
                    parameter_type=ParameterType.FLOAT,
                    default_value=0.08,
                    minimum_value=0.001,
                    maximum_value=0.5,
                    step=0.01,
                ),
                StrategyParameterDefinition(
                    name="risk_per_trade_percent",
                    display_name="Risk Per Trade %",
                    description="Fraction of strategy equity risked per trade (e.g. 0.01 = 1%).",
                    parameter_type=ParameterType.FLOAT,
                    default_value=0.01,
                    minimum_value=0.001,
                    maximum_value=0.02,
                    step=0.001,
                ),
            ),
            risk_notes=(
                "No strategy is guaranteed to be profitable. Crypto can experience severe "
                "drawdowns. Sideways markets may create repeated losing trades. The stop-loss "
                "is evaluated using completed daily candles. Next-bar execution can produce a "
                "loss larger than the configured stop percentage. Paper results may differ from "
                "live-market results."
            ),
            asset_type="CRYPTO",
            supported_symbols=SUPPORTED_SYMBOLS,
            long_only=True,
            supports_leverage=False,
            risk_model_type="fixed_risk_entry_stop",
        )

    def validate_parameters(self) -> None:
        for name, value in (
            ("fast_ema_period", self._fast),
            ("medium_ema_period", self._medium),
            ("long_ema_period", self._long),
        ):
            if not isinstance(value, int):
                raise StrategyError(f"{name} must be an integer.")
        if self._fast < 2:
            raise StrategyError("Fast EMA must be an integer of at least 2.")
        if self._medium <= self._fast:
            raise StrategyError("Medium EMA must be greater than Fast EMA.")
        if self._long <= self._medium:
            raise StrategyError("Long EMA must be greater than Medium EMA.")
        stop = self._stop_loss_percent
        if stop <= 0 or stop > Decimal("0.5"):
            raise StrategyError("Stop-loss percentage must be greater than 0 and no more than 50%.")
        risk = self._risk_per_trade_percent
        if risk <= 0 or risk > Decimal("0.02"):
            raise StrategyError("Risk per trade must be greater than 0 and no more than 2%.")
        if self._symbol and self._symbol not in SUPPORTED_SYMBOLS:
            raise StrategyError(f"Symbol must be one of: {', '.join(SUPPORTED_SYMBOLS)}.")

    @classmethod
    def from_parameters(cls, parameters: dict[str, Any]) -> CryptoEMATrendFollowingStrategy:
        return cls(
            fast_ema_period=int(parameters["fast_ema_period"]),
            medium_ema_period=int(parameters["medium_ema_period"]),
            long_ema_period=int(parameters["long_ema_period"]),
            stop_loss_percent=to_decimal(parameters.get("stop_loss_percent", "0.08")),
            risk_per_trade_percent=to_decimal(parameters.get("risk_per_trade_percent", "0.01")),
            symbol=str(parameters.get("symbol", "BTC/USD")),
        )

    def get_risk_overlay(self) -> StrategyRiskOverlay:
        return StrategyRiskOverlay(
            position_sizer=FixedRiskPositionSizer(
                self._risk_per_trade_percent,
                self._stop_loss_percent,
            ),
            stop_loss=PercentageStopLoss(self._stop_loss_percent),
        )

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        self.validate_input_data(data)
        result = data.copy()
        if not result.index.is_monotonic_increasing:
            result = result.sort_index()
        close = pd.to_numeric(result["Close"], errors="coerce")
        result["EMA_Fast"] = close.ewm(span=self._fast, adjust=False).mean()
        result["EMA_Medium"] = close.ewm(span=self._medium, adjust=False).mean()
        result["EMA_Long"] = close.ewm(span=self._long, adjust=False).mean()
        if self._fast == 20:
            result["EMA_20"] = result["EMA_Fast"]
        if self._medium == 50:
            result["EMA_50"] = result["EMA_Medium"]
        if self._long == 200:
            result["EMA_200"] = result["EMA_Long"]
        return result

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        self.validate_input_data(data)
        result = self.calculate_indicators(data)
        close = pd.to_numeric(result["Close"], errors="coerce")

        if len(result) < MINIMUM_HISTORY_BARS:
            result["TrendFilterPassed"] = 0
            result["DesiredPosition"] = 0
            result["Position"] = 0
            result["PositionChange"] = 0
            result["Signal"] = SignalType.HOLD.value
            result["SignalReason"] = SIGNAL_REASON_HOLD
            return result

        ema_fast = result["EMA_Fast"]
        ema_medium = result["EMA_Medium"]
        ema_long = result["EMA_Long"]
        prev_fast = ema_fast.shift(1)
        prev_medium = ema_medium.shift(1)

        all_valid = (
            ema_fast.notna()
            & ema_medium.notna()
            & ema_long.notna()
            & prev_fast.notna()
            & prev_medium.notna()
            & close.notna()
        )
        enough_history = all_valid.copy()
        if len(result) >= MINIMUM_HISTORY_BARS:
            enough_history.iloc[: MINIMUM_HISTORY_BARS - 1] = False

        buy_crossover = (ema_fast > ema_medium) & (prev_fast <= prev_medium)
        long_term_filter = close > ema_long
        bearish_crossover = (ema_fast < ema_medium) & (prev_fast >= prev_medium)

        result["TrendFilterPassed"] = long_term_filter.astype(int)
        result["DesiredPosition"] = 0
        result["Position"] = 0
        result["PositionChange"] = 0
        result["Signal"] = SignalType.HOLD.value
        result["SignalReason"] = SIGNAL_REASON_HOLD

        in_position = False
        for idx in result.index:
            if not bool(enough_history.loc[idx]):
                continue

            if not in_position:
                if bool(buy_crossover.loc[idx]) and bool(long_term_filter.loc[idx]):
                    result.at[idx, "DesiredPosition"] = 1
                    result.at[idx, "Position"] = 1
                    result.at[idx, "PositionChange"] = 1
                    result.at[idx, "Signal"] = SignalType.BUY.value
                    result.at[idx, "SignalReason"] = SIGNAL_REASON_BUY
                    in_position = True
            elif bool(bearish_crossover.loc[idx]):
                result.at[idx, "DesiredPosition"] = 0
                result.at[idx, "Position"] = 0
                result.at[idx, "PositionChange"] = -1
                result.at[idx, "Signal"] = SignalType.SELL.value
                result.at[idx, "SignalReason"] = SIGNAL_REASON_SELL
                in_position = False

        return result

    def _extract_indicators(self, row: pd.Series) -> dict[str, Any]:
        indicators: dict[str, Any] = {}
        for key in ("EMA_Fast", "EMA_Medium", "EMA_Long", "Close", "TrendFilterPassed"):
            if key in row.index and pd.notna(row[key]):
                indicators[key] = float(row[key])
        return indicators

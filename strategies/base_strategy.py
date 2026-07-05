"""Abstract base class for trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from core.exceptions import StrategyError
from strategies.metadata import StrategyEvaluation, StrategyMetadata


class BaseStrategy(ABC):
    """Interface that every trading strategy must implement."""

    @property
    @abstractmethod
    def metadata(self) -> StrategyMetadata:
        """Return static strategy metadata."""

    @property
    def name(self) -> str:
        """Human-readable strategy name derived from metadata and parameters."""
        return self.metadata.display_name

    @abstractmethod
    def validate_parameters(self) -> None:
        """Validate strategy-specific parameters."""

    @abstractmethod
    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Add indicator columns to a copy of the input data."""

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """Add position and signal columns to indicator data."""

    def get_current_evaluation(self, data: pd.DataFrame) -> StrategyEvaluation:
        """Evaluate the latest bar using standardized signal columns."""
        processed = self.generate_signals(data)
        if processed.empty:
            raise StrategyError("No data available for evaluation.")

        latest = processed.iloc[-1]
        signal_value = str(latest.get("Signal", "HOLD"))
        from core.models import SignalType

        latest_signal = SignalType(signal_value) if signal_value in SignalType.__members__ else SignalType.HOLD
        position = int(latest.get("Position", 0))
        signal_reason = latest.get("SignalReason")
        if pd.isna(signal_reason):
            signal_reason = None

        signal_ts = None
        data_ts = pd.Timestamp(processed.index[-1]).to_pydatetime()
        if data_ts.tzinfo is not None:
            data_ts = data_ts.replace(tzinfo=None)

        signal_rows = processed[processed["Signal"].isin(["BUY", "SELL"])]
        if not signal_rows.empty:
            signal_ts = pd.Timestamp(signal_rows.index[-1]).to_pydatetime()
            if signal_ts.tzinfo is not None:
                signal_ts = signal_ts.replace(tzinfo=None)

        explanation = str(signal_reason) if signal_reason else "No actionable signal on the latest bar."
        indicators = self._extract_indicators(latest)

        return StrategyEvaluation(
            latest_signal=latest_signal,
            signal_timestamp=signal_ts,
            current_desired_position=position,
            is_actionable=latest_signal in (SignalType.BUY, SignalType.SELL),
            explanation=explanation,
            signal_reason=str(signal_reason) if signal_reason else None,
            indicators=indicators,
            data_timestamp=data_ts,
        )

    def validate_input_data(self, data: pd.DataFrame) -> None:
        """Confirm the input contains a numeric Close column and is not empty."""
        if data is None or data.empty:
            raise StrategyError("Input data is empty.")
        if "Close" not in data.columns:
            raise StrategyError("Input data must contain a 'Close' column.")
        if not pd.api.types.is_numeric_dtype(data["Close"]):
            raise StrategyError("'Close' column must be numeric.")

    @classmethod
    def minimum_history_bars(cls, parameters: dict[str, Any]) -> int:
        """Return minimum bars required for the given parameters."""
        instance = cls.from_parameters(parameters)
        return instance.metadata.minimum_history_bars

    @classmethod
    @abstractmethod
    def from_parameters(cls, parameters: dict[str, Any]) -> BaseStrategy:
        """Construct a strategy from a parameter dictionary."""

    def _extract_indicators(self, row: pd.Series) -> dict[str, Any]:
        """Extract indicator values from the latest row for persistence/display."""
        indicators: dict[str, Any] = {}
        for key in (
            "SMA_Short",
            "SMA_Long",
            "EMA_Fast",
            "EMA_Medium",
            "EMA_Long",
            "RSI",
            "Close",
        ):
            if key in row.index and pd.notna(row[key]):
                indicators[key] = float(row[key])
        return indicators

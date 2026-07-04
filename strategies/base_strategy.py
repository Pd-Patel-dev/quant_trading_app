"""Abstract base class for trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from core.exceptions import StrategyError


class BaseStrategy(ABC):
    """Interface that every trading strategy must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""

    @abstractmethod
    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Add indicator columns to a copy of the input data."""

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """Add position and signal columns to indicator data."""

    def validate_input_data(self, data: pd.DataFrame) -> None:
        """Confirm the input contains a numeric Close column and is not empty."""
        if data is None or data.empty:
            raise StrategyError("Input data is empty.")
        if "Close" not in data.columns:
            raise StrategyError("Input data must contain a 'Close' column.")
        if not pd.api.types.is_numeric_dtype(data["Close"]):
            raise StrategyError("'Close' column must be numeric.")

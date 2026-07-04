"""Core data models for Quant Strategy Lab."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Literal

import pandas as pd

from core.exceptions import ConfigurationError


class SignalType(str, Enum):
    """Trading signal types produced by strategies."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class Trade:
    """Record of a single executed order during a backtest."""

    timestamp: datetime
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int
    execution_price: float
    gross_value: float
    commission: float
    cash_after_trade: float
    position_after_trade: int
    reason: str


@dataclass(frozen=True)
class BacktestConfiguration:
    """User-defined parameters for a single backtest run."""

    symbol: str
    start_date: date
    end_date: date
    starting_capital: float
    allocation: float
    commission: float
    slippage_percent: float
    cash_reserve_percent: float

    def __post_init__(self) -> None:
        if self.starting_capital <= 0:
            raise ConfigurationError("Starting capital must be greater than zero.")
        if self.allocation <= 0:
            raise ConfigurationError("Allocation must be greater than zero.")
        if self.allocation > self.starting_capital:
            raise ConfigurationError("Allocation cannot exceed starting capital.")
        if self.slippage_percent < 0:
            raise ConfigurationError("Slippage cannot be negative.")
        if self.commission < 0:
            raise ConfigurationError("Commission cannot be negative.")
        if not 0 <= self.cash_reserve_percent <= 1:
            raise ConfigurationError("Cash reserve must be between zero and one.")
        if self.start_date >= self.end_date:
            raise ConfigurationError("Start date must be before end date.")


@dataclass
class BacktestResult:
    """Complete output from a backtest run."""

    symbol: str
    strategy_name: str
    starting_capital: float
    final_value: float
    total_return_percent: float
    buy_and_hold_return_percent: float
    total_trades: int
    completed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_percent: float
    maximum_drawdown_percent: float
    annualized_volatility_percent: float
    sharpe_ratio: float
    equity_curve: pd.DataFrame
    processed_data: pd.DataFrame
    trades: list[Trade] = field(default_factory=list)

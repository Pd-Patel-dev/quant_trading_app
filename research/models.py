"""Research evaluation result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd


@dataclass
class EvaluationPeriodResult:
    period_label: str
    start_date: date
    end_date: date
    starting_capital: float
    final_value: float
    total_return_percent: float
    annualized_return_percent: float
    maximum_drawdown_percent: float
    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float
    completed_trades: int
    win_rate_percent: float
    average_holding_period_days: float
    exposure_percent: float


@dataclass
class WalkForwardWindowResult:
    window_number: int
    training_start: date
    training_end: date
    testing_start: date
    testing_end: date
    training_return_percent: float
    testing_return_percent: float
    testing_drawdown_percent: float
    testing_sharpe_ratio: float
    testing_trade_count: int


@dataclass
class WalkForwardResult:
    symbol: str
    strategy_type: str
    strategy_name: str
    windows: list[WalkForwardWindowResult] = field(default_factory=list)
    combined_oos_equity: pd.Series | None = None
    positive_windows: int = 0
    negative_windows: int = 0
    consistency_percent: float = 0.0
    best_testing_return_percent: float = 0.0
    worst_testing_return_percent: float = 0.0
    summary_message: str = ""


@dataclass
class StrategyComparisonResult:
    strategy_name: str
    strategy_type: str
    symbol: str
    starting_capital: float
    allocation: float
    final_value: float
    total_return_percent: float
    annualized_return_percent: float
    buy_and_hold_return_percent: float
    maximum_drawdown_percent: float
    annualized_volatility_percent: float
    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float
    completed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_percent: float
    average_trade_return_percent: float
    average_holding_period_days: float
    exposure_percent: float
    normalized_equity: pd.Series | None = None

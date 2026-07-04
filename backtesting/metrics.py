"""Performance metric calculations for backtests."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from core.models import Trade

TRADING_DAYS_PER_YEAR = 252


def total_return_percent(starting_value: float, final_value: float) -> float:
    """Calculate total return as a percentage."""
    if starting_value <= 0:
        return 0.0
    return ((final_value / starting_value) - 1.0) * 100.0


def buy_and_hold_return_percent(close_prices: pd.Series) -> float:
    """Compare the first valid close with the final valid close."""
    valid = close_prices.dropna()
    if len(valid) < 2:
        return 0.0
    first_close = float(valid.iloc[0])
    last_close = float(valid.iloc[-1])
    if first_close <= 0:
        return 0.0
    return ((last_close / first_close) - 1.0) * 100.0


def maximum_drawdown_percent(equity_curve: pd.Series) -> float:
    """Return the maximum drawdown percentage from an equity curve."""
    if equity_curve.empty:
        return 0.0
    running_max = equity_curve.cummax()
    drawdown = (equity_curve / running_max) - 1.0
    return abs(float(drawdown.min()) * 100.0)


def annualized_volatility_percent(daily_returns: pd.Series) -> float:
    """Annualize daily return volatility."""
    clean = daily_returns.dropna()
    if len(clean) < 2:
        return 0.0
    std = float(clean.std())
    if math.isnan(std) or math.isinf(std):
        return 0.0
    return std * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0


def sharpe_ratio(daily_returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    """Calculate Sharpe ratio using daily portfolio returns."""
    clean = daily_returns.dropna()
    if len(clean) < 2:
        return 0.0
    excess = clean - (risk_free_rate / TRADING_DAYS_PER_YEAR)
    std = float(excess.std())
    if std == 0.0 or math.isnan(std) or math.isinf(std):
        return 0.0
    mean = float(excess.mean())
    if math.isnan(mean) or math.isinf(mean):
        return 0.0
    value = (mean / std) * math.sqrt(TRADING_DAYS_PER_YEAR)
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return float(value)


def count_completed_trades(trades: list[Trade]) -> int:
    """Count completed round trips (one BUY followed by its SELL)."""
    completed = 0
    open_buy: Trade | None = None
    for trade in trades:
        if trade.side == "BUY":
            open_buy = trade
        elif trade.side == "SELL" and open_buy is not None:
            completed += 1
            open_buy = None
    return completed


def count_winning_trades(trades: list[Trade]) -> int:
    """Count completed round trips with positive net cash result."""
    return _count_outcome_trades(trades, winning=True)


def count_losing_trades(trades: list[Trade]) -> int:
    """Count completed round trips with negative net cash result."""
    return _count_outcome_trades(trades, winning=False)


def win_rate_percent(trades: list[Trade]) -> float:
    """Calculate win rate from completed round trips."""
    completed = count_completed_trades(trades)
    if completed == 0:
        return 0.0
    winners = count_winning_trades(trades)
    return (winners / completed) * 100.0


def _count_outcome_trades(trades: list[Trade], winning: bool) -> int:
    count = 0
    open_buy: Trade | None = None
    for trade in trades:
        if trade.side == "BUY":
            open_buy = trade
        elif trade.side == "SELL" and open_buy is not None:
            buy_cost = open_buy.gross_value + open_buy.commission
            sell_proceeds = trade.gross_value - trade.commission
            net = sell_proceeds - buy_cost
            if winning and net > 0:
                count += 1
            elif not winning and net < 0:
                count += 1
            open_buy = None
    return count


def compute_drawdown_series(equity_curve: pd.Series) -> pd.Series:
    """Return drawdown percentage over time."""
    running_max = equity_curve.cummax()
    drawdown = ((equity_curve / running_max) - 1.0) * 100.0
    return drawdown.fillna(0.0)

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


def annualized_return_percent(
    starting_value: float,
    final_value: float,
    calendar_days: int,
) -> float:
    """Annualize total return using calendar days."""
    if starting_value <= 0 or calendar_days <= 0:
        return 0.0
    total_return = (final_value / starting_value) - 1.0
    if calendar_days < 365:
        return total_return * 100.0
    years = calendar_days / 365.25
    if years <= 0:
        return 0.0
    try:
        ann = ((final_value / starting_value) ** (1.0 / years) - 1.0) * 100.0
    except (OverflowError, ValueError):
        return 0.0
    if math.isnan(ann) or math.isinf(ann):
        return 0.0
    return float(ann)


def sortino_ratio(daily_returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    """Sortino ratio using negative daily returns as downside risk."""
    clean = daily_returns.dropna()
    if len(clean) < 2:
        return 0.0
    excess = clean - (risk_free_rate / TRADING_DAYS_PER_YEAR)
    downside = excess[excess < 0]
    if len(downside) == 0:
        return 0.0
    downside_std = float(downside.std())
    if downside_std == 0.0 or math.isnan(downside_std) or math.isinf(downside_std):
        return 0.0
    mean = float(excess.mean())
    if math.isnan(mean) or math.isinf(mean):
        return 0.0
    value = (mean / downside_std) * math.sqrt(TRADING_DAYS_PER_YEAR)
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return float(value)


def profit_factor(trades: list[Trade]) -> float:
    """Gross profits divided by absolute gross losses."""
    gross_profit = 0.0
    gross_loss = 0.0
    open_buy: Trade | None = None
    for trade in trades:
        if trade.side == "BUY":
            open_buy = trade
        elif trade.side == "SELL" and open_buy is not None:
            buy_cost = open_buy.gross_value + open_buy.commission
            sell_proceeds = trade.gross_value - trade.commission
            net = sell_proceeds - buy_cost
            if net > 0:
                gross_profit += net
            elif net < 0:
                gross_loss += abs(net)
            open_buy = None
    if gross_loss == 0.0:
        return gross_profit if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def average_trade_return_percent(trades: list[Trade]) -> float:
    """Average net return percent across completed round trips."""
    returns: list[float] = []
    open_buy: Trade | None = None
    for trade in trades:
        if trade.side == "BUY":
            open_buy = trade
        elif trade.side == "SELL" and open_buy is not None:
            buy_cost = open_buy.gross_value + open_buy.commission
            sell_proceeds = trade.gross_value - trade.commission
            if buy_cost > 0:
                returns.append(((sell_proceeds - buy_cost) / buy_cost) * 100.0)
            open_buy = None
    if not returns:
        return 0.0
    return float(sum(returns) / len(returns))


def average_holding_period_days(trades: list[Trade]) -> float:
    """Average calendar days between paired BUY and SELL executions."""
    periods: list[float] = []
    open_buy: Trade | None = None
    for trade in trades:
        if trade.side == "BUY":
            open_buy = trade
        elif trade.side == "SELL" and open_buy is not None:
            delta = (trade.timestamp - open_buy.timestamp).days
            periods.append(float(max(delta, 0)))
            open_buy = None
    if not periods:
        return 0.0
    return float(sum(periods) / len(periods))


def exposure_percent(position_quantities: pd.Series) -> float:
    """Percentage of days with a long position."""
    if position_quantities.empty:
        return 0.0
    long_days = int((position_quantities > 0).sum())
    return (long_days / len(position_quantities)) * 100.0


def normalized_equity_curve(equity_curve: pd.Series, base: float = 100.0) -> pd.Series:
    """Normalize equity curve to start at a base value."""
    if equity_curve.empty:
        return equity_curve
    start = float(equity_curve.iloc[0])
    if start <= 0:
        return equity_curve * 0.0 + base
    return (equity_curve / start) * base

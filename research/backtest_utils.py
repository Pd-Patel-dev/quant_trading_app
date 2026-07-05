"""Research evaluation utilities."""

from __future__ import annotations

from datetime import date

import pandas as pd

from backtesting import metrics
from backtesting.engine import BacktestEngine
from core.models import BacktestConfiguration, BacktestResult
from research.models import EvaluationPeriodResult, StrategyComparisonResult
from strategies.base_strategy import BaseStrategy


def calendar_days_between(start: date, end: date) -> int:
    return max((end - start).days, 1)


def backtest_result_to_comparison(
    result: BacktestResult,
    strategy_type: str,
    allocation: float,
    start_date: date,
    end_date: date,
) -> StrategyComparisonResult:
    daily_returns = result.equity_curve["DailyReturn"]
    position_qty = result.equity_curve.get("PositionQuantity", result.processed_data.get("Position", pd.Series(0)))
    days = calendar_days_between(start_date, end_date)
    return StrategyComparisonResult(
        strategy_name=result.strategy_name,
        strategy_type=strategy_type,
        symbol=result.symbol,
        starting_capital=result.starting_capital,
        allocation=allocation,
        final_value=result.final_value,
        total_return_percent=result.total_return_percent,
        annualized_return_percent=metrics.annualized_return_percent(
            result.starting_capital, result.final_value, days
        ),
        buy_and_hold_return_percent=result.buy_and_hold_return_percent,
        maximum_drawdown_percent=result.maximum_drawdown_percent,
        annualized_volatility_percent=result.annualized_volatility_percent,
        sharpe_ratio=result.sharpe_ratio,
        sortino_ratio=metrics.sortino_ratio(daily_returns),
        profit_factor=metrics.profit_factor(result.trades),
        completed_trades=result.completed_trades,
        winning_trades=result.winning_trades,
        losing_trades=result.losing_trades,
        win_rate_percent=result.win_rate_percent,
        average_trade_return_percent=metrics.average_trade_return_percent(result.trades),
        average_holding_period_days=metrics.average_holding_period_days(result.trades),
        exposure_percent=metrics.exposure_percent(position_qty),
        normalized_equity=metrics.normalized_equity_curve(result.equity_curve["PortfolioValue"]),
    )


def run_backtest(
    strategy: BaseStrategy,
    configuration: BacktestConfiguration,
    data: pd.DataFrame,
) -> BacktestResult:
    return BacktestEngine(strategy, configuration, data).run()


def period_result_from_backtest(
    label: str,
    result: BacktestResult,
    start_date: date,
    end_date: date,
) -> EvaluationPeriodResult:
    days = calendar_days_between(start_date, end_date)
    daily_returns = result.equity_curve["DailyReturn"]
    position_qty = result.equity_curve.get("PositionQuantity", pd.Series(0, index=result.equity_curve.index))
    return EvaluationPeriodResult(
        period_label=label,
        start_date=start_date,
        end_date=end_date,
        starting_capital=result.starting_capital,
        final_value=result.final_value,
        total_return_percent=result.total_return_percent,
        annualized_return_percent=metrics.annualized_return_percent(
            result.starting_capital, result.final_value, days
        ),
        maximum_drawdown_percent=result.maximum_drawdown_percent,
        sharpe_ratio=result.sharpe_ratio,
        sortino_ratio=metrics.sortino_ratio(daily_returns),
        profit_factor=metrics.profit_factor(result.trades),
        completed_trades=result.completed_trades,
        win_rate_percent=result.win_rate_percent,
        average_holding_period_days=metrics.average_holding_period_days(result.trades),
        exposure_percent=metrics.exposure_percent(position_qty),
    )

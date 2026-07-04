"""Backtesting engine for strategy simulation."""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from backtesting import metrics
from core.exceptions import BacktestError
from core.models import BacktestConfiguration, BacktestResult, SignalType, Trade
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Simulate a strategy against historical OHLCV data."""

    def __init__(
        self,
        strategy: BaseStrategy,
        configuration: BacktestConfiguration,
        data: pd.DataFrame,
    ) -> None:
        self._strategy = strategy
        self._config = configuration
        self._data = data.copy()

    def run(self) -> BacktestResult:
        """Execute the backtest and return a complete result object."""
        required_columns = {"Open", "High", "Low", "Close", "Volume"}
        missing = required_columns - set(self._data.columns)
        if missing:
            raise BacktestError(f"Historical data is missing columns: {sorted(missing)}")
        if self._data.empty:
            raise BacktestError("Historical data is empty.")

        processed = self._strategy.generate_signals(self._data)
        trades, equity_curve = self._simulate(processed)

        starting_capital = self._config.starting_capital
        final_value = float(equity_curve["PortfolioValue"].iloc[-1])
        daily_returns = equity_curve["DailyReturn"]

        return BacktestResult(
            symbol=self._config.symbol.upper(),
            strategy_name=self._strategy.name,
            starting_capital=starting_capital,
            final_value=final_value,
            total_return_percent=metrics.total_return_percent(starting_capital, final_value),
            buy_and_hold_return_percent=metrics.buy_and_hold_return_percent(processed["Close"]),
            total_trades=len(trades),
            completed_trades=metrics.count_completed_trades(trades),
            winning_trades=metrics.count_winning_trades(trades),
            losing_trades=metrics.count_losing_trades(trades),
            win_rate_percent=metrics.win_rate_percent(trades),
            maximum_drawdown_percent=metrics.maximum_drawdown_percent(
                equity_curve["PortfolioValue"]
            ),
            annualized_volatility_percent=metrics.annualized_volatility_percent(daily_returns),
            sharpe_ratio=metrics.sharpe_ratio(daily_returns),
            equity_curve=equity_curve,
            processed_data=processed,
            trades=trades,
        )

    def _simulate(self, processed: pd.DataFrame) -> tuple[list[Trade], pd.DataFrame]:
        """Simulate trades and build the equity curve in one chronological pass."""
        cfg = self._config
        unallocated_cash = cfg.starting_capital - cfg.allocation
        strategy_cash = cfg.allocation
        position_qty = 0
        trades: list[Trade] = []
        rows: list[dict[str, float | int]] = []
        prev_portfolio = cfg.starting_capital

        for i in range(len(processed)):
            if i > 0:
                prev_signal = processed.iloc[i - 1]["Signal"]
                row = processed.iloc[i]
                open_price = float(row["Open"])
                timestamp = _to_timestamp(row.name)

                if prev_signal == SignalType.BUY.value and position_qty == 0:
                    trade = self._execute_buy(
                        timestamp=timestamp,
                        open_price=open_price,
                        strategy_cash=strategy_cash,
                    )
                    if trade is not None:
                        strategy_cash = trade.cash_after_trade - unallocated_cash
                        position_qty = trade.position_after_trade
                        trades.append(trade)

                elif prev_signal == SignalType.SELL.value and position_qty > 0:
                    trade = self._execute_sell(
                        timestamp=timestamp,
                        open_price=open_price,
                        strategy_cash=strategy_cash,
                        unallocated_cash=unallocated_cash,
                        position_qty=position_qty,
                    )
                    strategy_cash = trade.cash_after_trade - unallocated_cash
                    position_qty = trade.position_after_trade
                    trades.append(trade)

            row = processed.iloc[i]
            close_price = float(row["Close"])
            total_cash = unallocated_cash + strategy_cash
            position_value = position_qty * close_price
            portfolio_value = total_cash + position_value
            daily_return = (
                (portfolio_value / prev_portfolio) - 1.0 if prev_portfolio > 0 else 0.0
            )

            rows.append(
                {
                    "Cash": total_cash,
                    "PositionQuantity": position_qty,
                    "Close": close_price,
                    "PositionValue": position_value,
                    "PortfolioValue": portfolio_value,
                    "DailyReturn": daily_return,
                    "Drawdown": 0.0,
                }
            )
            prev_portfolio = portfolio_value

        equity = pd.DataFrame(rows, index=processed.index)
        equity["Drawdown"] = metrics.compute_drawdown_series(equity["PortfolioValue"])
        return trades, equity

    def _execute_buy(
        self,
        timestamp: datetime,
        open_price: float,
        strategy_cash: float,
    ) -> Trade | None:
        cfg = self._config
        execution_price = open_price * (1.0 + cfg.slippage_percent)
        available_cash = strategy_cash * (1.0 - cfg.cash_reserve_percent)
        quantity = int(available_cash // execution_price)

        if quantity <= 0:
            logger.info("Skipping BUY: insufficient cash for one whole share.")
            return None

        gross_value = quantity * execution_price
        total_cost = gross_value + cfg.commission
        if total_cost > strategy_cash:
            return None

        unallocated_cash = cfg.starting_capital - cfg.allocation
        new_strategy_cash = strategy_cash - total_cost
        cash_after = unallocated_cash + new_strategy_cash

        return Trade(
            timestamp=timestamp,
            symbol=cfg.symbol.upper(),
            side="BUY",
            quantity=quantity,
            execution_price=execution_price,
            gross_value=gross_value,
            commission=cfg.commission,
            cash_after_trade=cash_after,
            position_after_trade=quantity,
            reason=f"BUY signal executed at next-day open with {cfg.slippage_percent:.4%} slippage",
        )

    def _execute_sell(
        self,
        timestamp: datetime,
        open_price: float,
        strategy_cash: float,
        unallocated_cash: float,
        position_qty: int,
    ) -> Trade:
        cfg = self._config
        execution_price = open_price * (1.0 - cfg.slippage_percent)
        gross_value = position_qty * execution_price
        proceeds = gross_value - cfg.commission
        new_strategy_cash = strategy_cash + proceeds
        cash_after = unallocated_cash + new_strategy_cash

        return Trade(
            timestamp=timestamp,
            symbol=cfg.symbol.upper(),
            side="SELL",
            quantity=position_qty,
            execution_price=execution_price,
            gross_value=gross_value,
            commission=cfg.commission,
            cash_after_trade=cash_after,
            position_after_trade=0,
            reason=f"SELL signal executed at next-day open with {cfg.slippage_percent:.4%} slippage",
        )


def _to_timestamp(index_value: object) -> datetime:
    """Convert a DataFrame index value to a datetime."""
    ts = pd.Timestamp(index_value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return ts.to_pydatetime()

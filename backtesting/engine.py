"""Backtesting engine for strategy simulation."""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, ROUND_DOWN

import pandas as pd

from backtesting import metrics
from core.exceptions import BacktestError
from core.models import BacktestConfiguration, BacktestResult, SignalType, Trade
from market_data.models import QuantityMode
from strategies.base_strategy import BaseStrategy
from strategies.capabilities import get_risk_overlay, has_risk_overlay
from strategies.crypto_ema_trend_following import (
    SIGNAL_REASON_BUY,
    SIGNAL_REASON_SELL,
    SIGNAL_REASON_STOP,
)

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
        if has_risk_overlay(self._strategy):
            trades, equity_curve, extended = self._simulate_with_risk_overlay(processed)
        else:
            trades, equity_curve = self._simulate(processed)
            extended = {}

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
            extended_metrics=extended,
        )

    def _simulate(self, processed: pd.DataFrame) -> tuple[list[Trade], pd.DataFrame]:
        """Simulate trades and build the equity curve in one chronological pass."""
        cfg = self._config
        unallocated_cash = cfg.starting_capital - cfg.allocation
        strategy_cash = cfg.allocation
        position_qty: int | float = 0
        trades: list[Trade] = []
        rows: list[dict[str, float | int]] = []
        prev_portfolio = cfg.starting_capital

        for i in range(len(processed)):
            if i > 0:
                prev_signal = processed.iloc[i - 1]["Signal"]
                row = processed.iloc[i]
                open_price = float(row["Open"])
                timestamp = _to_timestamp(row.name)

                if prev_signal == SignalType.BUY.value and _is_flat(position_qty):
                    trade = self._execute_buy(
                        timestamp=timestamp,
                        open_price=open_price,
                        strategy_cash=strategy_cash,
                    )
                    if trade is not None:
                        strategy_cash = trade.cash_after_trade - unallocated_cash
                        position_qty = trade.position_after_trade
                        trades.append(trade)

                elif prev_signal == SignalType.SELL.value and not _is_flat(position_qty):
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
            position_value = float(position_qty) * close_price
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
        quantity = self._calculate_buy_quantity(available_cash, execution_price)

        if quantity <= 0:
            logger.info("Skipping BUY: insufficient cash for requested quantity.")
            return None

        gross_value = float(quantity) * execution_price
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
        position_qty: int | float,
    ) -> Trade:
        cfg = self._config
        execution_price = open_price * (1.0 - cfg.slippage_percent)
        gross_value = float(position_qty) * execution_price
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

    def _calculate_buy_quantity(self, available_cash: float, execution_price: float) -> int | float:
        cfg = self._config
        if cfg.quantity_mode == QuantityMode.FRACTIONAL_RESEARCH:
            if execution_price <= 0:
                return 0
            precision = cfg.quantity_precision
            quantizer = Decimal("1").scaleb(-precision)
            raw = Decimal(str(available_cash)) / Decimal(str(execution_price))
            quantity = raw.quantize(quantizer, rounding=ROUND_DOWN)
            if quantity <= 0:
                return 0
            return float(quantity)
        return int(available_cash // execution_price)

    def _simulate_with_risk_overlay(
        self, processed: pd.DataFrame
    ) -> tuple[list[Trade], pd.DataFrame, dict[str, float | int]]:
        """Simulate with risk-based sizing and entry-price daily close stop-loss."""
        cfg = self._config
        overlay = get_risk_overlay(self._strategy)
        unallocated_cash = cfg.starting_capital - cfg.allocation
        strategy_cash = float(cfg.allocation)
        position_qty: float = 0.0
        entry_price: float | None = None
        scheduled: tuple[str, str] | None = None
        trades: list[Trade] = []
        rows: list[dict[str, float | int]] = []
        prev_portfolio = cfg.starting_capital
        stop_loss_exits = 0
        ema_exits = 0
        stop_slippage_impacts: list[float] = []

        for i in range(len(processed)):
            row = processed.iloc[i]
            open_price = float(row["Open"])
            close_price = float(row["Close"])
            timestamp = _to_timestamp(row.name)

            if i > 0 and scheduled is not None:
                action, signal_reason = scheduled
                scheduled = None
                prev_close = float(processed.iloc[i - 1]["Close"])
                strategy_equity = strategy_cash + position_qty * prev_close

                if action == "BUY" and _is_flat(position_qty):
                    trade = self._execute_risk_buy(
                        timestamp=timestamp,
                        open_price=open_price,
                        strategy_cash=strategy_cash,
                        strategy_equity=strategy_equity,
                        overlay=overlay,
                        signal_reason=signal_reason,
                    )
                    if trade is not None:
                        strategy_cash = trade.cash_after_trade - unallocated_cash
                        position_qty = float(trade.position_after_trade)
                        entry_price = trade.execution_price
                        trades.append(trade)

                elif action == "SELL" and not _is_flat(position_qty) and entry_price is not None:
                    if signal_reason == SIGNAL_REASON_STOP:
                        threshold = entry_price * (1.0 - float(overlay.stop_loss.stop_loss_percent))
                        stop_slippage_impacts.append(
                            (threshold - open_price * (1.0 - cfg.slippage_percent)) / threshold * 100.0
                            if threshold > 0
                            else 0.0
                        )
                        stop_loss_exits += 1
                    else:
                        ema_exits += 1
                    trade = self._execute_risk_sell(
                        timestamp=timestamp,
                        open_price=open_price,
                        strategy_cash=strategy_cash,
                        unallocated_cash=unallocated_cash,
                        position_qty=position_qty,
                        signal_reason=signal_reason,
                    )
                    strategy_cash = trade.cash_after_trade - unallocated_cash
                    position_qty = 0.0
                    entry_price = None
                    trades.append(trade)

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
                    "StopPrice": (
                        entry_price * (1.0 - float(overlay.stop_loss.stop_loss_percent))
                        if entry_price is not None
                        else 0.0
                    ),
                }
            )
            prev_portfolio = portfolio_value

            if i + 1 >= len(processed):
                continue

            bar_signal = str(processed.iloc[i]["Signal"])
            bar_reason = str(processed.iloc[i].get("SignalReason", ""))

            if not _is_flat(position_qty) and entry_price is not None:
                stop_eval = overlay.stop_loss.evaluate(
                    Decimal(str(entry_price)), Decimal(str(close_price))
                )
                if stop_eval.triggered:
                    scheduled = ("SELL", SIGNAL_REASON_STOP)
                elif bar_signal == SignalType.SELL.value:
                    scheduled = ("SELL", SIGNAL_REASON_SELL)
            elif _is_flat(position_qty) and bar_signal == SignalType.BUY.value:
                scheduled = ("BUY", SIGNAL_REASON_BUY)

        equity = pd.DataFrame(rows, index=processed.index)
        equity["Drawdown"] = metrics.compute_drawdown_series(equity["PortfolioValue"])
        avg_stop_slippage = (
            sum(stop_slippage_impacts) / len(stop_slippage_impacts)
            if stop_slippage_impacts
            else 0.0
        )
        extended = {
            "stop_loss_exit_count": stop_loss_exits,
            "ema_exit_count": ema_exits,
            "stop_slippage_impact_percent": avg_stop_slippage,
            "average_planned_risk_percent": float(overlay.risk_per_trade_percent) * 100.0,
        }
        return trades, equity, extended

    def _execute_risk_buy(
        self,
        timestamp: datetime,
        open_price: float,
        strategy_cash: float,
        strategy_equity: float,
        overlay: object,
        signal_reason: str,
    ) -> Trade | None:
        cfg = self._config
        execution_price = open_price * (1.0 + cfg.slippage_percent)
        max_notional = cfg.max_order_notional or cfg.allocation
        sizing = overlay.position_sizer.calculate(
            strategy_equity=Decimal(str(strategy_equity)),
            available_cash=Decimal(str(strategy_cash)),
            cash_reserve_percent=Decimal(str(cfg.cash_reserve_percent)),
            strategy_allocation_limit=Decimal(str(cfg.allocation)),
            application_max_order_notional=Decimal(str(max_notional)),
        )
        if sizing.blocking_reasons or sizing.final_notional <= 0:
            return None

        notional = float(sizing.final_notional)
        fee = notional * cfg.crypto_fee_percent
        total_cost = notional + cfg.commission + fee
        if total_cost > strategy_cash:
            return None

        quantity = self._quantity_from_notional(notional, execution_price)
        if quantity <= 0:
            return None

        gross_value = float(quantity) * execution_price
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
            commission=cfg.commission + fee,
            cash_after_trade=cash_after,
            position_after_trade=quantity,
            reason=(
                f"Risk-based BUY at next-day open ({signal_reason}); "
                f"notional={notional:.2f}, slippage={cfg.slippage_percent:.4%}"
            ),
            signal_reason=signal_reason,
        )

    def _execute_risk_sell(
        self,
        timestamp: datetime,
        open_price: float,
        strategy_cash: float,
        unallocated_cash: float,
        position_qty: float,
        signal_reason: str,
    ) -> Trade:
        cfg = self._config
        execution_price = open_price * (1.0 - cfg.slippage_percent)
        gross_value = position_qty * execution_price
        fee = gross_value * cfg.crypto_fee_percent
        proceeds = gross_value - cfg.commission - fee
        new_strategy_cash = strategy_cash + proceeds
        cash_after = unallocated_cash + new_strategy_cash

        return Trade(
            timestamp=timestamp,
            symbol=cfg.symbol.upper(),
            side="SELL",
            quantity=position_qty,
            execution_price=execution_price,
            gross_value=gross_value,
            commission=cfg.commission + fee,
            cash_after_trade=cash_after,
            position_after_trade=0,
            reason=(
                f"Full SELL at next-day open ({signal_reason}); "
                f"slippage={cfg.slippage_percent:.4%}"
            ),
            signal_reason=signal_reason,
        )

    def _quantity_from_notional(self, notional: float, execution_price: float) -> float:
        cfg = self._config
        if execution_price <= 0:
            return 0
        precision = cfg.quantity_precision
        quantizer = Decimal("1").scaleb(-precision)
        raw = Decimal(str(notional)) / Decimal(str(execution_price))
        quantity = raw.quantize(quantizer, rounding=ROUND_DOWN)
        if quantity <= 0:
            return 0
        return float(quantity)


def _is_flat(position_qty: int | float) -> bool:
    return float(position_qty) <= 0


def _to_timestamp(index_value: object) -> datetime:
    """Convert a DataFrame index value to a datetime."""
    ts = pd.Timestamp(index_value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return ts.to_pydatetime()

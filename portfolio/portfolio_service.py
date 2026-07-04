"""Portfolio aggregation and reconciliation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from core.models import (
    StrategyLedgerSummary,
    StrategyPaperPosition,
    StrategyRecord,
    decimal_to_float,
    to_decimal,
)
from data.database import DatabaseManager
from portfolio.allocation_manager import AllocationManager
from portfolio.ledger import StrategyLedger

logger = logging.getLogger(__name__)


class PortfolioService:
    """Aggregate managed strategy portfolio metrics."""

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database
        self._allocation = AllocationManager(database)
        self._ledger = StrategyLedger(database)

    def get_strategy_summary(
        self,
        strategy_id: int,
        market_price: Decimal | None = None,
    ) -> StrategyLedgerSummary:
        strategy = self._db.get_strategy(strategy_id)
        if strategy is None:
            raise ValueError(f"Strategy {strategy_id} not found.")

        position_row = self._db.get_strategy_position(strategy_id, strategy.symbol)
        quantity = int(position_row["quantity"]) if position_row else 0
        cost_basis = to_decimal(position_row["cost_basis"]) if position_row else Decimal("0")
        realized_pl = to_decimal(position_row["realized_profit_loss"]) if position_row else Decimal("0")
        avg_entry = to_decimal(position_row["average_entry_price"]) if position_row else Decimal("0")

        if market_price is None:
            market_price = avg_entry if quantity > 0 else Decimal("0")

        market_value = to_decimal(quantity) * market_price
        unrealized_pl = market_value - cost_basis if quantity > 0 else Decimal("0")
        available = self._ledger.get_available_cash(strategy_id)
        reserved = self._ledger.get_reserved_cash(strategy_id)
        current_value = available + reserved + market_value
        total_pl = realized_pl + unrealized_pl
        allocated = strategy.allocated_funds
        return_pct = (
            (total_pl / allocated * Decimal("100")) if allocated > 0 else Decimal("0")
        )

        return StrategyLedgerSummary(
            strategy_id=strategy_id,
            allocated_funds=allocated,
            available_cash=available,
            reserved_cash=reserved,
            invested_value=cost_basis,
            current_value=current_value,
            realized_profit_loss=realized_pl,
            unrealized_profit_loss=unrealized_pl,
            total_profit_loss=total_pl,
            total_return_percent=return_pct,
        )

    def get_strategy_position_view(
        self,
        strategy: StrategyRecord,
        market_price: Decimal,
    ) -> StrategyPaperPosition | None:
        row = self._db.get_strategy_position(strategy.id, strategy.symbol)
        if not row or row["quantity"] <= 0:
            return None
        quantity = int(row["quantity"])
        cost_basis = to_decimal(row["cost_basis"])
        avg_entry = to_decimal(row["average_entry_price"])
        market_value = to_decimal(quantity) * market_price
        return StrategyPaperPosition(
            strategy_id=strategy.id,
            symbol=strategy.symbol,
            quantity=quantity,
            average_entry_price=avg_entry,
            cost_basis=cost_basis,
            market_price=market_price,
            market_value=market_value,
            unrealized_profit_loss=market_value - cost_basis,
            updated_at=datetime.now(timezone.utc),
        )

    def get_managed_totals(self) -> dict[str, Decimal]:
        total_allocated = self._allocation.get_total_allocated()
        total_available = Decimal("0")
        total_reserved = Decimal("0")
        total_positions_value = Decimal("0")
        total_realized = Decimal("0")
        total_unrealized = Decimal("0")

        for strategy in self._db.list_strategies():
            summary = self.get_strategy_summary(strategy.id)
            total_available += summary.available_cash
            total_reserved += summary.reserved_cash
            total_positions_value += summary.invested_value
            total_realized += summary.realized_profit_loss
            total_unrealized += summary.unrealized_profit_loss

        return {
            "total_allocated": total_allocated,
            "unallocated": self._allocation.get_unallocated_capital(),
            "managed_cash": total_available,
            "reserved_cash": total_reserved,
            "positions_value": total_positions_value,
            "managed_portfolio_value": total_available + total_reserved + total_positions_value,
            "realized_pl": total_realized,
            "unrealized_pl": total_unrealized,
            "total_pl": total_realized + total_unrealized,
        }

    def list_managed_positions(self) -> list[dict]:
        """Return all locally tracked strategy positions with quantity > 0."""
        return self._db.list_strategy_positions()

    def get_reconciliation_warnings(
        self,
        alpaca_positions: dict[str, int],
    ) -> list[str]:
        warnings: list[str] = []
        managed_by_symbol: dict[str, int] = {}

        for pos in self._db.list_strategy_positions():
            symbol = pos["symbol"]
            managed_by_symbol[symbol] = managed_by_symbol.get(symbol, 0) + int(pos["quantity"])

        for symbol, local_qty in managed_by_symbol.items():
            broker_qty = alpaca_positions.get(symbol, 0)
            if local_qty > broker_qty:
                warnings.append(
                    f"Local managed quantity for {symbol} ({local_qty}) exceeds Alpaca position ({broker_qty})."
                )
            elif broker_qty > local_qty:
                warnings.append(
                    f"Alpaca holds {broker_qty - local_qty} unmanaged share(s) of {symbol}."
                )

        for symbol, broker_qty in alpaca_positions.items():
            if symbol not in managed_by_symbol and broker_qty > 0:
                warnings.append(
                    f"Alpaca position in {symbol} ({broker_qty} shares) has no local strategy owner."
                )

        if self._db.count_unknown_orders() > 0:
            warnings.append("One or more orders remain in UNKNOWN status.")

        if self._allocation.get_total_allocated() > self._allocation.capital_pool:
            warnings.append("Total allocated funds exceed the configured local paper capital pool.")

        active_symbols: dict[str, int] = {}
        for strategy in self._db.list_strategies():
            if strategy.status.value == "ACTIVE":
                active_symbols[strategy.symbol] = active_symbols.get(strategy.symbol, 0) + 1
        for symbol, count in active_symbols.items():
            if count > 1:
                warnings.append(f"Multiple active strategies found for symbol {symbol}.")

        return warnings

    @staticmethod
    def format_summary_for_ui(summary: StrategyLedgerSummary) -> dict[str, float]:
        return {
            "allocated_funds": decimal_to_float(summary.allocated_funds),
            "available_cash": decimal_to_float(summary.available_cash),
            "reserved_cash": decimal_to_float(summary.reserved_cash),
            "current_value": decimal_to_float(summary.current_value),
            "realized_pl": decimal_to_float(summary.realized_profit_loss),
            "unrealized_pl": decimal_to_float(summary.unrealized_profit_loss),
            "total_pl": decimal_to_float(summary.total_profit_loss),
            "total_return_percent": decimal_to_float(summary.total_return_percent),
        }

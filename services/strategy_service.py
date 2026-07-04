"""Strategy lifecycle management."""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal

from core.exceptions import AllocationError, StrategyError
from core.models import EntryPolicy, StrategyStatus, to_decimal
from data.database import DatabaseManager
from portfolio.allocation_manager import AllocationManager

logger = logging.getLogger(__name__)

_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class StrategyService:
    """Create and manage strategy lifecycle."""

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database
        self._allocation = AllocationManager(database)

    def create_strategy(
        self,
        name: str,
        symbol: str,
        short_window: int,
        long_window: int,
        allocated_funds: Decimal,
        cash_reserve_percent: Decimal,
        entry_policy: EntryPolicy,
        *,
        activate: bool = False,
    ) -> int:
        self._validate_inputs(name, symbol, short_window, long_window, allocated_funds, cash_reserve_percent)
        self._allocation.validate_allocation_amount(allocated_funds)
        if activate:
            self._allocation.validate_symbol_uniqueness(symbol)

        parameters = json.dumps({"short_window": short_window, "long_window": long_window})
        status = StrategyStatus.ACTIVE if activate else StrategyStatus.DRAFT
        strategy_id = self._db.create_strategy(
            name=name.strip(),
            strategy_type="moving_average_crossover",
            symbol=symbol.upper(),
            parameters_json=parameters,
            allocated_funds=allocated_funds,
            cash_reserve_percent=cash_reserve_percent,
            entry_policy=entry_policy,
            status=status,
        )
        self._allocation.allocate_to_strategy(strategy_id, allocated_funds)
        if activate:
            from datetime import datetime, timezone

            self._db.update_strategy_status(
                strategy_id,
                StrategyStatus.ACTIVE,
                activated_at=datetime.now(timezone.utc).isoformat(),
            )
        logger.info("Created strategy %s (%s).", strategy_id, name)
        return strategy_id

    def activate(self, strategy_id: int) -> None:
        strategy = self._require(strategy_id)
        self._allocation.validate_strategy_activation(strategy)
        from datetime import datetime, timezone

        self._db.update_strategy_status(
            strategy_id,
            StrategyStatus.ACTIVE,
            activated_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info("Activated strategy %s.", strategy_id)

    def pause(self, strategy_id: int) -> None:
        strategy = self._require(strategy_id)
        if strategy.status != StrategyStatus.ACTIVE:
            raise StrategyError("Only active strategies can be paused.")
        from datetime import datetime, timezone

        self._db.update_strategy_status(
            strategy_id,
            StrategyStatus.PAUSED,
            paused_at=datetime.now(timezone.utc).isoformat(),
        )

    def resume(self, strategy_id: int) -> None:
        strategy = self._require(strategy_id)
        if strategy.status != StrategyStatus.PAUSED:
            raise StrategyError("Only paused strategies can be resumed.")
        self._allocation.validate_symbol_uniqueness(strategy.symbol, exclude_strategy_id=strategy_id)
        self._db.update_strategy_status(strategy_id, StrategyStatus.ACTIVE)

    def stop(self, strategy_id: int) -> None:
        strategy = self._require(strategy_id)
        if strategy.status == StrategyStatus.STOPPED:
            raise StrategyError("Strategy is already stopped.")
        self._db.update_strategy_status(strategy_id, StrategyStatus.STOPPED)

    def save_draft(
        self,
        name: str,
        symbol: str,
        short_window: int,
        long_window: int,
        allocated_funds: Decimal,
        cash_reserve_percent: Decimal,
        entry_policy: EntryPolicy,
    ) -> int:
        return self.create_strategy(
            name,
            symbol,
            short_window,
            long_window,
            allocated_funds,
            cash_reserve_percent,
            entry_policy,
            activate=False,
        )

    def _validate_inputs(
        self,
        name: str,
        symbol: str,
        short_window: int,
        long_window: int,
        allocated_funds: Decimal,
        cash_reserve_percent: Decimal,
    ) -> None:
        if not name.strip():
            raise StrategyError("Strategy name cannot be empty.")
        normalized = symbol.strip().upper()
        if not _SYMBOL_PATTERN.match(normalized):
            raise StrategyError("Symbol must be a valid uppercase ticker.")
        if short_window < 2:
            raise StrategyError("Short window must be at least 2.")
        if long_window <= short_window:
            raise StrategyError("Long window must be greater than short window.")
        if allocated_funds <= 0:
            raise StrategyError("Allocation must be greater than zero.")
        if not (Decimal("0") <= cash_reserve_percent <= Decimal("0.5")):
            raise StrategyError("Cash reserve must be between 0% and 50%.")

    def _require(self, strategy_id: int):
        strategy = self._db.get_strategy(strategy_id)
        if strategy is None:
            raise StrategyError(f"Strategy {strategy_id} not found.")
        return strategy

    def has_open_position(self, strategy_id: int) -> bool:
        strategy = self._require(strategy_id)
        pos = self._db.get_strategy_position(strategy_id, strategy.symbol)
        return bool(pos and int(pos["quantity"]) > 0)

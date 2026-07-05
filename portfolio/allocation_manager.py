"""Strategy fund allocation management."""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from config.settings import Settings, get_settings
from core.crypto_decimal import parse_decimal
from core.exceptions import AllocationError
from core.models import LedgerEntryType, StrategyRecord, StrategyStatus, to_decimal
from data.database import DatabaseManager
from portfolio.ledger import StrategyLedger
from portfolio.crypto_ledger import CryptoStrategyLedger
from services.paper_capital_service import PaperCapitalService

logger = logging.getLogger(__name__)


class AllocationManager:
    """Manage virtual fund allocation across strategies."""

    def __init__(
        self,
        database: DatabaseManager,
        settings: Settings | None = None,
        capital_service: PaperCapitalService | None = None,
    ) -> None:
        self._db = database
        self._settings = settings or get_settings()
        self._ledger = StrategyLedger(database)
        self._crypto_ledger = CryptoStrategyLedger(database)
        self._capital = capital_service or PaperCapitalService(self._settings)

    @property
    def capital_source_label(self) -> str:
        return self._capital.source_label

    @property
    def uses_alpaca_capital(self) -> bool:
        return self._capital.uses_alpaca_capital

    @property
    def capital_pool(self) -> Decimal:
        return self._capital.get_capital_pool()

    @property
    def crypto_capital_pool(self) -> Decimal:
        return self._capital.get_crypto_capital_pool()

    def get_crypto_total_allocated(self) -> Decimal:
        return self._db.sum_crypto_allocations()

    def get_crypto_unallocated_capital(self) -> Decimal:
        if self.uses_alpaca_capital:
            return self.get_unallocated_capital()
        return self.crypto_capital_pool - self.get_crypto_total_allocated()

    def get_total_allocated(self) -> Decimal:
        return self._db.get_total_allocated_funds()

    def get_unallocated_capital(self) -> Decimal:
        return self.capital_pool - self.get_total_allocated()

    def validate_allocation_amount(self, amount: Decimal, exclude_strategy_id: int | None = None) -> None:
        if amount <= 0:
            raise AllocationError("Allocation must be greater than zero.")
        current_total = self.get_total_allocated()
        if exclude_strategy_id:
            strategy = self._db.get_strategy(exclude_strategy_id)
            if strategy:
                current_total -= strategy.allocated_funds
        if current_total + amount > self.capital_pool:
            raise AllocationError(
                f"Allocation exceeds available pool. "
                f"Unallocated: {self.get_unallocated_capital():.2f}, requested: {amount:.2f}."
            )

    def validate_crypto_allocation_amount(
        self,
        amount: Decimal,
        exclude_strategy_id: int | None = None,
    ) -> None:
        if self.uses_alpaca_capital:
            self.validate_allocation_amount(amount, exclude_strategy_id=exclude_strategy_id)
            return
        if amount <= 0:
            raise AllocationError("Allocation must be greater than zero.")
        current_total = self.get_crypto_total_allocated()
        if exclude_strategy_id:
            strategy = self._db.get_strategy(exclude_strategy_id)
            if strategy and getattr(strategy, "asset_type", "STOCK") == "CRYPTO":
                current_total -= strategy.allocated_funds
        if current_total + amount > self.crypto_capital_pool:
            raise AllocationError(
                f"Crypto allocation exceeds configured limit. "
                f"Available: {self.get_crypto_unallocated_capital():.2f}, requested: {amount:.2f}."
            )

    def increase_strategy_allocation(self, strategy_id: int, amount: Decimal) -> None:
        strategy = self._require_strategy(strategy_id)
        if getattr(strategy, "asset_type", "STOCK") == "CRYPTO":
            self.increase_crypto_allocation(strategy_id, amount)
        else:
            self.increase_allocation(strategy_id, amount)

    def decrease_strategy_allocation(self, strategy_id: int, amount: Decimal) -> None:
        strategy = self._require_strategy(strategy_id)
        if getattr(strategy, "asset_type", "STOCK") == "CRYPTO":
            self.decrease_crypto_allocation(strategy_id, amount)
        else:
            self.decrease_allocation(strategy_id, amount)

    def increase_crypto_allocation(self, strategy_id: int, amount: Decimal) -> None:
        if amount <= 0:
            raise AllocationError("Increase amount must be positive.")
        self.validate_crypto_allocation_amount(amount, exclude_strategy_id=strategy_id)
        strategy = self._require_strategy(strategy_id)
        new_total = strategy.allocated_funds + amount
        self._db.update_strategy(strategy_id, allocated_funds=new_total)
        self._crypto_ledger.increase_allocation(
            strategy_id,
            strategy.symbol,
            amount,
            str(uuid.uuid4()),
        )
        logger.info("Increased crypto allocation by %.2f for strategy %s.", amount, strategy_id)

    def decrease_crypto_allocation(self, strategy_id: int, amount: Decimal) -> None:
        if amount <= 0:
            raise AllocationError("Decrease amount must be positive.")
        strategy = self._require_strategy(strategy_id)
        if amount > strategy.allocated_funds:
            raise AllocationError("Cannot decrease allocation below zero.")
        available = self._crypto_ledger.get_available_usd(strategy_id)
        position = self._db.get_crypto_position(strategy_id, strategy.symbol)
        invested = Decimal("0")
        if position:
            invested = parse_decimal(position["cost_basis_usd_text"])
        reserved = self._crypto_ledger.get_reserved_usd(strategy_id)
        committed = invested + reserved
        new_total = strategy.allocated_funds - amount
        if new_total < committed:
            raise AllocationError(
                f"Cannot reduce allocation below committed capital ({committed:.2f})."
            )
        if amount > available:
            raise AllocationError(
                f"Cannot withdraw {amount:.2f}; only {available:.2f} USD is available."
            )
        self._db.update_strategy(strategy_id, allocated_funds=new_total)
        self._crypto_ledger.decrease_allocation(
            strategy_id,
            strategy.symbol,
            amount,
            str(uuid.uuid4()),
        )
        logger.info("Decreased crypto allocation by %.2f for strategy %s.", amount, strategy_id)

    def allocate_to_strategy(self, strategy_id: int, amount: Decimal) -> None:
        """Record initial allocation in the append-only ledger."""
        self.validate_allocation_amount(amount)
        balance = self._ledger.get_cash_balance(strategy_id) + amount
        self._db.append_ledger_entry(
            strategy_id=strategy_id,
            entry_type=LedgerEntryType.ALLOCATION,
            amount=amount,
            balance_after=balance,
            description=f"Initial allocation of {amount:.2f}",
            reference_type="strategy",
            reference_id=str(strategy_id),
        )
        logger.info("Allocated %.2f to strategy %s.", amount, strategy_id)

    def increase_allocation(self, strategy_id: int, amount: Decimal) -> None:
        if amount <= 0:
            raise AllocationError("Increase amount must be positive.")
        self.validate_allocation_amount(amount, exclude_strategy_id=strategy_id)
        strategy = self._require_strategy(strategy_id)
        new_total = strategy.allocated_funds + amount
        self._db.update_strategy(strategy_id, allocated_funds=new_total)
        balance = self._ledger.get_cash_balance(strategy_id) + amount
        self._db.append_ledger_entry(
            strategy_id=strategy_id,
            entry_type=LedgerEntryType.ALLOCATION_INCREASE,
            amount=amount,
            balance_after=balance,
            description=f"Allocation increase of {amount:.2f}",
            reference_type="strategy",
            reference_id=str(strategy_id),
        )

    def decrease_allocation(self, strategy_id: int, amount: Decimal) -> None:
        if amount <= 0:
            raise AllocationError("Decrease amount must be positive.")
        strategy = self._require_strategy(strategy_id)
        if amount > strategy.allocated_funds:
            raise AllocationError("Cannot decrease allocation below zero.")
        committed = self._ledger.get_committed_capital(strategy_id)
        new_total = strategy.allocated_funds - amount
        if new_total < committed:
            raise AllocationError(
                f"Cannot reduce allocation below committed capital ({committed:.2f})."
            )
        self._db.update_strategy(strategy_id, allocated_funds=new_total)
        balance = self._ledger.get_cash_balance(strategy_id) - amount
        self._db.append_ledger_entry(
            strategy_id=strategy_id,
            entry_type=LedgerEntryType.ALLOCATION_DECREASE,
            amount=-amount,
            balance_after=balance,
            description=f"Allocation decrease of {amount:.2f}",
            reference_type="strategy",
            reference_id=str(strategy_id),
        )

    def get_strategy_available_cash(self, strategy_id: int) -> Decimal:
        return self._ledger.get_available_cash(strategy_id)

    def get_strategy_reserved_cash(self, strategy_id: int) -> Decimal:
        return self._ledger.get_reserved_cash(strategy_id)

    def validate_symbol_uniqueness(
        self,
        symbol: str,
        asset_type: str = "STOCK",
        exclude_strategy_id: int | None = None,
    ) -> None:
        existing = self._db.get_active_strategy_for_asset_symbol(
            asset_type,
            symbol.upper(),
            exclude_strategy_id=exclude_strategy_id,
        )
        if existing is not None:
            raise AllocationError(
                f"Symbol {symbol.upper()} is already used by active strategy '{existing.name}'."
            )

    def validate_strategy_activation(self, strategy: StrategyRecord) -> None:
        self.validate_symbol_uniqueness(
            strategy.symbol,
            asset_type=getattr(strategy, "asset_type", "STOCK"),
            exclude_strategy_id=strategy.id,
        )
        self.validate_allocation_amount(strategy.allocated_funds, exclude_strategy_id=strategy.id)
        if strategy.status not in (StrategyStatus.DRAFT, StrategyStatus.PAUSED):
            raise AllocationError(f"Strategy cannot be activated from status {strategy.status.value}.")

    def _require_strategy(self, strategy_id: int) -> StrategyRecord:
        strategy = self._db.get_strategy(strategy_id)
        if strategy is None:
            raise AllocationError(f"Strategy {strategy_id} not found.")
        return strategy

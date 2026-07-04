"""Append-only strategy cash ledger."""

from __future__ import annotations

import logging
from decimal import Decimal

from core.exceptions import LedgerError
from core.models import LedgerEntryType, to_decimal
from data.database import DatabaseManager

logger = logging.getLogger(__name__)

_CREDIT_TYPES = {
    LedgerEntryType.ALLOCATION,
    LedgerEntryType.ALLOCATION_INCREASE,
    LedgerEntryType.SELL_CREDIT,
    LedgerEntryType.RELEASE_RESERVE,
    LedgerEntryType.ADJUSTMENT,
}

_DEBIT_TYPES = {
    LedgerEntryType.ALLOCATION_DECREASE,
    LedgerEntryType.BUY_DEBIT,
    LedgerEntryType.COMMISSION_DEBIT,
    LedgerEntryType.RESERVE,
}


class StrategyLedger:
    """Manage append-only strategy ledger transactions."""

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database

    def get_ledger_entries(self, strategy_id: int) -> list[dict]:
        return self._db.get_ledger_entries(strategy_id)

    def get_cash_balance(self, strategy_id: int) -> Decimal:
        """Rebuild cash balance entirely from ledger entries."""
        balance = Decimal("0")
        for entry in self.get_ledger_entries(strategy_id):
            amount = to_decimal(entry["amount"])
            entry_type = LedgerEntryType(entry["entry_type"])
            if entry_type in _CREDIT_TYPES:
                balance += abs(amount) if entry_type != LedgerEntryType.ADJUSTMENT else amount
            elif entry_type in _DEBIT_TYPES:
                balance -= abs(amount)
        return balance

    def get_reserved_cash(self, strategy_id: int) -> Decimal:
        reserved = Decimal("0")
        for entry in self.get_ledger_entries(strategy_id):
            entry_type = LedgerEntryType(entry["entry_type"])
            amount = abs(to_decimal(entry["amount"]))
            if entry_type == LedgerEntryType.RESERVE:
                reserved += amount
            elif entry_type == LedgerEntryType.RELEASE_RESERVE:
                reserved -= amount
        return max(reserved, Decimal("0"))

    def get_available_cash(self, strategy_id: int) -> Decimal:
        return self.get_cash_balance(strategy_id) - self.get_reserved_cash(strategy_id)

    def get_committed_capital(self, strategy_id: int) -> Decimal:
        """Capital currently invested or reserved."""
        position = self._db.get_strategy_position(strategy_id, _any_symbol(strategy_id, self._db))
        invested = Decimal("0")
        if position:
            invested = to_decimal(position["cost_basis"])
        return invested + self.get_reserved_cash(strategy_id)

    def reserve_funds(
        self,
        strategy_id: int,
        amount: Decimal,
        reference_type: str,
        reference_id: str,
    ) -> None:
        if amount <= 0:
            raise LedgerError("Reserve amount must be positive.")
        available = self.get_available_cash(strategy_id)
        if amount > available:
            raise LedgerError(f"Insufficient available cash. Available: {available:.2f}.")
        balance = self.get_cash_balance(strategy_id)
        self._db.append_ledger_entry(
            strategy_id=strategy_id,
            entry_type=LedgerEntryType.RESERVE,
            amount=amount,
            balance_after=balance,
            description=f"Reserved {amount:.2f} for pending order",
            reference_type=reference_type,
            reference_id=reference_id,
        )

    def release_reserved_funds(
        self,
        strategy_id: int,
        amount: Decimal,
        reference_type: str,
        reference_id: str,
    ) -> None:
        if self._db.ledger_entry_exists(
            strategy_id, LedgerEntryType.RELEASE_RESERVE, reference_type, reference_id
        ):
            return
        balance = self.get_cash_balance(strategy_id)
        self._db.append_ledger_entry(
            strategy_id=strategy_id,
            entry_type=LedgerEntryType.RELEASE_RESERVE,
            amount=amount,
            balance_after=balance + amount,
            description=f"Released reserved funds {amount:.2f}",
            reference_type=reference_type,
            reference_id=reference_id,
        )

    def record_buy_debit(
        self,
        strategy_id: int,
        amount: Decimal,
        reference_type: str,
        reference_id: str,
        *,
        release_reserve: Decimal | None = None,
    ) -> None:
        if self._db.ledger_entry_exists(
            strategy_id, LedgerEntryType.BUY_DEBIT, reference_type, reference_id
        ):
            return
        if release_reserve and release_reserve > 0:
            self.release_reserved_funds(strategy_id, release_reserve, reference_type, reference_id)
        balance = self.get_cash_balance(strategy_id) - amount
        self._db.append_ledger_entry(
            strategy_id=strategy_id,
            entry_type=LedgerEntryType.BUY_DEBIT,
            amount=-amount,
            balance_after=balance,
            description=f"BUY debit {amount:.2f}",
            reference_type=reference_type,
            reference_id=reference_id,
        )

    def record_sell_credit(
        self,
        strategy_id: int,
        amount: Decimal,
        reference_type: str,
        reference_id: str,
    ) -> None:
        if self._db.ledger_entry_exists(
            strategy_id, LedgerEntryType.SELL_CREDIT, reference_type, reference_id
        ):
            return
        balance = self.get_cash_balance(strategy_id) + amount
        self._db.append_ledger_entry(
            strategy_id=strategy_id,
            entry_type=LedgerEntryType.SELL_CREDIT,
            amount=amount,
            balance_after=balance,
            description=f"SELL credit {amount:.2f}",
            reference_type=reference_type,
            reference_id=reference_id,
        )

    def record_commission(
        self,
        strategy_id: int,
        amount: Decimal,
        reference_type: str,
        reference_id: str,
    ) -> None:
        if amount <= 0:
            return
        if self._db.ledger_entry_exists(
            strategy_id, LedgerEntryType.COMMISSION_DEBIT, reference_type, reference_id
        ):
            return
        balance = self.get_cash_balance(strategy_id) - amount
        self._db.append_ledger_entry(
            strategy_id=strategy_id,
            entry_type=LedgerEntryType.COMMISSION_DEBIT,
            amount=-amount,
            balance_after=balance,
            description=f"Commission {amount:.2f}",
            reference_type=reference_type,
            reference_id=reference_id,
        )


def _any_symbol(strategy_id: int, database: DatabaseManager) -> str:
    positions = database.list_strategy_positions(strategy_id)
    return positions[0]["symbol"] if positions else ""

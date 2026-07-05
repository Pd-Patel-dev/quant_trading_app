"""Append-only crypto strategy ledger."""

from __future__ import annotations

from decimal import Decimal

from core.asset_models import CryptoLedgerEntryType
from core.crypto_decimal import format_decimal, parse_decimal
from data.database import DatabaseManager


class CryptoStrategyLedger:
    """Track USD and base-asset balances for crypto strategies."""

    USD = "USD"

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database

    def get_usd_balance(self, strategy_id: int) -> Decimal:
        return self._balance_for_currency(strategy_id, self.USD)

    def get_reserved_usd(self, strategy_id: int) -> Decimal:
        reserved = Decimal("0")
        for entry in self._db.get_crypto_ledger_entries(strategy_id):
            if entry["entry_type"] == CryptoLedgerEntryType.CRYPTO_BUY_RESERVE.value:
                reserved += parse_decimal(entry["amount_text"])
            elif entry["entry_type"] == CryptoLedgerEntryType.CRYPTO_BUY_RESERVE_RELEASE.value:
                reserved -= parse_decimal(entry["amount_text"])
        return max(reserved, Decimal("0"))

    def get_available_usd(self, strategy_id: int) -> Decimal:
        return self.get_usd_balance(strategy_id) - self.get_reserved_usd(strategy_id)

    def get_asset_balance(self, strategy_id: int, symbol: str) -> Decimal:
        base, _ = symbol.split("/", 1)
        return self._balance_for_currency(strategy_id, base)

    def allocate(self, strategy_id: int, symbol: str, amount: Decimal, reference_id: str) -> None:
        balance = self.get_usd_balance(strategy_id) + amount
        self._append(
            strategy_id,
            CryptoLedgerEntryType.CRYPTO_ALLOCATION,
            symbol,
            self.USD,
            amount,
            balance,
            "Initial crypto strategy allocation",
            reference_type="allocation",
            reference_id=reference_id,
            idempotency_key=f"alloc-{strategy_id}-{reference_id}",
        )

    def increase_allocation(
        self,
        strategy_id: int,
        symbol: str,
        amount: Decimal,
        reference_id: str,
    ) -> None:
        balance = self.get_usd_balance(strategy_id) + amount
        self._append(
            strategy_id,
            CryptoLedgerEntryType.CRYPTO_ALLOCATION_INCREASE,
            symbol,
            self.USD,
            amount,
            balance,
            f"Crypto allocation increase of {amount}",
            reference_type="allocation",
            reference_id=reference_id,
            idempotency_key=f"alloc-inc-{strategy_id}-{reference_id}",
        )

    def decrease_allocation(
        self,
        strategy_id: int,
        symbol: str,
        amount: Decimal,
        reference_id: str,
    ) -> None:
        balance = self.get_usd_balance(strategy_id) - amount
        self._append(
            strategy_id,
            CryptoLedgerEntryType.CRYPTO_ALLOCATION_DECREASE,
            symbol,
            self.USD,
            -amount,
            balance,
            f"Crypto allocation decrease of {amount}",
            reference_type="allocation",
            reference_id=reference_id,
            idempotency_key=f"alloc-dec-{strategy_id}-{reference_id}",
        )

    def reserve_usd(
        self,
        strategy_id: int,
        symbol: str,
        amount: Decimal,
        reference_id: str,
    ) -> None:
        balance = self.get_usd_balance(strategy_id)
        self._append(
            strategy_id,
            CryptoLedgerEntryType.CRYPTO_BUY_RESERVE,
            symbol,
            self.USD,
            amount,
            balance,
            "Reserve USD for crypto BUY",
            reference_type="proposal",
            reference_id=reference_id,
            idempotency_key=f"reserve-{reference_id}",
        )

    def release_reserved_usd(
        self,
        strategy_id: int,
        symbol: str,
        amount: Decimal,
        reference_id: str,
    ) -> None:
        balance = self.get_usd_balance(strategy_id)
        self._append(
            strategy_id,
            CryptoLedgerEntryType.CRYPTO_BUY_RESERVE_RELEASE,
            symbol,
            self.USD,
            amount,
            balance,
            "Release reserved USD",
            reference_type="order",
            reference_id=reference_id,
            idempotency_key=f"release-{reference_id}",
        )

    def record_buy_fill(
        self,
        strategy_id: int,
        symbol: str,
        usd_debit: Decimal,
        asset_credit: Decimal,
        reference_id: str,
    ) -> None:
        base, _ = symbol.split("/", 1)
        usd_balance = self.get_usd_balance(strategy_id) - usd_debit
        self._append(
            strategy_id,
            CryptoLedgerEntryType.CRYPTO_BUY_USD_DEBIT,
            symbol,
            self.USD,
            -usd_debit,
            usd_balance,
            "Crypto BUY USD debit",
            reference_type="fill",
            reference_id=reference_id,
            idempotency_key=f"buy-usd-{reference_id}",
        )
        asset_balance = self.get_asset_balance(strategy_id, symbol) + asset_credit
        self._append(
            strategy_id,
            CryptoLedgerEntryType.CRYPTO_BUY_ASSET_CREDIT,
            symbol,
            base,
            asset_credit,
            asset_balance,
            "Crypto BUY asset credit",
            reference_type="fill",
            reference_id=reference_id,
            idempotency_key=f"buy-asset-{reference_id}",
        )

    def record_sell_fill(
        self,
        strategy_id: int,
        symbol: str,
        asset_debit: Decimal,
        usd_credit: Decimal,
        reference_id: str,
    ) -> None:
        base, _ = symbol.split("/", 1)
        asset_balance = self.get_asset_balance(strategy_id, symbol) - asset_debit
        self._append(
            strategy_id,
            CryptoLedgerEntryType.CRYPTO_SELL_ASSET_DEBIT,
            symbol,
            base,
            -asset_debit,
            asset_balance,
            "Crypto SELL asset debit",
            reference_type="fill",
            reference_id=reference_id,
            idempotency_key=f"sell-asset-{reference_id}",
        )
        usd_balance = self.get_usd_balance(strategy_id) + usd_credit
        self._append(
            strategy_id,
            CryptoLedgerEntryType.CRYPTO_SELL_USD_CREDIT,
            symbol,
            self.USD,
            usd_credit,
            usd_balance,
            "Crypto SELL USD credit",
            reference_type="fill",
            reference_id=reference_id,
            idempotency_key=f"sell-usd-{reference_id}",
        )

    def _balance_for_currency(self, strategy_id: int, currency: str) -> Decimal:
        balance = Decimal("0")
        for entry in self._db.get_crypto_ledger_entries(strategy_id):
            if entry["currency"] == currency:
                balance += parse_decimal(entry["amount_text"])
        return balance

    def _append(
        self,
        strategy_id: int,
        entry_type: CryptoLedgerEntryType,
        symbol: str,
        currency: str,
        amount: Decimal,
        balance_after: Decimal,
        description: str,
        *,
        reference_type: str | None,
        reference_id: str | None,
        idempotency_key: str,
    ) -> None:
        self._db.append_crypto_ledger_entry(
            strategy_id,
            entry_type,
            symbol,
            currency,
            amount,
            balance_after,
            description,
            reference_type=reference_type,
            reference_id=reference_id,
            idempotency_key=idempotency_key,
        )

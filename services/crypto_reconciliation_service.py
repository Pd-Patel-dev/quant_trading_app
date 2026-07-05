"""Crypto reconciliation between local and Alpaca state."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from broker.crypto_asset_service import CryptoAssetService
from core.crypto_decimal import parse_decimal
from data.database import DatabaseManager
from portfolio.crypto_ledger import CryptoStrategyLedger


@dataclass
class CryptoReconciliationResult:
    warnings: list[str] = field(default_factory=list)
    critical: list[str] = field(default_factory=list)
    is_clear: bool = True


class CryptoReconciliationService:
    """Detect mismatches without auto-repair."""

    def __init__(
        self,
        database: DatabaseManager,
        crypto_order_manager: object,
        asset_service: CryptoAssetService,
    ) -> None:
        self._db = database
        self._orders = crypto_order_manager
        self._assets = asset_service
        self._ledger = CryptoStrategyLedger(database)

    def run_reconciliation(self, *, persist: bool = True) -> CryptoReconciliationResult:
        result = CryptoReconciliationResult()
        local_positions = self._db.list_crypto_positions()
        broker_positions = {
            pos["symbol"]: parse_decimal(pos["quantity"])
            for pos in self._orders.get_crypto_positions()
        }
        managed_by_symbol: dict[str, Decimal] = {}
        for pos in local_positions:
            symbol = pos["symbol"]
            qty = parse_decimal(pos["quantity_text"])
            managed_by_symbol[symbol] = managed_by_symbol.get(symbol, Decimal("0")) + qty

        for symbol, broker_qty in broker_positions.items():
            managed_qty = managed_by_symbol.get(symbol, Decimal("0"))
            if managed_qty == 0:
                result.critical.append(f"Unmanaged Alpaca crypto position: {symbol}")
            elif managed_qty > broker_qty:
                result.critical.append(
                    f"Local quantity exceeds broker quantity for {symbol}: {managed_qty} > {broker_qty}"
                )
            elif broker_qty > managed_qty:
                result.warnings.append(
                    f"Broker quantity exceeds locally managed quantity for {symbol}."
                )

        for symbol, managed_qty in managed_by_symbol.items():
            if symbol not in broker_positions and managed_qty > 0:
                result.warnings.append(f"Local crypto position without broker position: {symbol}")

        for order in self._db.list_open_crypto_paper_orders():
            if order["status"] == "UNKNOWN":
                result.critical.append(f"Unknown crypto order: {order['client_order_id']}")

        if persist:
            self._db.save_crypto_reconciliation_run(
                uuid.uuid4().hex,
                "COMPLETED" if not result.critical else "COMPLETED_WITH_ISSUES",
                len(local_positions),
                len(broker_positions),
                len(result.warnings),
                len(result.critical),
                {"warnings": result.warnings, "critical": result.critical},
            )
        result.is_clear = not result.critical
        return result

    def has_critical_issues(self) -> bool:
        return bool(self.run_reconciliation(persist=False).critical)

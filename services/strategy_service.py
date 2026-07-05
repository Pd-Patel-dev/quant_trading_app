"""Strategy lifecycle management."""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal
from typing import Any

from core.exceptions import AllocationError, StrategyError
from core.models import EntryPolicy, StrategyStatus, to_decimal
from data.database import DatabaseManager
from portfolio.allocation_manager import AllocationManager
from services.strategy_lifecycle_service import StrategyLifecycleService
from strategies.registry import get_registry

from market_data.models import AssetType
from market_data.symbol_normalizer import SymbolNormalizer
from portfolio.crypto_ledger import CryptoStrategyLedger

logger = logging.getLogger(__name__)

_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
PAPER_APPROVAL_PHRASE = "APPROVE PAPER STRATEGY"
CRYPTO_PAPER_APPROVAL_PHRASE = "APPROVE CRYPTO PAPER STRATEGY"


class StrategyService:
    """Create and manage strategy lifecycle."""

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database
        self._allocation = AllocationManager(database)
        self._registry = get_registry()
        self._lifecycle = StrategyLifecycleService(database)

    def create_strategy(
        self,
        name: str,
        symbol: str,
        strategy_type: str,
        parameters: dict[str, Any],
        allocated_funds: Decimal,
        cash_reserve_percent: Decimal,
        entry_policy: EntryPolicy,
        *,
        activate: bool = False,
    ) -> int:
        self._validate_common_inputs(name, symbol, allocated_funds, cash_reserve_percent)
        self._registry.validate_parameters(strategy_type, parameters)
        self._allocation.validate_allocation_amount(allocated_funds)
        if activate:
            self._allocation.validate_symbol_uniqueness(symbol, asset_type="STOCK")

        parameters_json = json.dumps(parameters)
        strategy_id = self._db.create_strategy(
            name=name.strip(),
            strategy_type=strategy_type,
            symbol=symbol.upper(),
            parameters_json=parameters_json,
            allocated_funds=allocated_funds,
            cash_reserve_percent=cash_reserve_percent,
            entry_policy=entry_policy,
            status=StrategyStatus.DRAFT,
        )
        self._allocation.allocate_to_strategy(strategy_id, allocated_funds)
        if activate:
            self._lifecycle.activate_strategy(strategy_id)
        logger.info("Created strategy %s (%s).", strategy_id, name)
        return strategy_id

    def create_moving_average_strategy(
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
        return self.create_strategy(
            name,
            symbol,
            "moving_average_crossover",
            {"short_window": short_window, "long_window": long_window},
            allocated_funds,
            cash_reserve_percent,
            entry_policy,
            activate=activate,
        )

    def approve_for_paper_trading(
        self,
        strategy_id: int,
        confirmation_text: str,
        *,
        reviewed_rules: bool,
        reviewed_backtest: bool,
        understood_disclaimer: bool,
    ) -> None:
        if confirmation_text.strip() != PAPER_APPROVAL_PHRASE:
            raise StrategyError(f"Confirmation text must be exactly: {PAPER_APPROVAL_PHRASE}")
        if not (reviewed_rules and reviewed_backtest and understood_disclaimer):
            raise StrategyError("All approval checkboxes are required.")

        strategy = self._require(strategy_id)
        if strategy.status not in (StrategyStatus.DRAFT, StrategyStatus.PAUSED):
            raise StrategyError("Paper approval is only available for DRAFT or PAUSED strategies.")
        if not self._db.has_matching_backtest(
            strategy.strategy_type, strategy.symbol, strategy.parameters_json
        ):
            raise StrategyError(
                "At least one completed backtest must exist for this strategy type, symbol, and parameters."
            )

        from datetime import datetime, timezone

        self._db.update_strategy_paper_approval(
            strategy_id,
            approved=True,
            approved_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info("Paper trading approved for strategy %s.", strategy_id)

    def create_crypto_strategy(
        self,
        name: str,
        symbol: str,
        strategy_type: str,
        parameters: dict[str, Any],
        allocated_funds: Decimal,
        cash_reserve_percent: Decimal,
        entry_policy: EntryPolicy,
    ) -> int:
        normalizer = SymbolNormalizer()
        canonical = normalizer.normalize(AssetType.CRYPTO, symbol)
        _, quote = normalizer.split_crypto_pair(canonical)
        if quote != "USD":
            raise StrategyError("Only USD-quoted crypto pairs are supported for paper trading.")
        self._registry.validate_parameters(strategy_type, parameters)
        if allocated_funds <= 0:
            raise StrategyError("Allocation must be greater than zero.")
        self._allocation.validate_crypto_allocation_amount(allocated_funds)
        strategy_id = self._db.create_crypto_strategy(
            name.strip(),
            strategy_type,
            canonical,
            quote,
            json.dumps(parameters),
            float(allocated_funds),
            float(cash_reserve_percent),
            entry_policy.value,
        )
        CryptoStrategyLedger(self._db).allocate(strategy_id, canonical, allocated_funds, str(strategy_id))
        logger.info("Created crypto strategy %s (%s).", strategy_id, canonical)
        return strategy_id

    def approve_for_crypto_paper_trading(
        self,
        strategy_id: int,
        confirmation_text: str,
        *,
        reviewed_rules: bool,
        reviewed_backtest: bool,
        understood_disclaimer: bool,
        understood_continuous: bool,
        understood_volatility: bool,
    ) -> None:
        if confirmation_text.strip() != CRYPTO_PAPER_APPROVAL_PHRASE:
            raise StrategyError(f"Confirmation text must be exactly: {CRYPTO_PAPER_APPROVAL_PHRASE}")
        if not all(
            [
                reviewed_rules,
                reviewed_backtest,
                understood_disclaimer,
                understood_continuous,
                understood_volatility,
            ]
        ):
            raise StrategyError("All crypto approval checkboxes are required.")
        strategy = self._require(strategy_id)
        if getattr(strategy, "asset_type", "STOCK") != AssetType.CRYPTO.value:
            raise StrategyError("Strategy is not a crypto strategy.")
        if strategy.status not in (StrategyStatus.DRAFT, StrategyStatus.PAUSED):
            raise StrategyError("Crypto approval is only available for DRAFT or PAUSED strategies.")
        if not self._db.has_matching_backtest(
            strategy.strategy_type, strategy.symbol, strategy.parameters_json
        ):
            raise StrategyError("Matching completed backtest required before crypto paper approval.")
        from datetime import datetime, timezone

        self._db.update_crypto_paper_approval(
            strategy_id,
            approved=True,
            approved_at=datetime.now(timezone.utc).isoformat(),
        )

    def activate_crypto(self, strategy_id: int) -> None:
        self._lifecycle.activate_strategy(strategy_id)

    def activate(self, strategy_id: int) -> None:
        self._lifecycle.activate_strategy(strategy_id)

    def pause(self, strategy_id: int, reason: str | None = None) -> None:
        self._lifecycle.pause_strategy(strategy_id, reason=reason)

    def resume(self, strategy_id: int) -> None:
        self._lifecycle.resume_strategy(strategy_id)

    def stop(self, strategy_id: int, reason: str | None = None) -> None:
        self._lifecycle.stop_strategy(strategy_id, reason=reason)

    def archive(self, strategy_id: int) -> None:
        self._lifecycle.archive_strategy(strategy_id)

    def restore(self, strategy_id: int) -> None:
        self._lifecycle.restore_strategy(strategy_id)

    def permanently_delete(self, strategy_id: int) -> None:
        self._lifecycle.permanently_delete_strategy(strategy_id)

    def get_deletion_eligibility(self, strategy_id: int):
        return self._lifecycle.get_deletion_eligibility(strategy_id)

    def save_draft(
        self,
        name: str,
        symbol: str,
        strategy_type: str,
        parameters: dict[str, Any],
        allocated_funds: Decimal,
        cash_reserve_percent: Decimal,
        entry_policy: EntryPolicy,
    ) -> int:
        return self.create_strategy(
            name,
            symbol,
            strategy_type,
            parameters,
            allocated_funds,
            cash_reserve_percent,
            entry_policy,
            activate=False,
        )

    def _validate_common_inputs(
        self,
        name: str,
        symbol: str,
        allocated_funds: Decimal,
        cash_reserve_percent: Decimal,
    ) -> None:
        if not name.strip():
            raise StrategyError("Strategy name cannot be empty.")
        normalized = symbol.strip().upper()
        if not _SYMBOL_PATTERN.match(normalized):
            raise StrategyError("Symbol must be a valid uppercase ticker.")
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
        if getattr(strategy, "asset_type", "STOCK") == "CRYPTO":
            pos = self._db.get_crypto_position(strategy_id, strategy.symbol)
            if not pos:
                return False
            from core.crypto_decimal import parse_decimal

            return parse_decimal(pos["quantity_text"]) > 0
        pos = self._db.get_strategy_position(strategy_id, strategy.symbol)
        return bool(pos and int(pos["quantity"]) > 0)

    def increase_allocation(self, strategy_id: int, amount: Decimal) -> None:
        self._allocation.increase_strategy_allocation(strategy_id, amount)

    def decrease_allocation(self, strategy_id: int, amount: Decimal) -> None:
        self._allocation.decrease_strategy_allocation(strategy_id, amount)

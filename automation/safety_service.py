"""Automation safety validation before paper order submission."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from automation.models import AutomationValidationResult, ConfirmationMode, ProposalSource
from config.settings import Settings, get_settings
from core.models import OrderProposal, StrategyRecord, StrategyStatus, to_decimal
from portfolio.ledger import StrategyLedger

if TYPE_CHECKING:
    from data.database import DatabaseManager


class AutomationSafetyService:
    """Validate automated submissions against all safety rules."""

    VALIDATION_VERSION = "1.0"

    def __init__(
        self,
        database: DatabaseManager,
        settings: Settings | None = None,
    ) -> None:
        self._db = database
        self._settings = settings or get_settings()
        self._ledger = StrategyLedger(database)

    def validate_automated_submission(
        self,
        strategy: StrategyRecord,
        proposal: OrderProposal,
        account: dict | None,
        market_clock: dict | None,
        portfolio_summary: dict | None,
        *,
        trading_day: str | None = None,
        daily_order_count: int = 0,
        daily_notional: float = 0.0,
    ) -> AutomationValidationResult:
        passed: list[str] = []
        warnings: list[str] = []
        blocking: list[str] = []
        now = datetime.now(timezone.utc)

        auto_settings = self._db.get_automation_settings()

        if self._settings.trading_mode != "paper":
            blocking.append("Trading mode is not paper.")
        else:
            passed.append("Trading mode is paper.")

        if self._settings.live_trading_enabled:
            blocking.append("Live trading is enabled in configuration.")
        else:
            passed.append("Live trading is disabled.")

        if not auto_settings.automated_paper_trading_enabled:
            blocking.append("Global automated paper trading is disabled.")
        else:
            passed.append("Global automation is enabled.")

        if auto_settings.kill_switch_engaged:
            blocking.append("Emergency kill switch is engaged.")
        else:
            passed.append("Kill switch is disengaged.")

        if not strategy.automation_enabled:
            blocking.append("Strategy automation is not enabled.")
        else:
            passed.append("Strategy automation is enabled.")

        if strategy.status != StrategyStatus.ACTIVE:
            blocking.append(f"Strategy status is {strategy.status.value}.")
        else:
            passed.append("Strategy is active.")

        if proposal.proposal_source != ProposalSource.AUTOMATION.value:
            blocking.append("Proposal source is not automation.")
        else:
            passed.append("Proposal source is automation.")

        if proposal.confirmation_mode == ConfirmationMode.MANUAL.value:
            blocking.append("Proposal requires manual confirmation.")

        if proposal.expires_at and proposal.expires_at.replace(tzinfo=timezone.utc) < now.replace(tzinfo=None):
            blocking.append("Proposal has expired.")
        else:
            passed.append("Proposal is not expired.")

        if market_clock:
            if not market_clock.get("is_open"):
                blocking.append("Market is not open.")
            else:
                passed.append("Market is open.")
        else:
            blocking.append("Unable to verify market clock.")

        if self._db.count_unknown_orders() > 0:
            blocking.append("An order with UNKNOWN status exists.")
        else:
            passed.append("No unknown orders.")

        if self._has_pending_order(strategy.id, proposal.symbol):
            blocking.append("A pending order exists for this strategy and symbol.")

        if self._db.get_proposal_by_client_order_id(proposal.client_order_id):
            existing = self._db.get_proposal(proposal.proposal_id)
            if existing and existing.get("status") in ("SUBMITTED", "FILLED", "ACCEPTED"):
                blocking.append("Proposal has already been submitted.")

        local_position = self._db.get_strategy_position(strategy.id, proposal.symbol)
        local_qty = int(local_position["quantity"]) if local_position else 0
        available_cash = self._ledger.get_available_cash(strategy.id)

        if proposal.side == "BUY":
            if local_qty > 0:
                blocking.append("Strategy already holds a local position.")
            if available_cash < proposal.estimated_notional:
                blocking.append("Insufficient available local strategy cash.")
            if auto_settings.maximum_active_positions <= self._db.count_managed_positions() and local_qty == 0:
                blocking.append("Maximum managed positions limit reached.")
        elif proposal.side == "SELL":
            if proposal.proposed_quantity > local_qty:
                blocking.append("SELL quantity exceeds local strategy quantity.")
            broker_qty = None
            if account is not None and portfolio_summary is not None:
                broker_qty = portfolio_summary.get("broker_quantity")
            if broker_qty is not None and proposal.proposed_quantity > int(broker_qty):
                blocking.append("SELL quantity exceeds Alpaca account quantity.")
            if proposal.proposed_quantity > local_qty:
                blocking.append("Order would create or increase a short position.")

        notional = float(proposal.estimated_notional)
        if notional > auto_settings.maximum_order_notional:
            blocking.append(
                f"Per-order notional ({notional:.2f}) exceeds limit "
                f"({auto_settings.maximum_order_notional:.2f})."
            )

        if trading_day:
            if daily_order_count >= auto_settings.maximum_orders_per_day:
                blocking.append("Daily automated order count limit reached.")
            if daily_notional + notional > auto_settings.maximum_daily_notional:
                blocking.append("Daily automated notional limit would be exceeded.")

        if account:
            if account.get("trading_blocked"):
                blocking.append("Alpaca account trading is blocked.")
            if proposal.side == "BUY" and to_decimal(account.get("buying_power", 0)) < proposal.estimated_notional:
                blocking.append("Insufficient Alpaca buying power.")
        else:
            blocking.append("Unable to verify Alpaca account.")

        return AutomationValidationResult(
            passed=passed,
            warnings=warnings,
            blocking_reasons=blocking,
            validated_at=now,
            validation_version=self.VALIDATION_VERSION,
        )

    def _has_pending_order(self, strategy_id: int, symbol: str) -> bool:
        for order in self._db.list_open_paper_orders():
            if order.strategy_id == strategy_id and order.symbol == symbol.upper():
                return True
        return False

    @staticmethod
    def result_to_json(result: AutomationValidationResult) -> str:
        return json.dumps(
            {
                "passed": result.passed,
                "warnings": result.warnings,
                "blocking_reasons": result.blocking_reasons,
                "validated_at": result.validated_at.isoformat() if result.validated_at else None,
                "validation_version": result.validation_version,
            }
        )

"""Order proposal generation and validation."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from decimal import Decimal

from config.settings import Settings, get_settings
from core.client_order_id import build_client_order_id
from core.exceptions import OrderProposalError
from core.models import (
    EntryPolicy,
    OrderProposal,
    OrderProposalStatus,
    SignalEvaluation,
    SignalType,
    StrategyRecord,
    StrategyStatus,
    to_decimal,
)
from data.database import DatabaseManager
from portfolio.allocation_manager import AllocationManager
from portfolio.ledger import StrategyLedger

logger = logging.getLogger(__name__)


class OrderProposalService:
    """Generate validated order proposals without submitting orders."""

    def __init__(
        self,
        database: DatabaseManager,
        order_manager: object | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._db = database
        self._order_manager = order_manager
        self._settings = settings or get_settings()
        self._allocation = AllocationManager(database, self._settings)
        self._ledger = StrategyLedger(database)

    def build_proposal(
        self,
        strategy: StrategyRecord,
        evaluation: SignalEvaluation,
        *,
        persist: bool = True,
    ) -> OrderProposal:
        """Create a proposal with full validation results."""
        blocking: list[str] = []
        messages: list[str] = []
        now = datetime.now(timezone.utc)

        if self._settings.trading_mode != "paper":
            blocking.append("Trading mode is not paper.")
        if self._settings.live_trading_enabled:
            blocking.append("Live trading is enabled in configuration (blocked).")
        if not self._settings.paper_order_submission_enabled:
            blocking.append("Paper order submission is disabled.")
        if not self._settings.alpaca_configured:
            blocking.append("Alpaca credentials are not configured.")
        if strategy.status != StrategyStatus.ACTIVE:
            blocking.append(f"Strategy status is {strategy.status.value}.")
        if not getattr(strategy, "paper_trading_approved", False):
            blocking.append("Strategy is not approved for paper trading.")
        if not evaluation.is_actionable:
            blocking.append("Signal is not actionable.")
        if evaluation.latest_signal == SignalType.HOLD:
            blocking.append("Latest signal is HOLD.")

        account = self._safe_account()
        if account:
            if str(account.get("status", "")).upper() not in ("ACTIVE", "ACCOUNTSTATUS.ACTIVE"):
                blocking.append("Alpaca account is not active.")
            if account.get("trading_blocked"):
                blocking.append("Alpaca account trading is blocked.")
        else:
            blocking.append("Unable to verify Alpaca account status.")

        clock = self._safe_clock()
        if clock and not clock.get("is_open"):
            blocking.append("Market is currently closed.")

        if self._db.get_active_proposal_for_strategy(strategy.id):
            blocking.append("An active proposal already exists for this strategy.")
        if self._has_pending_local_order(strategy.id, strategy.symbol):
            blocking.append("A pending local order exists for this strategy.")
        if self._db.count_unknown_orders() > 0:
            blocking.append("An order with UNKNOWN status exists.")

        active_for_symbol = self._db.get_active_strategy_for_symbol(
            strategy.symbol,
            asset_type=getattr(strategy, "asset_type", "STOCK"),
            exclude_strategy_id=strategy.id,
        )
        if active_for_symbol is not None:
            blocking.append(f"Another active strategy uses symbol {strategy.symbol}.")

        local_position = self._db.get_strategy_position(strategy.id, strategy.symbol)
        local_qty = int(local_position["quantity"]) if local_position else 0
        available_cash = self._ledger.get_available_cash(strategy.id)

        side = evaluation.latest_signal.value
        if side not in ("BUY", "SELL"):
            side = "BUY" if evaluation.current_desired_position == 1 else "SELL"

        reference_price = evaluation.close_price or Decimal("0")
        estimated_price = reference_price * (
            Decimal("1") + to_decimal(self._settings.price_estimate_buffer_percent)
        )
        quantity = 0
        estimated_notional = Decimal("0")

        if side == "BUY":
            blocking.extend(self._validate_buy(strategy, local_qty, available_cash, estimated_price))
            if not blocking:
                reserve_pct = strategy.cash_reserve_percent
                spendable = available_cash * (Decimal("1") - reserve_pct)
                quantity = int(spendable // estimated_price) if estimated_price > 0 else 0
                estimated_notional = to_decimal(quantity) * estimated_price
                if quantity < 1:
                    blocking.append("Insufficient cash to buy at least one whole share.")
                if estimated_notional > self._settings.max_paper_order_notional:
                    blocking.append(
                        f"Estimated notional exceeds maximum ({self._settings.max_paper_order_notional:.2f})."
                    )
                if account and estimated_notional > to_decimal(account.get("buying_power", 0)):
                    blocking.append("Estimated notional exceeds Alpaca buying power.")
                messages.append("BUY validation passed.")
        elif side == "SELL":
            blocking.extend(self._validate_sell(strategy, local_qty))
            quantity = local_qty
            estimated_notional = to_decimal(quantity) * estimated_price
            broker_pos = self._safe_broker_position(strategy.symbol)
            if broker_pos is not None and broker_pos < quantity:
                blocking.append(
                    f"Alpaca position ({broker_pos}) is smaller than local position ({quantity})."
                )
            messages.append("SELL validation prepared.")

        signal_ts = evaluation.signal_timestamp or now.replace(tzinfo=None)
        client_order_id = build_client_order_id(
            self._settings.client_order_id_prefix,
            strategy.id,
            strategy.symbol,
            side,
            signal_ts,
        )

        if self._db.get_proposal_by_client_order_id(client_order_id):
            blocking.append("Duplicate proposal for this signal already exists.")

        expires_at = now + timedelta(hours=self._settings.proposal_expiry_hours)
        status = OrderProposalStatus.PROPOSED if not blocking and quantity > 0 else OrderProposalStatus.BLOCKED

        proposal = OrderProposal(
            proposal_id=str(uuid.uuid4()),
            strategy_id=strategy.id,
            strategy_name=strategy.name,
            symbol=strategy.symbol,
            signal=SignalType(side),
            signal_timestamp=signal_ts,
            side=side,  # type: ignore[arg-type]
            proposed_quantity=quantity,
            estimated_price=estimated_price,
            estimated_notional=estimated_notional,
            allocated_funds=strategy.allocated_funds,
            strategy_cash_available=available_cash,
            strategy_position_quantity=local_qty,
            cash_reserve_percent=strategy.cash_reserve_percent,
            client_order_id=client_order_id,
            status=status,
            validation_messages=messages,
            blocking_reasons=blocking,
            created_at=now.replace(tzinfo=None),
            expires_at=expires_at.replace(tzinfo=None),
            requires_alignment_confirmation=evaluation.requires_alignment,
        )

        if persist:
            self._db.save_proposal(proposal)
        logger.info(
            "Proposal generated strategy=%s side=%s qty=%s blocked=%s",
            strategy.id,
            side,
            quantity,
            bool(blocking),
        )
        return proposal

    def _validate_buy(
        self,
        strategy: StrategyRecord,
        local_qty: int,
        available_cash: Decimal,
        estimated_price: Decimal,
    ) -> list[str]:
        errors: list[str] = []
        if local_qty > 0:
            errors.append("Strategy already holds a local position.")
        if available_cash <= 0:
            errors.append("No available strategy cash.")
        if estimated_price <= 0:
            errors.append("Invalid estimated execution price.")
        return errors

    def _validate_sell(self, strategy: StrategyRecord, local_qty: int) -> list[str]:
        errors: list[str] = []
        if local_qty <= 0:
            errors.append("No local strategy position to sell.")
        return errors

    def _has_pending_local_order(self, strategy_id: int, symbol: str) -> bool:
        for order in self._db.list_open_paper_orders():
            if order.strategy_id == strategy_id and order.symbol == symbol.upper():
                return True
        return False

    def _safe_account(self) -> dict | None:
        if self._order_manager is None:
            return None
        try:
            return self._order_manager.get_account_summary()
        except Exception:
            return None

    def _safe_clock(self) -> dict | None:
        if self._order_manager is None:
            return None
        try:
            return self._order_manager.get_market_clock()
        except Exception:
            return None

    def _safe_broker_position(self, symbol: str) -> int | None:
        if self._order_manager is None:
            return None
        try:
            position = self._order_manager.get_position(symbol)
            return int(position["quantity"]) if position else 0
        except Exception:
            return None

    @staticmethod
    def proposal_from_row(row: dict) -> OrderProposal:
        import json

        blocking = json.loads(row.get("blocking_reasons_json") or "[]")
        messages = json.loads(row.get("validation_json") or "[]")
        return OrderProposal(
            proposal_id=row["proposal_id"],
            strategy_id=row["strategy_id"],
            strategy_name="",
            symbol=row["symbol"],
            signal=SignalType(row["signal"]),
            signal_timestamp=datetime.fromisoformat(row["signal_timestamp"]),
            side=row["side"],  # type: ignore[arg-type]
            proposed_quantity=row["quantity"],
            estimated_price=to_decimal(row["estimated_price"]),
            estimated_notional=to_decimal(row["estimated_notional"]),
            allocated_funds=Decimal("0"),
            strategy_cash_available=Decimal("0"),
            strategy_position_quantity=0,
            cash_reserve_percent=Decimal("0"),
            client_order_id=row["client_order_id"],
            status=OrderProposalStatus(row["status"]),
            validation_messages=messages,
            blocking_reasons=blocking,
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row.get("expires_at") else None,
            proposal_source=row.get("proposal_source", "MANUAL"),
            confirmation_mode=row.get("confirmation_mode", "MANUAL"),
            automation_eligible=bool(row.get("automation_eligible", 0)),
        )

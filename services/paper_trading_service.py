"""Paper trading orchestration service."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

from config.settings import Settings, get_settings
from core.exceptions import PaperTradingError
from core.models import (
    ConfirmationData,
    OrderProposalStatus,
    SignalEvaluation,
    StrategyStatus,
    to_decimal,
)
from data.alpaca_data import AlpacaMarketDataProvider
from data.database import DatabaseManager
from portfolio.ledger import StrategyLedger
from portfolio.portfolio_service import PortfolioService
from services.order_proposal_service import OrderProposalService
from services.signal_service import SignalService

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}


class PaperTradingService:
    """Orchestrate signal evaluation through order synchronization."""

    def __init__(
        self,
        database: DatabaseManager,
        order_manager: object,
        data_provider: AlpacaMarketDataProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._db = database
        self._order_manager = order_manager
        self._settings = settings or get_settings()
        self._signal_service = SignalService(database, data_provider, order_manager, self._settings)
        self._proposal_service = OrderProposalService(database, order_manager, self._settings)
        self._ledger = StrategyLedger(database)
        self._portfolio = PortfolioService(database)

    def evaluate_strategy(self, strategy_id: int) -> SignalEvaluation:
        strategy = self._require_strategy(strategy_id)
        if strategy.status != StrategyStatus.ACTIVE:
            raise PaperTradingError(f"Strategy {strategy_id} is not active.")
        return self._signal_service.evaluate_strategy(strategy)

    def build_order_proposal(self, strategy_id: int) -> object:
        strategy = self._require_strategy(strategy_id)
        evaluation = self.evaluate_strategy(strategy_id)
        return self._proposal_service.build_proposal(strategy, evaluation)

    def confirm_proposal(self, proposal_id: str, confirmation: ConfirmationData) -> None:
        row = self._db.get_proposal(proposal_id)
        if row is None:
            raise PaperTradingError("Proposal not found.")
        blocking = json.loads(row.get("blocking_reasons_json") or "[]")
        if blocking:
            raise PaperTradingError("Proposal has blocking reasons.")
        if row["status"] not in (OrderProposalStatus.PROPOSED.value, OrderProposalStatus.BLOCKED.value):
            raise PaperTradingError(f"Proposal status is {row['status']}.")

        if row.get("proposal_source") == "AUTOMATION":
            raise PaperTradingError(
                "Automated proposals cannot be manually confirmed. "
                "Use the Automation page or market-open worker."
            )

        expires_at = row.get("expires_at")
        if expires_at and datetime.fromisoformat(expires_at) < datetime.now(timezone.utc).replace(tzinfo=None):
            raise PaperTradingError("Proposal has expired.")

        if not confirmation.paper_trading_acknowledged or not confirmation.details_reviewed:
            raise PaperTradingError("Required confirmation checkboxes were not checked.")
        if confirmation.paper_text.strip().upper() != "PAPER":
            raise PaperTradingError("Confirmation text must be exactly PAPER.")

        strategy = self._db.get_strategy(row["strategy_id"])
        if strategy and strategy.entry_policy.value == "ALIGN_WITH_CURRENT_POSITION":
            if confirmation.alignment_text.strip().upper() != "ALIGN":
                raise PaperTradingError("Alignment confirmation text must be exactly ALIGN.")

        self._db.update_proposal_status(
            proposal_id,
            OrderProposalStatus.CONFIRMED,
            confirmed_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info("Proposal %s confirmed.", proposal_id)

    def submit_confirmed_proposal(self, proposal_id: str) -> dict:
        row = self._db.get_proposal(proposal_id)
        if row is None:
            raise PaperTradingError("Proposal not found.")
        if row["status"] != OrderProposalStatus.CONFIRMED.value:
            raise PaperTradingError("Proposal must be CONFIRMED before submission.")

        client_order_id = row["client_order_id"]
        existing_local = self._db.get_paper_order_by_client_id(client_order_id)
        if existing_local:
            return self.synchronize_order(existing_local.id)

        existing_alpaca = self._order_manager.get_order_by_client_order_id(client_order_id)
        if existing_alpaca:
            order_id = self._db.save_paper_order(
                strategy_id=row["strategy_id"],
                proposal_id=proposal_id,
                client_order_id=client_order_id,
                symbol=row["symbol"],
                side=row["side"],
                quantity=row["quantity"],
                status=str(existing_alpaca["status"]).upper(),
                alpaca_order_id=existing_alpaca["alpaca_order_id"],
                submitted_at=existing_alpaca.get("submitted_at"),
            )
            return self.synchronize_order(order_id)

        try:
            result = self._order_manager.submit_market_order(
                symbol=row["symbol"],
                quantity=row["quantity"],
                side=row["side"],
                client_order_id=client_order_id,
            )
        except Exception as exc:
            self._db.update_proposal_status(proposal_id, OrderProposalStatus.UNKNOWN)
            logger.error("Order submission uncertain for proposal %s.", proposal_id)
            raise PaperTradingError(
                f"Submission result uncertain. Order marked UNKNOWN. {exc}"
            ) from exc

        order_id = self._db.save_paper_order(
            strategy_id=row["strategy_id"],
            proposal_id=proposal_id,
            client_order_id=client_order_id,
            symbol=row["symbol"],
            side=row["side"],
            quantity=row["quantity"],
            status=str(result["status"]).upper(),
            alpaca_order_id=result["alpaca_order_id"],
            submitted_at=result.get("submitted_at"),
        )

        if row["side"].upper() == "BUY":
            reserve_amount = to_decimal(row["estimated_notional"])
            self._ledger.reserve_funds(
                row["strategy_id"],
                reserve_amount,
                "proposal",
                proposal_id,
            )

        self._db.update_proposal_status(
            proposal_id,
            OrderProposalStatus.SUBMITTED,
            submitted_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info("Submitted paper order for proposal %s.", proposal_id)
        return self.synchronize_order(order_id)

    def synchronize_order(self, order_id: int) -> dict:
        local_order = self._db.get_paper_order(order_id)
        if local_order is None:
            raise PaperTradingError(f"Order {order_id} not found.")

        alpaca_id = local_order.alpaca_order_id
        if not alpaca_id:
            existing = self._order_manager.get_order_by_client_order_id(local_order.client_order_id)
            if existing:
                alpaca_id = existing["alpaca_order_id"]
            else:
                raise PaperTradingError("No Alpaca order ID available for synchronization.")

        broker_order = self._order_manager.synchronize_order(alpaca_id)
        status = str(broker_order["status"]).upper().replace("ORDERSTATUS.", "")
        now = datetime.now(timezone.utc).isoformat()

        self._db.update_paper_order(
            order_id,
            alpaca_order_id=broker_order["alpaca_order_id"],
            status=status,
            filled_quantity=broker_order["filled_quantity"],
            filled_average_price=broker_order["filled_average_price"],
            filled_at=broker_order.get("filled_at"),
            failure_message=broker_order.get("failure_message"),
            raw_status=status,
            last_synced_at=now,
        )

        self._apply_fill_changes(local_order, broker_order)
        return broker_order

    def synchronize_all_open_orders(self) -> list[dict]:
        results = []
        for order in self._db.list_open_paper_orders():
            results.append(self.synchronize_order(order.id))
        return results

    def get_strategy_paper_summary(self, strategy_id: int) -> dict:
        summary = self._portfolio.get_strategy_summary(strategy_id)
        return self._portfolio.format_summary_for_ui(summary)

    def _apply_fill_changes(self, local_order, broker_order: dict) -> None:
        status = str(broker_order["status"]).upper().replace("ORDERSTATUS.", "")
        filled_qty = int(broker_order["filled_quantity"])
        prev_processed = local_order.last_processed_filled_qty
        new_fill_qty = filled_qty - prev_processed

        if new_fill_qty > 0 and broker_order.get("filled_average_price"):
            self._process_fill_increment(
                local_order,
                new_fill_qty,
                to_decimal(broker_order["filled_average_price"]),
            )
            self._db.update_paper_order(
                local_order.id,
                last_processed_filled_qty=filled_qty,
            )

        if status in ("CANCELED", "REJECTED", "EXPIRED"):
            if local_order.side.upper() == "BUY":
                proposal = self._db.get_proposal(local_order.proposal_id or "")
                reserve = to_decimal(proposal["estimated_notional"]) if proposal else Decimal("0")
                if reserve > 0:
                    self._ledger.release_reserved_funds(
                        local_order.strategy_id,
                        reserve,
                        "order",
                        str(local_order.id),
                    )
            self._db.update_paper_order(
                local_order.id,
                failure_message=broker_order.get("failure_message"),
            )

    def _process_fill_increment(
        self,
        local_order,
        quantity: int,
        fill_price: Decimal,
    ) -> None:
        ref_type = "order"
        ref_id = f"{local_order.id}-fill-{local_order.last_processed_filled_qty + quantity}"
        cost = fill_price * to_decimal(quantity)

        if local_order.side.upper() == "BUY":
            proposal = self._db.get_proposal(local_order.proposal_id or "")
            reserve = to_decimal(proposal["estimated_notional"]) if proposal else cost
            self._ledger.record_buy_debit(
                local_order.strategy_id,
                cost,
                ref_type,
                ref_id,
                release_reserve=reserve,
            )
            self._update_buy_position(local_order, quantity, fill_price)
        else:
            self._ledger.record_sell_credit(local_order.strategy_id, cost, ref_type, ref_id)
            self._update_sell_position(local_order, quantity, fill_price)

    def _update_buy_position(self, local_order, quantity: int, fill_price: Decimal) -> None:
        row = self._db.get_strategy_position(local_order.strategy_id, local_order.symbol)
        if row:
            old_qty = int(row["quantity"])
            old_cost = to_decimal(row["cost_basis"])
            new_qty = old_qty + quantity
            add_cost = fill_price * to_decimal(quantity)
            new_cost = old_cost + add_cost
            avg = new_cost / to_decimal(new_qty) if new_qty else Decimal("0")
        else:
            new_qty = quantity
            new_cost = fill_price * to_decimal(quantity)
            avg = fill_price
        self._db.upsert_strategy_position(
            local_order.strategy_id,
            local_order.symbol,
            new_qty,
            avg,
            new_cost,
            to_decimal(row["realized_profit_loss"]) if row else Decimal("0"),
        )

    def _update_sell_position(self, local_order, quantity: int, fill_price: Decimal) -> None:
        row = self._db.get_strategy_position(local_order.strategy_id, local_order.symbol)
        if not row:
            return
        old_qty = int(row["quantity"])
        cost_basis = to_decimal(row["cost_basis"])
        avg_entry = to_decimal(row["average_entry_price"])
        realized = to_decimal(row["realized_profit_loss"])
        sold_cost = avg_entry * to_decimal(quantity)
        proceeds = fill_price * to_decimal(quantity)
        realized += proceeds - sold_cost
        new_qty = max(old_qty - quantity, 0)
        new_cost = avg_entry * to_decimal(new_qty) if new_qty else Decimal("0")
        self._db.upsert_strategy_position(
            local_order.strategy_id,
            local_order.symbol,
            new_qty,
            avg_entry if new_qty else Decimal("0"),
            new_cost,
            realized,
        )

    def _require_strategy(self, strategy_id: int):
        strategy = self._db.get_strategy(strategy_id)
        if strategy is None:
            raise PaperTradingError(f"Strategy {strategy_id} not found.")
        return strategy

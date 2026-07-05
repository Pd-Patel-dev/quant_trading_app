"""Strategy lifecycle transitions and deletion eligibility."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from core.exceptions import (
    ActiveSymbolConflictError,
    InvalidStrategyTransitionError,
    StrategyDeletionBlockedError,
    StrategyError,
    StrategyNotFoundError,
)
from core.models import StrategyDeletionEligibility, StrategyRecord, StrategyStatus
from data.database import DatabaseManager
from market_data.models import AssetType
from portfolio.allocation_manager import AllocationManager
from strategies.registry import get_registry

logger = logging.getLogger(__name__)

LIFECYCLE_EVENTS = {
    "STRATEGY_ACTIVATED": "STRATEGY_ACTIVATED",
    "STRATEGY_PAUSED": "STRATEGY_PAUSED",
    "STRATEGY_RESUMED": "STRATEGY_RESUMED",
    "STRATEGY_STOPPED": "STRATEGY_STOPPED",
    "STRATEGY_ARCHIVED": "STRATEGY_ARCHIVED",
    "STRATEGY_RESTORED": "STRATEGY_RESTORED",
    "STRATEGY_PERMANENTLY_DELETED": "STRATEGY_PERMANENTLY_DELETED",
    "STRATEGY_DELETE_BLOCKED": "STRATEGY_DELETE_BLOCKED",
}

_ACTIVATE_FROM = {StrategyStatus.DRAFT, StrategyStatus.PAUSED}
_PAUSE_FROM = {StrategyStatus.ACTIVE}
_RESUME_FROM = {StrategyStatus.PAUSED}
_STOP_FROM = {StrategyStatus.ACTIVE, StrategyStatus.PAUSED, StrategyStatus.DRAFT}
_ARCHIVE_FROM = {StrategyStatus.DRAFT, StrategyStatus.PAUSED, StrategyStatus.STOPPED}
_RESTORE_FROM = {StrategyStatus.ARCHIVED}


class StrategyLifecycleService:
    """Manage strategy status transitions with validation and audit logging."""

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database
        self._allocation = AllocationManager(database)
        self._registry = get_registry()

    def get_strategy(self, strategy_id: int) -> StrategyRecord:
        strategy = self._db.get_strategy(strategy_id)
        if strategy is None:
            raise StrategyNotFoundError(f"Strategy {strategy_id} not found.")
        return strategy

    def list_strategies(
        self,
        statuses: list[StrategyStatus] | None = None,
        *,
        include_archived: bool = False,
    ) -> list[StrategyRecord]:
        return self._db.list_strategies_filtered(
            statuses=statuses,
            include_archived=include_archived,
        )

    def activate_strategy(self, strategy_id: int) -> StrategyRecord:
        strategy = self.get_strategy(strategy_id)
        if strategy.status not in _ACTIVATE_FROM:
            raise InvalidStrategyTransitionError(
                f"Cannot activate strategy from status {strategy.status.value}."
            )
        self._validate_activation(strategy)
        return self._transition(
            strategy,
            StrategyStatus.ACTIVE,
            event_type=LIFECYCLE_EVENTS["STRATEGY_ACTIVATED"],
            activated_at=self._now(),
            clear_paused_at=True,
            clear_deactivated_reason=True,
        )

    def pause_strategy(self, strategy_id: int, reason: str | None = None) -> StrategyRecord:
        strategy = self.get_strategy(strategy_id)
        if strategy.status not in _PAUSE_FROM:
            raise InvalidStrategyTransitionError(
                f"Cannot pause strategy from status {strategy.status.value}."
            )
        return self._transition(
            strategy,
            StrategyStatus.PAUSED,
            event_type=LIFECYCLE_EVENTS["STRATEGY_PAUSED"],
            paused_at=self._now(),
            deactivated_reason=reason,
            disable_automation=True,
        )

    def resume_strategy(self, strategy_id: int) -> StrategyRecord:
        strategy = self.get_strategy(strategy_id)
        if strategy.status not in _RESUME_FROM:
            raise InvalidStrategyTransitionError(
                f"Cannot resume strategy from status {strategy.status.value}."
            )
        self._validate_activation(strategy)
        return self._transition(
            strategy,
            StrategyStatus.ACTIVE,
            event_type=LIFECYCLE_EVENTS["STRATEGY_RESUMED"],
            activated_at=self._now(),
            clear_paused_at=True,
            clear_deactivated_reason=True,
        )

    def stop_strategy(self, strategy_id: int, reason: str | None = None) -> StrategyRecord:
        strategy = self.get_strategy(strategy_id)
        if strategy.status not in _STOP_FROM:
            raise InvalidStrategyTransitionError(
                f"Cannot stop strategy from status {strategy.status.value}."
            )
        return self._transition(
            strategy,
            StrategyStatus.STOPPED,
            event_type=LIFECYCLE_EVENTS["STRATEGY_STOPPED"],
            stopped_at=self._now(),
            deactivated_reason=reason,
            disable_automation=True,
        )

    def archive_strategy(self, strategy_id: int) -> StrategyRecord:
        strategy = self.get_strategy(strategy_id)
        if strategy.status == StrategyStatus.ACTIVE:
            raise InvalidStrategyTransitionError(
                "Active strategies must be paused or stopped before archiving."
            )
        if strategy.status not in _ARCHIVE_FROM:
            raise InvalidStrategyTransitionError(
                f"Cannot archive strategy from status {strategy.status.value}."
            )
        open_orders = self._db.count_open_orders_for_strategy(strategy_id)
        if open_orders > 0:
            raise InvalidStrategyTransitionError(
                "Cannot archive while open orders exist. Wait for orders to resolve or stop the strategy."
            )
        return self._transition(
            strategy,
            StrategyStatus.ARCHIVED,
            event_type=LIFECYCLE_EVENTS["STRATEGY_ARCHIVED"],
            archived_at=self._now(),
            disable_automation=True,
        )

    def restore_strategy(self, strategy_id: int) -> StrategyRecord:
        strategy = self.get_strategy(strategy_id)
        if strategy.status not in _RESTORE_FROM:
            raise InvalidStrategyTransitionError(
                f"Cannot restore strategy from status {strategy.status.value}."
            )
        return self._transition(
            strategy,
            StrategyStatus.DRAFT,
            event_type=LIFECYCLE_EVENTS["STRATEGY_RESTORED"],
            clear_archived_at=True,
            disable_automation=True,
        )

    def get_deletion_eligibility(self, strategy_id: int) -> StrategyDeletionEligibility:
        strategy = self.get_strategy(strategy_id)
        blocking: list[str] = []
        counts = self._db.count_strategy_related_records(strategy_id)

        if strategy.status != StrategyStatus.DRAFT:
            blocking.append(
                f"Status is {strategy.status.value}; only DRAFT strategies may be permanently deleted."
            )
        if strategy.is_active:
            blocking.append("Strategy is marked active.")

        meaningful_ledger = self._db.count_meaningful_ledger_entries(strategy_id)
        if meaningful_ledger > 0:
            blocking.append(f"{meaningful_ledger} non-allocation ledger entries exist.")
            counts["meaningful_ledger_entries"] = meaningful_ledger

        if counts.get("ledger_entries", 0) > 1:
            blocking.append(f"{counts['ledger_entries']} ledger entries exist.")

        for key, label in (
            ("signals", "signals"),
            ("order_proposals", "order proposals"),
            ("paper_orders", "paper orders"),
            ("positions", "open positions"),
            ("crypto_ledger_entries", "crypto ledger entries"),
            ("crypto_positions", "crypto positions"),
            ("automation_audit", "automation audit records"),
            ("lifecycle_events", "lifecycle audit events"),
        ):
            if counts.get(key, 0) > 0:
                blocking.append(f"{counts[key]} related {label} exist.")

        can_delete = not blocking
        return StrategyDeletionEligibility(
            can_delete=can_delete,
            strategy_id=strategy_id,
            blocking_reasons=blocking,
            related_counts=counts,
            recommended_action="DELETE" if can_delete else "ARCHIVE",
        )

    def permanently_delete_strategy(self, strategy_id: int) -> None:
        eligibility = self.get_deletion_eligibility(strategy_id)
        if not eligibility.can_delete:
            self._db.append_strategy_lifecycle_event(
                strategy_id=strategy_id,
                event_type=LIFECYCLE_EVENTS["STRATEGY_DELETE_BLOCKED"],
                previous_status=StrategyStatus.DRAFT.value,
                new_status=None,
                reason="; ".join(eligibility.blocking_reasons),
                details={"related_counts": eligibility.related_counts},
            )
            raise StrategyDeletionBlockedError(
                "This strategy has related history and cannot be permanently deleted. Archive it instead."
            )

        strategy = self.get_strategy(strategy_id)
        snapshot = {
            "strategy_id": strategy_id,
            "name": strategy.name,
            "symbol": strategy.symbol,
            "asset_type": strategy.asset_type,
        }
        self._db.append_strategy_lifecycle_event(
            strategy_id=None,
            event_type=LIFECYCLE_EVENTS["STRATEGY_PERMANENTLY_DELETED"],
            previous_status=strategy.status.value,
            new_status=None,
            details=snapshot,
        )
        self._db.permanently_remove_strategy(strategy_id)
        logger.info("Permanently deleted unused draft strategy %s.", strategy_id)

    def get_strategy_summary(self, strategy_id: int) -> dict[str, Any]:
        strategy = self.get_strategy(strategy_id)
        pos_qty = self._db.get_strategy_position_quantity(strategy_id, strategy.symbol)
        open_orders = self._db.count_open_orders_for_strategy(strategy_id)
        eligibility = self.get_deletion_eligibility(strategy_id)
        events = self._db.list_strategy_lifecycle_events(strategy_id)
        return {
            "strategy": strategy,
            "position_quantity": pos_qty,
            "open_order_count": open_orders,
            "deletion_eligibility": eligibility,
            "lifecycle_events": events,
        }

    def _validate_activation(self, strategy: StrategyRecord) -> None:
        if strategy.status == StrategyStatus.ARCHIVED:
            raise InvalidStrategyTransitionError("Archived strategies cannot be activated.")
        if strategy.status == StrategyStatus.STOPPED:
            raise InvalidStrategyTransitionError(
                "Stopped strategies cannot be activated. Restore or create a new strategy."
            )
        try:
            params = json.loads(strategy.parameters_json)
            self._registry.validate_parameters(strategy.strategy_type, params)
        except Exception as exc:
            raise StrategyError(f"Strategy configuration is invalid: {exc}") from exc

        asset_type = getattr(strategy, "asset_type", AssetType.STOCK.value)
        if asset_type == AssetType.CRYPTO.value:
            if not strategy.crypto_paper_trading_approved:
                raise StrategyError("Strategy must be approved for crypto paper trading.")
        elif not strategy.paper_trading_approved:
            raise StrategyError("Strategy must be approved for paper trading before activation.")

        existing = self._db.get_active_strategy_for_asset_symbol(
            asset_type,
            strategy.symbol,
            exclude_strategy_id=strategy.id,
        )
        if existing is not None:
            raise ActiveSymbolConflictError(
                f"Symbol {strategy.symbol} is already managed by active strategy '{existing.name}'."
            )

        self._allocation.validate_allocation_amount(
            strategy.allocated_funds,
            exclude_strategy_id=strategy.id,
        )

        if self._db.count_unknown_orders_for_strategy(strategy.id) > 0:
            raise StrategyError("Strategy has unknown orders that must be reconciled before activation.")

        latest = self._db.get_latest_reconciliation()
        if latest:
            warnings = json.loads(latest.get("warnings_json") or "[]")
            for warning in warnings:
                if warning.get("strategy_id") == strategy.id and warning.get("type") in (
                    "LOCAL_EXCEEDS_ALPACA",
                    "ALPACA_EXCEEDS_LOCAL",
                    "NEGATIVE_BALANCE",
                ):
                    raise StrategyError(
                        f"Unresolved reconciliation issue blocks activation: {warning.get('message')}"
                    )

    def _transition(
        self,
        strategy: StrategyRecord,
        new_status: StrategyStatus,
        *,
        event_type: str,
        activated_at: str | None = None,
        paused_at: str | None = None,
        stopped_at: str | None = None,
        archived_at: str | None = None,
        deactivated_reason: str | None = None,
        clear_paused_at: bool = False,
        clear_stopped_at: bool = False,
        clear_archived_at: bool = False,
        clear_deactivated_reason: bool = False,
        disable_automation: bool = False,
    ) -> StrategyRecord:
        previous = strategy.status
        pos_qty = self._db.get_strategy_position_quantity(strategy.id, strategy.symbol)
        open_orders = self._db.count_open_orders_for_strategy(strategy.id)

        self._db.apply_strategy_lifecycle_transition(
            strategy.id,
            new_status,
            activated_at=activated_at,
            paused_at=paused_at,
            stopped_at=stopped_at,
            archived_at=archived_at,
            deactivated_reason=deactivated_reason,
            clear_paused_at=clear_paused_at,
            clear_stopped_at=clear_stopped_at,
            clear_archived_at=clear_archived_at,
            clear_deactivated_reason=clear_deactivated_reason,
            disable_automation=disable_automation,
        )
        self._db.append_strategy_lifecycle_event(
            strategy_id=strategy.id,
            event_type=event_type,
            previous_status=previous.value,
            new_status=new_status.value,
            reason=deactivated_reason,
            position_quantity=pos_qty,
            open_order_count=open_orders,
        )
        updated = self.get_strategy(strategy.id)
        if updated.status != new_status:
            raise StrategyError("Lifecycle transition did not persist.")
        if new_status == StrategyStatus.ACTIVE and not updated.is_active:
            raise StrategyError("Active strategy must have is_active=1.")
        if new_status != StrategyStatus.ACTIVE and updated.is_active:
            raise StrategyError("Non-active strategy must have is_active=0.")
        logger.info(
            "Strategy %s transitioned %s -> %s (%s).",
            strategy.id,
            previous.value,
            new_status.value,
            event_type,
        )
        return updated

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

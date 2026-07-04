"""Core automation orchestration for daily paper-trading workflow."""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from automation.audit_service import AuditService
from automation.models import (
    AuditEventType,
    AuditSeverity,
    AutomationRunStatus,
    AutomationRunType,
    ConfirmationMode,
    ProposalSource,
    WorkerRunResult,
)
from automation.safety_service import AutomationSafetyService
from automation.worker_lock import WorkerLock
from config.settings import Settings, get_settings
from core.client_order_id import build_client_order_id
from core.models import (
    OrderProposal,
    OrderProposalStatus,
    SignalType,
    StrategyRecord,
    StrategyStatus,
    to_decimal,
)
from data.alpaca_data import AlpacaMarketDataProvider
from data.database import DatabaseManager
from portfolio.ledger import StrategyLedger
from services.order_proposal_service import OrderProposalService
from services.paper_trading_service import PaperTradingService
from services.signal_service import SignalService

logger = logging.getLogger(__name__)

LOCK_NAMES = {
    AutomationRunType.AFTER_CLOSE_EVALUATION: "after-close-evaluation",
    AutomationRunType.MARKET_OPEN_EXECUTION: "market-open-execution",
    AutomationRunType.ORDER_SYNCHRONIZATION: "order-synchronization",
    AutomationRunType.DAILY_RECONCILIATION: "daily-reconciliation",
}

GLOBAL_ENABLE_PHRASE = "ENABLE AUTOMATED PAPER TRADING"
STRATEGY_ENABLE_PHRASE = "ENABLE PAPER AUTOMATION"
KILL_SWITCH_DISENGAGE_PHRASE = "DISENGAGE PAPER KILL SWITCH"


class AutomationService:
    """Orchestrate automated daily paper-trading workers."""

    def __init__(
        self,
        database: DatabaseManager,
        order_manager: object | None = None,
        data_provider: AlpacaMarketDataProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._db = database
        self._order_manager = order_manager
        self._settings = settings or get_settings()
        self._audit = AuditService(database)
        self._safety = AutomationSafetyService(database, self._settings)
        self._signal_service = SignalService(database, data_provider, order_manager, self._settings)
        self._proposal_service = OrderProposalService(database, order_manager, self._settings)
        self._paper = PaperTradingService(database, order_manager, data_provider, self._settings)
        self._ledger = StrategyLedger(database)
        self._tz = ZoneInfo(self._settings.market_timezone)

    def run_after_close_evaluation(self) -> WorkerRunResult:
        return self._run_worker(AutomationRunType.AFTER_CLOSE_EVALUATION, self._after_close_body)

    def run_market_open_execution(self) -> WorkerRunResult:
        return self._run_worker(AutomationRunType.MARKET_OPEN_EXECUTION, self._market_open_body)

    def run_order_synchronization(self) -> WorkerRunResult:
        return self._run_worker(AutomationRunType.ORDER_SYNCHRONIZATION, self._sync_body)

    def run_daily_reconciliation(self) -> WorkerRunResult:
        return self._run_worker(AutomationRunType.DAILY_RECONCILIATION, self._reconciliation_body)

    def enable_global_automation(self, confirmation_text: str) -> None:
        if confirmation_text.strip() != GLOBAL_ENABLE_PHRASE:
            raise ValueError(f"Confirmation text must be exactly: {GLOBAL_ENABLE_PHRASE}")
        readiness = self.check_readiness()
        if not readiness["ready"]:
            raise ValueError("Readiness checks failed. Resolve issues before enabling automation.")
        self._db.update_automation_settings(automated_paper_trading_enabled=True)
        self._audit.log(
            AuditEventType.AUTOMATION_ENABLED,
            "Global automated paper trading was enabled.",
        )

    def disable_global_automation(self) -> None:
        self._db.update_automation_settings(automated_paper_trading_enabled=False)
        self._audit.log(
            AuditEventType.AUTOMATION_DISABLED,
            "Global automated paper trading was disabled.",
        )

    def engage_kill_switch(self) -> None:
        self._db.update_automation_settings(kill_switch_engaged=True)
        self._audit.log(
            AuditEventType.KILL_SWITCH_ENGAGED,
            "Emergency kill switch was engaged. Automated submissions are blocked.",
            severity=AuditSeverity.WARNING,
        )

    def disengage_kill_switch(self, confirmation_text: str) -> None:
        if confirmation_text.strip() != KILL_SWITCH_DISENGAGE_PHRASE:
            raise ValueError(f"Confirmation text must be exactly: {KILL_SWITCH_DISENGAGE_PHRASE}")
        self._db.update_automation_settings(kill_switch_engaged=False)
        self._audit.log(
            AuditEventType.KILL_SWITCH_DISENGAGED,
            "Emergency kill switch was disengaged.",
        )

    def enable_strategy_automation(self, strategy_id: int, confirmation_text: str) -> None:
        if confirmation_text.strip() != STRATEGY_ENABLE_PHRASE:
            raise ValueError(f"Confirmation text must be exactly: {STRATEGY_ENABLE_PHRASE}")
        strategy = self._require_strategy(strategy_id)
        auto_settings = self._db.get_automation_settings()
        if not auto_settings.automated_paper_trading_enabled:
            raise ValueError("Global automation must be enabled first.")
        if strategy.status != StrategyStatus.ACTIVE:
            raise ValueError("Strategy must be active.")
        if self._db.count_unknown_orders() > 0:
            raise ValueError("Unknown orders must be resolved first.")
        self._db.update_strategy_automation(
            strategy_id,
            automation_enabled=True,
            automation_approved_at=datetime.now(timezone.utc).isoformat(),
            automation_paused_reason=None,
        )
        self._audit.log(
            AuditEventType.STRATEGY_AUTOMATION_ENABLED,
            f"Automation enabled for strategy {strategy.name}.",
            strategy_id=strategy_id,
        )

    def disable_strategy_automation(self, strategy_id: int) -> None:
        strategy = self._require_strategy(strategy_id)
        self._db.update_strategy_automation(
            strategy_id,
            automation_enabled=False,
            automation_paused_reason="Disabled by user",
        )
        self._audit.log(
            AuditEventType.STRATEGY_AUTOMATION_DISABLED,
            f"Automation disabled for strategy {strategy.name}.",
            strategy_id=strategy_id,
        )

    def check_readiness(self) -> dict:
        checks: list[tuple[str, bool, str]] = []
        settings = self._settings

        checks.append(("Credentials configured", settings.alpaca_configured, "Alpaca API keys missing"))
        checks.append(("Paper mode enabled", settings.trading_mode == "paper", "Not in paper mode"))
        checks.append(("Live trading disabled", not settings.live_trading_enabled, "Live trading enabled"))
        checks.append(("Database healthy", self._db.database_exists(), "Database missing"))
        checks.append(("Schema migration current", self._db.schema_version >= 3, "Schema not at v3"))
        try:
            self._db.get_automation_settings()
            checks.append(("Automation settings available", True, ""))
        except Exception:
            checks.append(("Automation settings available", False, "Settings row missing"))

        auto = self._db.get_automation_settings()
        checks.append(("Kill switch status readable", True, ""))

        if self._order_manager:
            try:
                account = self._order_manager.get_account_summary()
                checks.append(("Account active", str(account.get("status", "")).upper() in ("ACTIVE", "ACCOUNTSTATUS.ACTIVE"), "Account inactive"))
                checks.append(("Trading not blocked", not account.get("trading_blocked"), "Trading blocked"))
                self._order_manager.get_market_clock()
                checks.append(("Market clock accessible", True, ""))
            except Exception as exc:
                checks.append(("Account active", False, str(exc)))
                checks.append(("Market clock accessible", False, str(exc)))
        else:
            checks.append(("Account active", False, "Order manager not configured"))
            checks.append(("Market clock accessible", False, "Order manager not configured"))

        total_alloc = float(self._db.get_total_allocated_funds())
        checks.append(
            (
                "Local allocations valid",
                total_alloc <= settings.local_paper_capital_pool,
                f"Allocations ({total_alloc}) exceed pool",
            )
        )

        symbols = [s.symbol for s in self._db.list_strategies()]
        checks.append(("No duplicate strategy symbols", len(symbols) == len(set(symbols)), "Duplicate symbols"))

        checks.append(("No unknown orders", self._db.count_unknown_orders() == 0, "Unknown orders exist"))

        stale_locks = [
            lock for lock in self._db.list_worker_locks()
            if lock["expires_at"] < datetime.now(timezone.utc).isoformat()
        ]
        checks.append(("No stale worker locks", len(stale_locks) == 0, f"{len(stale_locks)} stale locks"))

        limits_ok = (
            auto.maximum_order_notional > 0
            and auto.maximum_orders_per_day > 0
            and auto.maximum_daily_notional > 0
            and auto.maximum_active_positions > 0
        )
        checks.append(("Daily safety limits valid", limits_ok, "Invalid safety limits"))

        passed = [name for name, ok, _ in checks if ok]
        failed = [{"check": name, "reason": reason} for name, ok, reason in checks if not ok]
        ready = len(failed) == 0
        return {"ready": ready, "passed": passed, "failed": failed, "kill_switch_engaged": auto.kill_switch_engaged}

    def get_dashboard_status(self) -> dict:
        auto = self._db.get_automation_settings()
        latest_recon = self._db.get_latest_reconciliation()
        warnings = []
        if latest_recon:
            warnings = json.loads(latest_recon.get("warnings_json") or "[]")
        today = datetime.now(self._tz).date().isoformat()
        return {
            "trading_mode": self._settings.trading_mode,
            "live_trading_enabled": self._settings.live_trading_enabled,
            "automated_paper_trading_enabled": auto.automated_paper_trading_enabled,
            "kill_switch_engaged": auto.kill_switch_engaged,
            "last_after_close": self._db.get_last_automation_run(AutomationRunType.AFTER_CLOSE_EVALUATION),
            "last_market_open": self._db.get_last_automation_run(AutomationRunType.MARKET_OPEN_EXECUTION),
            "last_sync": self._db.get_last_automation_run(AutomationRunType.ORDER_SYNCHRONIZATION),
            "last_reconciliation": self._db.get_last_automation_run(AutomationRunType.DAILY_RECONCILIATION),
            "unknown_orders": self._db.count_unknown_orders(),
            "reconciliation_warnings": len(warnings),
            "orders_submitted_today": self._db.count_automated_orders_submitted_today(today),
            "notional_submitted_today": self._db.sum_automated_notional_submitted_today(today),
            "automated_strategies": sum(
                1 for s in self._db.list_strategies() if getattr(s, "automation_enabled", False)
            ),
            "pending_orders": len(self._db.list_open_paper_orders()),
        }

    def _run_worker(self, run_type: AutomationRunType, body) -> WorkerRunResult:
        run_id = str(uuid.uuid4())
        lock_name = LOCK_NAMES[run_type]
        lock = WorkerLock(self._db, lock_name)
        host = socket.gethostname()
        pid = os.getpid()

        if not lock.acquire():
            self._db.create_automation_run(run_id, run_type, host, pid)
            self._db.complete_automation_run(run_id, AutomationRunStatus.BLOCKED, error_message="Lock held")
            self._audit.log(
                AuditEventType.WORKER_SKIPPED,
                f"Worker {run_type.value} skipped because lock is held.",
                run_id=run_id,
                severity=AuditSeverity.WARNING,
            )
            return WorkerRunResult(run_id=run_id, status=AutomationRunStatus.BLOCKED)

        self._db.create_automation_run(run_id, run_type, host, pid)
        self._audit.log(AuditEventType.WORKER_STARTED, f"Worker {run_type.value} started.", run_id=run_id)

        try:
            result = body(run_id)
            self._db.complete_automation_run(
                run_id,
                result.status,
                strategies_checked=result.strategies_checked,
                signals_generated=result.signals_generated,
                proposals_created=result.proposals_created,
                orders_submitted=result.orders_submitted,
                orders_updated=result.orders_updated,
                warnings_count=result.warnings_count,
                errors_count=result.errors_count,
                summary_json=result.summary,
                error_message=result.error_message,
            )
            event = AuditEventType.WORKER_COMPLETED if result.status != AutomationRunStatus.FAILED else AuditEventType.WORKER_FAILED
            self._audit.log(event, f"Worker {run_type.value} finished with status {result.status.value}.", run_id=run_id)
            return result
        except Exception as exc:
            logger.exception("Worker %s failed.", run_type.value)
            self._db.complete_automation_run(
                run_id,
                AutomationRunStatus.FAILED,
                errors_count=1,
                error_message=str(exc),
            )
            self._audit.log(
                AuditEventType.WORKER_FAILED,
                f"Worker {run_type.value} failed: {exc}",
                run_id=run_id,
                severity=AuditSeverity.ERROR,
            )
            return WorkerRunResult(run_id=run_id, status=AutomationRunStatus.FAILED, errors_count=1, error_message=str(exc))
        finally:
            lock.release()

    def _after_close_body(self, run_id: str) -> WorkerRunResult:
        clock = self._safe_clock()
        if clock is None:
            return WorkerRunResult(run_id=run_id, status=AutomationRunStatus.FAILED, error_message="No market clock")

        if clock.get("is_open"):
            return WorkerRunResult(
                run_id=run_id,
                status=AutomationRunStatus.SKIPPED,
                summary={"reason": "Market still open"},
            )

        if not self._is_weekday_trading_context(clock):
            return WorkerRunResult(
                run_id=run_id,
                status=AutomationRunStatus.SKIPPED,
                summary={"reason": "Not a trading day"},
            )

        auto_settings = self._db.get_automation_settings()
        strategies = [s for s in self._db.list_strategies(StrategyStatus.ACTIVE) if s.strategy_type == "moving_average"]
        signals = 0
        proposals = 0
        warnings = 0

        for strategy in strategies:
            try:
                evaluation = self._signal_service.evaluate_strategy(strategy)
                signals += 1
                self._audit.log(
                    AuditEventType.SIGNAL_EVALUATED,
                    f"Evaluated {strategy.symbol}: {evaluation.latest_signal.value}",
                    run_id=run_id,
                    strategy_id=strategy.id,
                )

                if not evaluation.is_actionable:
                    continue
                if evaluation.latest_signal == SignalType.HOLD:
                    continue

                signal_ts = evaluation.signal_timestamp.isoformat() if evaluation.signal_timestamp else ""
                side = evaluation.latest_signal.value
                if self._db.proposal_exists_for_signal(strategy.id, signal_ts, side):
                    continue

                proposal = self._build_automation_proposal(strategy, evaluation, auto_settings)
                if proposal.status == OrderProposalStatus.BLOCKED:
                    self._audit.log(
                        AuditEventType.PROPOSAL_BLOCKED,
                        f"Automated proposal blocked for {strategy.name}.",
                        run_id=run_id,
                        strategy_id=strategy.id,
                        proposal_id=proposal.proposal_id,
                        details={"reasons": proposal.blocking_reasons},
                    )
                    warnings += 1
                else:
                    proposals += 1
                    self._audit.log(
                        AuditEventType.PROPOSAL_CREATED,
                        f"Automated proposal created for {strategy.name} ({side}).",
                        run_id=run_id,
                        strategy_id=strategy.id,
                        proposal_id=proposal.proposal_id,
                    )
            except Exception as exc:
                logger.warning("Strategy %s evaluation failed: %s", strategy.id, exc)
                warnings += 1

        status = AutomationRunStatus.COMPLETED_WITH_WARNINGS if warnings else AutomationRunStatus.COMPLETED
        return WorkerRunResult(
            run_id=run_id,
            status=status,
            strategies_checked=len(strategies),
            signals_generated=signals,
            proposals_created=proposals,
            warnings_count=warnings,
        )

    def _market_open_body(self, run_id: str) -> WorkerRunResult:
        if self._settings.trading_mode != "paper" or self._settings.live_trading_enabled:
            return WorkerRunResult(run_id=run_id, status=AutomationRunStatus.BLOCKED, error_message="Not paper-only")

        auto_settings = self._db.get_automation_settings()
        if not auto_settings.automated_paper_trading_enabled:
            return WorkerRunResult(run_id=run_id, status=AutomationRunStatus.SKIPPED, summary={"reason": "Automation disabled"})
        if auto_settings.kill_switch_engaged:
            return WorkerRunResult(run_id=run_id, status=AutomationRunStatus.BLOCKED, summary={"reason": "Kill switch engaged"})

        clock = self._safe_clock()
        if clock is None or not clock.get("is_open"):
            return WorkerRunResult(run_id=run_id, status=AutomationRunStatus.SKIPPED, summary={"reason": "Market closed"})

        if not self._post_open_delay_elapsed(clock):
            return WorkerRunResult(run_id=run_id, status=AutomationRunStatus.SKIPPED, summary={"reason": "Post-open delay not elapsed"})

        today = datetime.now(self._tz).date().isoformat()
        daily_count = self._db.count_automated_orders_submitted_today(today)
        daily_notional = self._db.sum_automated_notional_submitted_today(today)
        account = self._safe_account()
        submitted = 0
        warnings = 0

        for row in self._db.list_automation_eligible_proposals():
            if daily_count >= auto_settings.maximum_orders_per_day:
                break
            if daily_notional >= auto_settings.maximum_daily_notional:
                break

            strategy = self._db.get_strategy(row["strategy_id"])
            if strategy is None:
                continue
            proposal = OrderProposalService.proposal_from_row(row)
            proposal.proposal_source = ProposalSource.AUTOMATION.value
            proposal.confirmation_mode = ConfirmationMode.AUTOMATION_POLICY.value
            proposal.strategy_name = strategy.name

            broker_pos = self._safe_broker_position(proposal.symbol)
            portfolio_summary = {"broker_quantity": broker_pos}

            validation = self._safety.validate_automated_submission(
                strategy,
                proposal,
                account,
                clock,
                portfolio_summary,
                trading_day=today,
                daily_order_count=daily_count,
                daily_notional=daily_notional,
            )
            self._db.update_proposal_automation_validation(
                proposal.proposal_id,
                AutomationSafetyService.result_to_json(validation),
                datetime.now(timezone.utc).isoformat(),
                status=OrderProposalStatus.BLOCKED if not validation.is_executable else OrderProposalStatus.PROPOSED,
                automation_eligible=validation.is_executable,
            )

            if not validation.is_executable:
                warnings += 1
                continue

            # Recheck kill switch immediately before submission
            if self._db.get_automation_settings().kill_switch_engaged:
                return WorkerRunResult(
                    run_id=run_id,
                    status=AutomationRunStatus.BLOCKED,
                    orders_submitted=submitted,
                    warnings_count=warnings,
                    summary={"reason": "Kill switch engaged before submission"},
                )

            existing_alpaca = None
            if self._order_manager:
                existing_alpaca = self._order_manager.get_order_by_client_order_id(proposal.client_order_id)

            if existing_alpaca:
                order_id = self._db.save_paper_order(
                    strategy_id=strategy.id,
                    proposal_id=proposal.proposal_id,
                    client_order_id=proposal.client_order_id,
                    symbol=proposal.symbol,
                    side=proposal.side,
                    quantity=proposal.proposed_quantity,
                    status=str(existing_alpaca["status"]).upper(),
                    alpaca_order_id=existing_alpaca["alpaca_order_id"],
                    submitted_at=existing_alpaca.get("submitted_at"),
                    submission_source="AUTOMATION",
                    automation_run_id=run_id,
                )
                self._paper.synchronize_order(order_id)
                submitted += 0
                continue

            self._audit.log(
                AuditEventType.ORDER_SUBMISSION_STARTED,
                f"Submitting automated {proposal.side} order for {proposal.symbol}.",
                run_id=run_id,
                strategy_id=strategy.id,
                proposal_id=proposal.proposal_id,
            )

            try:
                result = self._order_manager.submit_market_order(
                    symbol=proposal.symbol,
                    quantity=proposal.proposed_quantity,
                    side=proposal.side,
                    client_order_id=proposal.client_order_id,
                )
            except Exception as exc:
                self._db.update_proposal_status(proposal.proposal_id, OrderProposalStatus.UNKNOWN)
                self._audit.log(
                    AuditEventType.ORDER_UNKNOWN,
                    f"Submission uncertain for {proposal.symbol}: {exc}",
                    run_id=run_id,
                    strategy_id=strategy.id,
                    proposal_id=proposal.proposal_id,
                    severity=AuditSeverity.ERROR,
                )
                warnings += 1
                continue

            order_id = self._db.save_paper_order(
                strategy_id=strategy.id,
                proposal_id=proposal.proposal_id,
                client_order_id=proposal.client_order_id,
                symbol=proposal.symbol,
                side=proposal.side,
                quantity=proposal.proposed_quantity,
                status=str(result["status"]).upper(),
                alpaca_order_id=result["alpaca_order_id"],
                submitted_at=result.get("submitted_at"),
                submission_source="AUTOMATION",
                automation_run_id=run_id,
            )

            if proposal.side == "BUY":
                self._ledger.reserve_funds(
                    strategy.id,
                    proposal.estimated_notional,
                    "proposal",
                    proposal.proposal_id,
                )

            self._db.update_proposal_status(
                proposal.proposal_id,
                OrderProposalStatus.SUBMITTED,
                submitted_at=datetime.now(timezone.utc).isoformat(),
            )
            self._paper.synchronize_order(order_id)
            submitted += 1
            daily_count += 1
            daily_notional += float(proposal.estimated_notional)
            self._audit.log(
                AuditEventType.ORDER_SUBMITTED,
                f"Automated order submitted for {proposal.symbol}.",
                run_id=run_id,
                strategy_id=strategy.id,
                proposal_id=proposal.proposal_id,
                paper_order_id=order_id,
            )

        status = AutomationRunStatus.COMPLETED_WITH_WARNINGS if warnings else AutomationRunStatus.COMPLETED
        return WorkerRunResult(
            run_id=run_id,
            status=status,
            orders_submitted=submitted,
            warnings_count=warnings,
        )

    def _sync_body(self, run_id: str) -> WorkerRunResult:
        updated = 0
        warnings = 0
        for order in self._db.list_open_paper_orders():
            try:
                self._paper.synchronize_order(order.id)
                updated += 1
                self._audit.log(
                    AuditEventType.ORDER_STATUS_UPDATED,
                    f"Synchronized order {order.id} for {order.symbol}.",
                    run_id=run_id,
                    paper_order_id=order.id,
                    strategy_id=order.strategy_id,
                )
            except Exception as exc:
                warnings += 1
                self._audit.log(
                    AuditEventType.RECONCILIATION_WARNING,
                    f"Failed to sync order {order.id}: {exc}",
                    run_id=run_id,
                    paper_order_id=order.id,
                    severity=AuditSeverity.WARNING,
                )
        status = AutomationRunStatus.COMPLETED_WITH_WARNINGS if warnings else AutomationRunStatus.COMPLETED
        return WorkerRunResult(run_id=run_id, status=status, orders_updated=updated, warnings_count=warnings)

    def _reconciliation_body(self, run_id: str) -> WorkerRunResult:
        warnings = self._collect_reconciliation_warnings()
        self._db.save_reconciliation_result(run_id, warnings)
        for warning in warnings:
            self._audit.log(
                AuditEventType.RECONCILIATION_WARNING,
                warning["message"],
                run_id=run_id,
                strategy_id=warning.get("strategy_id"),
                severity=AuditSeverity.WARNING,
                details=warning,
            )
        status = AutomationRunStatus.COMPLETED_WITH_WARNINGS if warnings else AutomationRunStatus.COMPLETED
        return WorkerRunResult(run_id=run_id, status=status, warnings_count=len(warnings), summary={"warnings": len(warnings)})

    def _collect_reconciliation_warnings(self) -> list[dict]:
        warnings: list[dict] = []
        broker_positions = {}
        if self._order_manager:
            try:
                for pos in self._order_manager.get_all_positions():
                    broker_positions[pos["symbol"]] = int(pos["quantity"])
            except Exception:
                pass

        managed_symbols = set()
        for pos in self._db.list_strategy_positions():
            symbol = pos["symbol"]
            managed_symbols.add(symbol)
            local_qty = int(pos["quantity"])
            broker_qty = broker_positions.get(symbol, 0)
            if local_qty > broker_qty:
                warnings.append({
                    "type": "LOCAL_EXCEEDS_ALPACA",
                    "message": f"Local quantity for {symbol} ({local_qty}) exceeds Alpaca ({broker_qty}).",
                    "strategy_id": pos["strategy_id"],
                    "symbol": symbol,
                })
            if broker_qty > local_qty:
                warnings.append({
                    "type": "ALPACA_EXCEEDS_LOCAL",
                    "message": f"Alpaca quantity for {symbol} ({broker_qty}) exceeds locally managed ({local_qty}).",
                    "strategy_id": pos["strategy_id"],
                    "symbol": symbol,
                })

        for symbol, qty in broker_positions.items():
            if symbol not in managed_symbols and qty > 0:
                warnings.append({
                    "type": "UNMANAGED_ALPACA_POSITION",
                    "message": f"Alpaca holds {qty} shares of {symbol} with no local strategy position.",
                    "symbol": symbol,
                })

        if self._db.count_unknown_orders() > 0:
            warnings.append({
                "type": "UNKNOWN_ORDERS",
                "message": "One or more orders have UNKNOWN status.",
            })

        total_alloc = float(self._db.get_total_allocated_funds())
        if total_alloc > self._settings.local_paper_capital_pool:
            warnings.append({
                "type": "ALLOCATION_EXCEEDS_POOL",
                "message": f"Total allocations ({total_alloc}) exceed configured pool.",
            })

        for strategy in self._db.list_strategies():
            if getattr(strategy, "automation_enabled", False) and strategy.status != StrategyStatus.ACTIVE:
                warnings.append({
                    "type": "AUTOMATION_ON_INACTIVE",
                    "message": f"Strategy {strategy.name} has automation enabled but is inactive.",
                    "strategy_id": strategy.id,
                })
            balance = self._ledger.get_available_cash(strategy.id)
            if balance < 0:
                warnings.append({
                    "type": "NEGATIVE_BALANCE",
                    "message": f"Strategy {strategy.name} has negative available cash.",
                    "strategy_id": strategy.id,
                })

        return warnings

    def _build_automation_proposal(
        self,
        strategy: StrategyRecord,
        evaluation,
        auto_settings,
    ) -> OrderProposal:
        manual = self._proposal_service.build_proposal(strategy, evaluation, persist=False)
        blocking = list(manual.blocking_reasons)
        eligible = manual.is_executable

        if auto_settings.kill_switch_engaged:
            blocking.append("Kill switch is engaged.")
            eligible = False
        if not strategy.automation_enabled:
            blocking.append("Strategy automation is not enabled.")
            eligible = False
        if not auto_settings.automated_paper_trading_enabled:
            blocking.append("Global automation is disabled.")
            eligible = False

        expires_hours = self._settings.automation_proposal_expiration_hours
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=expires_hours)

        proposal = OrderProposal(
            proposal_id=str(uuid.uuid4()),
            strategy_id=strategy.id,
            strategy_name=strategy.name,
            symbol=strategy.symbol,
            signal=manual.signal,
            signal_timestamp=manual.signal_timestamp,
            side=manual.side,
            proposed_quantity=manual.proposed_quantity,
            estimated_price=manual.estimated_price,
            estimated_notional=manual.estimated_notional,
            allocated_funds=manual.allocated_funds,
            strategy_cash_available=manual.strategy_cash_available,
            strategy_position_quantity=manual.strategy_position_quantity,
            cash_reserve_percent=manual.cash_reserve_percent,
            client_order_id=manual.client_order_id,
            status=OrderProposalStatus.PROPOSED if eligible else OrderProposalStatus.BLOCKED,
            validation_messages=manual.validation_messages,
            blocking_reasons=blocking,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            expires_at=expires_at,
            proposal_source=ProposalSource.AUTOMATION.value,
            confirmation_mode=ConfirmationMode.AUTOMATION_POLICY.value,
            automation_eligible=eligible,
        )
        self._db.save_automation_proposal(proposal)
        return proposal

    def _post_open_delay_elapsed(self, clock: dict) -> bool:
        next_open = clock.get("next_open")
        ts = clock.get("timestamp")
        if not ts:
            return False
        now = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if clock.get("is_open") and next_open:
            open_time = datetime.fromisoformat(str(next_open).replace("Z", "+00:00"))
            if open_time > now:
                session_open = open_time - timedelta(days=1)
            else:
                session_open = open_time
            delay = timedelta(minutes=self._settings.market_open_execution_delay_minutes)
            return now >= session_open + delay
        return clock.get("is_open", False)

    def _is_weekday_trading_context(self, clock: dict) -> bool:
        ts = clock.get("timestamp")
        if not ts:
            return False
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(self._tz)
        return dt.weekday() < 5

    def _safe_clock(self) -> dict | None:
        if self._order_manager is None:
            return None
        try:
            return self._order_manager.get_market_clock()
        except Exception:
            return None

    def _safe_account(self) -> dict | None:
        if self._order_manager is None:
            return None
        try:
            return self._order_manager.get_account_summary()
        except Exception:
            return None

    def _safe_broker_position(self, symbol: str) -> int:
        if self._order_manager is None:
            return 0
        try:
            pos = self._order_manager.get_position(symbol)
            return int(pos["quantity"]) if pos else 0
        except Exception:
            return 0

    def _require_strategy(self, strategy_id: int) -> StrategyRecord:
        strategy = self._db.get_strategy(strategy_id)
        if strategy is None:
            raise ValueError(f"Strategy {strategy_id} not found.")
        return strategy

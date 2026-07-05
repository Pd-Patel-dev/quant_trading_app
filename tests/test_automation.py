"""Automation worker, safety, and migration tests."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from automation.automation_service import AutomationService
from automation.models import AutomationRunStatus, AutomationRunType, ConfirmationMode, ProposalSource
from automation.safety_service import AutomationSafetyService
from automation.worker_lock import WorkerLock
from core.models import (
    EntryPolicy,
    OrderProposal,
    OrderProposalStatus,
    SignalType,
    StrategyStatus,
)
from data.database import DatabaseManager
from services.strategy_service import StrategyService


def _automation_service(temp_db, mock_order_manager) -> AutomationService:
    return AutomationService(temp_db, mock_order_manager, None)


from tests.conftest import create_approved_active_strategy


def _active_strategy(temp_db) -> int:
    return create_approved_active_strategy(temp_db)


def test_schema_version_three(temp_db) -> None:
    assert temp_db.schema_version >= 4
    settings = temp_db.get_automation_settings()
    assert settings.automated_paper_trading_enabled is False
    assert settings.kill_switch_engaged is True


def test_migration_idempotent_v3(temp_db) -> None:
    path = temp_db._database_path
    db2 = DatabaseManager(path)
    assert db2.schema_version == temp_db.schema_version
    assert db2.get_automation_settings().kill_switch_engaged is True


def test_worker_lock_prevents_duplicate(temp_db) -> None:
    lock1 = WorkerLock(temp_db, "test-lock")
    lock2 = WorkerLock(temp_db, "test-lock")
    assert lock1.acquire(ttl_minutes=30) is True
    assert lock2.acquire(ttl_minutes=30) is False
    lock1.release()
    assert lock2.acquire(ttl_minutes=30) is True
    lock2.release()


def test_expired_lock_recovery(temp_db) -> None:
    owner = f"stale-{uuid.uuid4().hex[:6]}"
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with temp_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO automation_worker_locks (lock_name, owner_id, acquired_at, expires_at, heartbeat_at)
            VALUES ('stale-lock', ?, ?, ?, ?)
            """,
            (owner, past, past, past),
        )
    lock = WorkerLock(temp_db, "stale-lock")
    assert lock.acquire() is True
    lock.release()


def test_lock_released_after_exception(temp_db) -> None:
    lock = WorkerLock(temp_db, "exception-lock")
    try:
        with lock:
            lock.acquire()
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    fresh = WorkerLock(temp_db, "exception-lock")
    assert fresh.acquire() is True
    fresh.release()


def test_kill_switch_engaged_by_default(temp_db) -> None:
    assert temp_db.get_automation_settings().kill_switch_engaged is True


def test_global_automation_disabled_by_default(temp_db) -> None:
    assert temp_db.get_automation_settings().automated_paper_trading_enabled is False


def test_strategy_automation_disabled_by_default(temp_db) -> None:
    sid = _active_strategy(temp_db)
    strategy = temp_db.get_strategy(sid)
    assert strategy.automation_enabled is False


def test_after_close_skips_when_market_open(temp_db, mock_order_manager) -> None:
    mock_order_manager.get_market_clock.return_value = {
        "is_open": True,
        "timestamp": "2026-07-02T15:00:00+00:00",
        "next_open": "2026-07-03T13:30:00+00:00",
        "next_close": "2026-07-02T20:00:00+00:00",
    }
    service = _automation_service(temp_db, mock_order_manager)
    result = service.run_after_close_evaluation()
    assert result.status == AutomationRunStatus.SKIPPED
    mock_order_manager.submit_market_order.assert_not_called()


def test_after_close_never_submits_orders(temp_db, mock_order_manager) -> None:
    mock_order_manager.get_market_clock.return_value = {
        "is_open": False,
        "timestamp": "2026-07-02T21:00:00+00:00",
        "next_open": "2026-07-03T13:30:00+00:00",
        "next_close": "2026-07-03T20:00:00+00:00",
    }
    _active_strategy(temp_db)
    service = _automation_service(temp_db, mock_order_manager)
    with patch.object(service._signal_service, "evaluate_strategy") as mock_eval:
        from core.models import SignalEvaluation

        mock_eval.return_value = SignalEvaluation(
            strategy_id=1,
            symbol="SPY",
            current_desired_position=1,
            latest_signal=SignalType.BUY,
            signal_timestamp=datetime(2026, 7, 2),
            short_sma=Decimal("100"),
            long_sma=Decimal("99"),
            close_price=Decimal("101"),
            data_timestamp=datetime(2026, 7, 2),
            is_actionable=True,
            requires_alignment=False,
            explanation="test",
        )
        service.run_after_close_evaluation()
    mock_order_manager.submit_market_order.assert_not_called()


def test_market_open_blocks_when_closed(temp_db, mock_order_manager) -> None:
    mock_order_manager.get_market_clock.return_value = {
        "is_open": False,
        "timestamp": "2026-07-02T21:00:00+00:00",
    }
    temp_db.update_automation_settings(automated_paper_trading_enabled=True, kill_switch_engaged=False)
    service = _automation_service(temp_db, mock_order_manager)
    result = service.run_market_open_execution()
    assert result.status in (AutomationRunStatus.SKIPPED, AutomationRunStatus.BLOCKED)
    mock_order_manager.submit_market_order.assert_not_called()


def test_market_open_blocks_kill_switch(temp_db, mock_order_manager) -> None:
    mock_order_manager.get_market_clock.return_value = {
        "is_open": True,
        "timestamp": "2026-07-02T15:00:00+00:00",
        "next_open": "2026-07-02T13:30:00+00:00",
        "next_close": "2026-07-02T20:00:00+00:00",
    }
    temp_db.update_automation_settings(automated_paper_trading_enabled=True, kill_switch_engaged=True)
    service = _automation_service(temp_db, mock_order_manager)
    result = service.run_market_open_execution()
    assert result.status == AutomationRunStatus.BLOCKED
    mock_order_manager.submit_market_order.assert_not_called()


def test_sync_never_resubmits(temp_db, mock_order_manager) -> None:
    sid = _active_strategy(temp_db)
    temp_db.save_paper_order(
        strategy_id=sid,
        proposal_id="p1",
        client_order_id="client-1",
        symbol="SPY",
        side="BUY",
        quantity=1,
        status="ACCEPTED",
        alpaca_order_id="order-1",
    )
    mock_order_manager.synchronize_order.return_value = {
        "alpaca_order_id": "order-1",
        "status": "accepted",
        "filled_quantity": 0,
        "filled_average_price": None,
        "failure_message": None,
    }
    service = _automation_service(temp_db, mock_order_manager)
    service.run_order_synchronization()
    mock_order_manager.submit_market_order.assert_not_called()


def test_worker_run_recorded(temp_db, mock_order_manager) -> None:
    mock_order_manager.get_market_clock.return_value = {"is_open": False, "timestamp": "2026-07-02T21:00:00+00:00"}
    service = _automation_service(temp_db, mock_order_manager)
    result = service.run_daily_reconciliation()
    runs = temp_db.list_automation_runs(limit=1)
    assert runs
    assert runs[0]["run_id"] == result.run_id


def test_audit_log_append_only(temp_db) -> None:
    temp_db.append_audit_log(
        __import__("automation.models", fromlist=["AuditEventType"]).AuditEventType.WORKER_STARTED,
        "Test event",
    )
    entries = temp_db.list_audit_log(limit=5)
    assert len(entries) >= 1


def test_duplicate_proposal_prevented(temp_db) -> None:
    sid = _active_strategy(temp_db)
    ts = "2026-07-02T16:00:00"
    assert temp_db.proposal_exists_for_signal(sid, ts, "BUY") is False
    proposal = OrderProposal(
        proposal_id=str(uuid.uuid4()),
        strategy_id=sid,
        strategy_name="AutoTest",
        symbol="SPY",
        signal=SignalType.BUY,
        signal_timestamp=datetime.fromisoformat(ts),
        side="BUY",
        proposed_quantity=1,
        estimated_price=Decimal("100"),
        estimated_notional=Decimal("100"),
        allocated_funds=Decimal("5000"),
        strategy_cash_available=Decimal("5000"),
        strategy_position_quantity=0,
        cash_reserve_percent=Decimal("0.05"),
        client_order_id="test-client-id",
        status=OrderProposalStatus.PROPOSED,
        proposal_source=ProposalSource.AUTOMATION.value,
        confirmation_mode=ConfirmationMode.AUTOMATION_POLICY.value,
        automation_eligible=True,
    )
    temp_db.save_automation_proposal(proposal)
    assert temp_db.proposal_exists_for_signal(sid, ts, "BUY") is True


def test_safety_blocks_kill_switch(temp_db, mock_order_manager) -> None:
    sid = _active_strategy(temp_db)
    strategy = temp_db.get_strategy(sid)
    temp_db.update_strategy_automation(sid, automation_enabled=True)
    proposal = OrderProposal(
        proposal_id="p-kill",
        strategy_id=sid,
        strategy_name="AutoTest",
        symbol="SPY",
        signal=SignalType.BUY,
        signal_timestamp=datetime(2026, 7, 2),
        side="BUY",
        proposed_quantity=1,
        estimated_price=Decimal("100"),
        estimated_notional=Decimal("100"),
        allocated_funds=Decimal("5000"),
        strategy_cash_available=Decimal("5000"),
        strategy_position_quantity=0,
        cash_reserve_percent=Decimal("0.05"),
        client_order_id="kill-test",
        status=OrderProposalStatus.PROPOSED,
        proposal_source=ProposalSource.AUTOMATION.value,
        confirmation_mode=ConfirmationMode.AUTOMATION_POLICY.value,
    )
    temp_db.update_automation_settings(automated_paper_trading_enabled=True, kill_switch_engaged=True)
    safety = AutomationSafetyService(temp_db)
    result = safety.validate_automated_submission(
        strategy,
        proposal,
        mock_order_manager.get_account_summary(),
        mock_order_manager.get_market_clock(),
        {"broker_quantity": 0},
    )
    assert not result.is_executable
    assert any("kill switch" in r.lower() for r in result.blocking_reasons)


def test_manual_proposal_cannot_be_manually_confirmed_if_automation(temp_db, mock_order_manager) -> None:
    from core.models import ConfirmationData
    from core.exceptions import PaperTradingError
    from services.paper_trading_service import PaperTradingService

    sid = _active_strategy(temp_db)
    proposal = OrderProposal(
        proposal_id="auto-prop",
        strategy_id=sid,
        strategy_name="AutoTest",
        symbol="SPY",
        signal=SignalType.BUY,
        signal_timestamp=datetime(2026, 7, 2),
        side="BUY",
        proposed_quantity=1,
        estimated_price=Decimal("100"),
        estimated_notional=Decimal("100"),
        allocated_funds=Decimal("5000"),
        strategy_cash_available=Decimal("5000"),
        strategy_position_quantity=0,
        cash_reserve_percent=Decimal("0.05"),
        client_order_id="auto-client",
        status=OrderProposalStatus.PROPOSED,
        blocking_reasons=[],
        proposal_source=ProposalSource.AUTOMATION.value,
        confirmation_mode=ConfirmationMode.AUTOMATION_POLICY.value,
    )
    temp_db.save_automation_proposal(proposal)
    paper = PaperTradingService(temp_db, mock_order_manager, None)
    with pytest.raises(PaperTradingError):
        paper.confirm_proposal(
            "auto-prop",
            ConfirmationData(paper_text="PAPER", paper_trading_acknowledged=True, details_reviewed=True),
        )


def test_reconciliation_detects_unmanaged_position(temp_db, mock_order_manager) -> None:
    mock_order_manager.get_all_positions.return_value = [{"symbol": "AAPL", "quantity": 5}]
    service = _automation_service(temp_db, mock_order_manager)
    warnings = service._collect_reconciliation_warnings()
    assert any(w["type"] == "UNMANAGED_ALPACA_POSITION" for w in warnings)


def test_enable_global_requires_exact_phrase(temp_db, mock_order_manager) -> None:
    service = _automation_service(temp_db, mock_order_manager)
    with pytest.raises(ValueError):
        service.enable_global_automation("WRONG PHRASE")


def test_disengage_kill_switch_requires_exact_phrase(temp_db, mock_order_manager) -> None:
    service = _automation_service(temp_db, mock_order_manager)
    with pytest.raises(ValueError):
        service.disengage_kill_switch("WRONG")

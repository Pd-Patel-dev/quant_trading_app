"""Strategy lifecycle management tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.exceptions import (
    ActiveSymbolConflictError,
    InvalidStrategyTransitionError,
    StrategyDeletionBlockedError,
)
from core.models import EntryPolicy, StrategyStatus
from services.strategy_lifecycle_service import StrategyLifecycleService
from services.strategy_service import StrategyService
from tests.conftest import create_approved_active_strategy, seed_backtest_for_approval


def _lifecycle(db) -> StrategyLifecycleService:
    return StrategyLifecycleService(db)


def _create_draft(db, symbol: str = "SPY", name: str = "Draft") -> int:
    service = StrategyService(db)
    return service.create_moving_average_strategy(
        name,
        symbol,
        50,
        200,
        Decimal("5000"),
        Decimal("0.05"),
        EntryPolicy.WAIT_FOR_NEXT_CROSSOVER,
        activate=False,
    )


def _approve(db, strategy_id: int) -> None:
    seed_backtest_for_approval(db, "moving_average_crossover", "SPY")
    db.update_strategy_paper_approval(
        strategy_id,
        approved=True,
        approved_at=datetime.now(timezone.utc).isoformat(),
    )


def test_draft_to_active(temp_db) -> None:
    strategy_id = _create_draft(temp_db)
    _approve(temp_db, strategy_id)
    updated = _lifecycle(temp_db).activate_strategy(strategy_id)
    assert updated.status == StrategyStatus.ACTIVE
    assert updated.is_active is True
    assert updated.activated_at is not None


def test_active_to_paused(temp_db) -> None:
    strategy_id = create_approved_active_strategy(temp_db)
    updated = _lifecycle(temp_db).pause_strategy(strategy_id)
    assert updated.status == StrategyStatus.PAUSED
    assert updated.is_active is False
    assert updated.paused_at is not None
    assert updated.automation_enabled is False


def test_paused_to_active(temp_db) -> None:
    strategy_id = create_approved_active_strategy(temp_db)
    lifecycle = _lifecycle(temp_db)
    lifecycle.pause_strategy(strategy_id)
    updated = lifecycle.resume_strategy(strategy_id)
    assert updated.status == StrategyStatus.ACTIVE
    assert updated.is_active is True


def test_active_to_stopped(temp_db) -> None:
    strategy_id = create_approved_active_strategy(temp_db)
    updated = _lifecycle(temp_db).stop_strategy(strategy_id)
    assert updated.status == StrategyStatus.STOPPED
    assert updated.is_active is False
    assert updated.stopped_at is not None


def test_paused_to_stopped(temp_db) -> None:
    strategy_id = create_approved_active_strategy(temp_db)
    lifecycle = _lifecycle(temp_db)
    lifecycle.pause_strategy(strategy_id)
    updated = lifecycle.stop_strategy(strategy_id)
    assert updated.status == StrategyStatus.STOPPED


def test_draft_to_stopped(temp_db) -> None:
    strategy_id = _create_draft(temp_db)
    updated = _lifecycle(temp_db).stop_strategy(strategy_id)
    assert updated.status == StrategyStatus.STOPPED


def test_draft_to_archived(temp_db) -> None:
    strategy_id = _create_draft(temp_db)
    updated = _lifecycle(temp_db).archive_strategy(strategy_id)
    assert updated.status == StrategyStatus.ARCHIVED
    assert updated.archived_at is not None


def test_paused_to_archived(temp_db) -> None:
    strategy_id = create_approved_active_strategy(temp_db)
    lifecycle = _lifecycle(temp_db)
    lifecycle.pause_strategy(strategy_id)
    updated = lifecycle.archive_strategy(strategy_id)
    assert updated.status == StrategyStatus.ARCHIVED


def test_stopped_to_archived(temp_db) -> None:
    strategy_id = create_approved_active_strategy(temp_db)
    lifecycle = _lifecycle(temp_db)
    lifecycle.stop_strategy(strategy_id)
    updated = lifecycle.archive_strategy(strategy_id)
    assert updated.status == StrategyStatus.ARCHIVED


def test_archived_to_draft(temp_db) -> None:
    strategy_id = _create_draft(temp_db)
    lifecycle = _lifecycle(temp_db)
    lifecycle.archive_strategy(strategy_id)
    updated = lifecycle.restore_strategy(strategy_id)
    assert updated.status == StrategyStatus.DRAFT
    assert updated.is_active is False
    assert updated.automation_enabled is False


def test_active_cannot_archive(temp_db) -> None:
    strategy_id = create_approved_active_strategy(temp_db)
    with pytest.raises(InvalidStrategyTransitionError):
        _lifecycle(temp_db).archive_strategy(strategy_id)


def test_stopped_cannot_activate(temp_db) -> None:
    strategy_id = _create_draft(temp_db)
    _approve(temp_db, strategy_id)
    lifecycle = _lifecycle(temp_db)
    lifecycle.stop_strategy(strategy_id)
    with pytest.raises(InvalidStrategyTransitionError):
        lifecycle.activate_strategy(strategy_id)


def test_is_active_sync(temp_db) -> None:
    strategy_id = create_approved_active_strategy(temp_db)
    active = temp_db.get_strategy(strategy_id)
    assert active.status == StrategyStatus.ACTIVE
    assert active.is_active is True
    _lifecycle(temp_db).pause_strategy(strategy_id)
    paused = temp_db.get_strategy(strategy_id)
    assert paused.status == StrategyStatus.PAUSED
    assert paused.is_active is False


def test_pause_with_open_position(temp_db) -> None:
    strategy_id = create_approved_active_strategy(temp_db)
    temp_db.upsert_strategy_position(strategy_id, "SPY", 10, Decimal("100"), Decimal("1000"), Decimal("0"))
    updated = _lifecycle(temp_db).pause_strategy(strategy_id)
    assert updated.status == StrategyStatus.PAUSED
    pos = temp_db.get_strategy_position(strategy_id, "SPY")
    assert int(pos["quantity"]) == 10


def test_pause_with_pending_order(temp_db) -> None:
    strategy_id = create_approved_active_strategy(temp_db)
    now = datetime.now(timezone.utc).isoformat()
    with temp_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_orders (
                strategy_id, symbol, side, quantity, order_type, status, created_at
            ) VALUES (?, 'SPY', 'BUY', 1, 'market', 'SUBMITTED', ?)
            """,
            (strategy_id, now),
        )
    updated = _lifecycle(temp_db).pause_strategy(strategy_id)
    assert updated.status == StrategyStatus.PAUSED
    assert temp_db.count_open_orders_for_strategy(strategy_id) == 1


def test_stop_disables_automation(temp_db) -> None:
    strategy_id = create_approved_active_strategy(temp_db)
    temp_db.update_strategy_automation(
        strategy_id,
        automation_enabled=True,
        automation_approved_at=now_iso(),
    )
    _lifecycle(temp_db).stop_strategy(strategy_id)
    strategy = temp_db.get_strategy(strategy_id)
    assert strategy.automation_enabled is False


def test_active_symbol_uniqueness(temp_db) -> None:
    create_approved_active_strategy(temp_db, name="First", symbol="SPY")
    strategy_id = _create_draft(temp_db, name="Second")
    _approve(temp_db, strategy_id)
    with pytest.raises(ActiveSymbolConflictError):
        _lifecycle(temp_db).activate_strategy(strategy_id)


def test_paused_does_not_block_activation(temp_db) -> None:
    first_id = create_approved_active_strategy(temp_db, name="First", symbol="SPY")
    _lifecycle(temp_db).pause_strategy(first_id)
    second_id = _create_draft(temp_db, name="Second", symbol="SPY")
    _approve(temp_db, second_id)
    updated = _lifecycle(temp_db).activate_strategy(second_id)
    assert updated.status == StrategyStatus.ACTIVE


def test_stopped_does_not_block_activation(temp_db) -> None:
    first_id = create_approved_active_strategy(temp_db, name="First", symbol="SPY")
    _lifecycle(temp_db).stop_strategy(first_id)
    second_id = _create_draft(temp_db, name="Second", symbol="SPY")
    _approve(temp_db, second_id)
    updated = _lifecycle(temp_db).activate_strategy(second_id)
    assert updated.status == StrategyStatus.ACTIVE


def test_empty_draft_can_delete(temp_db) -> None:
    strategy_id = _create_draft(temp_db)
    lifecycle = _lifecycle(temp_db)
    assert lifecycle.get_deletion_eligibility(strategy_id).can_delete is True
    lifecycle.permanently_delete_strategy(strategy_id)
    assert temp_db.get_strategy(strategy_id) is None


def test_draft_with_signal_cannot_delete(temp_db) -> None:
    strategy_id = _create_draft(temp_db)
    now = datetime.now(timezone.utc).isoformat()
    with temp_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO strategy_signals (
                strategy_id, symbol, signal, signal_timestamp, created_at
            ) VALUES (?, 'SPY', 'BUY', ?, ?)
            """,
            (strategy_id, now, now),
        )
    eligibility = _lifecycle(temp_db).get_deletion_eligibility(strategy_id)
    assert eligibility.can_delete is False
    assert eligibility.recommended_action == "ARCHIVE"


def test_active_cannot_delete(temp_db) -> None:
    strategy_id = create_approved_active_strategy(temp_db)
    eligibility = _lifecycle(temp_db).get_deletion_eligibility(strategy_id)
    assert eligibility.can_delete is False


def test_archive_blocks_open_orders(temp_db) -> None:
    strategy_id = create_approved_active_strategy(temp_db)
    lifecycle = _lifecycle(temp_db)
    lifecycle.pause_strategy(strategy_id)
    now = datetime.now(timezone.utc).isoformat()
    with temp_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_orders (
                strategy_id, symbol, side, quantity, order_type, status, created_at
            ) VALUES (?, 'SPY', 'BUY', 1, 'market', 'SUBMITTED', ?)
            """,
            (strategy_id, now),
        )
    with pytest.raises(InvalidStrategyTransitionError):
        lifecycle.archive_strategy(strategy_id)


def test_list_strategies_excludes_archived_by_default(temp_db) -> None:
    strategy_id = _create_draft(temp_db)
    _lifecycle(temp_db).archive_strategy(strategy_id)
    visible = temp_db.list_strategies()
    assert all(s.status != StrategyStatus.ARCHIVED for s in visible)
    archived = temp_db.list_strategies(StrategyStatus.ARCHIVED)
    assert len(archived) == 1


def test_migration_v7_idempotent(temp_db) -> None:
    assert temp_db.schema_version >= 7
    db2 = __import__("data.database", fromlist=["DatabaseManager"]).DatabaseManager(temp_db._database_path)
    assert db2.schema_version == temp_db.schema_version


def test_legacy_is_active_migration(temp_db) -> None:
    strategy_id = _create_draft(temp_db)
    with temp_db.connect() as conn:
        conn.execute(
            "UPDATE strategies SET is_active = 1, status = 'PAUSED' WHERE id = ?",
            (strategy_id,),
        )
    from data.migrations import apply_migrations

    with temp_db.connect() as conn:
        apply_migrations(conn)
    strategy = temp_db.get_strategy(strategy_id)
    assert strategy.status == StrategyStatus.PAUSED
    assert strategy.is_active is False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

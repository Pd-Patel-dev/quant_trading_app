"""Allocation manager tests."""

from decimal import Decimal

import pytest

from tests.conftest import create_approved_active_strategy, seed_backtest_for_approval
from core.exceptions import AllocationError
from core.models import EntryPolicy, StrategyStatus
from portfolio.allocation_manager import AllocationManager
from services.strategy_service import StrategyService


def _create_strategy(db, allocation: Decimal = Decimal("5000")) -> int:
    service = StrategyService(db)
    return service.create_moving_average_strategy(
        name="Test Strategy",
        symbol="SPY",
        short_window=50,
        long_window=200,
        allocated_funds=allocation,
        cash_reserve_percent=Decimal("0.05"),
        entry_policy=EntryPolicy.WAIT_FOR_NEXT_CROSSOVER,
        activate=False,
    )


def test_allocation_creation(temp_db) -> None:
    manager = AllocationManager(temp_db)
    strategy_id = _create_strategy(temp_db)
    assert manager.get_strategy_available_cash(strategy_id) == Decimal("5000")


def test_allocation_increase(temp_db) -> None:
    strategy_id = _create_strategy(temp_db)
    manager = AllocationManager(temp_db)
    manager.increase_allocation(strategy_id, Decimal("1000"))
    strategy = temp_db.get_strategy(strategy_id)
    assert strategy.allocated_funds == Decimal("6000")


def test_allocation_decrease(temp_db) -> None:
    strategy_id = _create_strategy(temp_db)
    manager = AllocationManager(temp_db)
    manager.decrease_allocation(strategy_id, Decimal("1000"))
    strategy = temp_db.get_strategy(strategy_id)
    assert strategy.allocated_funds == Decimal("4000")


def test_overspending_prevention(temp_db) -> None:
    manager = AllocationManager(temp_db)
    with pytest.raises(AllocationError):
        manager.validate_allocation_amount(Decimal("200000"))


def test_cannot_reduce_below_committed(temp_db) -> None:
    strategy_id = _create_strategy(temp_db, Decimal("5000"))
    manager = AllocationManager(temp_db)
    temp_db.upsert_strategy_position(strategy_id, "SPY", 10, Decimal("100"), Decimal("1000"), Decimal("0"))
    with pytest.raises(AllocationError):
        manager.decrease_allocation(strategy_id, Decimal("4500"))


def test_only_one_active_strategy_per_symbol(temp_db) -> None:
    id1 = create_approved_active_strategy(temp_db, name="S1")
    service = StrategyService(temp_db)
    seed_backtest_for_approval(temp_db, "moving_average_crossover", "SPY")
    id2 = service.create_moving_average_strategy(
        "S2", "SPY", 50, 200, Decimal("5000"), Decimal("0.05"),
        EntryPolicy.WAIT_FOR_NEXT_CROSSOVER, activate=False,
    )
    temp_db.update_strategy_paper_approval(id2, approved=True, approved_at="2026-01-01T00:00:00+00:00")
    with pytest.raises(AllocationError):
        service.activate(id2)
    assert id1 > 0

"""Order proposal service tests."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest

from core.models import EntryPolicy, OrderProposalStatus, SignalEvaluation, SignalType, StrategyStatus
from services.order_proposal_service import OrderProposalService
from services.strategy_service import StrategyService


from tests.conftest import create_approved_active_strategy, seed_backtest_for_approval


def _strategy_and_eval(temp_db, *, paused: bool = False, local_qty: int = 0):
    service = StrategyService(temp_db)
    strategy_id = service.create_moving_average_strategy(
        "Prop Test", "SPY", 50, 200, Decimal("5000"), Decimal("0.05"),
        EntryPolicy.WAIT_FOR_NEXT_CROSSOVER, activate=False,
    )
    seed_backtest_for_approval(temp_db, "moving_average_crossover", "SPY")
    temp_db.update_strategy_paper_approval(strategy_id, approved=True, approved_at="2026-01-01T00:00:00+00:00")
    service.activate(strategy_id)
    if paused:
        service.pause(strategy_id)
    if local_qty:
        temp_db.upsert_strategy_position(strategy_id, "SPY", local_qty, Decimal("100"), Decimal("1000"), Decimal("0"))
    strategy = temp_db.get_strategy(strategy_id)
    evaluation = SignalEvaluation(
        strategy_id=strategy_id,
        symbol="SPY",
        current_desired_position=1 if local_qty == 0 else 1,
        latest_signal=SignalType.BUY if local_qty == 0 else SignalType.SELL,
        signal_timestamp=datetime(2026, 7, 2),
        short_sma=Decimal("101"),
        long_sma=Decimal("100"),
        close_price=Decimal("100"),
        data_timestamp=datetime(2026, 7, 2),
        is_actionable=True,
        requires_alignment=False,
        explanation="test",
    )
    return strategy, evaluation


def test_blocks_when_strategy_paused(temp_db, mock_order_manager) -> None:
    strategy, evaluation = _strategy_and_eval(temp_db, paused=True)
    proposal_service = OrderProposalService(temp_db, mock_order_manager)
    proposal = proposal_service.build_proposal(strategy, evaluation)
    assert proposal.status == OrderProposalStatus.BLOCKED
    assert any("not active" in r.lower() or "PAUSED" in r for r in proposal.blocking_reasons)


def test_blocks_buy_when_already_holding(temp_db, mock_order_manager) -> None:
    strategy, evaluation = _strategy_and_eval(temp_db, local_qty=5)
    evaluation = SignalEvaluation(
        strategy_id=strategy.id,
        symbol="SPY",
        current_desired_position=1,
        latest_signal=SignalType.BUY,
        signal_timestamp=datetime(2026, 7, 2),
        short_sma=Decimal("101"),
        long_sma=Decimal("100"),
        close_price=Decimal("100"),
        data_timestamp=datetime(2026, 7, 2),
        is_actionable=True,
        requires_alignment=False,
        explanation="test",
    )
    proposal_service = OrderProposalService(temp_db, mock_order_manager)
    proposal = proposal_service.build_proposal(strategy, evaluation)
    assert any("already holds" in r.lower() for r in proposal.blocking_reasons)


def test_deterministic_client_order_id(temp_db, mock_order_manager) -> None:
    from core.client_order_id import build_client_order_id

    id1 = build_client_order_id("qslab", 1, "SPY", "BUY", datetime(2026, 7, 2))
    id2 = build_client_order_id("qslab", 1, "SPY", "BUY", datetime(2026, 7, 2))
    assert id1 == id2
    assert id1.startswith("qslab-1-spy-buy-20260702")


def test_blocks_insufficient_cash(temp_db, mock_order_manager) -> None:
    strategy, evaluation = _strategy_and_eval(temp_db)
    from portfolio.ledger import StrategyLedger

    ledger = StrategyLedger(temp_db)
    ledger.reserve_funds(strategy.id, Decimal("4800"), "test", "1")
    proposal_service = OrderProposalService(temp_db, mock_order_manager)
    proposal = proposal_service.build_proposal(strategy, evaluation)
    assert proposal.proposed_quantity == 0 or proposal.blocking_reasons

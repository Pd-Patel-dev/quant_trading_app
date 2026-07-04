"""Paper trading service tests."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest

from core.exceptions import PaperTradingError
from core.models import ConfirmationData, EntryPolicy, OrderProposalStatus
from services.paper_trading_service import PaperTradingService
from services.strategy_service import StrategyService


def _setup_confirmed_proposal(temp_db, mock_order_manager):
    service = StrategyService(temp_db)
    strategy_id = service.create_strategy(
        "PT", "SPY", 50, 200, Decimal("5000"), Decimal("0.05"),
        EntryPolicy.WAIT_FOR_NEXT_CROSSOVER, activate=True,
    )
    from core.models import OrderProposal, SignalType

    proposal = OrderProposal(
        proposal_id="prop-1",
        strategy_id=strategy_id,
        strategy_name="PT",
        symbol="SPY",
        signal=SignalType.BUY,
        signal_timestamp=datetime(2026, 7, 2),
        side="BUY",
        proposed_quantity=10,
        estimated_price=Decimal("100"),
        estimated_notional=Decimal("1000"),
        allocated_funds=Decimal("5000"),
        strategy_cash_available=Decimal("5000"),
        strategy_position_quantity=0,
        cash_reserve_percent=Decimal("0.05"),
        client_order_id="qslab-1-spy-buy-20260702-abc123",
        status=OrderProposalStatus.CONFIRMED,
        blocking_reasons=[],
        expires_at=datetime(2026, 12, 31),
    )
    temp_db.save_proposal(proposal)
    temp_db.update_proposal_status("prop-1", OrderProposalStatus.CONFIRMED)
    paper = PaperTradingService(temp_db, mock_order_manager, None)
    return paper, "prop-1"


def test_requires_paper_confirmation_text(temp_db, mock_order_manager) -> None:
    paper, proposal_id = _setup_confirmed_proposal(temp_db, mock_order_manager)
    temp_db.update_proposal_status(proposal_id, OrderProposalStatus.PROPOSED)
    with pytest.raises(PaperTradingError):
        paper.confirm_proposal(
            proposal_id,
            ConfirmationData(paper_text="WRONG", paper_trading_acknowledged=True, details_reviewed=True),
        )


def test_submission_idempotent_when_local_order_exists(temp_db, mock_order_manager) -> None:
    paper, proposal_id = _setup_confirmed_proposal(temp_db, mock_order_manager)
    row = temp_db.get_proposal(proposal_id)
    temp_db.save_paper_order(
        strategy_id=row["strategy_id"],
        proposal_id=proposal_id,
        client_order_id=row["client_order_id"],
        symbol="SPY",
        side="BUY",
        quantity=10,
        status="ACCEPTED",
        alpaca_order_id="existing-order",
    )
    mock_order_manager.synchronize_order.return_value = {
        "alpaca_order_id": "existing-order",
        "status": "accepted",
        "filled_quantity": 0,
        "filled_average_price": None,
        "failure_message": None,
    }
    result = paper.submit_confirmed_proposal(proposal_id)
    mock_order_manager.submit_market_order.assert_not_called()
    assert result["alpaca_order_id"] == "existing-order"


def test_buy_fill_updates_ledger_once(temp_db, mock_order_manager) -> None:
    paper, proposal_id = _setup_confirmed_proposal(temp_db, mock_order_manager)
    row = temp_db.get_proposal(proposal_id)
    order_id = temp_db.save_paper_order(
        strategy_id=row["strategy_id"],
        proposal_id=proposal_id,
        client_order_id=row["client_order_id"],
        symbol="SPY",
        side="BUY",
        quantity=10,
        status="SUBMITTED",
        alpaca_order_id="ord-1",
    )
    mock_order_manager.synchronize_order.return_value = {
        "alpaca_order_id": "ord-1",
        "status": "filled",
        "filled_quantity": 10,
        "filled_average_price": 100.0,
        "filled_at": "2026-07-02T16:00:00+00:00",
        "failure_message": None,
    }
    paper.synchronize_order(order_id)
    paper.synchronize_order(order_id)
    debits = [e for e in temp_db.get_ledger_entries(row["strategy_id"]) if e["entry_type"] == "BUY_DEBIT"]
    assert len(debits) == 1

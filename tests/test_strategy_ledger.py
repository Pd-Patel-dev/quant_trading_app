"""Strategy ledger tests."""

from decimal import Decimal

from core.models import EntryPolicy, LedgerEntryType
from portfolio.ledger import StrategyLedger
from services.strategy_service import StrategyService


def _strategy_id(temp_db) -> int:
    service = StrategyService(temp_db)
    return service.create_moving_average_strategy(
        "Ledger Test", "SPY", 50, 200, Decimal("5000"), Decimal("0.05"),
        EntryPolicy.WAIT_FOR_NEXT_CROSSOVER, activate=False,
    )


def test_ledger_rebuilds_balance(temp_db) -> None:
    strategy_id = _strategy_id(temp_db)
    ledger = StrategyLedger(temp_db)
    temp_db.append_ledger_entry(strategy_id, LedgerEntryType.RESERVE, Decimal("1000"), Decimal("4000"), "reserve")
    assert ledger.get_cash_balance(strategy_id) == Decimal("4000")
    assert ledger.get_reserved_cash(strategy_id) == Decimal("1000")
    assert ledger.get_available_cash(strategy_id) == Decimal("3000")


def test_release_reserved_funds(temp_db) -> None:
    strategy_id = _strategy_id(temp_db)
    ledger = StrategyLedger(temp_db)
    ledger.reserve_funds(strategy_id, Decimal("1000"), "order", "1")
    ledger.release_reserved_funds(strategy_id, Decimal("1000"), "order", "1")
    assert ledger.get_reserved_cash(strategy_id) == Decimal("0")
    assert ledger.get_available_cash(strategy_id) == Decimal("5000")


def test_buy_debit_idempotent(temp_db) -> None:
    strategy_id = _strategy_id(temp_db)
    ledger = StrategyLedger(temp_db)
    ledger.record_buy_debit(strategy_id, Decimal("1000"), "order", "fill-1")
    ledger.record_buy_debit(strategy_id, Decimal("1000"), "order", "fill-1")
    entries = [e for e in temp_db.get_ledger_entries(strategy_id) if e["entry_type"] == "BUY_DEBIT"]
    assert len(entries) == 1

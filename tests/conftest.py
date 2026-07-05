"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.models import EntryPolicy
from data.database import DatabaseManager
from services.strategy_service import StrategyService


@pytest.fixture(autouse=True)
def force_local_paper_capital_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests deterministic: local pool unless a test overrides capital source."""
    monkeypatch.setenv("PAPER_CAPITAL_SOURCE", "local")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)


@pytest.fixture
def temp_db() -> DatabaseManager:
    """Provide an isolated temporary database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = handle.name
    db = DatabaseManager(path)
    yield db
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def mock_order_manager():
    """Mock Alpaca order manager with market open and active account."""
    manager = Mock()
    manager.get_account_summary.return_value = {
        "status": "ACTIVE",
        "cash": 100000.0,
        "buying_power": 400000.0,
        "portfolio_value": 100000.0,
        "equity": 100000.0,
        "trading_blocked": False,
    }
    manager.get_market_clock.return_value = {
        "is_open": True,
        "timestamp": "2026-07-02T15:00:00+00:00",
        "next_open": "2026-07-03T13:30:00+00:00",
        "next_close": "2026-07-02T20:00:00+00:00",
    }
    manager.get_position.return_value = None
    manager.get_open_orders.return_value = []
    manager.get_order_by_client_order_id.return_value = None
    manager.synchronize_order.return_value = {
        "alpaca_order_id": "existing-order",
        "status": "accepted",
        "filled_quantity": 0,
        "filled_average_price": None,
        "failure_message": None,
    }
    manager.get_all_positions.return_value = []
    return manager


def seed_backtest_for_approval(database: DatabaseManager, strategy_type: str, symbol: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO backtest_runs (
                strategy_name, symbol, start_date, end_date,
                starting_capital, allocation, final_value,
                total_return_percent, buy_and_hold_return_percent,
                total_trades, win_rate_percent, maximum_drawdown_percent,
                sharpe_ratio, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{strategy_type} research",
                symbol.upper(),
                "2020-01-01",
                "2024-01-01",
                10000.0,
                5000.0,
                11000.0,
                10.0,
                8.0,
                2,
                50.0,
                5.0,
                1.0,
                now,
            ),
        )


def create_approved_active_strategy(
    database: DatabaseManager,
    *,
    name: str = "Test Strategy",
    symbol: str = "SPY",
    short_window: int = 50,
    long_window: int = 200,
    allocated_funds: Decimal = Decimal("5000"),
) -> int:
    service = StrategyService(database)
    strategy_id = service.create_moving_average_strategy(
        name,
        symbol,
        short_window,
        long_window,
        allocated_funds,
        Decimal("0.05"),
        EntryPolicy.WAIT_FOR_NEXT_CROSSOVER,
        activate=False,
    )
    seed_backtest_for_approval(database, "moving_average_crossover", symbol)
    database.update_strategy_paper_approval(
        strategy_id,
        approved=True,
        approved_at=datetime.now(timezone.utc).isoformat(),
    )
    service.activate(strategy_id)
    return strategy_id

"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.database import DatabaseManager


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
    return manager

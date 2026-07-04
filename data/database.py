"""SQLite database manager."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

import numpy as np

from core.models import BacktestResult

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manage SQLite connections and schema initialization."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Open a SQLite connection with foreign keys enabled."""
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create database tables when they do not exist."""
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS strategies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    strategy_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    allocated_funds REAL NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backtest_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    starting_capital REAL NOT NULL,
                    allocation REAL NOT NULL,
                    final_value REAL NOT NULL,
                    total_return_percent REAL NOT NULL,
                    buy_and_hold_return_percent REAL NOT NULL,
                    total_trades INTEGER NOT NULL,
                    win_rate_percent REAL NOT NULL,
                    maximum_drawdown_percent REAL NOT NULL,
                    sharpe_ratio REAL NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id INTEGER,
                    alpaca_order_id TEXT,
                    client_order_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    order_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    submitted_at TEXT,
                    filled_at TEXT,
                    filled_average_price REAL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
                );

                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date TEXT NOT NULL,
                    cash REAL NOT NULL,
                    positions_value REAL NOT NULL,
                    portfolio_value REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def save_backtest_summary(
        self,
        result: BacktestResult,
        configuration_start: str,
        configuration_end: str,
        allocation: float,
    ) -> None:
        """Persist a completed backtest summary without large DataFrames."""
        payload = (
            result.strategy_name,
            result.symbol,
            configuration_start,
            configuration_end,
            _to_python(result.starting_capital),
            _to_python(allocation),
            _to_python(result.final_value),
            _to_python(result.total_return_percent),
            _to_python(result.buy_and_hold_return_percent),
            result.total_trades,
            _to_python(result.win_rate_percent),
            _to_python(result.maximum_drawdown_percent),
            _to_python(result.sharpe_ratio),
            _utc_now(),
        )
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO backtest_runs (
                    strategy_name,
                    symbol,
                    start_date,
                    end_date,
                    starting_capital,
                    allocation,
                    final_value,
                    total_return_percent,
                    buy_and_hold_return_percent,
                    total_trades,
                    win_rate_percent,
                    maximum_drawdown_percent,
                    sharpe_ratio,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )

    def get_recent_backtests(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recent backtest summaries."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM backtest_runs
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def database_exists(self) -> bool:
        """Return True when the database file exists."""
        return self._database_path.exists()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_python(value: Any) -> Any:
    if isinstance(value, (np.generic,)):
        return value.item()
    return value

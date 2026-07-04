"""Idempotent SQLite schema migrations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "Initial milestone 1 schema",
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
        """,
    ),
    (
        2,
        "Milestone 2 paper trading schema",
        """
        CREATE TABLE IF NOT EXISTS schema_versions (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS strategy_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER NOT NULL,
            entry_type TEXT NOT NULL,
            amount REAL NOT NULL,
            balance_after REAL NOT NULL,
            reference_type TEXT,
            reference_id TEXT,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (strategy_id) REFERENCES strategies(id)
        );

        CREATE TABLE IF NOT EXISTS strategy_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            average_entry_price REAL NOT NULL DEFAULT 0,
            cost_basis REAL NOT NULL DEFAULT 0,
            realized_profit_loss REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (strategy_id) REFERENCES strategies(id),
            UNIQUE(strategy_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS strategy_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            signal TEXT NOT NULL,
            signal_timestamp TEXT NOT NULL,
            short_sma REAL,
            long_sma REAL,
            close_price REAL,
            data_timestamp TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (strategy_id) REFERENCES strategies(id),
            UNIQUE(strategy_id, signal_timestamp, signal)
        );

        CREATE TABLE IF NOT EXISTS order_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id TEXT NOT NULL UNIQUE,
            strategy_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            signal TEXT NOT NULL,
            signal_timestamp TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            estimated_price REAL NOT NULL,
            estimated_notional REAL NOT NULL,
            client_order_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            validation_json TEXT,
            blocking_reasons_json TEXT,
            expires_at TEXT,
            confirmed_at TEXT,
            submitted_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (strategy_id) REFERENCES strategies(id)
        );
        """,
    ),
]


def _column_exists(connection: sqlite3.Connection, table: str, column: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def apply_migrations(connection: sqlite3.Connection) -> int:
    """Apply pending migrations and return the latest schema version."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_versions (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        )
        """
    )

    current_version = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_versions"
    ).fetchone()[0]

    for version, description, sql in MIGRATIONS:
        if version <= current_version:
            continue
        connection.executescript(sql)
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_versions (version, applied_at, description)
            VALUES (?, ?, ?)
            """,
            (version, datetime.now(timezone.utc).isoformat(), description),
        )
        current_version = version

    _upgrade_strategies_table(connection)
    _upgrade_paper_orders_table(connection)
    _upgrade_portfolio_snapshots_table(connection)
    _ensure_indexes(connection)

    if current_version < 2:
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_versions (version, applied_at, description)
            VALUES (?, ?, ?)
            """,
            (2, datetime.now(timezone.utc).isoformat(), "Milestone 2 paper trading schema"),
        )
        current_version = 2

    _upgrade_milestone3(connection)

    if current_version < 3:
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_versions (version, applied_at, description)
            VALUES (?, ?, ?)
            """,
            (3, datetime.now(timezone.utc).isoformat(), "Milestone 3 automation schema"),
        )
        current_version = 3

    return current_version


def _upgrade_milestone3(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS automation_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            automated_paper_trading_enabled INTEGER NOT NULL DEFAULT 0,
            kill_switch_engaged INTEGER NOT NULL DEFAULT 1,
            maximum_order_notional REAL NOT NULL DEFAULT 500.0,
            maximum_orders_per_day INTEGER NOT NULL DEFAULT 3,
            maximum_daily_notional REAL NOT NULL DEFAULT 1000.0,
            maximum_active_positions INTEGER NOT NULL DEFAULT 3,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS automation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            run_type TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            strategies_checked INTEGER NOT NULL DEFAULT 0,
            signals_generated INTEGER NOT NULL DEFAULT 0,
            proposals_created INTEGER NOT NULL DEFAULT 0,
            orders_submitted INTEGER NOT NULL DEFAULT 0,
            orders_updated INTEGER NOT NULL DEFAULT 0,
            warnings_count INTEGER NOT NULL DEFAULT 0,
            errors_count INTEGER NOT NULL DEFAULT 0,
            summary_json TEXT,
            error_message TEXT,
            host_name TEXT,
            process_id INTEGER,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS automation_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            run_id TEXT,
            strategy_id INTEGER,
            proposal_id TEXT,
            paper_order_id INTEGER,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            details_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS automation_worker_locks (
            lock_name TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS automation_reconciliation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            warnings_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    row = connection.execute("SELECT COUNT(*) FROM automation_settings").fetchone()
    if row[0] == 0:
        connection.execute(
            """
            INSERT INTO automation_settings (
                id, automated_paper_trading_enabled, kill_switch_engaged,
                maximum_order_notional, maximum_orders_per_day,
                maximum_daily_notional, maximum_active_positions, updated_at
            ) VALUES (1, 0, 1, 500.0, 3, 1000.0, 3, ?)
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )

    _upgrade_strategies_table_m3(connection)
    _upgrade_order_proposals_table_m3(connection)
    _upgrade_paper_orders_table_m3(connection)


def _upgrade_strategies_table_m3(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "strategies"):
        return
    for column, definition in {
        "automation_enabled": "INTEGER NOT NULL DEFAULT 0",
        "automation_approved_at": "TEXT",
        "automation_paused_reason": "TEXT",
    }.items():
        if not _column_exists(connection, "strategies", column):
            connection.execute(f"ALTER TABLE strategies ADD COLUMN {column} {definition}")


def _upgrade_order_proposals_table_m3(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "order_proposals"):
        return
    for column, definition in {
        "proposal_source": "TEXT NOT NULL DEFAULT 'MANUAL'",
        "confirmation_mode": "TEXT NOT NULL DEFAULT 'MANUAL'",
        "automation_eligible": "INTEGER NOT NULL DEFAULT 0",
        "automation_validation_json": "TEXT",
        "automation_validated_at": "TEXT",
    }.items():
        if not _column_exists(connection, "order_proposals", column):
            connection.execute(f"ALTER TABLE order_proposals ADD COLUMN {column} {definition}")


def _upgrade_paper_orders_table_m3(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "paper_orders"):
        return
    for column, definition in {
        "submission_source": "TEXT NOT NULL DEFAULT 'MANUAL'",
        "last_processed_filled_quantity": "INTEGER NOT NULL DEFAULT 0",
        "automation_run_id": "TEXT",
    }.items():
        if not _column_exists(connection, "paper_orders", column):
            connection.execute(f"ALTER TABLE paper_orders ADD COLUMN {column} {definition}")


def _upgrade_strategies_table(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "strategies"):
        return
    columns = {
        "cash_reserve_percent": "REAL NOT NULL DEFAULT 0.05",
        "entry_policy": "TEXT NOT NULL DEFAULT 'WAIT_FOR_NEXT_CROSSOVER'",
        "status": "TEXT NOT NULL DEFAULT 'DRAFT'",
        "activated_at": "TEXT",
        "paused_at": "TEXT",
    }
    for column, definition in columns.items():
        if not _column_exists(connection, "strategies", column):
            connection.execute(f"ALTER TABLE strategies ADD COLUMN {column} {definition}")


def _upgrade_paper_orders_table(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "paper_orders"):
        return
    columns = {
        "proposal_id": "TEXT",
        "time_in_force": "TEXT NOT NULL DEFAULT 'day'",
        "filled_quantity": "INTEGER NOT NULL DEFAULT 0",
        "failure_message": "TEXT",
        "raw_status": "TEXT",
        "last_synced_at": "TEXT",
        "updated_at": "TEXT",
        "last_processed_filled_qty": "INTEGER NOT NULL DEFAULT 0",
    }
    for column, definition in columns.items():
        if not _column_exists(connection, "paper_orders", column):
            connection.execute(f"ALTER TABLE paper_orders ADD COLUMN {column} {definition}")


def _upgrade_portfolio_snapshots_table(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "portfolio_snapshots"):
        return
    columns = {
        "broker_cash": "REAL",
        "broker_positions_value": "REAL",
        "broker_portfolio_value": "REAL",
        "managed_strategy_value": "REAL",
        "unmanaged_position_value": "REAL",
    }
    for column, definition in columns.items():
        if not _column_exists(connection, "portfolio_snapshots", column):
            connection.execute(f"ALTER TABLE portfolio_snapshots ADD COLUMN {column} {definition}")


def _ensure_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_active_strategy_symbol
        ON strategies(symbol)
        WHERE is_active = 1 AND status = 'ACTIVE'
        """
    )

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

    _upgrade_milestone4(connection)

    if current_version < 4:
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_versions (version, applied_at, description)
            VALUES (?, ?, ?)
            """,
            (4, datetime.now(timezone.utc).isoformat(), "Milestone 4 research schema"),
        )
        current_version = 4

    _upgrade_milestone5(connection)

    if current_version < 5:
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_versions (version, applied_at, description)
            VALUES (?, ?, ?)
            """,
            (5, datetime.now(timezone.utc).isoformat(), "Milestone 5 market data warehouse"),
        )
        current_version = 5

    _upgrade_milestone6(connection)

    if current_version < 6:
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_versions (version, applied_at, description)
            VALUES (?, ?, ?)
            """,
            (6, datetime.now(timezone.utc).isoformat(), "Milestone 6 crypto paper trading"),
        )
        current_version = 6

    _upgrade_milestone7(connection)

    if current_version < 7:
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_versions (version, applied_at, description)
            VALUES (?, ?, ?)
            """,
            (7, datetime.now(timezone.utc).isoformat(), "Milestone 7 strategy lifecycle management"),
        )
        current_version = 7

    _upgrade_milestone8(connection)

    if current_version < 8:
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_versions (version, applied_at, description)
            VALUES (?, ?, ?)
            """,
            (8, datetime.now(timezone.utc).isoformat(), "Milestone 8 crypto EMA trend strategy"),
        )
        current_version = 8

    _sync_strategy_status_fields(connection)
    _ensure_indexes(connection)

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


def _upgrade_milestone4(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS strategy_definitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_type TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            version TEXT NOT NULL,
            category TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            is_available INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS strategy_research_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            run_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            starting_capital REAL NOT NULL,
            configuration_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS strategy_research_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            parameters_json TEXT NOT NULL,
            final_value REAL NOT NULL,
            total_return_percent REAL NOT NULL,
            annualized_return_percent REAL NOT NULL,
            maximum_drawdown_percent REAL NOT NULL,
            annualized_volatility_percent REAL NOT NULL,
            sharpe_ratio REAL NOT NULL,
            sortino_ratio REAL NOT NULL,
            profit_factor REAL NOT NULL,
            completed_trades INTEGER NOT NULL,
            win_rate_percent REAL NOT NULL,
            average_holding_period_days REAL NOT NULL,
            exposure_percent REAL NOT NULL,
            result_summary_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS walk_forward_windows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            window_number INTEGER NOT NULL,
            training_start TEXT NOT NULL,
            training_end TEXT NOT NULL,
            testing_start TEXT NOT NULL,
            testing_end TEXT NOT NULL,
            training_return_percent REAL NOT NULL,
            testing_return_percent REAL NOT NULL,
            testing_drawdown_percent REAL NOT NULL,
            testing_sharpe_ratio REAL NOT NULL,
            testing_trade_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    _upgrade_strategies_table_m4(connection)


def _upgrade_strategies_table_m4(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "strategies"):
        return
    for column, definition in {
        "paper_trading_approved": "INTEGER NOT NULL DEFAULT 0",
        "paper_trading_approved_at": "TEXT",
    }.items():
        if not _column_exists(connection, "strategies", column):
            connection.execute(f"ALTER TABLE strategies ADD COLUMN {column} {definition}")
    connection.execute(
        """
        UPDATE strategies
        SET paper_trading_approved = 1
        WHERE status = 'ACTIVE' AND paper_trading_approved = 0
        """
    )


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


def _upgrade_milestone5(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            base_currency TEXT,
            quote_currency TEXT,
            display_name TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            first_seen_at TEXT NOT NULL,
            last_validated_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(asset_type, symbol)
        );

        CREATE TABLE IF NOT EXISTS market_bars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp_utc TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            trade_count INTEGER,
            vwap REAL,
            source TEXT NOT NULL,
            feed TEXT NOT NULL,
            adjustment TEXT NOT NULL,
            download_run_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (asset_id) REFERENCES assets(id),
            UNIQUE(asset_id, timeframe, timestamp_utc, source, feed, adjustment)
        );

        CREATE TABLE IF NOT EXISTS market_data_coverage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            timeframe TEXT NOT NULL,
            source TEXT NOT NULL,
            feed TEXT NOT NULL,
            adjustment TEXT NOT NULL,
            coverage_start_utc TEXT,
            coverage_end_utc TEXT,
            row_count INTEGER NOT NULL DEFAULT 0,
            last_downloaded_at TEXT,
            last_validated_at TEXT,
            data_quality_status TEXT NOT NULL DEFAULT 'UNKNOWN',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (asset_id) REFERENCES assets(id),
            UNIQUE(asset_id, timeframe, source, feed, adjustment)
        );

        CREATE TABLE IF NOT EXISTS market_data_download_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            asset_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            requested_start_utc TEXT NOT NULL,
            requested_end_utc TEXT NOT NULL,
            actual_start_utc TEXT,
            actual_end_utc TEXT,
            rows_received INTEGER NOT NULL DEFAULT 0,
            rows_inserted INTEGER NOT NULL DEFAULT 0,
            rows_updated INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            provider TEXT NOT NULL,
            feed TEXT NOT NULL,
            error_message TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS market_data_quality_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp_utc TEXT,
            issue_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            description TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            resolved_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (asset_id) REFERENCES assets(id)
        );

        CREATE TABLE IF NOT EXISTS multi_asset_backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            run_mode TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            asset_count INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            starting_capital REAL NOT NULL,
            configuration_json TEXT NOT NULL,
            successful_assets INTEGER NOT NULL DEFAULT 0,
            failed_assets INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS multi_asset_backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            parameters_json TEXT NOT NULL,
            allocation REAL NOT NULL,
            final_value REAL NOT NULL,
            profit_loss REAL NOT NULL,
            total_return_percent REAL NOT NULL,
            annualized_return_percent REAL NOT NULL,
            maximum_drawdown_percent REAL NOT NULL,
            annualized_volatility_percent REAL NOT NULL,
            sharpe_ratio REAL NOT NULL,
            sortino_ratio REAL NOT NULL,
            completed_trades INTEGER NOT NULL,
            win_rate_percent REAL NOT NULL,
            exposure_percent REAL NOT NULL,
            first_bar TEXT NOT NULL,
            last_bar TEXT NOT NULL,
            bar_count INTEGER NOT NULL,
            data_source_status TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_market_bars_asset_time
            ON market_bars(asset_id, timeframe, timestamp_utc);
        CREATE INDEX IF NOT EXISTS idx_market_bars_timestamp
            ON market_bars(timestamp_utc);
        CREATE INDEX IF NOT EXISTS idx_market_bars_source
            ON market_bars(source);
        """
    )


def _upgrade_milestone6(connection: sqlite3.Connection) -> None:
    if _table_exists(connection, "strategies"):
        for column, definition in {
            "asset_type": "TEXT NOT NULL DEFAULT 'STOCK'",
            "quote_currency": "TEXT",
            "crypto_paper_trading_approved": "INTEGER NOT NULL DEFAULT 0",
            "crypto_approved_at": "TEXT",
        }.items():
            if not _column_exists(connection, "strategies", column):
                connection.execute(f"ALTER TABLE strategies ADD COLUMN {column} {definition}")

    if _table_exists(connection, "order_proposals"):
        for column, definition in {
            "asset_type": "TEXT NOT NULL DEFAULT 'STOCK'",
            "sizing_mode": "TEXT",
            "notional_text": "TEXT",
            "quantity_text": "TEXT",
            "estimated_price_text": "TEXT",
            "estimated_base_quantity_text": "TEXT",
            "estimated_fee_text": "TEXT",
            "estimated_fee_currency": "TEXT",
            "time_in_force": "TEXT",
        }.items():
            if not _column_exists(connection, "order_proposals", column):
                connection.execute(f"ALTER TABLE order_proposals ADD COLUMN {column} {definition}")

    if _table_exists(connection, "paper_orders"):
        for column, definition in {
            "asset_type": "TEXT NOT NULL DEFAULT 'STOCK'",
            "notional_text": "TEXT",
            "requested_quantity_text": "TEXT",
            "filled_quantity_text": "TEXT",
            "filled_average_price_text": "TEXT",
            "fee_amount_text": "TEXT",
            "fee_currency": "TEXT",
            "fee_status": "TEXT",
            "last_processed_filled_quantity_text": "TEXT",
            "last_processed_fee_amount_text": "TEXT",
        }.items():
            if not _column_exists(connection, "paper_orders", column):
                connection.execute(f"ALTER TABLE paper_orders ADD COLUMN {column} {definition}")

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS crypto_strategy_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER NOT NULL,
            entry_type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            currency TEXT NOT NULL,
            amount_text TEXT NOT NULL,
            balance_after_text TEXT NOT NULL,
            reference_type TEXT,
            reference_id TEXT,
            idempotency_key TEXT UNIQUE,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (strategy_id) REFERENCES strategies(id)
        );

        CREATE TABLE IF NOT EXISTS crypto_strategy_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            quantity_text TEXT NOT NULL,
            average_entry_price_text TEXT NOT NULL,
            cost_basis_usd_text TEXT NOT NULL,
            realized_profit_loss_usd_text TEXT NOT NULL DEFAULT '0',
            updated_at TEXT NOT NULL,
            FOREIGN KEY (strategy_id) REFERENCES strategies(id),
            UNIQUE(strategy_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS crypto_reconciliation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            local_position_count INTEGER NOT NULL DEFAULT 0,
            alpaca_position_count INTEGER NOT NULL DEFAULT 0,
            warning_count INTEGER NOT NULL DEFAULT 0,
            critical_count INTEGER NOT NULL DEFAULT 0,
            summary_json TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS crypto_fee_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            alpaca_activity_id TEXT UNIQUE,
            symbol TEXT NOT NULL,
            fee_amount_text TEXT NOT NULL,
            fee_currency TEXT NOT NULL,
            activity_timestamp TEXT,
            processing_status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (order_id) REFERENCES paper_orders(id)
        );

        CREATE INDEX IF NOT EXISTS idx_crypto_ledger_strategy
            ON crypto_strategy_ledger(strategy_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_crypto_positions_strategy
            ON crypto_strategy_positions(strategy_id);
        """
    )


def _ensure_indexes(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "strategies"):
        return
    connection.execute("DROP INDEX IF EXISTS idx_active_strategy_symbol")
    if _column_exists(connection, "strategies", "asset_type"):
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_strategy_per_asset_symbol
            ON strategies(asset_type, symbol)
            WHERE status = 'ACTIVE'
            """
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_strategies_status ON strategies(status)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_strategies_is_active ON strategies(is_active)"
    )
    if _column_exists(connection, "strategies", "asset_type"):
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_strategies_asset_symbol_status
            ON strategies(asset_type, symbol, status)
            """
        )
    if _column_exists(connection, "strategies", "archived_at"):
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_strategies_archived_at ON strategies(archived_at)"
        )


def _upgrade_milestone7(connection: sqlite3.Connection) -> None:
    if _table_exists(connection, "strategies"):
        for column, definition in {
            "stopped_at": "TEXT",
            "archived_at": "TEXT",
            "deactivated_reason": "TEXT",
            "deleted_at": "TEXT",
        }.items():
            if not _column_exists(connection, "strategies", column):
                connection.execute(f"ALTER TABLE strategies ADD COLUMN {column} {definition}")

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS strategy_lifecycle_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER,
            event_type TEXT NOT NULL,
            previous_status TEXT,
            new_status TEXT,
            reason TEXT,
            position_quantity INTEGER,
            open_order_count INTEGER,
            details_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_strategy_lifecycle_events_strategy
            ON strategy_lifecycle_events(strategy_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_strategy_lifecycle_events_type
            ON strategy_lifecycle_events(event_type);
        """
    )

    from core.models import StrategyStatus

    valid = {s.value for s in StrategyStatus}
    now = datetime.now(timezone.utc).isoformat()
    rows = connection.execute("SELECT id, status FROM strategies").fetchall()
    for row in rows:
        if row["status"] not in valid:
            connection.execute(
                "UPDATE strategies SET status = 'DRAFT', is_active = 0, updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )

    connection.execute(
        "UPDATE strategies SET is_active = 1, updated_at = ? WHERE status = 'ACTIVE'",
        (now,),
    )
    connection.execute(
        """
        UPDATE strategies SET is_active = 0, updated_at = ?
        WHERE status != 'ACTIVE' AND is_active != 0
        """,
        (now,),
    )

    rows = connection.execute(
        """
        SELECT id, status, is_active FROM strategies
        WHERE (status = 'ACTIVE' AND is_active != 1)
           OR (status != 'ACTIVE' AND is_active = 1)
        """
    ).fetchall()
    for row in rows:
        fixed_active = 1 if row["status"] == "ACTIVE" else 0
        connection.execute(
            "UPDATE strategies SET is_active = ?, updated_at = ? WHERE id = ?",
            (fixed_active, now, row["id"]),
        )

    missing_status = connection.execute(
        "SELECT id, is_active FROM strategies WHERE status IS NULL OR status = ''"
    ).fetchall()
    for row in missing_status:
        new_status = "ACTIVE" if row["is_active"] else "DRAFT"
        new_active = 1 if new_status == "ACTIVE" else 0
        connection.execute(
            """
            UPDATE strategies SET status = ?, is_active = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_status, new_active, now, row["id"]),
        )


def _upgrade_milestone8(connection: sqlite3.Connection) -> None:
    if _table_exists(connection, "strategies"):
        for column, definition in {
            "risk_model_type": "TEXT",
            "stop_loss_percent_text": "TEXT",
            "risk_per_trade_percent_text": "TEXT",
        }.items():
            if not _column_exists(connection, "strategies", column):
                connection.execute(f"ALTER TABLE strategies ADD COLUMN {column} {definition}")

    if _table_exists(connection, "crypto_strategy_positions"):
        for column, definition in {
            "entry_price_text": "TEXT",
            "stop_price_text": "TEXT",
            "stop_loss_percent_text": "TEXT",
            "risk_budget_text": "TEXT",
            "initial_position_notional_text": "TEXT",
            "entry_filled_at": "TEXT",
        }.items():
            if not _column_exists(connection, "crypto_strategy_positions", column):
                connection.execute(
                    f"ALTER TABLE crypto_strategy_positions ADD COLUMN {column} {definition}"
                )

    if _table_exists(connection, "strategy_signals"):
        for column, definition in {
            "signal_reason": "TEXT",
            "entry_price_reference_text": "TEXT",
            "stop_price_reference_text": "TEXT",
        }.items():
            if not _column_exists(connection, "strategy_signals", column):
                connection.execute(f"ALTER TABLE strategy_signals ADD COLUMN {column} {definition}")

    if _table_exists(connection, "strategy_research_results"):
        for column, definition in {
            "stop_loss_exit_count": "INTEGER",
            "ema_exit_count": "INTEGER",
            "average_planned_risk_text": "TEXT",
            "largest_loss_percent": "REAL",
            "largest_win_percent": "REAL",
            "stop_slippage_impact_percent": "REAL",
        }.items():
            if not _column_exists(connection, "strategy_research_results", column):
                connection.execute(
                    f"ALTER TABLE strategy_research_results ADD COLUMN {column} {definition}"
                )


def _sync_strategy_status_fields(connection: sqlite3.Connection) -> None:
    """Keep status and is_active aligned on every migration run."""
    now = datetime.now(timezone.utc).isoformat()
    connection.execute(
        "UPDATE strategies SET is_active = 1, updated_at = ? WHERE status = 'ACTIVE' AND is_active != 1",
        (now,),
    )
    connection.execute(
        """
        UPDATE strategies SET is_active = 0, updated_at = ?
        WHERE status != 'ACTIVE' AND is_active = 1
        """,
        (now,),
    )


def _sync_strategy_status_fields(connection: sqlite3.Connection) -> None:
    """Keep status and is_active aligned on every migration run."""
    now = datetime.now(timezone.utc).isoformat()
    connection.execute(
        "UPDATE strategies SET is_active = 1, updated_at = ? WHERE status = 'ACTIVE' AND is_active != 1",
        (now,),
    )
    connection.execute(
        """
        UPDATE strategies SET is_active = 0, updated_at = ?
        WHERE status != 'ACTIVE' AND is_active = 1
        """,
        (now,),
    )

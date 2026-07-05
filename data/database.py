"""SQLite database manager with migrations and repositories."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Generator

import numpy as np

from core.models import (
    BacktestResult,
    EntryPolicy,
    LedgerEntryType,
    OrderProposal,
    OrderProposalStatus,
    PaperOrderRecord,
    SignalType,
    StrategyRecord,
    StrategyStatus,
    to_decimal,
)
from data.migrations import apply_migrations
from data.automation_database import AutomationDatabaseMixin
from data.crypto_database import CryptoDatabaseMixin
from data.market_data_database import MarketDataDatabaseMixin
from data.research_database import ResearchDatabaseMixin
from data.strategy_lifecycle_database import StrategyLifecycleDatabaseMixin

logger = logging.getLogger(__name__)


class DatabaseManager(
    AutomationDatabaseMixin,
    ResearchDatabaseMixin,
    MarketDataDatabaseMixin,
    CryptoDatabaseMixin,
    StrategyLifecycleDatabaseMixin,
):
    """Manage SQLite connections, schema, and persistence."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_version = self.initialize()

    @property
    def schema_version(self) -> int:
        return self._schema_version

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Open a SQLite connection with foreign keys enabled."""
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 30000")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> int:
        """Create or migrate database tables."""
        with self.connect() as connection:
            version = apply_migrations(connection)
        logger.info("Database initialized at schema version %s.", version)
        return version

    def database_exists(self) -> bool:
        return self._database_path.exists()

    # --- Backtests (Milestone 1) ---

    def save_backtest_summary(
        self,
        result: BacktestResult,
        configuration_start: str,
        configuration_end: str,
        allocation: float,
    ) -> None:
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
                    strategy_name, symbol, start_date, end_date,
                    starting_capital, allocation, final_value,
                    total_return_percent, buy_and_hold_return_percent,
                    total_trades, win_rate_percent, maximum_drawdown_percent,
                    sharpe_ratio, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )

    def get_recent_backtests(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM backtest_runs ORDER BY datetime(created_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    # --- Strategies ---

    def create_strategy(
        self,
        name: str,
        strategy_type: str,
        symbol: str,
        parameters_json: str,
        allocated_funds: Decimal,
        cash_reserve_percent: Decimal,
        entry_policy: EntryPolicy,
        status: StrategyStatus = StrategyStatus.DRAFT,
    ) -> int:
        now = _utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO strategies (
                    name, strategy_type, symbol, parameters_json,
                    allocated_funds, cash_reserve_percent, entry_policy,
                    status, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    strategy_type,
                    symbol,
                    parameters_json,
                    float(allocated_funds),
                    float(cash_reserve_percent),
                    entry_policy.value,
                    status.value,
                    1 if status == StrategyStatus.ACTIVE else 0,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def get_strategy(self, strategy_id: int) -> StrategyRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM strategies WHERE id = ?",
                (strategy_id,),
            ).fetchone()
        return _row_to_strategy(row) if row else None

    def list_strategies(
        self,
        status: StrategyStatus | None = None,
        *,
        include_archived: bool = False,
    ) -> list[StrategyRecord]:
        return self.list_strategies_filtered(
            status=status,
            include_archived=include_archived,
        )

    def update_strategy(
        self,
        strategy_id: int,
        *,
        name: str | None = None,
        allocated_funds: Decimal | None = None,
        cash_reserve_percent: Decimal | None = None,
        entry_policy: EntryPolicy | None = None,
        parameters_json: str | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        if name is not None:
            fields.append("name = ?")
            values.append(name)
        if allocated_funds is not None:
            fields.append("allocated_funds = ?")
            values.append(float(allocated_funds))
        if cash_reserve_percent is not None:
            fields.append("cash_reserve_percent = ?")
            values.append(float(cash_reserve_percent))
        if entry_policy is not None:
            fields.append("entry_policy = ?")
            values.append(entry_policy.value)
        if parameters_json is not None:
            fields.append("parameters_json = ?")
            values.append(parameters_json)
        if not fields:
            return
        fields.append("updated_at = ?")
        values.append(_utc_now())
        values.append(strategy_id)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE strategies SET {', '.join(fields)} WHERE id = ?",
                values,
            )

    def update_strategy_status(
        self,
        strategy_id: int,
        status: StrategyStatus,
        *,
        activated_at: str | None = None,
        paused_at: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE strategies
                SET status = ?, is_active = ?, activated_at = COALESCE(?, activated_at),
                    paused_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    1 if status == StrategyStatus.ACTIVE else 0,
                    activated_at,
                    paused_at,
                    _utc_now(),
                    strategy_id,
                ),
            )

    def get_active_strategy_for_symbol(
        self,
        symbol: str,
        asset_type: str = "STOCK",
        *,
        exclude_strategy_id: int | None = None,
    ) -> StrategyRecord | None:
        return self.get_active_strategy_for_asset_symbol(
            asset_type,
            symbol,
            exclude_strategy_id=exclude_strategy_id,
        )

    def count_strategies_by_status(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM strategies GROUP BY status"
            ).fetchall()
        return {row["status"]: row["count"] for row in rows}

    def get_total_allocated_funds(self) -> Decimal:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(allocated_funds), 0) AS total FROM strategies"
            ).fetchone()
        return to_decimal(row["total"])

    # --- Ledger ---

    def append_ledger_entry(
        self,
        strategy_id: int,
        entry_type: LedgerEntryType,
        amount: Decimal,
        balance_after: Decimal,
        description: str,
        reference_type: str | None = None,
        reference_id: str | None = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO strategy_ledger (
                    strategy_id, entry_type, amount, balance_after,
                    reference_type, reference_id, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_id,
                    entry_type.value,
                    float(amount),
                    float(balance_after),
                    reference_type,
                    reference_id,
                    description,
                    _utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def get_ledger_entries(self, strategy_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM strategy_ledger
                WHERE strategy_id = ?
                ORDER BY id
                """,
                (strategy_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def ledger_entry_exists(
        self,
        strategy_id: int,
        entry_type: LedgerEntryType,
        reference_type: str,
        reference_id: str,
    ) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM strategy_ledger
                WHERE strategy_id = ? AND entry_type = ?
                  AND reference_type = ? AND reference_id = ?
                LIMIT 1
                """,
                (strategy_id, entry_type.value, reference_type, reference_id),
            ).fetchone()
        return row is not None

    # --- Positions ---

    def get_strategy_position(self, strategy_id: int, symbol: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM strategy_positions
                WHERE strategy_id = ? AND symbol = ?
                """,
                (strategy_id, symbol.upper()),
            ).fetchone()
        return dict(row) if row else None

    def upsert_strategy_position(
        self,
        strategy_id: int,
        symbol: str,
        quantity: int,
        average_entry_price: Decimal,
        cost_basis: Decimal,
        realized_profit_loss: Decimal,
    ) -> None:
        now = _utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_positions (
                    strategy_id, symbol, quantity, average_entry_price,
                    cost_basis, realized_profit_loss, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id, symbol) DO UPDATE SET
                    quantity = excluded.quantity,
                    average_entry_price = excluded.average_entry_price,
                    cost_basis = excluded.cost_basis,
                    realized_profit_loss = excluded.realized_profit_loss,
                    updated_at = excluded.updated_at
                """,
                (
                    strategy_id,
                    symbol.upper(),
                    quantity,
                    float(average_entry_price),
                    float(cost_basis),
                    float(realized_profit_loss),
                    now,
                ),
            )

    def list_strategy_positions(self, strategy_id: int | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if strategy_id is not None:
                rows = connection.execute(
                    "SELECT * FROM strategy_positions WHERE strategy_id = ? AND quantity > 0",
                    (strategy_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM strategy_positions WHERE quantity > 0"
                ).fetchall()
        return [dict(row) for row in rows]

    # --- Signals ---

    def save_signal_if_new(
        self,
        strategy_id: int,
        symbol: str,
        signal: SignalType,
        signal_timestamp: str,
        short_sma: float | None,
        long_sma: float | None,
        close_price: float | None,
        data_timestamp: str | None,
    ) -> int | None:
        with self.connect() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO strategy_signals (
                        strategy_id, symbol, signal, signal_timestamp,
                        short_sma, long_sma, close_price, data_timestamp, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        strategy_id,
                        symbol.upper(),
                        signal.value,
                        signal_timestamp,
                        short_sma,
                        long_sma,
                        close_price,
                        data_timestamp,
                        _utc_now(),
                    ),
                )
                return int(cursor.lastrowid)
            except sqlite3.IntegrityError:
                return None

    def get_signals_after(self, strategy_id: int, after_timestamp: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM strategy_signals
                WHERE strategy_id = ? AND signal_timestamp > ?
                ORDER BY signal_timestamp
                """,
                (strategy_id, after_timestamp),
            ).fetchall()
        return [dict(row) for row in rows]

    # --- Proposals ---

    def save_proposal(self, proposal: OrderProposal) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO order_proposals (
                    proposal_id, strategy_id, symbol, signal, signal_timestamp,
                    side, quantity, estimated_price, estimated_notional,
                    client_order_id, status, validation_json, blocking_reasons_json,
                    expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.strategy_id,
                    proposal.symbol,
                    proposal.signal.value,
                    proposal.signal_timestamp.isoformat(),
                    proposal.side,
                    proposal.proposed_quantity,
                    float(proposal.estimated_price),
                    float(proposal.estimated_notional),
                    proposal.client_order_id,
                    proposal.status.value,
                    json.dumps(proposal.validation_messages),
                    json.dumps(proposal.blocking_reasons),
                    proposal.expires_at.isoformat() if proposal.expires_at else None,
                    proposal.created_at.isoformat(),
                    _utc_now(),
                ),
            )

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM order_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_proposal_status(
        self,
        proposal_id: str,
        status: OrderProposalStatus,
        *,
        confirmed_at: str | None = None,
        submitted_at: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE order_proposals
                SET status = ?, confirmed_at = COALESCE(?, confirmed_at),
                    submitted_at = COALESCE(?, submitted_at), updated_at = ?
                WHERE proposal_id = ?
                """,
                (status.value, confirmed_at, submitted_at, _utc_now(), proposal_id),
            )

    def get_active_proposal_for_strategy(self, strategy_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM order_proposals
                WHERE strategy_id = ?
                  AND status IN ('PROPOSED', 'CONFIRMED', 'SUBMITTED', 'UNKNOWN')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (strategy_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_proposal_by_client_order_id(self, client_order_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM order_proposals WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return dict(row) if row else None

    # --- Paper orders ---

    def save_paper_order(
        self,
        strategy_id: int,
        proposal_id: str,
        client_order_id: str,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str = "market",
        time_in_force: str = "day",
        status: str = "SUBMITTED",
        alpaca_order_id: str | None = None,
        submitted_at: str | None = None,
        submission_source: str = "MANUAL",
        automation_run_id: str | None = None,
    ) -> int:
        now = _utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO paper_orders (
                    strategy_id, proposal_id, alpaca_order_id, client_order_id,
                    symbol, side, quantity, order_type, time_in_force, status,
                    submitted_at, filled_quantity, last_processed_filled_qty,
                    submission_source, automation_run_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)
                """,
                (
                    strategy_id,
                    proposal_id,
                    alpaca_order_id,
                    client_order_id,
                    symbol.upper(),
                    side,
                    quantity,
                    order_type,
                    time_in_force,
                    status,
                    submitted_at or now,
                    submission_source,
                    automation_run_id,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def update_paper_order(self, order_id: int, **fields: Any) -> None:
        allowed = {
            "alpaca_order_id",
            "status",
            "filled_at",
            "filled_quantity",
            "filled_average_price",
            "failure_message",
            "raw_status",
            "last_synced_at",
            "last_processed_filled_qty",
            "filled_quantity_text",
            "filled_average_price_text",
            "last_processed_filled_quantity_text",
            "fee_amount_text",
            "fee_currency",
            "fee_status",
            "last_processed_fee_amount_text",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = _utc_now()
        set_clause = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [order_id]
        with self.connect() as connection:
            connection.execute(
                f"UPDATE paper_orders SET {set_clause} WHERE id = ?",
                values,
            )

    def get_paper_order(self, order_id: int) -> PaperOrderRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_orders WHERE id = ?",
                (order_id,),
            ).fetchone()
        return _row_to_paper_order(row) if row else None

    def get_paper_order_by_client_id(self, client_order_id: str) -> PaperOrderRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return _row_to_paper_order(row) if row else None

    def list_paper_orders(self, limit: int = 50) -> list[PaperOrderRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM paper_orders ORDER BY datetime(created_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_paper_order(row) for row in rows]

    def list_open_paper_orders(self) -> list[PaperOrderRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM paper_orders
                WHERE status IN ('SUBMITTED', 'ACCEPTED', 'PARTIALLY_FILLED', 'UNKNOWN')
                ORDER BY created_at
                """
            ).fetchall()
        return [_row_to_paper_order(row) for row in rows]

    def count_unknown_orders(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM paper_orders WHERE status = 'UNKNOWN'"
            ).fetchone()
        return int(row["count"])

    def get_recent_paper_orders(self, limit: int = 5) -> list[PaperOrderRecord]:
        return self.list_paper_orders(limit=limit)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_python(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    return value


def _row_to_strategy(row: sqlite3.Row) -> StrategyRecord:
    keys = row.keys()
    return StrategyRecord(
        id=row["id"],
        name=row["name"],
        strategy_type=row["strategy_type"],
        symbol=row["symbol"],
        parameters_json=row["parameters_json"],
        allocated_funds=to_decimal(row["allocated_funds"]),
        cash_reserve_percent=to_decimal(row["cash_reserve_percent"]),
        entry_policy=EntryPolicy(row["entry_policy"]),
        status=StrategyStatus(row["status"]),
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        activated_at=row["activated_at"],
        paused_at=row["paused_at"],
        stopped_at=row["stopped_at"] if "stopped_at" in keys else None,
        archived_at=row["archived_at"] if "archived_at" in keys else None,
        deactivated_reason=row["deactivated_reason"] if "deactivated_reason" in keys else None,
        automation_enabled=bool(row["automation_enabled"]) if "automation_enabled" in keys else False,
        automation_approved_at=row["automation_approved_at"] if "automation_approved_at" in keys else None,
        automation_paused_reason=row["automation_paused_reason"] if "automation_paused_reason" in keys else None,
        paper_trading_approved=bool(row["paper_trading_approved"]) if "paper_trading_approved" in keys else False,
        paper_trading_approved_at=row["paper_trading_approved_at"] if "paper_trading_approved_at" in keys else None,
        asset_type=row["asset_type"] if "asset_type" in keys else "STOCK",
        quote_currency=row["quote_currency"] if "quote_currency" in keys else None,
        crypto_paper_trading_approved=bool(row["crypto_paper_trading_approved"]) if "crypto_paper_trading_approved" in keys else False,
        crypto_paper_trading_approved_at=row["crypto_approved_at"] if "crypto_approved_at" in keys else None,
    )


def _row_to_paper_order(row: sqlite3.Row) -> PaperOrderRecord:
    return PaperOrderRecord(
        id=row["id"],
        strategy_id=row["strategy_id"],
        proposal_id=row["proposal_id"] if "proposal_id" in row.keys() else None,
        alpaca_order_id=row["alpaca_order_id"],
        client_order_id=row["client_order_id"],
        symbol=row["symbol"],
        side=row["side"],
        quantity=row["quantity"],
        order_type=row["order_type"],
        time_in_force=row["time_in_force"] if "time_in_force" in row.keys() else "day",
        status=row["status"],
        submitted_at=row["submitted_at"],
        filled_at=row["filled_at"],
        filled_quantity=row["filled_quantity"] if "filled_quantity" in row.keys() else 0,
        filled_average_price=to_decimal(row["filled_average_price"])
        if row["filled_average_price"] is not None
        else None,
        failure_message=row["failure_message"] if "failure_message" in row.keys() else None,
        raw_status=row["raw_status"] if "raw_status" in row.keys() else None,
        last_synced_at=row["last_synced_at"] if "last_synced_at" in row.keys() else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"] if "updated_at" in row.keys() else row["created_at"],
        last_processed_filled_qty=row["last_processed_filled_qty"]
        if "last_processed_filled_qty" in row.keys()
        else 0,
    )

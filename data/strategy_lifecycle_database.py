"""Strategy lifecycle persistence helpers."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from core.models import StrategyRecord, StrategyStatus


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrategyLifecycleDatabaseMixin:
    """Database operations for strategy lifecycle management."""

    def list_strategies_filtered(
        self,
        *,
        status: StrategyStatus | None = None,
        statuses: list[StrategyStatus] | None = None,
        include_archived: bool = False,
    ) -> list[StrategyRecord]:
        clauses: list[str] = []
        params: list[Any] = []

        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        elif statuses is not None:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(s.value for s in statuses)
        elif not include_archived:
            clauses.append("status != ?")
            params.append(StrategyStatus.ARCHIVED.value)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM strategies {where} ORDER BY id"

        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._strategy_from_row(row) for row in rows]

    def get_active_strategy_for_asset_symbol(
        self,
        asset_type: str,
        symbol: str,
        *,
        exclude_strategy_id: int | None = None,
    ) -> StrategyRecord | None:
        query = """
            SELECT * FROM strategies
            WHERE asset_type = ? AND symbol = ? AND status = 'ACTIVE' AND is_active = 1
        """
        params: list[Any] = [asset_type, symbol.upper()]
        if exclude_strategy_id is not None:
            query += " AND id != ?"
            params.append(exclude_strategy_id)
        query += " LIMIT 1"
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
        return self._strategy_from_row(row) if row else None

    def count_strategy_related_records(self, strategy_id: int) -> dict[str, int]:
        tables = {
            "signals": "SELECT COUNT(*) FROM strategy_signals WHERE strategy_id = ?",
            "order_proposals": "SELECT COUNT(*) FROM order_proposals WHERE strategy_id = ?",
            "paper_orders": "SELECT COUNT(*) FROM paper_orders WHERE strategy_id = ?",
            "ledger_entries": "SELECT COUNT(*) FROM strategy_ledger WHERE strategy_id = ?",
            "positions": (
                "SELECT COUNT(*) FROM strategy_positions "
                "WHERE strategy_id = ? AND quantity > 0"
            ),
            "crypto_ledger_entries": (
                "SELECT COUNT(*) FROM crypto_strategy_ledger WHERE strategy_id = ?"
            ),
            "crypto_positions": (
                "SELECT COUNT(*) FROM crypto_strategy_positions "
                "WHERE strategy_id = ? AND CAST(quantity_text AS REAL) > 0"
            ),
            "automation_audit": (
                "SELECT COUNT(*) FROM automation_audit_log WHERE strategy_id = ?"
            ),
            "lifecycle_events": (
                "SELECT COUNT(*) FROM strategy_lifecycle_events WHERE strategy_id = ?"
            ),
        }
        counts: dict[str, int] = {}
        with self.connect() as connection:
            for key, sql in tables.items():
                row = connection.execute(sql, (strategy_id,)).fetchone()
                counts[key] = int(row[0])
        return counts

    def count_meaningful_ledger_entries(self, strategy_id: int) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM strategy_ledger
                WHERE strategy_id = ? AND entry_type NOT IN ('ALLOCATION')
                """,
                (strategy_id,),
            ).fetchone()
        return int(row[0])

    def count_open_orders_for_strategy(self, strategy_id: int) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM paper_orders
                WHERE strategy_id = ?
                  AND status IN ('SUBMITTED', 'ACCEPTED', 'PARTIALLY_FILLED', 'UNKNOWN')
                """,
                (strategy_id,),
            ).fetchone()
        return int(row[0])

    def count_unknown_orders_for_strategy(self, strategy_id: int) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM paper_orders
                WHERE strategy_id = ? AND status = 'UNKNOWN'
                """,
                (strategy_id,),
            ).fetchone()
        return int(row[0])

    def get_strategy_position_quantity(self, strategy_id: int, symbol: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT quantity FROM strategy_positions WHERE strategy_id = ? AND symbol = ?",
                (strategy_id, symbol.upper()),
            ).fetchone()
        return int(row["quantity"]) if row else 0

    def apply_strategy_lifecycle_transition(
        self,
        strategy_id: int,
        new_status: StrategyStatus,
        *,
        activated_at: str | None = None,
        paused_at: str | None = None,
        stopped_at: str | None = None,
        archived_at: str | None = None,
        deactivated_reason: str | None = None,
        clear_paused_at: bool = False,
        clear_stopped_at: bool = False,
        clear_archived_at: bool = False,
        clear_deactivated_reason: bool = False,
        disable_automation: bool = False,
    ) -> None:
        is_active = 1 if new_status == StrategyStatus.ACTIVE else 0
        now = _utc_now()
        fields = ["status = ?", "is_active = ?", "updated_at = ?"]
        values: list[Any] = [new_status.value, is_active, now]

        if activated_at is not None:
            fields.append("activated_at = ?")
            values.append(activated_at)
        if clear_paused_at:
            fields.append("paused_at = NULL")
        elif paused_at is not None:
            fields.append("paused_at = ?")
            values.append(paused_at)
        if clear_stopped_at:
            fields.append("stopped_at = NULL")
        elif stopped_at is not None:
            fields.append("stopped_at = ?")
            values.append(stopped_at)
        if clear_archived_at:
            fields.append("archived_at = NULL")
        elif archived_at is not None:
            fields.append("archived_at = ?")
            values.append(archived_at)
        if clear_deactivated_reason:
            fields.append("deactivated_reason = NULL")
        elif deactivated_reason is not None:
            fields.append("deactivated_reason = ?")
            values.append(deactivated_reason)
        if disable_automation:
            fields.append("automation_enabled = 0")

        values.append(strategy_id)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE strategies SET {', '.join(fields)} WHERE id = ?",
                values,
            )

    def _strategy_from_row(self, row: sqlite3.Row) -> StrategyRecord:
        from data.database import _row_to_strategy

        return _row_to_strategy(row)

    def append_strategy_lifecycle_event(
        self,
        *,
        strategy_id: int | None,
        event_type: str,
        previous_status: str | None,
        new_status: str | None,
        reason: str | None = None,
        position_quantity: int | None = None,
        open_order_count: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_lifecycle_events (
                    strategy_id, event_type, previous_status, new_status, reason,
                    position_quantity, open_order_count, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_id,
                    event_type,
                    previous_status,
                    new_status,
                    reason,
                    position_quantity,
                    open_order_count,
                    json.dumps(details) if details else None,
                    _utc_now(),
                ),
            )

    def list_strategy_lifecycle_events(
        self,
        strategy_id: int,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM strategy_lifecycle_events
                WHERE strategy_id = ?
                ORDER BY datetime(created_at) DESC
                LIMIT ?
                """,
                (strategy_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def permanently_remove_strategy(self, strategy_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM strategy_ledger WHERE strategy_id = ?",
                (strategy_id,),
            )
            connection.execute(
                "DELETE FROM strategy_positions WHERE strategy_id = ?",
                (strategy_id,),
            )
            connection.execute(
                "DELETE FROM crypto_strategy_ledger WHERE strategy_id = ?",
                (strategy_id,),
            )
            connection.execute(
                "DELETE FROM crypto_strategy_positions WHERE strategy_id = ?",
                (strategy_id,),
            )
            connection.execute(
                "DELETE FROM strategies WHERE id = ?",
                (strategy_id,),
            )

    def find_duplicate_active_strategies(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT asset_type, symbol, COUNT(*) AS active_count,
                       GROUP_CONCAT(id) AS strategy_ids
                FROM strategies
                WHERE status = 'ACTIVE'
                GROUP BY asset_type, symbol
                HAVING COUNT(*) > 1
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def report_migration_lifecycle_warnings(self, connection: sqlite3.Connection) -> list[str]:
        warnings: list[str] = []
        valid = {s.value for s in StrategyStatus}
        rows = connection.execute("SELECT id, status FROM strategies").fetchall()
        for row in rows:
            if row["status"] not in valid:
                connection.execute(
                    "UPDATE strategies SET status = 'DRAFT', is_active = 0, updated_at = ? WHERE id = ?",
                    (_utc_now(), row["id"]),
                )
                warnings.append(
                    f"Strategy {row['id']} had invalid status '{row['status']}' and was set to DRAFT."
                )

        connection.execute(
            "UPDATE strategies SET is_active = 1, updated_at = ? WHERE status = 'ACTIVE'",
            (_utc_now(),),
        )
        connection.execute(
            """
            UPDATE strategies SET is_active = 0, updated_at = ?
            WHERE status != 'ACTIVE' AND is_active != 0
            """,
            (_utc_now(),),
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
                (fixed_active, _utc_now(), row["id"]),
            )
            warnings.append(
                f"Strategy {row['id']} had contradictory status/is_active and was synchronized."
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
                (new_status, new_active, _utc_now(), row["id"]),
            )
            warnings.append(
                f"Strategy {row['id']} missing status; migrated to {new_status}."
            )

        duplicates = connection.execute(
            """
            SELECT asset_type, symbol, COUNT(*) AS cnt
            FROM strategies WHERE status = 'ACTIVE'
            GROUP BY asset_type, symbol HAVING COUNT(*) > 1
            """
        ).fetchall()
        for row in duplicates:
            warnings.append(
                f"Duplicate ACTIVE strategies for {row['asset_type']}/{row['symbol']} "
                f"({row['cnt']} strategies). Resolve manually."
            )
        return warnings

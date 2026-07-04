"""Database operations for Milestone 3 automation."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from automation.models import (
    AutomationRunStatus,
    AutomationRunType,
    AutomationSettingsRecord,
    AuditEventType,
    AuditSeverity,
)
from core.models import OrderProposal, OrderProposalStatus, SignalType, to_decimal


class AutomationDatabaseMixin:
    """Automation persistence methods mixed into DatabaseManager."""

    def get_automation_settings(self) -> AutomationSettingsRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM automation_settings WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("Automation settings row missing.")
        return AutomationSettingsRecord(
            automated_paper_trading_enabled=bool(row["automated_paper_trading_enabled"]),
            kill_switch_engaged=bool(row["kill_switch_engaged"]),
            maximum_order_notional=float(row["maximum_order_notional"]),
            maximum_orders_per_day=int(row["maximum_orders_per_day"]),
            maximum_daily_notional=float(row["maximum_daily_notional"]),
            maximum_active_positions=int(row["maximum_active_positions"]),
            updated_at=row["updated_at"],
        )

    def update_automation_settings(self, **fields: Any) -> None:
        allowed = {
            "automated_paper_trading_enabled",
            "kill_switch_engaged",
            "maximum_order_notional",
            "maximum_orders_per_day",
            "maximum_daily_notional",
            "maximum_active_positions",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = _utc_now()
        set_clause = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values())
        with self.connect() as connection:
            connection.execute(
                f"UPDATE automation_settings SET {set_clause} WHERE id = 1",
                values,
            )

    def create_automation_run(
        self,
        run_id: str,
        run_type: AutomationRunType,
        host_name: str | None = None,
        process_id: int | None = None,
    ) -> int:
        now = _utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO automation_runs (
                    run_id, run_type, status, started_at, host_name, process_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    run_type.value,
                    AutomationRunStatus.STARTED.value,
                    now,
                    host_name,
                    process_id,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def complete_automation_run(
        self,
        run_id: str,
        status: AutomationRunStatus,
        *,
        strategies_checked: int = 0,
        signals_generated: int = 0,
        proposals_created: int = 0,
        orders_submitted: int = 0,
        orders_updated: int = 0,
        warnings_count: int = 0,
        errors_count: int = 0,
        summary_json: dict | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE automation_runs
                SET status = ?, completed_at = ?, strategies_checked = ?,
                    signals_generated = ?, proposals_created = ?,
                    orders_submitted = ?, orders_updated = ?,
                    warnings_count = ?, errors_count = ?,
                    summary_json = ?, error_message = ?
                WHERE run_id = ?
                """,
                (
                    status.value,
                    _utc_now(),
                    strategies_checked,
                    signals_generated,
                    proposals_created,
                    orders_submitted,
                    orders_updated,
                    warnings_count,
                    errors_count,
                    json.dumps(summary_json or {}),
                    error_message,
                    run_id,
                ),
            )

    def get_last_automation_run(self, run_type: AutomationRunType) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM automation_runs
                WHERE run_type = ? AND status IN ('COMPLETED', 'COMPLETED_WITH_WARNINGS', 'SKIPPED')
                ORDER BY datetime(started_at) DESC LIMIT 1
                """,
                (run_type.value,),
            ).fetchone()
        return dict(row) if row else None

    def list_automation_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM automation_runs ORDER BY datetime(started_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def append_audit_log(
        self,
        event_type: AuditEventType,
        message: str,
        *,
        run_id: str | None = None,
        strategy_id: int | None = None,
        proposal_id: str | None = None,
        paper_order_id: int | None = None,
        severity: AuditSeverity = AuditSeverity.INFO,
        details: dict | None = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO automation_audit_log (
                    event_id, run_id, strategy_id, proposal_id, paper_order_id,
                    event_type, severity, message, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    run_id,
                    strategy_id,
                    proposal_id,
                    paper_order_id,
                    event_type.value,
                    severity.value,
                    message,
                    json.dumps(details or {}),
                    _utc_now(),
                ),
            )
        return event_id

    def list_audit_log(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM automation_audit_log ORDER BY datetime(created_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def acquire_worker_lock(
        self,
        lock_name: str,
        owner_id: str,
        expires_at: str,
    ) -> bool:
        now = _utc_now()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM automation_worker_locks WHERE lock_name = ?",
                (lock_name,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO automation_worker_locks (
                        lock_name, owner_id, acquired_at, expires_at, heartbeat_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (lock_name, owner_id, now, expires_at, now),
                )
                return True
            if row["owner_id"] == owner_id:
                connection.execute(
                    """
                    UPDATE automation_worker_locks
                    SET expires_at = ?, heartbeat_at = ?
                    WHERE lock_name = ? AND owner_id = ?
                    """,
                    (expires_at, now, lock_name, owner_id),
                )
                return True
            if row["expires_at"] < now:
                connection.execute(
                    """
                    UPDATE automation_worker_locks
                    SET owner_id = ?, acquired_at = ?, expires_at = ?, heartbeat_at = ?
                    WHERE lock_name = ? AND expires_at < ?
                    """,
                    (owner_id, now, expires_at, now, lock_name, now),
                )
                return connection.total_changes > 0
            return False

    def release_worker_lock(self, lock_name: str, owner_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                DELETE FROM automation_worker_locks
                WHERE lock_name = ? AND owner_id = ?
                """,
                (lock_name, owner_id),
            )

    def list_worker_locks(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM automation_worker_locks").fetchall()
        return [dict(row) for row in rows]

    def save_automation_proposal(self, proposal: OrderProposal) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO order_proposals (
                    proposal_id, strategy_id, symbol, signal, signal_timestamp,
                    side, quantity, estimated_price, estimated_notional,
                    client_order_id, status, validation_json, blocking_reasons_json,
                    expires_at, created_at, updated_at,
                    proposal_source, confirmation_mode, automation_eligible,
                    automation_validation_json, automation_validated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    proposal.proposal_source,
                    proposal.confirmation_mode,
                    1 if proposal.automation_eligible else 0,
                    proposal.automation_validation_json,
                    proposal.automation_validated_at,
                ),
            )

    def list_automation_eligible_proposals(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM order_proposals
                WHERE proposal_source = 'AUTOMATION'
                  AND automation_eligible = 1
                  AND status = 'PROPOSED'
                ORDER BY signal_timestamp ASC, strategy_id ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def proposal_exists_for_signal(
        self,
        strategy_id: int,
        signal_timestamp: str,
        side: str,
    ) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM order_proposals
                WHERE strategy_id = ? AND signal_timestamp = ? AND side = ?
                LIMIT 1
                """,
                (strategy_id, signal_timestamp, side),
            ).fetchone()
        return row is not None

    def count_automated_orders_submitted_today(self, trading_day: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM paper_orders
                WHERE submission_source = 'AUTOMATION'
                  AND date(submitted_at) = date(?)
                  AND status NOT IN ('REJECTED')
                """,
                (trading_day,),
            ).fetchone()
        return int(row["count"])

    def sum_automated_notional_submitted_today(self, trading_day: str) -> float:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(p.estimated_notional), 0) AS total
                FROM paper_orders o
                JOIN order_proposals p ON p.proposal_id = o.proposal_id
                WHERE o.submission_source = 'AUTOMATION'
                  AND date(o.submitted_at) = date(?)
                  AND o.status NOT IN ('REJECTED', 'CANCELED')
                """,
                (trading_day,),
            ).fetchone()
        return float(row["total"])

    def update_strategy_automation(
        self,
        strategy_id: int,
        *,
        automation_enabled: bool,
        automation_approved_at: str | None = None,
        automation_paused_reason: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE strategies
                SET automation_enabled = ?, automation_approved_at = ?,
                    automation_paused_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    1 if automation_enabled else 0,
                    automation_approved_at,
                    automation_paused_reason,
                    _utc_now(),
                    strategy_id,
                ),
            )

    def get_latest_signal(self, strategy_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM strategy_signals
                WHERE strategy_id = ?
                ORDER BY datetime(signal_timestamp) DESC LIMIT 1
                """,
                (strategy_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_reconciliation_result(self, run_id: str, warnings: list[dict]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO automation_reconciliation (run_id, warnings_json, created_at)
                VALUES (?, ?, ?)
                """,
                (run_id, json.dumps(warnings), _utc_now()),
            )

    def get_latest_reconciliation(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM automation_reconciliation ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def count_managed_positions(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM strategy_positions WHERE quantity > 0"
            ).fetchone()
        return int(row["count"])

    def update_proposal_automation_validation(
        self,
        proposal_id: str,
        validation_json: str,
        validated_at: str,
        *,
        status: OrderProposalStatus | None = None,
        automation_eligible: bool | None = None,
    ) -> None:
        fields = ["automation_validation_json = ?", "automation_validated_at = ?", "updated_at = ?"]
        values: list[Any] = [validation_json, validated_at, _utc_now()]
        if status is not None:
            fields.append("status = ?")
            values.append(status.value)
        if automation_eligible is not None:
            fields.append("automation_eligible = ?")
            values.append(1 if automation_eligible else 0)
        values.append(proposal_id)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE order_proposals SET {', '.join(fields)} WHERE proposal_id = ?",
                values,
            )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

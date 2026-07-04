"""Append-only automation audit logging."""

from __future__ import annotations

from typing import TYPE_CHECKING

from automation.models import AuditEventType, AuditSeverity

if TYPE_CHECKING:
    from data.database import DatabaseManager


class AuditService:
    """Record automation events for compliance and troubleshooting."""

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database

    def log(
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
        return self._db.append_audit_log(
            event_type,
            message,
            run_id=run_id,
            strategy_id=strategy_id,
            proposal_id=proposal_id,
            paper_order_id=paper_order_id,
            severity=severity,
            details=details,
        )

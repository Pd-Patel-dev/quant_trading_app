"""Automation domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class AutomationRunType(str, Enum):
    AFTER_CLOSE_EVALUATION = "AFTER_CLOSE_EVALUATION"
    MARKET_OPEN_EXECUTION = "MARKET_OPEN_EXECUTION"
    ORDER_SYNCHRONIZATION = "ORDER_SYNCHRONIZATION"
    DAILY_RECONCILIATION = "DAILY_RECONCILIATION"


class AutomationRunStatus(str, Enum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class ProposalSource(str, Enum):
    MANUAL = "MANUAL"
    AUTOMATION = "AUTOMATION"


class ConfirmationMode(str, Enum):
    MANUAL = "MANUAL"
    AUTOMATION_POLICY = "AUTOMATION_POLICY"


class AuditEventType(str, Enum):
    AUTOMATION_ENABLED = "AUTOMATION_ENABLED"
    AUTOMATION_DISABLED = "AUTOMATION_DISABLED"
    KILL_SWITCH_ENGAGED = "KILL_SWITCH_ENGAGED"
    KILL_SWITCH_DISENGAGED = "KILL_SWITCH_DISENGAGED"
    STRATEGY_AUTOMATION_ENABLED = "STRATEGY_AUTOMATION_ENABLED"
    STRATEGY_AUTOMATION_DISABLED = "STRATEGY_AUTOMATION_DISABLED"
    WORKER_STARTED = "WORKER_STARTED"
    WORKER_SKIPPED = "WORKER_SKIPPED"
    WORKER_COMPLETED = "WORKER_COMPLETED"
    WORKER_FAILED = "WORKER_FAILED"
    SIGNAL_EVALUATED = "SIGNAL_EVALUATED"
    PROPOSAL_CREATED = "PROPOSAL_CREATED"
    PROPOSAL_BLOCKED = "PROPOSAL_BLOCKED"
    ORDER_SUBMISSION_STARTED = "ORDER_SUBMISSION_STARTED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_STATUS_UPDATED = "ORDER_STATUS_UPDATED"
    ORDER_UNKNOWN = "ORDER_UNKNOWN"
    FILL_PROCESSED = "FILL_PROCESSED"
    RECONCILIATION_WARNING = "RECONCILIATION_WARNING"


class AuditSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class AutomationSettingsRecord:
    automated_paper_trading_enabled: bool
    kill_switch_engaged: bool
    maximum_order_notional: float
    maximum_orders_per_day: int
    maximum_daily_notional: float
    maximum_active_positions: int
    updated_at: str


@dataclass
class AutomationValidationResult:
    passed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)
    validated_at: datetime | None = None
    validation_version: str = "1.0"

    @property
    def is_executable(self) -> bool:
        return not self.blocking_reasons


@dataclass
class WorkerRunResult:
    run_id: str
    status: AutomationRunStatus
    strategies_checked: int = 0
    signals_generated: int = 0
    proposals_created: int = 0
    orders_submitted: int = 0
    orders_updated: int = 0
    warnings_count: int = 0
    errors_count: int = 0
    summary: dict = field(default_factory=dict)
    error_message: str | None = None

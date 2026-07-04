"""Automated daily paper-trading workflow."""

from automation.models import (
    AuditEventType,
    AuditSeverity,
    AutomationRunStatus,
    AutomationRunType,
    AutomationValidationResult,
    ConfirmationMode,
    ProposalSource,
    WorkerRunResult,
)

__all__ = [
    "AutomationRunType",
    "AutomationRunStatus",
    "ProposalSource",
    "ConfirmationMode",
    "AuditEventType",
    "AuditSeverity",
    "AutomationValidationResult",
    "WorkerRunResult",
]

def __getattr__(name: str):
    if name in ("AutomationService", "AuditService", "AutomationSafetyService", "WorkerLock"):
        if name == "AutomationService":
            from automation.automation_service import AutomationService
            return AutomationService
        if name == "AuditService":
            from automation.audit_service import AuditService
            return AuditService
        if name == "AutomationSafetyService":
            from automation.safety_service import AutomationSafetyService
            return AutomationSafetyService
        from automation.worker_lock import WorkerLock
        return WorkerLock
    raise AttributeError(name)

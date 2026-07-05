"""Human-readable automation status for UI."""

from __future__ import annotations

from core.models import StrategyRecord, StrategyStatus
from strategies.registry import StrategyRegistry


def explain_strategy_automation(
    strategy: StrategyRecord,
    auto_settings,
    registry: StrategyRegistry,
) -> tuple[str, str | None]:
    """
    Return a short automation label and an optional detail explaining why it is off.

    Global automation and per-strategy automation are separate controls by design.
    """
    asset_type = getattr(strategy, "asset_type", "STOCK")
    if asset_type == "CRYPTO":
        return (
            "Per-strategy: N/A (crypto)",
            "Crypto strategies use manual paper trading only. Stock automation does not apply.",
        )

    if strategy.automation_enabled:
        return ("Per-strategy: On", None)

    if strategy.status != StrategyStatus.ACTIVE:
        return (
            "Per-strategy: Off",
            f"Strategy status is {strategy.status.value}. Only ACTIVE strategies can enable automation.",
        )

    if not auto_settings.automated_paper_trading_enabled:
        return (
            "Per-strategy: Off",
            "Global automation is disabled. Enable it on the Automation page first.",
        )

    if auto_settings.kill_switch_engaged:
        return (
            "Per-strategy: Off",
            "Kill switch is engaged. Disengage it on the Automation page before enabling strategies.",
        )

    meta = registry.get_metadata(strategy.strategy_type)
    if not meta.supports_automated_paper_trading:
        return (
            "Per-strategy: Off",
            f"{meta.display_name} does not support automated paper trading.",
        )

    paused_reason = getattr(strategy, "automation_paused_reason", None)
    if paused_reason:
        return (
            "Per-strategy: Off",
            f"Automation was disabled ({paused_reason}). Re-enable below or on the Automation page.",
        )

    return (
        "Per-strategy: Off",
        "Global automation is ON, but this strategy still needs its own approval. "
        "Enable per-strategy automation below or on the Automation page "
        f'(type exactly: ENABLE PAPER AUTOMATION).',
    )


def can_enable_strategy_automation(
    strategy: StrategyRecord,
    auto_settings,
    registry: StrategyRegistry,
) -> bool:
    if getattr(strategy, "asset_type", "STOCK") == "CRYPTO":
        return False
    if strategy.automation_enabled:
        return False
    if strategy.status != StrategyStatus.ACTIVE:
        return False
    if not auto_settings.automated_paper_trading_enabled:
        return False
    if auto_settings.kill_switch_engaged:
        return False
    meta = registry.get_metadata(strategy.strategy_type)
    return meta.supports_automated_paper_trading

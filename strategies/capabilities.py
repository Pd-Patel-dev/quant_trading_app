"""Optional strategy capability protocols."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from risk.risk_overlay import StrategyRiskOverlay


@runtime_checkable
class SupportsRiskBasedSizing(Protocol):
    def get_risk_overlay(self) -> StrategyRiskOverlay:
        ...


@runtime_checkable
class SupportsEntryPriceStopLoss(Protocol):
    def get_risk_overlay(self) -> StrategyRiskOverlay:
        ...


def has_risk_overlay(strategy: object) -> bool:
    return isinstance(strategy, SupportsRiskBasedSizing) and isinstance(
        strategy, SupportsEntryPriceStopLoss
    )


def get_risk_overlay(strategy: object) -> StrategyRiskOverlay:
    if not has_risk_overlay(strategy):
        raise TypeError("Strategy does not expose a risk overlay.")
    return strategy.get_risk_overlay()

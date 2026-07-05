"""Risk overlay data models."""

from __future__ import annotations

from dataclasses import dataclass, field

from decimal import Decimal


@dataclass(frozen=True)
class PositionSizingResult:
    strategy_equity: Decimal
    risk_percent: Decimal
    risk_budget: Decimal
    stop_loss_percent: Decimal
    risk_based_notional: Decimal
    available_cash: Decimal
    cash_reserve: Decimal
    maximum_order_notional: Decimal
    final_notional: Decimal
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StopLossEvaluation:
    triggered: bool
    entry_price: Decimal
    stop_price: Decimal
    close_price: Decimal
    stop_loss_percent: Decimal

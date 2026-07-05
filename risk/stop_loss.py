"""Entry-price-based percentage stop-loss."""

from __future__ import annotations

from decimal import Decimal

from core.models import to_decimal
from risk.models import StopLossEvaluation


class PercentageStopLoss:
    """Daily close-based stop evaluated against actual entry execution price."""

    def __init__(self, stop_loss_percent: Decimal) -> None:
        self._stop_loss_percent = to_decimal(stop_loss_percent)

    @property
    def stop_loss_percent(self) -> Decimal:
        return self._stop_loss_percent

    def calculate_stop_price(self, entry_price: Decimal) -> Decimal:
        entry = to_decimal(entry_price)
        if entry <= 0:
            return Decimal("0")
        return entry * (Decimal("1") - self._stop_loss_percent)

    def evaluate(self, entry_price: Decimal, close_price: Decimal) -> StopLossEvaluation:
        entry = to_decimal(entry_price)
        close = to_decimal(close_price)
        stop = self.calculate_stop_price(entry)
        triggered = entry > 0 and close > 0 and close <= stop
        return StopLossEvaluation(
            triggered=triggered,
            entry_price=entry,
            stop_price=stop,
            close_price=close,
            stop_loss_percent=self._stop_loss_percent,
        )

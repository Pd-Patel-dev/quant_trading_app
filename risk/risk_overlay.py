"""Strategy risk overlay combining sizing and stop-loss."""

from __future__ import annotations

from decimal import Decimal

from risk.position_sizing import FixedRiskPositionSizer
from risk.stop_loss import PercentageStopLoss


class StrategyRiskOverlay:
    """Reusable risk overlay for indicator-driven strategies."""

    def __init__(
        self,
        position_sizer: FixedRiskPositionSizer,
        stop_loss: PercentageStopLoss,
    ) -> None:
        self.position_sizer = position_sizer
        self.stop_loss = stop_loss

    @property
    def risk_per_trade_percent(self) -> Decimal:
        return self.position_sizer._risk_percent

    @property
    def stop_loss_percent(self) -> Decimal:
        return self.stop_loss.stop_loss_percent

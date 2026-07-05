"""Fixed-risk position sizing."""

from __future__ import annotations

from decimal import Decimal

from core.models import to_decimal
from risk.models import PositionSizingResult


class FixedRiskPositionSizer:
    """Size BUY notional from strategy equity, risk percent, and stop-loss percent."""

    def __init__(
        self,
        risk_per_trade_percent: Decimal,
        stop_loss_percent: Decimal,
    ) -> None:
        self._risk_percent = to_decimal(risk_per_trade_percent)
        self._stop_loss_percent = to_decimal(stop_loss_percent)

    def calculate(
        self,
        *,
        strategy_equity: Decimal,
        available_cash: Decimal,
        cash_reserve_percent: Decimal,
        strategy_allocation_limit: Decimal,
        application_max_order_notional: Decimal,
        strategy_max_order_notional: Decimal | None = None,
        broker_buying_power: Decimal | None = None,
        minimum_order_notional: Decimal | None = None,
    ) -> PositionSizingResult:
        equity = to_decimal(strategy_equity)
        cash = to_decimal(available_cash)
        reserve_pct = to_decimal(cash_reserve_percent)
        allocation_limit = to_decimal(strategy_allocation_limit)
        app_cap = to_decimal(application_max_order_notional)

        blocking: list[str] = []
        warnings: list[str] = []

        if equity <= 0:
            blocking.append("Strategy equity must be greater than zero.")
        if self._risk_percent <= 0 or self._risk_percent > Decimal("0.02"):
            blocking.append("Risk per trade must be greater than 0 and no more than 2%.")
        if self._stop_loss_percent <= 0 or self._stop_loss_percent > Decimal("0.5"):
            blocking.append("Stop-loss percentage must be greater than 0 and no more than 50%.")

        required_reserve = equity * reserve_pct
        available_after_reserve = max(Decimal("0"), cash - required_reserve)

        risk_budget = max(Decimal("0"), equity * self._risk_percent)
        if self._stop_loss_percent > 0:
            risk_based = risk_budget / self._stop_loss_percent
        else:
            risk_based = Decimal("0")

        caps = [
            risk_based,
            available_after_reserve,
            allocation_limit,
            app_cap,
        ]
        if strategy_max_order_notional is not None:
            caps.append(to_decimal(strategy_max_order_notional))
        if broker_buying_power is not None:
            caps.append(to_decimal(broker_buying_power))

        final = min(caps) if caps else Decimal("0")
        if final < 0:
            final = Decimal("0")

        if minimum_order_notional is not None and final > 0 and final < to_decimal(minimum_order_notional):
            blocking.append(
                f"Final notional {final} is below minimum order size {minimum_order_notional}."
            )

        if risk_based > available_after_reserve:
            warnings.append("Risk-based notional exceeds available cash after reserve; capped.")

        return PositionSizingResult(
            strategy_equity=equity,
            risk_percent=self._risk_percent,
            risk_budget=risk_budget,
            stop_loss_percent=self._stop_loss_percent,
            risk_based_notional=risk_based,
            available_cash=cash,
            cash_reserve=required_reserve,
            maximum_order_notional=app_cap,
            final_notional=final,
            blocking_reasons=blocking,
            warnings=warnings,
        )

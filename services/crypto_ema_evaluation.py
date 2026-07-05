"""Crypto EMA strategy evaluation helpers for paper trading."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pandas as pd

from core.crypto_decimal import format_decimal, parse_decimal
from core.models import SignalType, to_decimal
from strategies.capabilities import get_risk_overlay, has_risk_overlay
from strategies.crypto_ema_trend_following import (
    MINIMUM_HISTORY_BARS,
    SIGNAL_REASON_BUY,
    SIGNAL_REASON_SELL,
    SIGNAL_REASON_STOP,
    CryptoEMATrendFollowingStrategy,
)
from strategies.registry import get_registry


def exclude_incomplete_daily_bar(bars: pd.DataFrame) -> pd.DataFrame:
    """Drop the last bar when it may still be forming (caller may pass pre-filtered data)."""
    if bars.empty:
        return bars
    return bars.iloc[:-1] if len(bars) > 1 else bars


def evaluate_crypto_ema_strategy(
    strategy_type: str,
    parameters: dict[str, Any],
    bars: pd.DataFrame,
    local_position: dict[str, Any] | None,
) -> dict[str, Any]:
    """Evaluate latest signal including daily close stop-loss when in position."""
    registry = get_registry()
    strategy_impl = registry.build(strategy_type, parameters)

    if bars.index.tz is not None:
        bars = bars.copy()
        bars.index = bars.index.tz_localize(None)

    completed = exclude_incomplete_daily_bar(bars)
    if len(completed) < MINIMUM_HISTORY_BARS:
        return {
            "signal": SignalType.HOLD.value,
            "signal_reason": "HOLD",
            "is_actionable": False,
            "explanation": (
                f"At least {MINIMUM_HISTORY_BARS} completed daily bars are required for this strategy."
            ),
            "close_price": float(completed.iloc[-1]["Close"]) if not completed.empty else 0.0,
            "signal_timestamp": completed.index[-1].isoformat() if not completed.empty else None,
            "blocking": [f"At least {MINIMUM_HISTORY_BARS} completed daily bars are required."],
        }

    processed = strategy_impl.generate_signals(completed)
    latest = processed.iloc[-1]
    signal = SignalType(latest["Signal"])
    signal_reason = str(latest.get("SignalReason", "HOLD"))

    local_qty = (
        parse_decimal(local_position["quantity_text"])
        if local_position
        else Decimal("0")
    )
    if local_qty > 0 and has_risk_overlay(strategy_impl) and local_position:
        entry_text = local_position.get("entry_price_text") or local_position.get(
            "average_entry_price_text"
        )
        if entry_text:
            overlay = get_risk_overlay(strategy_impl)
            stop_eval = overlay.stop_loss.evaluate(
                parse_decimal(entry_text),
                parse_decimal(latest["Close"]),
            )
            if stop_eval.triggered:
                signal = SignalType.SELL
                signal_reason = SIGNAL_REASON_STOP

    evaluation = strategy_impl.get_current_evaluation(processed)
    if signal == SignalType.SELL and signal_reason == SIGNAL_REASON_STOP:
        evaluation.latest_signal = SignalType.SELL
        evaluation.is_actionable = True
        evaluation.signal_reason = SIGNAL_REASON_STOP
        evaluation.explanation = (
            "Daily close-based stop-loss triggered on completed candle."
        )

    result: dict[str, Any] = {
        "signal": signal.value,
        "signal_reason": signal_reason,
        "is_actionable": signal in (SignalType.BUY, SignalType.SELL),
        "explanation": evaluation.explanation,
        "close_price": float(latest["Close"]),
        "signal_timestamp": processed.index[-1].isoformat(),
        "indicators": evaluation.indicators,
        "blocking": [],
    }

    if (
        signal == SignalType.BUY
        and has_risk_overlay(strategy_impl)
        and isinstance(strategy_impl, CryptoEMATrendFollowingStrategy)
    ):
        result["risk_overlay"] = True

    return result


def build_risk_sizing_context(
    strategy_impl: object,
    *,
    strategy_equity: Decimal,
    available_usd: Decimal,
    cash_reserve_percent: Decimal,
    allocation_limit: Decimal,
    application_max_notional: Decimal,
    broker_buying_power: Decimal | None = None,
    minimum_order_notional: Decimal | None = None,
) -> dict[str, Any]:
    """Return serializable risk sizing breakdown for proposals."""
    overlay = get_risk_overlay(strategy_impl)
    sizing = overlay.position_sizer.calculate(
        strategy_equity=strategy_equity,
        available_cash=available_usd,
        cash_reserve_percent=cash_reserve_percent,
        strategy_allocation_limit=allocation_limit,
        application_max_order_notional=application_max_notional,
        broker_buying_power=broker_buying_power,
        minimum_order_notional=minimum_order_notional,
    )
    return {
        "strategy_equity": format_decimal(sizing.strategy_equity),
        "risk_percent": format_decimal(sizing.risk_percent),
        "risk_budget": format_decimal(sizing.risk_budget),
        "stop_loss_percent": format_decimal(sizing.stop_loss_percent),
        "risk_based_notional": format_decimal(sizing.risk_based_notional),
        "available_cash": format_decimal(sizing.available_cash),
        "cash_reserve": format_decimal(sizing.cash_reserve),
        "maximum_order_notional": format_decimal(sizing.maximum_order_notional),
        "final_notional": format_decimal(sizing.final_notional),
        "blocking_reasons": sizing.blocking_reasons,
        "warnings": sizing.warnings,
        "sizing_result": sizing,
    }

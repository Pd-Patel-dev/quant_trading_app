"""Latest signal evaluation for active strategies."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd

from config.settings import Settings, get_settings
from core.exceptions import MarketDataError, StrategyError
from core.models import EntryPolicy, SignalEvaluation, SignalType, StrategyRecord, to_decimal
from data.alpaca_data import AlpacaMarketDataProvider
from data.database import DatabaseManager
from strategies.registry import get_registry

logger = logging.getLogger(__name__)


class SignalService:
    """Evaluate strategy signals using completed daily bars."""

    def __init__(
        self,
        database: DatabaseManager,
        data_provider: AlpacaMarketDataProvider | None = None,
        order_manager: object | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._db = database
        self._data_provider = data_provider
        self._order_manager = order_manager
        self._settings = settings or get_settings()
        self._registry = get_registry()

    def evaluate_strategy(self, strategy: StrategyRecord) -> SignalEvaluation:
        """Evaluate the latest signal for a strategy."""
        params = json.loads(strategy.parameters_json)
        strategy_impl = self._registry.build(strategy.strategy_type, params)
        min_bars = strategy_impl.metadata.minimum_history_bars

        bars = self._fetch_completed_bars(strategy.symbol, min_bars)
        evaluation = strategy_impl.get_current_evaluation(bars)

        if evaluation.latest_signal in (SignalType.BUY, SignalType.SELL) and evaluation.signal_timestamp:
            indicators = evaluation.indicators
            self._db.save_signal_if_new(
                strategy_id=strategy.id,
                symbol=strategy.symbol,
                signal=evaluation.latest_signal,
                signal_timestamp=evaluation.signal_timestamp.isoformat()
                if hasattr(evaluation.signal_timestamp, "isoformat")
                else str(evaluation.signal_timestamp),
                short_sma=indicators.get("SMA_Short"),
                long_sma=indicators.get("SMA_Long"),
                close_price=indicators.get("Close") or float(bars["Close"].iloc[-1]),
                data_timestamp=evaluation.data_timestamp.isoformat()
                if evaluation.data_timestamp and hasattr(evaluation.data_timestamp, "isoformat")
                else None,
            )

        is_actionable, requires_alignment, explanation = self._apply_entry_policy(
            strategy, evaluation, bars
        )

        logger.info(
            "Signal evaluation strategy=%s symbol=%s signal=%s actionable=%s",
            strategy.id,
            strategy.symbol,
            evaluation.latest_signal.value,
            is_actionable,
        )

        return SignalEvaluation(
            strategy_id=strategy.id,
            symbol=strategy.symbol,
            current_desired_position=evaluation.current_desired_position,
            latest_signal=evaluation.latest_signal,
            signal_timestamp=evaluation.signal_timestamp,
            short_sma=to_decimal(indicators["SMA_Short"]) if (indicators := evaluation.indicators).get("SMA_Short") else None,
            long_sma=to_decimal(indicators["SMA_Long"]) if indicators.get("SMA_Long") else None,
            close_price=to_decimal(indicators.get("Close")) if indicators.get("Close") else None,
            data_timestamp=evaluation.data_timestamp,
            is_actionable=is_actionable,
            requires_alignment=requires_alignment,
            explanation=explanation or evaluation.explanation,
        )

    def _apply_entry_policy(
        self,
        strategy: StrategyRecord,
        evaluation,
        bars: pd.DataFrame,
    ) -> tuple[bool, bool, str]:
        local_position = self._db.get_strategy_position(strategy.id, strategy.symbol)
        local_qty = int(local_position["quantity"]) if local_position else 0
        activated_at = self._parse_dt(strategy.activated_at)
        signal_timestamp = evaluation.signal_timestamp
        latest_signal = evaluation.latest_signal
        is_actionable = False
        requires_alignment = False
        explanation = evaluation.explanation or "Daily strategy based on completed closing bars."

        if latest_signal == SignalType.BUY:
            if strategy.entry_policy == EntryPolicy.WAIT_FOR_NEXT_CROSSOVER:
                if activated_at and signal_timestamp and signal_timestamp > activated_at:
                    is_actionable = local_qty == 0
                    explanation = evaluation.signal_reason or "New BUY signal detected after activation."
                else:
                    explanation = "BUY signal occurred before activation; waiting for next signal."
            elif strategy.entry_policy == EntryPolicy.ALIGN_WITH_CURRENT_POSITION:
                if evaluation.current_desired_position == 1 and local_qty == 0:
                    is_actionable = True
                    requires_alignment = True
                    explanation = (
                        "Strategy indicates long exposure but no local position. "
                        "Alignment entry available."
                    )
        elif latest_signal == SignalType.SELL:
            if local_qty > 0 and signal_timestamp:
                if activated_at is None or signal_timestamp > activated_at:
                    is_actionable = True
                    explanation = evaluation.signal_reason or "SELL signal detected with local position."
                else:
                    explanation = "SELL signal predates activation."
            else:
                explanation = "SELL signal ignored because local position is zero."

        if latest_signal == SignalType.HOLD:
            is_actionable = False

        return is_actionable, requires_alignment, explanation

    def _fetch_completed_bars(self, symbol: str, min_bars: int) -> pd.DataFrame:
        if self._data_provider is None:
            raise MarketDataError("Market data provider is not configured.")
        end_date = date.today()
        start_date = end_date - timedelta(days=max(min_bars * 3, 365))
        bars = self._data_provider.get_daily_bars(symbol, start_date, end_date)

        if self._order_manager is not None:
            try:
                clock = self._order_manager.get_market_clock()
                if clock.get("is_open") and not bars.empty:
                    last_index = pd.Timestamp(bars.index[-1]).date()
                    if last_index >= date.today():
                        bars = bars.iloc[:-1]
            except Exception:
                logger.warning("Unable to check market clock; using all returned bars.")

        if bars.empty:
            raise MarketDataError("No completed daily bars available.")
        return bars

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)

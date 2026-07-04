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
from strategies.moving_average import MovingAverageCrossoverStrategy

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

    def evaluate_strategy(self, strategy: StrategyRecord) -> SignalEvaluation:
        """Evaluate the latest signal for a strategy."""
        params = json.loads(strategy.parameters_json)
        short_window = int(params["short_window"])
        long_window = int(params["long_window"])
        ma_strategy = MovingAverageCrossoverStrategy(short_window, long_window)

        bars = self._fetch_completed_bars(strategy.symbol, long_window)
        processed = ma_strategy.generate_signals(bars)
        if processed.empty:
            raise StrategyError("No market data available for signal evaluation.")

        latest_row = processed.iloc[-1]
        data_timestamp = pd.Timestamp(processed.index[-1]).to_pydatetime()
        if data_timestamp.tzinfo is not None:
            data_timestamp = data_timestamp.replace(tzinfo=None)

        current_desired_position = int(latest_row["Position"])
        latest_signal = SignalType(str(latest_row["Signal"]))
        signal_timestamp = self._find_latest_crossover_timestamp(processed)
        short_sma = to_decimal(latest_row["SMA_Short"]) if pd.notna(latest_row["SMA_Short"]) else None
        long_sma = to_decimal(latest_row["SMA_Long"]) if pd.notna(latest_row["SMA_Long"]) else None
        close_price = to_decimal(latest_row["Close"])

        is_actionable = False
        requires_alignment = False
        explanation = "Daily strategy based on completed closing bars."

        local_position = self._db.get_strategy_position(strategy.id, strategy.symbol)
        local_qty = int(local_position["quantity"]) if local_position else 0

        if latest_signal in (SignalType.BUY, SignalType.SELL) and signal_timestamp:
            self._db.save_signal_if_new(
                strategy_id=strategy.id,
                symbol=strategy.symbol,
                signal=latest_signal,
                signal_timestamp=signal_timestamp.isoformat(),
                short_sma=float(short_sma) if short_sma else None,
                long_sma=float(long_sma) if long_sma else None,
                close_price=float(close_price),
                data_timestamp=data_timestamp.isoformat(),
            )

        activated_at = self._parse_dt(strategy.activated_at)

        if latest_signal == SignalType.BUY:
            if strategy.entry_policy == EntryPolicy.WAIT_FOR_NEXT_CROSSOVER:
                if activated_at and signal_timestamp and signal_timestamp > activated_at:
                    is_actionable = local_qty == 0
                    explanation = "New BUY crossover detected after activation."
                else:
                    explanation = "BUY crossover occurred before activation; waiting for next crossover."
            elif strategy.entry_policy == EntryPolicy.ALIGN_WITH_CURRENT_POSITION:
                if current_desired_position == 1 and local_qty == 0:
                    is_actionable = True
                    requires_alignment = True
                    explanation = (
                        "Strategy indicates long exposure but no local position. "
                        "Alignment entry available (not a new crossover)."
                    )
        elif latest_signal == SignalType.SELL:
            if local_qty > 0 and signal_timestamp:
                if activated_at is None or signal_timestamp > activated_at:
                    is_actionable = True
                    explanation = "SELL crossover detected with local position."
                else:
                    explanation = "SELL crossover predates activation."
            else:
                explanation = "SELL signal ignored because local position is zero."

        logger.info(
            "Signal evaluation strategy=%s symbol=%s signal=%s actionable=%s",
            strategy.id,
            strategy.symbol,
            latest_signal.value,
            is_actionable,
        )

        return SignalEvaluation(
            strategy_id=strategy.id,
            symbol=strategy.symbol,
            current_desired_position=current_desired_position,
            latest_signal=latest_signal,
            signal_timestamp=signal_timestamp,
            short_sma=short_sma,
            long_sma=long_sma,
            close_price=close_price,
            data_timestamp=data_timestamp,
            is_actionable=is_actionable,
            requires_alignment=requires_alignment,
            explanation=explanation,
        )

    def _fetch_completed_bars(self, symbol: str, long_window: int) -> pd.DataFrame:
        if self._data_provider is None:
            raise MarketDataError("Market data provider is not configured.")
        end_date = date.today()
        start_date = end_date - timedelta(days=long_window * 3)
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
    def _find_latest_crossover_timestamp(processed: pd.DataFrame) -> datetime | None:
        crossovers = processed[processed["Signal"].isin([SignalType.BUY.value, SignalType.SELL.value])]
        if crossovers.empty:
            return None
        ts = pd.Timestamp(crossovers.index[-1]).to_pydatetime()
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        return ts

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)

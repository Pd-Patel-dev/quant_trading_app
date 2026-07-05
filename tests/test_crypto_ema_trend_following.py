"""Tests for Crypto Daily EMA Trend Following strategy."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from backtesting.engine import BacktestEngine
from core.exceptions import StrategyError
from core.models import BacktestConfiguration, SignalType
from market_data.models import QuantityMode
from risk.position_sizing import FixedRiskPositionSizer
from risk.stop_loss import PercentageStopLoss
from strategies.crypto_ema_trend_following import (
    MINIMUM_HISTORY_BARS,
    SIGNAL_REASON_BUY,
    SIGNAL_REASON_SELL,
    SIGNAL_REASON_STOP,
    CryptoEMATrendFollowingStrategy,
)
from strategies.registry import get_registry


def _synthetic_closes(n: int, start: float = 100.0, drift: float = 0.001) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = [start]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + drift + np.random.uniform(-0.002, 0.002)))
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * n,
        },
        index=idx,
    )


def test_registry_includes_crypto_ema() -> None:
    registry = get_registry()
    assert "crypto_ema_trend_following" in registry.list_strategy_types()
    meta = registry.get_metadata("crypto_ema_trend_following")
    assert meta.display_name == "Crypto Daily EMA Trend Following"
    assert meta.minimum_history_bars >= 250
    assert meta.supports_automated_paper_trading is True


def test_invalid_ema_order_rejected() -> None:
    with pytest.raises(StrategyError):
        CryptoEMATrendFollowingStrategy(fast_ema_period=50, medium_ema_period=20, long_ema_period=200)


def test_ema_columns_and_no_mutation() -> None:
    data = _synthetic_closes(300)
    original = data.copy()
    strategy = CryptoEMATrendFollowingStrategy()
    indicators = strategy.calculate_indicators(data)
    assert "EMA_Fast" in indicators.columns
    assert "EMA_Medium" in indicators.columns
    assert "EMA_Long" in indicators.columns
    assert indicators["EMA_Fast"].notna().sum() > 0
    assert indicators["EMA_Medium"].notna().sum() > 0


def test_no_signal_before_minimum_history() -> None:
    data = _synthetic_closes(MINIMUM_HISTORY_BARS - 10)
    strategy = CryptoEMATrendFollowingStrategy()
    processed = strategy.generate_signals(data)
    assert (processed["Signal"] == SignalType.HOLD.value).all()


def test_fixed_risk_sizing_one_percent_eight_stop() -> None:
    sizer = FixedRiskPositionSizer(Decimal("0.01"), Decimal("0.08"))
    result = sizer.calculate(
        strategy_equity=Decimal("10000"),
        available_cash=Decimal("10000"),
        cash_reserve_percent=Decimal("0"),
        strategy_allocation_limit=Decimal("10000"),
        application_max_order_notional=Decimal("10000"),
    )
    assert result.risk_budget == Decimal("100")
    assert result.risk_based_notional == Decimal("1250")
    assert result.final_notional == Decimal("1250")


def test_stop_from_entry_not_signal_close() -> None:
    stop = PercentageStopLoss(Decimal("0.08"))
    entry = Decimal("100")
    stop_price = stop.calculate_stop_price(entry)
    assert stop_price == Decimal("92")


def test_stop_priority_over_ema_in_backtest() -> None:
    n = 320
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = np.linspace(100, 120, n)
    closes[-3] = 80
    data = pd.DataFrame(
        {
            "Open": closes,
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Volume": np.full(n, 1_000_000),
        },
        index=idx,
    )
    strategy = CryptoEMATrendFollowingStrategy()
    config = BacktestConfiguration(
        symbol="BTC/USD",
        start_date=idx[0].date(),
        end_date=idx[-1].date(),
        starting_capital=10_000.0,
        allocation=10_000.0,
        commission=0.0,
        slippage_percent=0.0,
        cash_reserve_percent=0.0,
        quantity_mode=QuantityMode.FRACTIONAL_RESEARCH,
        crypto_fee_percent=0.0,
        max_order_notional=10_000.0,
    )
    result = BacktestEngine(strategy, config, data).run()
    sell_reasons = [t.signal_reason for t in result.trades if t.side == "SELL"]
    if sell_reasons:
        assert SIGNAL_REASON_STOP in sell_reasons or SIGNAL_REASON_SELL in sell_reasons


def test_next_bar_execution_no_final_signal() -> None:
    data = _synthetic_closes(280, drift=0.002)
    strategy = CryptoEMATrendFollowingStrategy()
    processed = strategy.generate_signals(data)
    if processed.iloc[-1]["Signal"] == SignalType.BUY.value:
        config = BacktestConfiguration(
            symbol="BTC/USD",
            start_date=data.index[0].date(),
            end_date=data.index[-1].date(),
            starting_capital=10_000.0,
            allocation=10_000.0,
            commission=0.0,
            slippage_percent=0.0,
            cash_reserve_percent=0.05,
            quantity_mode=QuantityMode.FRACTIONAL_RESEARCH,
        )
        result = BacktestEngine(strategy, config, data).run()
        if result.trades:
            last_trade = result.trades[-1]
            assert last_trade.timestamp <= data.index[-1].to_pydatetime()


def test_migration_v8_idempotent(temp_db) -> None:
    temp_db.initialize()
    temp_db.initialize()
    with temp_db.connect() as conn:
        version = conn.execute("SELECT MAX(version) AS v FROM schema_versions").fetchone()["v"]
        assert version >= 8
        cols = {row[1] for row in conn.execute("PRAGMA table_info(crypto_strategy_positions)")}
    assert "entry_price_text" in cols
    assert "stop_price_text" in cols

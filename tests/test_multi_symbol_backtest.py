"""Multi-symbol backtest tests."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from backtesting.engine import BacktestEngine
from core.models import BacktestConfiguration, SignalType
from market_data.models import (
    AssetRequest,
    AssetType,
    BatchHistoricalDataResult,
    HistoricalDataResult,
    QuantityMode,
)
from services.multi_symbol_backtest_service import MultiSymbolBacktestService
from strategies.moving_average import MovingAverageCrossoverStrategy


def _ohlcv(days: int, start: datetime | None = None) -> pd.DataFrame:
    start = start or datetime(2020, 1, 1, tzinfo=timezone.utc)
    index = pd.date_range(start, periods=days, freq="D", tz="UTC")
    prices = np.linspace(100, 150, days)
    return pd.DataFrame(
        {
            "Open": prices,
            "High": prices + 1,
            "Low": prices - 1,
            "Close": prices,
            "Volume": np.full(days, 1000.0),
        },
        index=index,
    )


def _mock_batch(data_by_symbol: dict[str, pd.DataFrame]) -> MagicMock:
    from market_data.models import DataTimeframe

    batch = MagicMock()
    results = []
    for symbol, data in data_by_symbol.items():
        asset_type = AssetType.CRYPTO if "/" in symbol else AssetType.STOCK
        results.append(
            HistoricalDataResult(
                asset_type=asset_type,
                symbol=symbol,
                timeframe=DataTimeframe.DAY,
                data=data,
                requested_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
                requested_end=datetime(2020, 6, 1, tzinfo=timezone.utc),
                served_entirely_from_cache=True,
            )
        )
    batch.get_or_download_many.return_value = BatchHistoricalDataResult(results=results)
    return batch


def test_multiple_stocks_backtest() -> None:
    batch = _mock_batch({"AAPL": _ohlcv(250), "MSFT": _ohlcv(250)})
    service = MultiSymbolBacktestService(batch)
    result = service.run_independent_comparison(
        [(AssetType.STOCK, "AAPL"), (AssetType.STOCK, "MSFT")],
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2020, 8, 1, tzinfo=timezone.utc),
        "moving_average_crossover",
        {"short_window": 10, "long_window": 30},
        10000.0,
        0.0,
        0.0,
        0.0,
    )
    assert len(result.results) == 2
    assert not result.failures


def test_crypto_fractional_research() -> None:
    config = BacktestConfiguration(
        symbol="BTC/USD",
        start_date=datetime(2020, 1, 1).date(),
        end_date=datetime(2020, 6, 1).date(),
        starting_capital=1000.0,
        allocation=1000.0,
        commission=0.0,
        slippage_percent=0.0,
        cash_reserve_percent=0.0,
        quantity_mode=QuantityMode.FRACTIONAL_RESEARCH,
    )
    data = _ohlcv(120)
    data["Close"] = 50000.0
    data["Open"] = 50000.0
    data["High"] = 50100.0
    data["Low"] = 49900.0
    if data.index.tz is not None:
        data.index = data.index.tz_localize(None)
    strategy = MovingAverageCrossoverStrategy(5, 20)
    processed = strategy.generate_signals(data)
    processed.loc[processed.index[10], "Signal"] = SignalType.BUY.value
    engine = BacktestEngine(strategy, config, data)
    trades, _ = engine._simulate(processed)
    buy_trades = [t for t in trades if t.side == "BUY"]
    assert buy_trades
    assert isinstance(buy_trades[0].quantity, float)
    assert buy_trades[0].quantity > 0


def test_per_symbol_failure_isolated() -> None:
    batch = _mock_batch({"AAPL": _ohlcv(250)})
    service = MultiSymbolBacktestService(batch)
    result = service.run_independent_comparison(
        [(AssetType.STOCK, "AAPL"), (AssetType.STOCK, "MISSING")],
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2020, 8, 1, tzinfo=timezone.utc),
        "moving_average_crossover",
        {"short_window": 10, "long_window": 30},
        10000.0,
        0.0,
        0.0,
        0.0,
    )
    assert len(result.results) == 1
    assert len(result.failures) == 1

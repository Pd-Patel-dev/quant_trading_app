"""Coverage service tests."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from market_data.coverage_service import MarketDataCoverageService
from market_data.models import AssetType, DataTimeframe, HistoricalBar, MarketDataSource
from market_data.repository import MarketDataRepository


def _insert(repo, symbol, dates, asset_type=AssetType.STOCK):
    bars = []
    for d in dates:
        ts = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        bars.append(
            HistoricalBar(
                asset_type=asset_type,
                symbol=symbol,
                timeframe=DataTimeframe.DAY,
                timestamp=ts,
                open=Decimal("10"),
                high=Decimal("11"),
                low=Decimal("9"),
                close=Decimal("10"),
                volume=Decimal("100"),
                trade_count=None,
                vwap=None,
                source=MarketDataSource.ALPACA,
                feed="iex" if asset_type == AssetType.STOCK else "us",
                adjustment="split" if asset_type == AssetType.STOCK else "NONE",
            )
        )
    repo.upsert_bars(bars, "test")


def test_empty_cache_full_range(temp_db) -> None:
    repo = MarketDataRepository(temp_db)
    svc = MarketDataCoverageService(repo)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 10, tzinfo=timezone.utc)
    missing = svc.find_missing_ranges(
        AssetType.STOCK, "AAPL", DataTimeframe.DAY, start, end,
        MarketDataSource.ALPACA, "iex", "split",
    )
    assert len(missing) == 1
    assert missing[0].start == start


def test_complete_cache_no_missing(temp_db) -> None:
    repo = MarketDataRepository(temp_db)
    svc = MarketDataCoverageService(repo)
    dates = [datetime(2024, 1, d, tzinfo=timezone.utc).date() for d in range(1, 6)]
    _insert(repo, "AAPL", dates)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 5, tzinfo=timezone.utc)
    missing = svc.find_missing_ranges(
        AssetType.STOCK, "AAPL", DataTimeframe.DAY, start, end,
        MarketDataSource.ALPACA, "iex", "split", repair_gaps=False,
    )
    assert missing == []


def test_missing_end(temp_db) -> None:
    repo = MarketDataRepository(temp_db)
    svc = MarketDataCoverageService(repo)
    dates = [datetime(2024, 1, d, tzinfo=timezone.utc).date() for d in range(1, 4)]
    _insert(repo, "AAPL", dates)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 10, tzinfo=timezone.utc)
    missing = svc.find_missing_ranges(
        AssetType.STOCK, "AAPL", DataTimeframe.DAY, start, end,
        MarketDataSource.ALPACA, "iex", "split", repair_gaps=False,
    )
    assert any(r.end >= datetime(2024, 1, 9, tzinfo=timezone.utc) for r in missing)


def test_crypto_internal_gap(temp_db) -> None:
    repo = MarketDataRepository(temp_db)
    svc = MarketDataCoverageService(repo)
    dates = [
        datetime(2024, 1, 1, tzinfo=timezone.utc).date(),
        datetime(2024, 1, 3, tzinfo=timezone.utc).date(),
    ]
    _insert(repo, "BTC/USD", dates, AssetType.CRYPTO)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 3, tzinfo=timezone.utc)
    missing = svc.find_missing_ranges(
        AssetType.CRYPTO, "BTC/USD", DataTimeframe.DAY, start, end,
        MarketDataSource.ALPACA, "us", "NONE", repair_gaps=True,
    )
    assert missing


def test_stock_weekend_not_gap(temp_db) -> None:
    repo = MarketDataRepository(temp_db)
    svc = MarketDataCoverageService(repo)
    # Friday and Monday only
    dates = [
        datetime(2024, 1, 5, tzinfo=timezone.utc).date(),
        datetime(2024, 1, 8, tzinfo=timezone.utc).date(),
    ]
    _insert(repo, "AAPL", dates)
    start = datetime(2024, 1, 5, tzinfo=timezone.utc)
    end = datetime(2024, 1, 8, tzinfo=timezone.utc)
    missing = svc.find_missing_ranges(
        AssetType.STOCK, "AAPL", DataTimeframe.DAY, start, end,
        MarketDataSource.ALPACA, "iex", "split", repair_gaps=True,
    )
    assert missing == []

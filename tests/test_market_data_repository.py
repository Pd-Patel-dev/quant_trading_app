"""Market data repository tests."""

from datetime import datetime, timezone
from decimal import Decimal

from dataclasses import replace

import pytest

from market_data.models import AssetType, DataTimeframe, HistoricalBar, MarketDataSource
from market_data.repository import MarketDataRepository


def _bar(symbol: str, ts: datetime, close: float, asset_type=AssetType.STOCK) -> HistoricalBar:
    return HistoricalBar(
        asset_type=asset_type,
        symbol=symbol,
        timeframe=DataTimeframe.DAY,
        timestamp=ts,
        open=Decimal(str(close - 1)),
        high=Decimal(str(close + 1)),
        low=Decimal(str(close - 2)),
        close=Decimal(str(close)),
        volume=Decimal("1000"),
        trade_count=10,
        vwap=Decimal(str(close)),
        source=MarketDataSource.ALPACA,
        feed="iex",
        adjustment="split",
    )


def test_creates_asset_once(temp_db) -> None:
    repo = MarketDataRepository(temp_db)
    id1 = repo.get_or_create_asset(AssetType.STOCK, "AAPL")
    id2 = repo.get_or_create_asset(AssetType.STOCK, "aapl")
    assert id1 == id2


def test_upsert_and_read_range(temp_db) -> None:
    repo = MarketDataRepository(temp_db)
    ts1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    ts2 = datetime(2024, 1, 2, tzinfo=timezone.utc)
    repo.upsert_bars([_bar("AAPL", ts1, 100), _bar("AAPL", ts2, 101)], "run1")
    frame = repo.get_bars(
        AssetType.STOCK, "AAPL", DataTimeframe.DAY, ts1, ts2,
        MarketDataSource.ALPACA, "iex", "split",
    )
    assert len(frame) == 2
    assert frame.index.tz is not None


def test_updates_existing_bar(temp_db) -> None:
    repo = MarketDataRepository(temp_db)
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    repo.upsert_bars([_bar("AAPL", ts, 100)], "run1")
    repo.upsert_bars([_bar("AAPL", ts, 105)], "run2")
    frame = repo.get_bars(
        AssetType.STOCK, "AAPL", DataTimeframe.DAY, ts, ts,
        MarketDataSource.ALPACA, "iex", "split",
    )
    assert float(frame.iloc[0]["Close"]) == 105


def test_prevents_duplicates(temp_db) -> None:
    repo = MarketDataRepository(temp_db)
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    repo.upsert_bars([_bar("AAPL", ts, 100)], "run1")
    repo.upsert_bars([_bar("AAPL", ts, 100)], "run2")
    assert repo.count_bars(
        AssetType.STOCK, "AAPL", DataTimeframe.DAY,
        MarketDataSource.ALPACA, "iex", "split",
    ) == 1


def test_feed_and_adjustment_separation(temp_db) -> None:
    repo = MarketDataRepository(temp_db)
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bar_iex = _bar("AAPL", ts, 100)
    bar_sip = replace(bar_iex, feed="sip", adjustment="raw")
    repo.upsert_bars([bar_iex], "run1")
    repo.upsert_bars([bar_sip], "run2")
    assert repo.count_bars(
        AssetType.STOCK, "AAPL", DataTimeframe.DAY,
        MarketDataSource.ALPACA, "iex", "split",
    ) == 1
    assert repo.count_bars(
        AssetType.STOCK, "AAPL", DataTimeframe.DAY,
        MarketDataSource.ALPACA, "sip", "raw",
    ) == 1


def test_asset_separation(temp_db) -> None:
    repo = MarketDataRepository(temp_db)
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    repo.upsert_bars([_bar("AAPL", ts, 100), _bar("MSFT", ts, 200)], "run1")
    assert repo.count_bars(
        AssetType.STOCK, "AAPL", DataTimeframe.DAY,
        MarketDataSource.ALPACA, "iex", "split",
    ) == 1


def test_migration_v6(temp_db) -> None:
    assert temp_db.schema_version >= 6
    with temp_db.connect() as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "market_bars" in tables
    assert "assets" in tables

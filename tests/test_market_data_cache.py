"""Cache service tests."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from market_data.cache_service import HistoricalDataCacheService
from market_data.coverage_service import MarketDataCoverageService
from market_data.download_service import MarketDataDownloadService
from market_data.models import AssetType, DataTimeframe, HistoricalBar, MarketDataSource
from market_data.repository import MarketDataRepository
from market_data.validation_service import MarketDataValidationService


def _sample_bars(symbol: str, start: datetime, count: int) -> list[HistoricalBar]:
    bars = []
    for i in range(count):
        ts = start + timedelta(days=i)
        bars.append(
            HistoricalBar(
                asset_type=AssetType.STOCK,
                symbol=symbol,
                timeframe=DataTimeframe.DAY,
                timestamp=ts,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1000"),
                trade_count=1,
                vwap=Decimal("100"),
                source=MarketDataSource.ALPACA,
                feed="iex",
                adjustment="split",
            )
        )
    return bars


def _build_cache(temp_db, download_mock=None):
    repo = MarketDataRepository(temp_db)
    coverage = MarketDataCoverageService(repo)
    download = download_mock or MagicMock(spec=MarketDataDownloadService)
    return HistoricalDataCacheService(repo, coverage, download), repo, download


def test_cache_hit_no_api_call(temp_db) -> None:
    cache, repo, download = _build_cache(temp_db)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 3, tzinfo=timezone.utc)
    repo.upsert_bars(_sample_bars("AAPL", start, 3), "seed")
    with patch.object(
        HistoricalDataCacheService, "_apply_recent_overlap", return_value=[]
    ):
        result = cache.get_or_download(
            AssetType.STOCK, "AAPL", DataTimeframe.DAY, start, end, repair_gaps=False
        )
    download.download_range.assert_not_called()
    assert result.served_entirely_from_cache
    assert len(result.data) == 3


def test_cache_miss_downloads(temp_db) -> None:
    cache, repo, download = _build_cache(temp_db)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 2, tzinfo=timezone.utc)

    def fake_download(asset_type, symbol, timeframe, s, e):
        repo.upsert_bars(_sample_bars(symbol, s, 2), "dl1")
        return "run1", 2, 2, 0, []

    download.download_range.side_effect = fake_download
    result = cache.get_or_download(
        AssetType.STOCK, "AAPL", DataTimeframe.DAY, start, end, repair_gaps=False
    )
    assert result.downloaded_rows == 2
    assert len(result.data) == 2


def test_final_data_from_sqlite(temp_db) -> None:
    cache, repo, download = _build_cache(temp_db)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, tzinfo=timezone.utc)
    repo.upsert_bars(_sample_bars("AAPL", start, 1), "seed")
    with patch.object(HistoricalDataCacheService, "_apply_recent_overlap", return_value=[]):
        result = cache.get_or_download(
            AssetType.STOCK, "AAPL", DataTimeframe.DAY, start, end, repair_gaps=False
        )
    assert result.final_rows == 1
    assert "Open" in result.data.columns

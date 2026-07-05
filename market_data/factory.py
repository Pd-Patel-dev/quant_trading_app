"""Factory helpers for market data services."""

from __future__ import annotations

from config.settings import Settings, get_settings
from data.database import DatabaseManager
from market_data.batch_service import BatchHistoricalDataService
from market_data.cache_service import HistoricalDataCacheService
from market_data.coverage_service import MarketDataCoverageService
from market_data.crypto_provider import AlpacaCryptoDataProvider
from market_data.download_service import MarketDataDownloadService
from market_data.repository import MarketDataRepository
from market_data.stock_provider import AlpacaStockDataProvider
from market_data.validation_service import MarketDataValidationService


def build_market_data_stack(
    database: DatabaseManager,
    settings: Settings | None = None,
) -> tuple[
    MarketDataRepository,
    HistoricalDataCacheService,
    BatchHistoricalDataService,
]:
    settings = settings or get_settings()
    repository = MarketDataRepository(database)
    coverage = MarketDataCoverageService(repository)
    validator = MarketDataValidationService()

    stock_provider = None
    crypto_provider = AlpacaCryptoDataProvider(
        api_key=settings.alpaca_api_key or None,
        secret_key=settings.alpaca_secret_key or None,
        feed=settings.crypto_data_feed,
    )
    if settings.alpaca_configured:
        stock_provider = AlpacaStockDataProvider(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
            feed=settings.stock_data_feed,
            adjustment=settings.stock_data_adjustment,
        )

    download = MarketDataDownloadService(repository, stock_provider, crypto_provider, validator)
    cache = HistoricalDataCacheService(repository, coverage, download, validator)
    batch = BatchHistoricalDataService(cache)
    return repository, cache, batch

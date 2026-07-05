"""Cache-first historical data orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from config.settings import get_settings
from core.exceptions import ConfigurationError
from market_data.coverage_service import MarketDataCoverageService
from market_data.download_service import MarketDataDownloadService
from market_data.models import (
    AssetType,
    DataTimeframe,
    HistoricalDataResult,
    MarketDataSource,
)
from market_data.repository import MarketDataRepository
from market_data.symbol_normalizer import SymbolNormalizer
from market_data.validation_service import MarketDataValidationService


class HistoricalDataCacheService:
    """Synchronize local SQLite cache with Alpaca and serve complete ranges."""

    def __init__(
        self,
        repository: MarketDataRepository,
        coverage_service: MarketDataCoverageService,
        download_service: MarketDataDownloadService,
        validator: MarketDataValidationService | None = None,
        normalizer: SymbolNormalizer | None = None,
    ) -> None:
        self._repository = repository
        self._coverage = coverage_service
        self._download = download_service
        self._validator = validator or MarketDataValidationService()
        self._normalizer = normalizer or SymbolNormalizer()
        self._settings = get_settings()

    def get_or_download(
        self,
        asset_type: AssetType,
        symbol: str,
        timeframe: DataTimeframe,
        start: datetime,
        end: datetime,
        force_refresh: bool = False,
        repair_gaps: bool = True,
    ) -> HistoricalDataResult:
        normalized = self._normalizer.normalize(asset_type, symbol)
        start_utc = _ensure_utc(start)
        end_utc = _ensure_utc(end)
        self._validate_date_range(start_utc, end_utc)
        source = MarketDataSource.ALPACA
        feed, adjustment = self._resolve_feed_adjustment(asset_type)

        cached_before = self._repository.count_bars(
            asset_type, normalized, timeframe, source, feed, adjustment
        )
        download_runs: list[str] = []
        downloaded_rows = 0
        inserted_rows = 0
        updated_rows = 0
        warnings: list[str] = []

        if force_refresh:
            from market_data.models import DateRange

            missing_ranges = [DateRange(start=start_utc, end=end_utc)]
        else:
            missing_ranges = self._coverage.find_missing_ranges(
                asset_type,
                normalized,
                timeframe,
                start_utc,
                end_utc,
                source,
                feed,
                adjustment,
                repair_gaps=repair_gaps,
            )
            missing_ranges = self._apply_recent_overlap(
                asset_type, missing_ranges, start_utc, end_utc
            )

        served_from_cache = len(missing_ranges) == 0

        for date_range in missing_ranges:
            run_id, received, inserted, updated, range_warnings = self._download.download_range(
                asset_type,
                normalized,
                timeframe,
                date_range.start,
                date_range.end,
            )
            download_runs.append(run_id)
            downloaded_rows += received
            inserted_rows += inserted
            updated_rows += updated
            warnings.extend(range_warnings)

        final_data = self._repository.get_bars(
            asset_type,
            normalized,
            timeframe,
            start_utc,
            end_utc,
            source,
            feed,
            adjustment,
        )
        final_validation = self._validator.validate(final_data, start_utc, end_utc)
        quality_status = "VALID" if final_validation.is_usable else "VALIDATION_WARNING"
        if final_validation.warnings:
            warnings.extend(final_validation.warnings)
        self._repository.update_coverage_summary(
            asset_type, normalized, timeframe, source, feed, adjustment, quality_status
        )

        if final_validation.cleaned_data is not None and not final_validation.cleaned_data.empty:
            final_data = final_validation.cleaned_data

        return HistoricalDataResult(
            asset_type=asset_type,
            symbol=normalized,
            timeframe=timeframe,
            data=final_data,
            requested_start=start_utc,
            requested_end=end_utc,
            cached_rows_before=cached_before,
            downloaded_rows=downloaded_rows,
            inserted_rows=inserted_rows,
            updated_rows=updated_rows,
            final_rows=len(final_data),
            missing_ranges_requested=len(missing_ranges),
            download_runs=download_runs,
            served_entirely_from_cache=served_from_cache and downloaded_rows == 0,
            warnings=warnings,
        )

    def _validate_date_range(self, start: datetime, end: datetime) -> None:
        if start > end:
            raise ConfigurationError("Start date must be on or before end date.")
        max_years = self._settings.max_backtest_years
        if (end - start).days > max_years * 366:
            raise ConfigurationError(
                f"Requested range exceeds maximum of {max_years} years."
            )

    def _resolve_feed_adjustment(self, asset_type: AssetType) -> tuple[str, str]:
        if asset_type == AssetType.STOCK:
            return self._settings.stock_data_feed, self._settings.stock_data_adjustment
        return self._settings.crypto_data_feed, "NONE"

    def _apply_recent_overlap(self, asset_type, missing_ranges, start, end):
        from market_data.coverage_service import _merge_ranges
        from market_data.models import DateRange

        if asset_type == AssetType.STOCK:
            overlap_days = self._settings.recent_stock_refresh_sessions
        else:
            overlap_days = self._settings.recent_crypto_refresh_days
        overlap_start = max(start, end - timedelta(days=overlap_days))
        overlap_range = DateRange(start=overlap_start, end=end)
        if not missing_ranges:
            return [overlap_range]
        return _merge_ranges([*missing_ranges, overlap_range])


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

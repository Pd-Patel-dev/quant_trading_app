"""Batch historical data synchronization."""

from __future__ import annotations

from config.settings import get_settings
from core.exceptions import ConfigurationError
from market_data.cache_service import HistoricalDataCacheService
from market_data.models import AssetRequest, BatchHistoricalDataResult, HistoricalDataResult
from market_data.symbol_normalizer import SymbolNormalizer


class BatchHistoricalDataService:
    """Synchronize multiple assets with deduplication and per-symbol error isolation."""

    def __init__(
        self,
        cache_service: HistoricalDataCacheService,
        normalizer: SymbolNormalizer | None = None,
    ) -> None:
        self._cache = cache_service
        self._normalizer = normalizer or SymbolNormalizer()
        self._settings = get_settings()

    def get_or_download_many(self, assets: list[AssetRequest]) -> BatchHistoricalDataResult:
        if len(assets) > self._settings.max_symbols_per_batch:
            raise ConfigurationError(
                f"Maximum {self._settings.max_symbols_per_batch} symbols per batch."
            )
        seen: set[tuple[str, str]] = set()
        ordered: list[AssetRequest] = []
        for request in assets:
            normalized = self._normalizer.normalize(request.asset_type, request.symbol)
            key = (request.asset_type.value, normalized)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(
                AssetRequest(
                    asset_type=request.asset_type,
                    symbol=normalized,
                    start=request.start,
                    end=request.end,
                    force_refresh=request.force_refresh,
                    repair_gaps=request.repair_gaps,
                )
            )

        results: list[HistoricalDataResult] = []
        errors: list[str] = []
        for request in ordered:
            try:
                result = self._cache.get_or_download(
                    request.asset_type,
                    request.symbol,
                    DataTimeframe.DAY,
                    request.start,
                    request.end,
                    force_refresh=request.force_refresh,
                    repair_gaps=request.repair_gaps,
                )
                results.append(result)
            except Exception as exc:
                errors.append(f"{request.asset_type.value} {request.symbol}: {exc}")
        return BatchHistoricalDataResult(results=results, errors=errors)


from market_data.models import DataTimeframe  # noqa: E402

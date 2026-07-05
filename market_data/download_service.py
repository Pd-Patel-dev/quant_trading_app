"""Download historical bars from Alpaca providers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from market_data.crypto_provider import AlpacaCryptoDataProvider
from market_data.models import (
    AssetType,
    DataTimeframe,
    DownloadRunStatus,
    HistoricalBar,
    MarketDataSource,
)
from market_data.repository import MarketDataRepository
from market_data.stock_provider import AlpacaStockDataProvider
from market_data.validation_service import MarketDataValidationService


class MarketDataDownloadService:
    """Fetch bars from Alpaca and persist validated results."""

    def __init__(
        self,
        repository: MarketDataRepository,
        stock_provider: AlpacaStockDataProvider | None,
        crypto_provider: AlpacaCryptoDataProvider | None,
        validator: MarketDataValidationService | None = None,
    ) -> None:
        self._repository = repository
        self._stock_provider = stock_provider
        self._crypto_provider = crypto_provider
        self._validator = validator or MarketDataValidationService()

    def download_range(
        self,
        asset_type: AssetType,
        symbol: str,
        timeframe: DataTimeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[str, int, int, int, list[str]]:
        run_id = uuid.uuid4().hex
        provider_name = "ALPACA"
        feed, adjustment = self._resolve_feed_adjustment(asset_type)
        self._repository.record_download_run(
            run_id,
            asset_type,
            symbol,
            timeframe,
            start,
            end,
            DownloadRunStatus.STARTED.value,
            provider_name,
            feed,
            completed=False,
        )
        warnings: list[str] = []
        try:
            bars = self._fetch(asset_type, symbol, start, end)
            if not bars:
                self._repository.record_download_run(
                    run_id,
                    asset_type,
                    symbol,
                    timeframe,
                    start,
                    end,
                    DownloadRunStatus.NO_DATA.value,
                    provider_name,
                    feed,
                    rows_received=0,
                    error_message="No data returned from provider.",
                )
                return run_id, 0, 0, 0, warnings

            frame = self._bars_to_frame(bars, asset_type)
            validation = self._validator.validate(frame, start, end)
            warnings.extend(validation.warnings)
            if not validation.is_usable or validation.cleaned_data is None:
                self._repository.record_download_run(
                    run_id,
                    asset_type,
                    symbol,
                    timeframe,
                    start,
                    end,
                    DownloadRunStatus.FAILED.value,
                    provider_name,
                    feed,
                    rows_received=len(bars),
                    error_message="; ".join(validation.blocking_errors),
                )
                self._repository.save_quality_issues(
                    asset_type, symbol, timeframe, validation.quality_issues
                )
                return run_id, len(bars), 0, 0, warnings

            historical = self._frame_to_bars(
                validation.cleaned_data, asset_type, symbol, timeframe, feed, adjustment
            )
            upsert = self._repository.upsert_bars(historical, run_id)
            status = (
                DownloadRunStatus.COMPLETED_WITH_WARNINGS.value
                if warnings or validation.warnings
                else DownloadRunStatus.COMPLETED.value
            )
            self._repository.record_download_run(
                run_id,
                asset_type,
                symbol,
                timeframe,
                start,
                end,
                status,
                provider_name,
                feed,
                actual_start=historical[0].timestamp if historical else None,
                actual_end=historical[-1].timestamp if historical else None,
                rows_received=len(bars),
                rows_inserted=upsert.inserted,
                rows_updated=upsert.updated,
            )
            if validation.quality_issues:
                self._repository.save_quality_issues(
                    asset_type, symbol, timeframe, validation.quality_issues
                )
            return run_id, len(bars), upsert.inserted, upsert.updated, warnings
        except Exception as exc:
            self._repository.record_download_run(
                run_id,
                asset_type,
                symbol,
                timeframe,
                start,
                end,
                DownloadRunStatus.FAILED.value,
                provider_name,
                feed,
                error_message=str(exc),
            )
            raise

    def _fetch(
        self, asset_type: AssetType, symbol: str, start: datetime, end: datetime
    ) -> list[HistoricalBar]:
        if asset_type == AssetType.STOCK:
            if self._stock_provider is None:
                raise RuntimeError("Stock provider is not configured.")
            return self._stock_provider.fetch_bars([symbol], start, end).get(symbol, [])
        if self._crypto_provider is None:
            raise RuntimeError("Crypto provider is not configured.")
        return self._crypto_provider.fetch_bars([symbol], start, end).get(symbol, [])

    def _resolve_feed_adjustment(self, asset_type: AssetType) -> tuple[str, str]:
        if asset_type == AssetType.STOCK:
            assert self._stock_provider is not None
            return self._stock_provider.feed, self._stock_provider.adjustment
        assert self._crypto_provider is not None
        return self._crypto_provider.feed, self._crypto_provider.adjustment

    @staticmethod
    def _bars_to_frame(bars: list[HistoricalBar], asset_type: AssetType):
        import pandas as pd

        if not bars:
            return pd.DataFrame(
                columns=["Open", "High", "Low", "Close", "Volume", "TradeCount", "VWAP"]
            )
        index = pd.DatetimeIndex([bar.timestamp for bar in bars], tz="UTC")
        data = {
            "Open": [float(bar.open) for bar in bars],
            "High": [float(bar.high) for bar in bars],
            "Low": [float(bar.low) for bar in bars],
            "Close": [float(bar.close) for bar in bars],
            "Volume": [float(bar.volume) for bar in bars],
            "TradeCount": [bar.trade_count for bar in bars],
            "VWAP": [float(bar.vwap) if bar.vwap is not None else None for bar in bars],
        }
        return pd.DataFrame(data, index=index).sort_index()

    @staticmethod
    def _frame_to_bars(
        frame,
        asset_type: AssetType,
        symbol: str,
        timeframe: DataTimeframe,
        feed: str,
        adjustment: str,
    ) -> list[HistoricalBar]:
        from decimal import Decimal

        bars: list[HistoricalBar] = []
        for ts, row in frame.iterrows():
            bars.append(
                HistoricalBar(
                    asset_type=asset_type,
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=pd_timestamp(ts),
                    open=Decimal(str(row["Open"])),
                    high=Decimal(str(row["High"])),
                    low=Decimal(str(row["Low"])),
                    close=Decimal(str(row["Close"])),
                    volume=Decimal(str(row["Volume"])),
                    trade_count=int(row["TradeCount"]) if pd_notna(row.get("TradeCount")) else None,
                    vwap=Decimal(str(row["VWAP"])) if pd_notna(row.get("VWAP")) else None,
                    source=MarketDataSource.ALPACA,
                    feed=feed,
                    adjustment=adjustment,
                )
            )
        return bars


def pd_timestamp(ts) -> datetime:
    import pandas as pd

    return pd.Timestamp(ts).tz_convert("UTC").to_pydatetime()


def pd_notna(value) -> bool:
    import pandas as pd

    return pd.notna(value)

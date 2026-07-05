"""SQLite repository for historical market bars."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd

from market_data.models import (
    AssetDataSummary,
    AssetType,
    CachedRange,
    DataTimeframe,
    HistoricalBar,
    MarketDataSource,
    UpsertResult,
)
from market_data.symbol_normalizer import SymbolNormalizer

if TYPE_CHECKING:
    from data.database import DatabaseManager

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return _ensure_utc(dt).isoformat()


class MarketDataRepository:
    """Persist and query cached historical OHLCV bars."""

    def __init__(self, database: DatabaseManager) -> None:
        self._db = database
        self._normalizer = SymbolNormalizer()

    def get_or_create_asset(self, asset_type: AssetType, symbol: str) -> int:
        normalized = self._normalizer.normalize(asset_type, symbol)
        base_currency = None
        quote_currency = None
        if asset_type == AssetType.CRYPTO:
            base_currency, quote_currency = self._normalizer.split_crypto_pair(normalized)
        now = _utc_now()
        with self._db.connect() as connection:
            row = connection.execute(
                "SELECT id FROM assets WHERE asset_type = ? AND symbol = ?",
                (asset_type.value, normalized),
            ).fetchone()
            if row:
                return int(row[0])
            cursor = connection.execute(
                """
                INSERT INTO assets (
                    asset_type, symbol, base_currency, quote_currency, display_name,
                    is_active, first_seen_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    asset_type.value,
                    normalized,
                    base_currency,
                    quote_currency,
                    normalized,
                    now,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def get_bars(
        self,
        asset_type: AssetType,
        symbol: str,
        timeframe: DataTimeframe,
        start: datetime,
        end: datetime,
        source: MarketDataSource,
        feed: str,
        adjustment: str,
    ) -> pd.DataFrame:
        asset_id = self.get_or_create_asset(asset_type, symbol)
        start_iso = _iso(start)
        end_iso = _iso(end)
        with self._db.connect() as connection:
            rows = connection.execute(
                """
                SELECT timestamp_utc, open, high, low, close, volume, trade_count, vwap
                FROM market_bars
                WHERE asset_id = ? AND timeframe = ? AND source = ? AND feed = ?
                  AND adjustment = ? AND timestamp_utc >= ? AND timestamp_utc <= ?
                ORDER BY timestamp_utc ASC
                """,
                (
                    asset_id,
                    timeframe.value,
                    source.value,
                    feed,
                    adjustment,
                    start_iso,
                    end_iso,
                ),
            ).fetchall()
        if not rows:
            return _empty_frame()
        index = pd.DatetimeIndex(
            [pd.Timestamp(row["timestamp_utc"]).tz_convert("UTC") for row in rows]
        )
        data = {
            "Open": [row["open"] for row in rows],
            "High": [row["high"] for row in rows],
            "Low": [row["low"] for row in rows],
            "Close": [row["close"] for row in rows],
            "Volume": [row["volume"] for row in rows],
            "TradeCount": [row["trade_count"] for row in rows],
            "VWAP": [row["vwap"] for row in rows],
        }
        frame = pd.DataFrame(data, index=index)
        frame = frame[~frame.index.duplicated(keep="last")]
        return frame.sort_index()

    def upsert_bars(self, bars: list[HistoricalBar], download_run_id: str) -> UpsertResult:
        if not bars:
            return UpsertResult()
        inserted = 0
        updated = 0
        now = _utc_now()
        asset_cache: dict[tuple[str, str], int] = {}
        records: list[tuple] = []
        for bar in bars:
            key = (bar.asset_type.value, bar.symbol)
            if key not in asset_cache:
                asset_cache[key] = self.get_or_create_asset(bar.asset_type, bar.symbol)
            asset_id = asset_cache[key]
            records.append(
                (
                    asset_id,
                    bar.timeframe.value,
                    _iso(bar.timestamp),
                    float(bar.open),
                    float(bar.high),
                    float(bar.low),
                    float(bar.close),
                    float(bar.volume),
                    bar.trade_count,
                    float(bar.vwap) if bar.vwap is not None else None,
                    bar.source.value,
                    bar.feed,
                    bar.adjustment,
                    download_run_id,
                    now,
                    now,
                )
            )
        with self._db.connect() as connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT INTO market_bars (
                    asset_id, timeframe, timestamp_utc, open, high, low, close, volume,
                    trade_count, vwap, source, feed, adjustment, download_run_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id, timeframe, timestamp_utc, source, feed, adjustment)
                DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    trade_count = excluded.trade_count,
                    vwap = excluded.vwap,
                    download_run_id = excluded.download_run_id,
                    updated_at = excluded.updated_at
                """,
                records,
            )
            changes = connection.total_changes - before
        updated = max(0, changes - len(records))
        inserted = len(records) - updated
        return UpsertResult(inserted=inserted, updated=updated)

    def get_cached_range(
        self,
        asset_type: AssetType,
        symbol: str,
        timeframe: DataTimeframe,
        source: MarketDataSource,
        feed: str,
        adjustment: str,
    ) -> CachedRange | None:
        count = self.count_bars(asset_type, symbol, timeframe, source, feed, adjustment)
        if count == 0:
            return None
        asset_id = self.get_or_create_asset(asset_type, symbol)
        with self._db.connect() as connection:
            row = connection.execute(
                """
                SELECT MIN(timestamp_utc) AS start_ts, MAX(timestamp_utc) AS end_ts
                FROM market_bars
                WHERE asset_id = ? AND timeframe = ? AND source = ? AND feed = ? AND adjustment = ?
                """,
                (asset_id, timeframe.value, source.value, feed, adjustment),
            ).fetchone()
        if not row or not row["start_ts"]:
            return None
        return CachedRange(
            start=pd.Timestamp(row["start_ts"]).tz_convert("UTC").to_pydatetime(),
            end=pd.Timestamp(row["end_ts"]).tz_convert("UTC").to_pydatetime(),
            row_count=count,
        )

    def get_cached_timestamps(
        self,
        asset_type: AssetType,
        symbol: str,
        timeframe: DataTimeframe,
        source: MarketDataSource,
        feed: str,
        adjustment: str,
    ) -> set[datetime]:
        asset_id = self.get_or_create_asset(asset_type, symbol)
        with self._db.connect() as connection:
            rows = connection.execute(
                """
                SELECT timestamp_utc FROM market_bars
                WHERE asset_id = ? AND timeframe = ? AND source = ? AND feed = ? AND adjustment = ?
                ORDER BY timestamp_utc
                """,
                (asset_id, timeframe.value, source.value, feed, adjustment),
            ).fetchall()
        return {
            pd.Timestamp(row["timestamp_utc"]).tz_convert("UTC").to_pydatetime()
            for row in rows
        }

    def get_asset_summary(
        self,
        asset_type: AssetType,
        symbol: str,
        timeframe: DataTimeframe,
        source: MarketDataSource,
        feed: str,
        adjustment: str,
    ) -> AssetDataSummary:
        asset_id = self.get_or_create_asset(asset_type, symbol)
        cached = self.get_cached_range(asset_type, symbol, timeframe, source, feed, adjustment)
        quality_status = "UNKNOWN"
        last_downloaded = None
        with self._db.connect() as connection:
            row = connection.execute(
                """
                SELECT data_quality_status, last_downloaded_at
                FROM market_data_coverage
                WHERE asset_id = ? AND timeframe = ? AND source = ? AND feed = ? AND adjustment = ?
                """,
                (asset_id, timeframe.value, source.value, feed, adjustment),
            ).fetchone()
            if row:
                quality_status = row["data_quality_status"] or "UNKNOWN"
                last_downloaded = row["last_downloaded_at"]
        return AssetDataSummary(
            asset_id=asset_id,
            asset_type=asset_type,
            symbol=self._normalizer.normalize(asset_type, symbol),
            timeframe=timeframe.value,
            feed=feed,
            adjustment=adjustment,
            first_timestamp=cached.start if cached else None,
            last_timestamp=cached.end if cached else None,
            row_count=cached.row_count if cached else 0,
            quality_status=quality_status,
            last_downloaded_at=last_downloaded,
        )

    def delete_asset_history(
        self,
        asset_type: AssetType,
        symbol: str,
        timeframe: DataTimeframe,
        source: MarketDataSource,
        feed: str,
        adjustment: str,
    ) -> None:
        asset_id = self.get_or_create_asset(asset_type, symbol)
        with self._db.connect() as connection:
            connection.execute(
                """
                DELETE FROM market_bars
                WHERE asset_id = ? AND timeframe = ? AND source = ? AND feed = ? AND adjustment = ?
                """,
                (asset_id, timeframe.value, source.value, feed, adjustment),
            )
            connection.execute(
                """
                DELETE FROM market_data_coverage
                WHERE asset_id = ? AND timeframe = ? AND source = ? AND feed = ? AND adjustment = ?
                """,
                (asset_id, timeframe.value, source.value, feed, adjustment),
            )
            connection.execute(
                """
                DELETE FROM market_data_quality_issues
                WHERE asset_id = ? AND timeframe = ?
                """,
                (asset_id, timeframe.value),
            )

    def count_bars(
        self,
        asset_type: AssetType,
        symbol: str,
        timeframe: DataTimeframe,
        source: MarketDataSource,
        feed: str,
        adjustment: str,
    ) -> int:
        asset_id = self.get_or_create_asset(asset_type, symbol)
        with self._db.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM market_bars
                WHERE asset_id = ? AND timeframe = ? AND source = ? AND feed = ? AND adjustment = ?
                """,
                (asset_id, timeframe.value, source.value, feed, adjustment),
            ).fetchone()
        return int(row[0])

    def update_coverage_summary(
        self,
        asset_type: AssetType,
        symbol: str,
        timeframe: DataTimeframe,
        source: MarketDataSource,
        feed: str,
        adjustment: str,
        quality_status: str,
    ) -> None:
        asset_id = self.get_or_create_asset(asset_type, symbol)
        cached = self.get_cached_range(asset_type, symbol, timeframe, source, feed, adjustment)
        now = _utc_now()
        with self._db.connect() as connection:
            connection.execute(
                """
                INSERT INTO market_data_coverage (
                    asset_id, timeframe, source, feed, adjustment,
                    coverage_start_utc, coverage_end_utc, row_count,
                    last_downloaded_at, last_validated_at, data_quality_status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id, timeframe, source, feed, adjustment) DO UPDATE SET
                    coverage_start_utc = excluded.coverage_start_utc,
                    coverage_end_utc = excluded.coverage_end_utc,
                    row_count = excluded.row_count,
                    last_downloaded_at = excluded.last_downloaded_at,
                    last_validated_at = excluded.last_validated_at,
                    data_quality_status = excluded.data_quality_status,
                    updated_at = excluded.updated_at
                """,
                (
                    asset_id,
                    timeframe.value,
                    source.value,
                    feed,
                    adjustment,
                    _iso(cached.start) if cached else None,
                    _iso(cached.end) if cached else None,
                    cached.row_count if cached else 0,
                    now,
                    now,
                    quality_status,
                    now,
                    now,
                ),
            )

    def record_download_run(
        self,
        run_id: str,
        asset_type: AssetType,
        symbol: str,
        timeframe: DataTimeframe,
        requested_start: datetime,
        requested_end: datetime,
        status: str,
        provider: str,
        feed: str,
        *,
        actual_start: datetime | None = None,
        actual_end: datetime | None = None,
        rows_received: int = 0,
        rows_inserted: int = 0,
        rows_updated: int = 0,
        error_message: str | None = None,
        completed: bool = True,
    ) -> None:
        now = _utc_now()
        with self._db.connect() as connection:
            connection.execute(
                """
                INSERT INTO market_data_download_runs (
                    run_id, asset_type, symbol, timeframe,
                    requested_start_utc, requested_end_utc,
                    actual_start_utc, actual_end_utc,
                    rows_received, rows_inserted, rows_updated,
                    status, provider, feed, error_message,
                    started_at, completed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    actual_start_utc = excluded.actual_start_utc,
                    actual_end_utc = excluded.actual_end_utc,
                    rows_received = excluded.rows_received,
                    rows_inserted = excluded.rows_inserted,
                    rows_updated = excluded.rows_updated,
                    status = excluded.status,
                    error_message = excluded.error_message,
                    completed_at = excluded.completed_at
                """,
                (
                    run_id,
                    asset_type.value,
                    symbol,
                    timeframe.value,
                    _iso(requested_start),
                    _iso(requested_end),
                    _iso(actual_start) if actual_start else None,
                    _iso(actual_end) if actual_end else None,
                    rows_received,
                    rows_inserted,
                    rows_updated,
                    status,
                    provider,
                    feed,
                    error_message,
                    now,
                    now if completed else None,
                    now,
                ),
            )

    def save_quality_issues(
        self,
        asset_type: AssetType,
        symbol: str,
        timeframe: DataTimeframe,
        issues: list[dict],
    ) -> None:
        if not issues:
            return
        asset_id = self.get_or_create_asset(asset_type, symbol)
        now = _utc_now()
        records = [
            (
                asset_id,
                timeframe.value,
                issue.get("timestamp_utc"),
                issue["issue_type"],
                issue.get("severity", "WARNING"),
                issue["description"],
                now,
                now,
            )
            for issue in issues
        ]
        with self._db.connect() as connection:
            connection.executemany(
                """
                INSERT INTO market_data_quality_issues (
                    asset_id, timeframe, timestamp_utc, issue_type, severity,
                    description, detected_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["Open", "High", "Low", "Close", "Volume", "TradeCount", "VWAP"]
    )

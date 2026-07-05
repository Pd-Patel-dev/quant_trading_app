"""Detect missing historical data ranges in cached coverage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from market_data.models import AssetType, DataTimeframe, DateRange, MarketDataSource
from market_data.repository import MarketDataRepository

OVERLAP_DAYS = 1


class MarketDataCoverageService:
    """Compare requested coverage against cached bars."""

    def __init__(self, repository: MarketDataRepository) -> None:
        self._repository = repository

    def find_missing_ranges(
        self,
        asset_type: AssetType,
        symbol: str,
        timeframe: DataTimeframe,
        requested_start: datetime,
        requested_end: datetime,
        source: MarketDataSource,
        feed: str,
        adjustment: str,
        *,
        repair_gaps: bool = True,
    ) -> list[DateRange]:
        start = _ensure_utc(requested_start)
        end = _ensure_utc(requested_end)
        if start > end:
            return []

        cached = self._repository.get_cached_range(
            asset_type, symbol, timeframe, source, feed, adjustment
        )
        if cached is None:
            return [DateRange(start=start, end=end)]

        missing: list[DateRange] = []

        if start < cached.start:
            gap_end = min(cached.start - timedelta(days=OVERLAP_DAYS), end)
            if start <= gap_end:
                missing.append(DateRange(start=start, end=gap_end))

        if end > cached.end:
            gap_start = max(cached.end - timedelta(days=OVERLAP_DAYS), start)
            if gap_start <= end:
                missing.append(DateRange(start=gap_start, end=end))

        if repair_gaps:
            timestamps = self._repository.get_cached_timestamps(
                asset_type, symbol, timeframe, source, feed, adjustment
            )
            internal = self._find_internal_gaps(
                asset_type, timestamps, cached.start, cached.end, start, end
            )
            missing.extend(internal)

        return _merge_ranges(missing)

    def _find_internal_gaps(
        self,
        asset_type: AssetType,
        timestamps: set[datetime],
        cache_start: datetime,
        cache_end: datetime,
        requested_start: datetime,
        requested_end: datetime,
    ) -> list[DateRange]:
        if not timestamps:
            return []

        window_start = max(cache_start, requested_start)
        window_end = min(cache_end, requested_end)
        if window_start > window_end:
            return []

        if asset_type == AssetType.CRYPTO:
            expected = pd.date_range(
                pd.Timestamp(window_start).normalize(),
                pd.Timestamp(window_end).normalize(),
                freq="D",
                tz="UTC",
            )
            present = {pd.Timestamp(ts).normalize() for ts in timestamps}
            missing_dates = [d for d in expected if d not in present]
            return _dates_to_ranges(missing_dates)

        # Stocks: only flag suspicious weekday gaps (3+ consecutive weekdays)
        weekday_dates = sorted(
            pd.Timestamp(ts).normalize()
            for ts in timestamps
            if window_start <= ts <= window_end
        )
        if len(weekday_dates) < 2:
            return []

        gaps: list[DateRange] = []
        for i in range(len(weekday_dates) - 1):
            current = weekday_dates[i]
            nxt = weekday_dates[i + 1]
            gap_days = (nxt - current).days
            if gap_days <= 1:
                continue
            suspicious_start = current + timedelta(days=1)
            suspicious_end = nxt - timedelta(days=1)
            weekday_count = 0
            cursor = suspicious_start
            while cursor <= suspicious_end:
                if cursor.weekday() < 5:
                    weekday_count += 1
                cursor += timedelta(days=1)
            if weekday_count >= 3:
                gaps.append(
                    DateRange(
                        start=suspicious_start.to_pydatetime().replace(tzinfo=timezone.utc),
                        end=suspicious_end.to_pydatetime().replace(tzinfo=timezone.utc),
                    )
                )
        return gaps


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dates_to_ranges(dates: list[pd.Timestamp]) -> list[DateRange]:
    if not dates:
        return []
    dates = sorted(dates)
    ranges: list[DateRange] = []
    range_start = dates[0]
    prev = dates[0]
    for current in dates[1:]:
        if (current - prev).days > 1:
            ranges.append(
                DateRange(
                    start=range_start.to_pydatetime().replace(tzinfo=timezone.utc),
                    end=prev.to_pydatetime().replace(tzinfo=timezone.utc),
                )
            )
            range_start = current
        prev = current
    ranges.append(
        DateRange(
            start=range_start.to_pydatetime().replace(tzinfo=timezone.utc),
            end=prev.to_pydatetime().replace(tzinfo=timezone.utc),
        )
    )
    return ranges


def _merge_ranges(ranges: list[DateRange]) -> list[DateRange]:
    if not ranges:
        return []
    sorted_ranges = sorted(ranges, key=lambda r: r.start)
    merged: list[DateRange] = [sorted_ranges[0]]
    for current in sorted_ranges[1:]:
        last = merged[-1]
        if current.start <= last.end + timedelta(days=OVERLAP_DAYS + 1):
            merged[-1] = DateRange(start=last.start, end=max(last.end, current.end))
        else:
            merged.append(current)
    return merged

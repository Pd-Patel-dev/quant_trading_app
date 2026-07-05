"""Alpaca stock historical data provider."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, time as dt_time, timezone
from decimal import Decimal

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from core.exceptions import ConfigurationError, MarketDataError
from market_data.models import AssetType, DataTimeframe, HistoricalBar, MarketDataSource

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class AlpacaStockDataProvider:
    """Download daily stock bars from Alpaca."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        *,
        feed: str = "iex",
        adjustment: str = "split",
    ) -> None:
        if not api_key or not secret_key:
            raise ConfigurationError(
                "Alpaca API credentials are required for stock historical data."
            )
        self._client = StockHistoricalDataClient(api_key, secret_key)
        self._feed = feed
        self._adjustment = adjustment

    @property
    def feed(self) -> str:
        return self._feed

    @property
    def adjustment(self) -> str:
        return self._adjustment

    def fetch_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> dict[str, list[HistoricalBar]]:
        if not symbols:
            return {}
        normalized = [s.strip().upper() for s in symbols]
        request = StockBarsRequest(
            symbol_or_symbols=normalized if len(normalized) > 1 else normalized[0],
            timeframe=TimeFrame.Day,
            start=_ensure_utc(start),
            end=_ensure_utc(end),
            feed=_parse_feed(self._feed),
            adjustment=_parse_adjustment(self._adjustment),
        )
        bar_set = self._request_with_retry(request)
        dataframe = bar_set.df
        if dataframe is None or dataframe.empty:
            return {symbol: [] for symbol in normalized}

        result: dict[str, list[HistoricalBar]] = {symbol: [] for symbol in normalized}
        if isinstance(dataframe.index, pd.MultiIndex):
            for symbol in normalized:
                if symbol in dataframe.index.get_level_values(0):
                    subset = dataframe.xs(symbol, level=0)
                    result[symbol] = self._to_bars(subset, symbol)
        else:
            symbol = normalized[0]
            result[symbol] = self._to_bars(dataframe, symbol)
        return result

    def _request_with_retry(self, request: StockBarsRequest):
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                return self._client.get_stock_bars(request)
            except Exception as exc:
                last_error = exc
                message = str(exc).lower()
                if "unauthorized" in message or "invalid" in message and "symbol" in message:
                    raise MarketDataError(f"Alpaca stock request failed: {exc}") from exc
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2**attempt)
                    continue
                raise MarketDataError(f"Alpaca stock request failed: {exc}") from exc
        raise MarketDataError(f"Alpaca stock request failed: {last_error}")

    def _to_bars(self, dataframe: pd.DataFrame, symbol: str) -> list[HistoricalBar]:
        frame = dataframe.copy()
        if "timestamp" in frame.columns:
            frame = frame.set_index("timestamp")
        index = pd.to_datetime(frame.index, utc=True)
        bars: list[HistoricalBar] = []
        for ts, row in frame.iterrows():
            timestamp = pd.Timestamp(ts).tz_convert("UTC").to_pydatetime()
            bars.append(
                HistoricalBar(
                    asset_type=AssetType.STOCK,
                    symbol=symbol,
                    timeframe=DataTimeframe.DAY,
                    timestamp=timestamp,
                    open=Decimal(str(row.get("open", row.get("Open", 0)))),
                    high=Decimal(str(row.get("high", row.get("High", 0)))),
                    low=Decimal(str(row.get("low", row.get("Low", 0)))),
                    close=Decimal(str(row.get("close", row.get("Close", 0)))),
                    volume=Decimal(str(row.get("volume", row.get("Volume", 0)))),
                    trade_count=int(row["trade_count"]) if pd.notna(row.get("trade_count")) else None,
                    vwap=Decimal(str(row["vwap"])) if pd.notna(row.get("vwap")) else None,
                    source=MarketDataSource.ALPACA,
                    feed=self._feed,
                    adjustment=self._adjustment,
                )
            )
        return bars

    def bars_to_dataframe(self, bars: list[HistoricalBar]) -> pd.DataFrame:
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
        frame = pd.DataFrame(data, index=index)
        return frame[~frame.index.duplicated(keep="last")].sort_index()


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_feed(feed: str) -> DataFeed:
    mapping = {
        "iex": DataFeed.IEX,
        "sip": DataFeed.SIP,
        "otc": DataFeed.OTC,
    }
    key = feed.lower()
    if key not in mapping:
        raise ConfigurationError(f"Unsupported stock feed: {feed}")
    return mapping[key]


def _parse_adjustment(adjustment: str) -> Adjustment:
    mapping = {
        "raw": Adjustment.RAW,
        "split": Adjustment.SPLIT,
        "dividend": Adjustment.DIVIDEND,
        "all": Adjustment.ALL,
    }
    key = adjustment.lower()
    if key not in mapping:
        raise ConfigurationError(f"Unsupported stock adjustment: {adjustment}")
    return mapping[key]

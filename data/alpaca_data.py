"""Alpaca historical market data provider."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from core.exceptions import ConfigurationError, MarketDataError

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class AlpacaMarketDataProvider:
    """Download daily OHLCV bars from Alpaca."""

    def __init__(self, api_key: str, secret_key: str) -> None:
        if not api_key or not secret_key:
            raise ConfigurationError(
                "Alpaca API credentials are missing. "
                "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in your .env file."
            )
        self._client = StockHistoricalDataClient(api_key, secret_key)

    def get_daily_bars(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        """Fetch daily historical bars for a single symbol."""
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise MarketDataError("Symbol cannot be empty.")

        request = StockBarsRequest(
            symbol_or_symbols=normalized_symbol,
            timeframe=TimeFrame.Day,
            start=datetime.combine(start_date, time.min, tzinfo=timezone.utc),
            end=datetime.combine(end_date, time.max, tzinfo=timezone.utc),
            feed=DataFeed.IEX,
        )

        try:
            bar_set = self._client.get_stock_bars(request)
        except Exception as exc:
            logger.error("Alpaca rejected the market data request for %s.", normalized_symbol)
            raise MarketDataError(
                f"Unable to download historical data for {normalized_symbol}: {exc}"
            ) from exc

        dataframe = bar_set.df
        if dataframe is None or dataframe.empty:
            raise MarketDataError(
                f"Alpaca returned no historical data for {normalized_symbol} "
                f"between {start_date} and {end_date}."
            )

        dataframe = self._normalize_dataframe(dataframe, normalized_symbol)
        self._validate_dataframe(dataframe, normalized_symbol)
        return dataframe

    def _normalize_dataframe(self, dataframe: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Convert Alpaca bar data into a clean OHLCV DataFrame."""
        result = dataframe.copy()

        if isinstance(result.index, pd.MultiIndex):
            if symbol in result.index.get_level_values(0):
                result = result.xs(symbol, level=0)
            else:
                result = result.reset_index(level=0, drop=True)

        if "timestamp" in result.columns:
            result = result.set_index("timestamp")

        index = pd.to_datetime(result.index, utc=True)
        if index.tz is not None:
            index = index.tz_convert(None)
        result.index = pd.DatetimeIndex(index)
        result.index.name = None

        rename_map = {
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
        result = result.rename(columns={k: v for k, v in rename_map.items() if k in result.columns})

        result = result[~result.index.duplicated(keep="last")]
        result = result.sort_index()

        for column in REQUIRED_COLUMNS:
            if column in result.columns:
                result[column] = pd.to_numeric(result[column], errors="coerce")

        return result[REQUIRED_COLUMNS]

    def _validate_dataframe(self, dataframe: pd.DataFrame, symbol: str) -> None:
        """Ensure the returned DataFrame contains usable OHLCV data."""
        if dataframe.empty:
            raise MarketDataError(f"No usable historical data returned for {symbol}.")
        missing = [column for column in REQUIRED_COLUMNS if column not in dataframe.columns]
        if missing:
            raise MarketDataError(
                f"Historical data for {symbol} is missing columns: {missing}"
            )
        if dataframe[REQUIRED_COLUMNS].isna().all().any():
            raise MarketDataError(
                f"Historical data for {symbol} contains columns with no numeric values."
            )

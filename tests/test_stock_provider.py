"""Stock provider tests."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.exceptions import ConfigurationError, MarketDataError
from market_data.stock_provider import AlpacaStockDataProvider


def test_credentials_required() -> None:
    with pytest.raises(ConfigurationError):
        AlpacaStockDataProvider("", "")


def test_multiindex_single_symbol() -> None:
    provider = AlpacaStockDataProvider("key", "secret")
    index = pd.MultiIndex.from_tuples(
        [("AAPL", datetime(2024, 1, 1, tzinfo=timezone.utc))],
        names=["symbol", "timestamp"],
    )
    df = pd.DataFrame(
        {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [1000]},
        index=index,
    )
    mock_client = MagicMock()
    mock_client.get_stock_bars.return_value = MagicMock(df=df)
    provider._client = mock_client
    result = provider.fetch_bars(
        ["AAPL"],
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    assert len(result["AAPL"]) == 1
    assert result["AAPL"][0].symbol == "AAPL"


def test_missing_symbol_returns_empty() -> None:
    provider = AlpacaStockDataProvider("key", "secret")
    index = pd.MultiIndex.from_tuples(
        [("MSFT", datetime(2024, 1, 1, tzinfo=timezone.utc))],
        names=["symbol", "timestamp"],
    )
    df = pd.DataFrame(
        {"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [1000]},
        index=index,
    )
    mock_client = MagicMock()
    mock_client.get_stock_bars.return_value = MagicMock(df=df)
    provider._client = mock_client
    result = provider.fetch_bars(
        ["AAPL", "MSFT"],
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    assert result["AAPL"] == []
    assert len(result["MSFT"]) == 1


def test_api_error() -> None:
    provider = AlpacaStockDataProvider("key", "secret")
    mock_client = MagicMock()
    mock_client.get_stock_bars.side_effect = RuntimeError("unauthorized")
    provider._client = mock_client
    with pytest.raises(MarketDataError):
        provider.fetch_bars(
            ["AAPL"],
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )


def test_credentials_not_exposed_in_repr() -> None:
    provider = AlpacaStockDataProvider("secret-key", "secret-val")
    text = repr(provider._client)
    assert "secret-key" not in text

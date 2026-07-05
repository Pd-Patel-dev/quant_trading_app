"""Crypto provider tests."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from core.exceptions import MarketDataError
from market_data.crypto_provider import AlpacaCryptoDataProvider


def test_multiindex_response() -> None:
    provider = AlpacaCryptoDataProvider()
    index = pd.MultiIndex.from_tuples(
        [("BTC/USD", datetime(2024, 1, 1, tzinfo=timezone.utc))],
        names=["symbol", "timestamp"],
    )
    df = pd.DataFrame(
        {"open": [40000.0], "high": [41000.0], "low": [39000.0], "close": [40500.0], "volume": [100]},
        index=index,
    )
    mock_client = MagicMock()
    mock_client.get_crypto_bars.return_value = MagicMock(df=df)
    provider._client = mock_client
    result = provider.fetch_bars(
        ["BTC/USD"],
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    assert len(result["BTC/USD"]) == 1


def test_unsupported_pair_error() -> None:
    provider = AlpacaCryptoDataProvider()
    mock_client = MagicMock()
    mock_client.get_crypto_bars.side_effect = RuntimeError("symbol not found")
    provider._client = mock_client
    with pytest.raises(MarketDataError):
        provider.fetch_bars(
            ["INVALID/PAIR"],
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )


def test_empty_response() -> None:
    provider = AlpacaCryptoDataProvider()
    mock_client = MagicMock()
    mock_client.get_crypto_bars.return_value = MagicMock(df=pd.DataFrame())
    provider._client = mock_client
    result = provider.fetch_bars(
        ["BTC/USD"],
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    assert result["BTC/USD"] == []

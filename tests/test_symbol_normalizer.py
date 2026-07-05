"""Symbol normalizer tests."""

import pytest

from core.exceptions import ConfigurationError
from market_data.models import AssetType
from market_data.symbol_normalizer import SymbolNormalizer


@pytest.fixture
def normalizer() -> SymbolNormalizer:
    return SymbolNormalizer()


def test_stock_uppercase(normalizer) -> None:
    assert normalizer.normalize(AssetType.STOCK, "aapl") == "AAPL"


def test_stock_period_and_hyphen(normalizer) -> None:
    assert normalizer.normalize(AssetType.STOCK, "brk.b") == "BRK.B"
    assert normalizer.normalize(AssetType.STOCK, "bf-b") == "BF-B"


def test_crypto_slash_format(normalizer) -> None:
    assert normalizer.normalize(AssetType.CRYPTO, "btc/usd") == "BTC/USD"


def test_crypto_dash_format(normalizer) -> None:
    assert normalizer.normalize(AssetType.CRYPTO, "ETH-USD") == "ETH/USD"


def test_crypto_underscore_format(normalizer) -> None:
    assert normalizer.normalize(AssetType.CRYPTO, "SOL_USD") == "SOL/USD"


def test_btcusd_normalization(normalizer) -> None:
    assert normalizer.normalize(AssetType.CRYPTO, "BTCUSD") == "BTC/USD"


def test_ambiguous_pair_rejection(normalizer) -> None:
    with pytest.raises(ConfigurationError):
        normalizer.normalize(AssetType.CRYPTO, "FOO")


def test_empty_symbol_rejection(normalizer) -> None:
    with pytest.raises(ConfigurationError):
        normalizer.normalize(AssetType.STOCK, "   ")


def test_duplicate_input_removal(normalizer) -> None:
    parsed = normalizer.parse_input(AssetType.STOCK, "AAPL, aapl\nMSFT")
    assert parsed.normalized == ["AAPL", "MSFT"]
    assert "AAPL" in parsed.duplicates_removed

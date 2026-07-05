"""Crypto asset service tests."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from broker.crypto_asset_service import CryptoAssetService
from core.asset_models import CryptoTradingStatus
from core.exceptions import ConfigurationError


def test_credentials_required() -> None:
    with pytest.raises(ConfigurationError):
        CryptoAssetService("", "")


@patch("broker.crypto_asset_service.TradingClient")
def test_filters_usd_pairs(mock_client) -> None:
    asset = MagicMock()
    asset.symbol = "BTC/USD"
    asset.status = "active"
    asset.tradable = True
    asset.fractionable = True
    asset.min_order_size = "1"
    asset.min_trade_increment = "0.00000001"
    asset.price_increment = "0.01"
    mock_client.return_value.get_all_assets.return_value = [asset]
    service = CryptoAssetService("key", "secret")
    pairs = service.list_active_usd_pairs()
    assert len(pairs) == 1
    assert pairs[0].symbol == "BTC/USD"


@patch("broker.crypto_asset_service.TradingClient")
def test_rejects_non_usd_quote(mock_client) -> None:
    mock_client.return_value.get_all_assets.return_value = []
    service = CryptoAssetService("key", "secret")
    result = service.validate_pair("ETH/BTC")
    assert not result.is_valid
    assert result.status == CryptoTradingStatus.ASSET_NOT_TRADABLE


@patch("broker.crypto_asset_service.TradingClient")
def test_missing_asset(mock_client) -> None:
    mock_client.return_value.get_all_assets.return_value = []
    service = CryptoAssetService("key", "secret")
    result = service.validate_pair("BTC/USD")
    assert not result.is_valid

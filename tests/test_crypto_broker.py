"""Crypto broker request tests."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from alpaca.trading.enums import TimeInForce

from broker.crypto_order_manager import AlpacaCryptoPaperOrderManager


@patch("broker.crypto_order_manager.TradingClient")
@patch("broker.crypto_order_manager.CryptoAssetService")
def test_buy_uses_notional(mock_asset_service, mock_client) -> None:
    mock_asset_service.return_value.normalize_broker_symbol.side_effect = lambda s: s
    manager = AlpacaCryptoPaperOrderManager("k", "s")
    order = MagicMock()
    order.id = "1"
    order.client_order_id = "cid"
    order.symbol = "BTC/USD"
    order.side = MagicMock(value="buy")
    order.qty = None
    order.notional = 100
    order.filled_qty = 0
    order.filled_avg_price = None
    order.status = MagicMock(value="accepted")
    order.time_in_force = TimeInForce.GTC
    order.submitted_at = None
    order.filled_at = None
    mock_client.return_value.submit_order.return_value = order
    manager.submit_crypto_market_buy("BTC/USD", Decimal("100"), "cid")
    request = mock_client.return_value.submit_order.call_args.kwargs["order_data"]
    assert request.notional is not None
    assert request.qty is None
    assert request.time_in_force == TimeInForce.GTC


@patch("broker.crypto_order_manager.TradingClient")
@patch("broker.crypto_order_manager.CryptoAssetService")
def test_sell_uses_quantity(mock_asset_service, mock_client) -> None:
    mock_asset_service.return_value.normalize_broker_symbol.side_effect = lambda s: s
    manager = AlpacaCryptoPaperOrderManager("k", "s")
    order = MagicMock()
    order.id = "1"
    order.client_order_id = "cid"
    order.symbol = "BTC/USD"
    order.side = MagicMock(value="sell")
    order.qty = 0.5
    order.notional = None
    order.filled_qty = 0
    order.filled_avg_price = None
    order.status = MagicMock(value="accepted")
    order.time_in_force = TimeInForce.GTC
    order.submitted_at = None
    order.filled_at = None
    mock_client.return_value.submit_order.return_value = order
    manager.submit_crypto_market_sell("BTC/USD", Decimal("0.5"), "cid")
    request = mock_client.return_value.submit_order.call_args.kwargs["order_data"]
    assert request.qty is not None
    assert request.notional is None

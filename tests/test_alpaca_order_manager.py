"""Alpaca order manager tests."""

from unittest.mock import Mock, patch

import pytest

from broker.alpaca_order_manager import AlpacaPaperOrderManager
from core.exceptions import ConfigurationError


def test_requires_credentials() -> None:
    with pytest.raises(ConfigurationError):
        AlpacaPaperOrderManager("", "")


@patch("broker.alpaca_order_manager.TradingClient")
def test_submit_market_order(mock_client_cls) -> None:
    mock_client = Mock()
    mock_client_cls.return_value = mock_client
    mock_order = Mock()
    mock_order.id = "order-123"
    mock_order.client_order_id = "qslab-1-spy-buy-20260702-abc"
    mock_order.symbol = "SPY"
    mock_order.side = Mock(value="buy")
    mock_order.qty = "10"
    mock_order.filled_qty = "0"
    mock_order.filled_avg_price = None
    mock_order.status = Mock(value="accepted")
    mock_order.type = Mock(value="market")
    mock_order.time_in_force = Mock(value="day")
    mock_order.submitted_at = None
    mock_order.filled_at = None
    mock_client.submit_order.return_value = mock_order

    manager = AlpacaPaperOrderManager("key", "secret")
    result = manager.submit_market_order("SPY", 10, "BUY", "qslab-1-spy-buy-20260702-abc")
    assert result["alpaca_order_id"] == "order-123"
    mock_client_cls.assert_called_with("key", "secret", paper=True)

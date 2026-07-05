"""Alpaca crypto paper order manager."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetAssetsRequest, GetOrdersRequest, MarketOrderRequest

from broker.crypto_asset_service import CryptoAssetService
from core.crypto_decimal import decimal_to_sdk_float, parse_decimal
from core.exceptions import AlpacaConnectionError, ConfigurationError

logger = logging.getLogger(__name__)


class AlpacaCryptoPaperOrderManager:
    """Submit and synchronize Alpaca crypto paper market orders."""

    def __init__(self, api_key: str, secret_key: str) -> None:
        if not api_key or not secret_key:
            raise ConfigurationError("Alpaca API credentials are required.")
        self._client = TradingClient(api_key, secret_key, paper=True)
        self._asset_service = CryptoAssetService(api_key, secret_key)

    def get_account_summary(self) -> dict[str, object]:
        try:
            account = self._client.get_account()
        except Exception as exc:
            raise AlpacaConnectionError(f"Unable to connect to Alpaca paper account: {exc}") from exc
        return {
            "status": str(account.status),
            "cash": float(account.cash),
            "portfolio_value": float(account.portfolio_value),
            "buying_power": float(account.buying_power),
            "equity": float(account.equity),
            "trading_blocked": bool(account.trading_blocked),
        }

    def get_active_crypto_assets(self) -> list[dict[str, object]]:
        return [
            {
                "symbol": asset.symbol,
                "tradable": asset.tradable,
                "fractionable": asset.fractionable,
                "minimum_order_size": str(asset.minimum_order_size or ""),
                "minimum_trade_increment": str(asset.minimum_trade_increment or ""),
            }
            for asset in self._asset_service.list_active_usd_pairs()
        ]

    def get_crypto_asset(self, symbol: str) -> dict[str, object] | None:
        rules = self._asset_service.get_asset_rules(symbol)
        if rules is None:
            return None
        return {
            "symbol": rules.symbol,
            "tradable": rules.tradable,
            "fractionable": rules.fractionable,
            "minimum_order_size": str(rules.minimum_order_size or ""),
            "minimum_trade_increment": str(rules.minimum_trade_increment or ""),
            "price_increment": str(rules.price_increment or ""),
        }

    def get_crypto_positions(self) -> list[dict[str, object]]:
        try:
            positions = self._client.get_all_positions()
        except Exception as exc:
            raise AlpacaConnectionError(f"Unable to retrieve positions: {exc}") from exc
        crypto_positions = []
        for position in positions:
            symbol = self._asset_service.normalize_broker_symbol(str(position.symbol))
            if "/" not in symbol:
                continue
            crypto_positions.append(_safe_crypto_position(position, symbol))
        return crypto_positions

    def get_crypto_position(self, symbol: str) -> dict[str, object] | None:
        canonical = self._asset_service.normalize_broker_symbol(symbol)
        try:
            position = self._client.get_open_position(canonical)
        except Exception:
            try:
                position = self._client.get_open_position(canonical.replace("/", ""))
            except Exception:
                return None
        return _safe_crypto_position(position, canonical)

    def get_open_crypto_orders(self) -> list[dict[str, object]]:
        try:
            orders = self._client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
        except Exception as exc:
            raise AlpacaConnectionError(f"Unable to retrieve open orders: {exc}") from exc
        return [_safe_crypto_order(order, self._asset_service) for order in orders if _is_crypto_order(order)]

    def get_crypto_order_by_client_order_id(self, client_order_id: str) -> dict[str, object] | None:
        try:
            orders = self._client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.ALL))
        except Exception as exc:
            raise AlpacaConnectionError(f"Unable to query orders: {exc}") from exc
        for order in orders:
            if str(order.client_order_id) == client_order_id:
                return _safe_crypto_order(order, self._asset_service)
        return None

    def submit_crypto_market_buy(
        self,
        symbol: str,
        notional: Decimal,
        client_order_id: str,
        *,
        time_in_force: str = "gtc",
    ) -> dict[str, object]:
        canonical = self._asset_service.normalize_broker_symbol(symbol)
        request = MarketOrderRequest(
            symbol=canonical,
            notional=decimal_to_sdk_float(notional),
            side=OrderSide.BUY,
            time_in_force=_parse_tif(time_in_force),
            client_order_id=client_order_id,
        )
        return self._submit(request, canonical, "BUY")

    def submit_crypto_market_sell(
        self,
        symbol: str,
        quantity: Decimal,
        client_order_id: str,
        *,
        time_in_force: str = "gtc",
    ) -> dict[str, object]:
        canonical = self._asset_service.normalize_broker_symbol(symbol)
        request = MarketOrderRequest(
            symbol=canonical,
            qty=decimal_to_sdk_float(quantity),
            side=OrderSide.SELL,
            time_in_force=_parse_tif(time_in_force),
            client_order_id=client_order_id,
        )
        return self._submit(request, canonical, "SELL")

    def synchronize_crypto_order(self, alpaca_order_id: str) -> dict[str, object]:
        try:
            order = self._client.get_order_by_id(alpaca_order_id)
        except Exception as exc:
            raise AlpacaConnectionError(f"Unable to synchronize crypto order: {exc}") from exc
        return _safe_crypto_order(order, self._asset_service)

    def _submit(self, request: MarketOrderRequest, symbol: str, side: str) -> dict[str, object]:
        try:
            order = self._client.submit_order(order_data=request)
            logger.info("Submitted crypto paper %s order for %s.", side, symbol)
            return _safe_crypto_order(order, self._asset_service)
        except Exception as exc:
            raise AlpacaConnectionError(f"Crypto order submission failed: {exc}") from exc


def _parse_tif(value: str) -> TimeInForce:
    mapping = {"gtc": TimeInForce.GTC, "ioc": TimeInForce.IOC}
    return mapping.get(value.lower(), TimeInForce.GTC)


def _is_crypto_order(order: Any) -> bool:
    symbol = str(order.symbol)
    return "/" in symbol or symbol.upper().endswith("USD")


def _safe_crypto_order(order: Any, asset_service: CryptoAssetService) -> dict[str, object]:
    symbol = asset_service.normalize_broker_symbol(str(order.symbol))
    filled_qty = parse_decimal(getattr(order, "filled_qty", 0))
    requested_qty = parse_decimal(getattr(order, "qty", 0) or 0)
    notional = parse_decimal(getattr(order, "notional", 0) or 0)
    filled_avg = parse_decimal(getattr(order, "filled_avg_price", 0) or 0)
    return {
        "alpaca_order_id": str(order.id),
        "client_order_id": str(order.client_order_id),
        "symbol": symbol,
        "side": str(order.side.value if hasattr(order.side, "value") else order.side),
        "quantity": str(requested_qty),
        "notional": str(notional),
        "filled_quantity": str(filled_qty),
        "filled_average_price": str(filled_avg) if filled_avg > 0 else None,
        "status": str(order.status.value if hasattr(order.status, "value") else order.status),
        "time_in_force": str(
            order.time_in_force.value if hasattr(order.time_in_force, "value") else order.time_in_force
        ),
        "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
        "filled_at": order.filled_at.isoformat() if order.filled_at else None,
        "failure_message": getattr(order, "reject_reason", None) or getattr(order, "cancel_reason", None),
        "asset_type": "CRYPTO",
    }


def _safe_crypto_position(position: Any, symbol: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "quantity": str(parse_decimal(position.qty)),
        "market_value": float(position.market_value),
        "current_price": float(position.current_price),
        "avg_entry_price": float(position.avg_entry_price),
        "cost_basis": float(position.cost_basis),
        "unrealized_pl": float(position.unrealized_pl),
        "asset_type": "CRYPTO",
    }

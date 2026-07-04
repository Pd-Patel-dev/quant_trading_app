"""Alpaca paper order management."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

from broker.alpaca_account import _mask_account_number, _to_float
from core.exceptions import AlpacaConnectionError, ConfigurationError

logger = logging.getLogger(__name__)


class AlpacaPaperOrderManager:
    """Submit and synchronize Alpaca paper market orders."""

    def __init__(self, api_key: str, secret_key: str) -> None:
        if not api_key or not secret_key:
            raise ConfigurationError(
                "Alpaca API credentials are missing. "
                "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in your .env file."
            )
        self._client = TradingClient(api_key, secret_key, paper=True)

    def get_account_summary(self) -> dict[str, object]:
        try:
            account = self._client.get_account()
        except Exception as exc:
            logger.error("Failed to retrieve Alpaca paper account summary.")
            raise AlpacaConnectionError(
                f"Unable to connect to Alpaca paper account: {exc}"
            ) from exc
        return {
            "account_number": _mask_account_number(str(account.account_number)),
            "status": str(account.status),
            "currency": str(account.currency),
            "cash": _to_float(account.cash),
            "portfolio_value": _to_float(account.portfolio_value),
            "buying_power": _to_float(account.buying_power),
            "equity": _to_float(account.equity),
            "last_equity": _to_float(account.last_equity),
            "pattern_day_trader": bool(account.pattern_day_trader),
            "trading_blocked": bool(account.trading_blocked),
        }

    def get_market_clock(self) -> dict[str, object]:
        try:
            clock = self._client.get_clock()
        except Exception as exc:
            raise AlpacaConnectionError(f"Unable to retrieve market clock: {exc}") from exc
        return {
            "is_open": bool(clock.is_open),
            "timestamp": clock.timestamp.isoformat() if clock.timestamp else None,
            "next_open": clock.next_open.isoformat() if clock.next_open else None,
            "next_close": clock.next_close.isoformat() if clock.next_close else None,
        }

    def get_all_positions(self) -> list[dict[str, object]]:
        try:
            positions = self._client.get_all_positions()
        except Exception as exc:
            raise AlpacaConnectionError(f"Unable to retrieve positions: {exc}") from exc
        return [_safe_position(position) for position in positions]

    def get_position(self, symbol: str) -> dict[str, object] | None:
        try:
            position = self._client.get_open_position(symbol.upper())
        except Exception:
            return None
        return _safe_position(position)

    def get_open_orders(self) -> list[dict[str, object]]:
        try:
            request = GetOrdersRequest(status=QueryOrderStatus.OPEN)
            orders = self._client.get_orders(filter=request)
        except Exception as exc:
            raise AlpacaConnectionError(f"Unable to retrieve open orders: {exc}") from exc
        return [_safe_order(order) for order in orders]

    def get_order_by_client_order_id(self, client_order_id: str) -> dict[str, object] | None:
        try:
            orders = self._client.get_orders(
                filter=GetOrdersRequest(status=QueryOrderStatus.ALL)
            )
        except Exception as exc:
            raise AlpacaConnectionError(f"Unable to query orders: {exc}") from exc
        for order in orders:
            if str(order.client_order_id) == client_order_id:
                return _safe_order(order)
        return None

    def submit_market_order(
        self,
        symbol: str,
        quantity: int,
        side: str,
        client_order_id: str,
    ) -> dict[str, object]:
        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        request = MarketOrderRequest(
            symbol=symbol.upper(),
            qty=quantity,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        try:
            order = self._client.submit_order(order_data=request)
            logger.info(
                "Submitted paper %s order for %s shares of %s.",
                side,
                quantity,
                symbol.upper(),
            )
            return _safe_order(order)
        except Exception as exc:
            logger.error("Paper order submission failed for %s.", symbol.upper())
            raise AlpacaConnectionError(f"Order submission failed: {exc}") from exc

    def synchronize_order(self, alpaca_order_id: str) -> dict[str, object]:
        try:
            order = self._client.get_order_by_id(alpaca_order_id)
        except Exception as exc:
            raise AlpacaConnectionError(f"Unable to synchronize order: {exc}") from exc
        return _safe_order(order)


def _safe_position(position: Any) -> dict[str, object]:
    return {
        "symbol": str(position.symbol),
        "quantity": int(float(position.qty)),
        "market_value": _to_float(position.market_value),
        "current_price": _to_float(position.current_price),
        "avg_entry_price": _to_float(position.avg_entry_price),
        "cost_basis": _to_float(position.cost_basis),
        "unrealized_pl": _to_float(position.unrealized_pl),
    }


def _safe_order(order: Any) -> dict[str, object]:
    filled_qty = int(float(order.filled_qty or 0))
    filled_avg = _to_float(order.filled_avg_price) if order.filled_avg_price else None
    return {
        "alpaca_order_id": str(order.id),
        "client_order_id": str(order.client_order_id),
        "symbol": str(order.symbol),
        "side": str(order.side.value if hasattr(order.side, "value") else order.side),
        "quantity": int(float(order.qty)),
        "filled_quantity": filled_qty,
        "filled_average_price": filled_avg,
        "status": str(order.status.value if hasattr(order.status, "value") else order.status),
        "order_type": str(order.type.value if hasattr(order.type, "value") else order.type),
        "time_in_force": str(
            order.time_in_force.value
            if hasattr(order.time_in_force, "value")
            else order.time_in_force
        ),
        "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
        "filled_at": order.filled_at.isoformat() if order.filled_at else None,
        "failure_message": _safe_failure_message(order),
    }


def _safe_failure_message(order: Any) -> str | None:
    for attr in ("reject_reason", "cancel_reason"):
        value = getattr(order, attr, None)
        if value:
            return str(value)
    return None

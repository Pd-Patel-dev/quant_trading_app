"""Read-only Alpaca paper account client."""

from __future__ import annotations

import logging
from typing import Any

from alpaca.trading.client import TradingClient

from core.exceptions import AlpacaConnectionError, ConfigurationError

logger = logging.getLogger(__name__)


class AlpacaPaperAccountClient:
    """Retrieve read-only paper account information from Alpaca."""

    def __init__(self, api_key: str, secret_key: str) -> None:
        if not api_key or not secret_key:
            raise ConfigurationError(
                "Alpaca API credentials are missing. "
                "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in your .env file."
            )
        self._client = TradingClient(api_key, secret_key, paper=True)

    def get_account_summary(self) -> dict[str, object]:
        """Return safe, read-only account fields."""
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


def _mask_account_number(account_number: str) -> str:
    if len(account_number) <= 4:
        return "****"
    return f"{'*' * (len(account_number) - 4)}{account_number[-4:]}"


def _to_float(value: Any) -> float:
    return float(value)

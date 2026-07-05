"""Resolve the effective paper-trading capital pool."""

from __future__ import annotations

import logging
from decimal import Decimal

from config.settings import Settings, get_settings
from core.exceptions import AllocationError, AlpacaConnectionError
from core.models import to_decimal

logger = logging.getLogger(__name__)


class PaperCapitalService:
    """Use Alpaca paper account cash or a local configured pool for allocations."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def uses_alpaca_capital(self) -> bool:
        return (
            self._settings.paper_capital_source == "alpaca"
            and self._settings.alpaca_configured
        )

    @property
    def source_label(self) -> str:
        if self.uses_alpaca_capital:
            return "Alpaca paper account cash"
        return "Local paper capital pool"

    def get_capital_pool(self) -> Decimal:
        if not self.uses_alpaca_capital:
            return to_decimal(self._settings.local_paper_capital_pool)
        account = self._fetch_alpaca_account()
        return to_decimal(account.get("cash", 0))

    def get_crypto_capital_pool(self) -> Decimal:
        if not self.uses_alpaca_capital:
            return to_decimal(self._settings.max_crypto_total_allocation)
        return self.get_capital_pool()

    def _fetch_alpaca_account(self) -> dict[str, object]:
        try:
            from broker.alpaca_account import AlpacaPaperAccountClient

            client = AlpacaPaperAccountClient(
                self._settings.alpaca_api_key,
                self._settings.alpaca_secret_key,
            )
            return client.get_account_summary()
        except AlpacaConnectionError:
            raise
        except Exception as exc:
            logger.error("Failed to load Alpaca paper account for capital pool.")
            raise AllocationError(
                f"Unable to load Alpaca paper account balance: {exc}"
            ) from exc

"""Crypto fee estimation and processing."""

from __future__ import annotations

from decimal import Decimal

from config.settings import Settings, get_settings
from core.asset_models import CryptoFeeStatus
from core.crypto_decimal import parse_decimal


class CryptoFeeService:
    """Estimate and record crypto trading fees."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def estimate_fee(self, notional: Decimal) -> tuple[Decimal, str]:
        fee = notional * self._settings.crypto_estimated_fee_percent
        return fee, "USD"

    def process_confirmed_fee(
        self,
        database,
        order_id: int,
        symbol: str,
        fee_amount: Decimal,
        fee_currency: str,
        *,
        alpaca_activity_id: str | None = None,
        activity_timestamp: str | None = None,
    ) -> None:
        if fee_amount <= 0:
            return
        database.save_crypto_fee_record(
            order_id,
            symbol,
            fee_amount,
            fee_currency,
            alpaca_activity_id=alpaca_activity_id,
            activity_timestamp=activity_timestamp,
            processing_status=CryptoFeeStatus.CONFIRMED,
        )

    @staticmethod
    def fee_status_from_amount(fee_amount: Decimal | None) -> CryptoFeeStatus:
        if fee_amount is None:
            return CryptoFeeStatus.NOT_AVAILABLE
        if fee_amount > 0:
            return CryptoFeeStatus.CONFIRMED
        return CryptoFeeStatus.NOT_AVAILABLE

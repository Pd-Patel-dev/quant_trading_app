"""Alpaca crypto asset discovery and validation."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from config.settings import Settings, get_settings
from core.asset_models import AssetType, CryptoTradingStatus
from core.crypto_decimal import parse_decimal
from core.exceptions import AlpacaConnectionError, ConfigurationError
from market_data.symbol_normalizer import SymbolNormalizer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CryptoAssetRules:
    symbol: str
    status: str
    tradable: bool
    fractionable: bool
    minimum_order_size: Decimal | None
    minimum_trade_increment: Decimal | None
    price_increment: Decimal | None
    base_currency: str
    quote_currency: str


@dataclass(frozen=True)
class CryptoAssetValidation:
    symbol: str
    is_valid: bool
    status: CryptoTradingStatus
    rules: CryptoAssetRules | None
    messages: tuple[str, ...]


class CryptoAssetService:
    """Query and cache Alpaca crypto asset metadata."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        settings: Settings | None = None,
        normalizer: SymbolNormalizer | None = None,
    ) -> None:
        if not api_key or not secret_key:
            raise ConfigurationError("Alpaca credentials are required for crypto asset discovery.")
        self._client = TradingClient(api_key, secret_key, paper=True)
        self._settings = settings or get_settings()
        self._normalizer = normalizer or SymbolNormalizer()
        self._cache: dict[str, CryptoAssetRules] = {}
        self._cache_expires_at = 0.0

    def refresh_assets(self) -> list[CryptoAssetRules]:
        self._cache.clear()
        self._cache_expires_at = 0.0
        return self.list_active_usd_pairs()

    def list_active_usd_pairs(self) -> list[CryptoAssetRules]:
        assets = self._load_assets()
        return [
            asset
            for asset in assets.values()
            if asset.tradable and asset.quote_currency == "USD"
        ]

    def validate_pair(self, symbol: str) -> CryptoAssetValidation:
        try:
            canonical = self._normalizer.normalize(AssetType.CRYPTO, symbol)
        except Exception as exc:
            return CryptoAssetValidation(
                symbol=symbol,
                is_valid=False,
                status=CryptoTradingStatus.ASSET_NOT_TRADABLE,
                rules=None,
                messages=(str(exc),),
            )
        _, quote = self._normalizer.split_crypto_pair(canonical)
        if quote not in self._settings.supported_crypto_quote_currencies:
            if quote != "USD":
                return CryptoAssetValidation(
                    symbol=canonical,
                    is_valid=False,
                    status=CryptoTradingStatus.ASSET_NOT_TRADABLE,
                    rules=None,
                    messages=(f"Quote currency {quote} is not supported for order submission.",),
                )
        rules = self.get_asset_rules(canonical)
        if rules is None:
            return CryptoAssetValidation(
                symbol=canonical,
                is_valid=False,
                status=CryptoTradingStatus.ASSET_NOT_TRADABLE,
                rules=None,
                messages=("Asset not found at Alpaca.",),
            )
        if not rules.tradable:
            return CryptoAssetValidation(
                symbol=canonical,
                is_valid=False,
                status=CryptoTradingStatus.ASSET_NOT_TRADABLE,
                rules=rules,
                messages=("Asset is not tradable.",),
            )
        return CryptoAssetValidation(
            symbol=canonical,
            is_valid=True,
            status=CryptoTradingStatus.READY,
            rules=rules,
            messages=(),
        )

    def get_asset_rules(self, symbol: str) -> CryptoAssetRules | None:
        canonical = self._normalizer.normalize(AssetType.CRYPTO, symbol)
        assets = self._load_assets()
        return assets.get(canonical)

    def normalize_broker_symbol(self, broker_symbol: str) -> str:
        if "/" in broker_symbol:
            return self._normalizer.normalize(AssetType.CRYPTO, broker_symbol)
        return self._normalizer.normalize(AssetType.CRYPTO, broker_symbol)

    def _load_assets(self) -> dict[str, CryptoAssetRules]:
        now = time.time()
        if self._cache and now < self._cache_expires_at:
            return self._cache
        try:
            request = GetAssetsRequest(asset_class=AssetClass.CRYPTO, status=AssetStatus.ACTIVE)
            raw_assets = self._client.get_all_assets(request)
        except Exception as exc:
            raise AlpacaConnectionError(f"Unable to load crypto assets: {exc}") from exc
        parsed: dict[str, CryptoAssetRules] = {}
        for asset in raw_assets:
            symbol = str(asset.symbol)
            try:
                canonical = self.normalize_broker_symbol(symbol)
            except Exception:
                continue
            base, quote = self._normalizer.split_crypto_pair(canonical)
            parsed[canonical] = CryptoAssetRules(
                symbol=canonical,
                status=str(asset.status),
                tradable=bool(getattr(asset, "tradable", False)),
                fractionable=bool(getattr(asset, "fractionable", False)),
                minimum_order_size=_optional_decimal(getattr(asset, "min_order_size", None)),
                minimum_trade_increment=_optional_decimal(getattr(asset, "min_trade_increment", None)),
                price_increment=_optional_decimal(getattr(asset, "price_increment", None)),
                base_currency=base,
                quote_currency=quote,
            )
        self._cache = parsed
        self._cache_expires_at = now + self._settings.crypto_asset_cache_seconds
        return parsed


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    parsed = parse_decimal(value)
    return parsed if parsed > 0 else None

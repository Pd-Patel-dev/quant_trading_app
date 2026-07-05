"""Symbol normalization for stocks and crypto pairs."""

from __future__ import annotations

import re

from core.exceptions import ConfigurationError
from market_data.models import AssetType, ParseSymbolsResult

_STOCK_PATTERN = re.compile(r"^[A-Z0-9.\-]{1,12}$")
_FORBIDDEN = re.compile(r"[/\\;]|(\b(DROP|DELETE|SELECT|INSERT|UPDATE)\b)", re.I)
_QUOTE_SUFFIXES = ("USDT", "USDC", "USD")
_MAX_SYMBOL_LENGTH = 20


class SymbolNormalizer:
    """Normalize and validate stock tickers and crypto pairs."""

    def normalize(self, asset_type: AssetType, raw: str) -> str:
        text = raw.strip()
        if not text:
            raise ConfigurationError("Symbol cannot be empty.")
        if len(text) > _MAX_SYMBOL_LENGTH:
            raise ConfigurationError(f"Symbol exceeds maximum length ({_MAX_SYMBOL_LENGTH}).")
        if asset_type == AssetType.STOCK:
            return self._normalize_stock(text)
        return self._normalize_crypto(text)

    def parse_input(self, asset_type: AssetType, text: str) -> ParseSymbolsResult:
        parts = re.split(r"[\s,]+", text.strip())
        normalized: list[str] = []
        duplicates: list[str] = []
        invalid: list[str] = []
        warnings: list[str] = []
        seen: set[str] = set()

        for part in parts:
            if not part.strip():
                continue
            try:
                symbol = self.normalize(asset_type, part)
            except ConfigurationError as exc:
                invalid.append(f"{part}: {exc}")
                continue
            if symbol in seen:
                duplicates.append(symbol)
                continue
            seen.add(symbol)
            normalized.append(symbol)

        return ParseSymbolsResult(
            normalized=normalized,
            duplicates_removed=duplicates,
            invalid=invalid,
            warnings=warnings,
        )

    @staticmethod
    def _normalize_stock(text: str) -> str:
        symbol = text.upper()
        if _FORBIDDEN.search(symbol):
            raise ConfigurationError("Symbol contains invalid characters.")
        if not _STOCK_PATTERN.match(symbol):
            raise ConfigurationError(f"Invalid stock symbol: {text}")
        return symbol

    @staticmethod
    def _normalize_crypto(text: str) -> str:
        cleaned = text.upper().replace("-", "/").replace("_", "/")
        if "/" in cleaned:
            base, quote = cleaned.split("/", 1)
            if not base or not quote:
                raise ConfigurationError("Use canonical format such as BTC/USD.")
            return f"{base}/{quote}"

        upper = cleaned
        for suffix in _QUOTE_SUFFIXES:
            if upper.endswith(suffix) and len(upper) > len(suffix):
                base = upper[: -len(suffix)]
                if len(base) >= 2:
                    return f"{base}/{suffix}"
        raise ConfigurationError(
            f"Ambiguous crypto symbol '{text}'. Use canonical format such as BTC/USD."
        )

    @staticmethod
    def split_crypto_pair(symbol: str) -> tuple[str, str]:
        base, quote = symbol.split("/", 1)
        return base, quote

"""Deterministic client order ID generation."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime


def build_client_order_id(
    prefix: str,
    strategy_id: int,
    symbol: str,
    side: str,
    signal_timestamp: datetime,
) -> str:
    """Build a stable client order ID for a stock strategy signal."""
    date_str = signal_timestamp.strftime("%Y%m%d")
    raw = f"{strategy_id}-{symbol.upper()}-{side.upper()}-{date_str}"
    suffix = hashlib.sha256(raw.encode()).hexdigest()[:6]
    return f"{prefix}-{strategy_id}-{symbol.lower()}-{side.lower()}-{date_str}-{suffix}"


def build_crypto_client_order_id(
    prefix: str,
    strategy_id: int,
    symbol: str,
    side: str,
    signal_timestamp: datetime,
    signal_reason: str | None = None,
) -> str:
    """Build a stable client order ID for a crypto strategy signal."""
    date_str = signal_timestamp.strftime("%Y%m%d")
    pair_token = _pair_token(symbol)
    reason_token = _reason_token(signal_reason)
    raw = f"{strategy_id}-CRYPTO-{pair_token}-{side.upper()}-{date_str}-{reason_token}"
    suffix = hashlib.sha256(raw.encode()).hexdigest()[:6]
    if reason_token:
        return (
            f"{prefix}-{strategy_id}-crypto-{pair_token}-{side.lower()}-"
            f"{date_str}-{reason_token}-{suffix}"
        )
    return f"{prefix}-{strategy_id}-crypto-{pair_token}-{side.lower()}-{date_str}-{suffix}"


def _reason_token(signal_reason: str | None) -> str:
    if not signal_reason:
        return ""
    cleaned = re.sub(r"[^A-Z0-9_]", "", signal_reason.upper())[:12]
    mapping = {
        "EMA_BULLISH_CROSS_WITH_LONG_TERM_FILTER": "bull",
        "EMA_BEARISH_CROSS": "bear",
        "STOP_LOSS": "stop",
    }
    return mapping.get(signal_reason, cleaned.lower()[:6])


def _pair_token(symbol: str) -> str:
    cleaned = symbol.upper().replace("/", "").replace("-", "").replace("_", "")
    return re.sub(r"[^A-Z0-9]", "", cleaned).lower()

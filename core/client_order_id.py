"""Deterministic client order ID generation."""

from __future__ import annotations

import hashlib
from datetime import datetime


def build_client_order_id(
    prefix: str,
    strategy_id: int,
    symbol: str,
    side: str,
    signal_timestamp: datetime,
) -> str:
    """Build a stable client order ID for a strategy signal."""
    date_str = signal_timestamp.strftime("%Y%m%d")
    raw = f"{strategy_id}-{symbol.upper()}-{side.upper()}-{date_str}"
    suffix = hashlib.sha256(raw.encode()).hexdigest()[:6]
    return f"{prefix}-{strategy_id}-{symbol.lower()}-{side.lower()}-{date_str}-{suffix}"

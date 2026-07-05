"""One-shot historical data refresh worker."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from automation.worker_lock import WorkerLock
from config.settings import get_settings
from data.database import DatabaseManager
from market_data.factory import build_market_data_stack
from market_data.models import AssetRequest, AssetType, DataTimeframe

logger = logging.getLogger(__name__)
LOCK_NAME = "historical-data-refresh"


def _latest_completed_stock_end() -> datetime:
    now = datetime.now(timezone.utc)
    if now.weekday() == 5:
        now -= timedelta(days=1)
    if now.weekday() == 6:
        now -= timedelta(days=2)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _latest_completed_crypto_end() -> datetime:
    now = datetime.now(timezone.utc)
    return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    database = DatabaseManager(settings.database_full_path)

    lock = WorkerLock(database, LOCK_NAME)
    if not lock.acquire():
        logger.warning("Another historical data refresh worker holds the lock.")
        return 1

    try:
        _, cache, batch = build_market_data_stack(database, settings)
        symbols: list[tuple[AssetType, str]] = database.get_active_strategy_symbols()
        watchlist = [
            s.strip().upper()
            for s in settings.research_watchlist_symbols.split(",")
            if s.strip()
        ]
        for symbol in watchlist:
            symbols.append((AssetType.STOCK, symbol))

        seen: set[tuple[str, str]] = set()
        requests: list[AssetRequest] = []
        for asset_type, symbol in symbols:
            key = (asset_type, symbol)
            if key in seen:
                continue
            seen.add(key)
            end = (
                _latest_completed_crypto_end()
                if asset_type == AssetType.CRYPTO.value
                else _latest_completed_stock_end()
            )
            start = end - timedelta(days=365 * 2)
            requests.append(
                AssetRequest(
                    asset_type=AssetType(asset_type),
                    symbol=symbol,
                    start=start,
                    end=end,
                    repair_gaps=True,
                )
            )

        if not requests:
            logger.info("No symbols to refresh.")
            return 0

        batch_result = batch.get_or_download_many(requests)
        for error in batch_result.errors:
            logger.error("Refresh error: %s", error)
        logger.info("Refreshed %s symbols.", len(batch_result.results))
        return 0 if not batch_result.errors else 2
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())

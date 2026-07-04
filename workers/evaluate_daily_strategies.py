"""One-shot after-close strategy evaluation worker."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from automation.automation_service import AutomationService
from automation.models import AutomationRunStatus
from broker.alpaca_order_manager import AlpacaPaperOrderManager
from config.settings import get_settings
from data.alpaca_data import AlpacaMarketDataProvider
from data.database import DatabaseManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    settings = get_settings()
    database = DatabaseManager(settings.database_full_path)
    order_manager = None
    data_provider = None
    if settings.alpaca_configured:
        order_manager = AlpacaPaperOrderManager(settings.alpaca_api_key, settings.alpaca_secret_key)
        data_provider = AlpacaMarketDataProvider(settings.alpaca_api_key, settings.alpaca_secret_key)

    service = AutomationService(database, order_manager, data_provider, settings)
    result = service.run_after_close_evaluation()
    logger.info("After-close evaluation finished: %s", result.status.value)
    if result.status == AutomationRunStatus.FAILED:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

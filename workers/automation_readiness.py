"""Read-only automation readiness checks."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from automation.automation_service import AutomationService
from broker.alpaca_order_manager import AlpacaPaperOrderManager
from config.settings import get_settings
from data.database import DatabaseManager


def main() -> int:
    settings = get_settings()
    database = DatabaseManager(settings.database_full_path)
    order_manager = None
    if settings.alpaca_configured:
        order_manager = AlpacaPaperOrderManager(settings.alpaca_api_key, settings.alpaca_secret_key)

    service = AutomationService(database, order_manager, None, settings)
    result = service.check_readiness()

    print("PASSED CHECKS:")
    for check in result["passed"]:
        print(f"  [OK] {check}")

    if result["failed"]:
        print("\nFAILED CHECKS:")
        for item in result["failed"]:
            print(f"  [FAIL] {item['check']}: {item['reason']}")

    print()
    if result["ready"]:
        print("READY")
        return 0
    print("NOT READY")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

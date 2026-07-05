"""Read-only crypto paper trading readiness checks."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings
from data.database import DatabaseManager


def main() -> int:
    settings = get_settings()
    checks: list[tuple[str, bool, str]] = []

    checks.append(("Credentials configured", settings.alpaca_configured, "Alpaca keys present"))
    checks.append(("Paper mode", settings.trading_mode == "paper", f"mode={settings.trading_mode}"))
    checks.append(("Live trading disabled", not settings.live_trading_enabled, "LIVE_TRADING_ENABLED=False"))
    checks.append(
        (
            "Crypto config present",
            hasattr(settings, "crypto_paper_trading_enabled"),
            "Crypto settings loaded",
        )
    )
    checks.append(
        (
            "Crypto kill switch status known",
            hasattr(settings, "crypto_kill_switch_engaged"),
            f"engaged={settings.crypto_kill_switch_engaged}",
        )
    )

    db = DatabaseManager(settings.database_full_path)
    checks.append(("Database healthy", db.database_exists(), str(settings.database_full_path)))
    checks.append(("Migration current", db.schema_version >= 6, f"schema v{db.schema_version}"))

    if settings.alpaca_configured:
        try:
            from broker.crypto_asset_service import CryptoAssetService

            assets = CryptoAssetService(settings.alpaca_api_key, settings.alpaca_secret_key)
            pairs = assets.list_active_usd_pairs()
            checks.append(("Crypto Assets API reachable", True, f"{len(pairs)} USD pairs"))
            checks.append(("Tradable USD pair exists", len(pairs) > 0, "At least one pair"))
        except Exception as exc:
            checks.append(("Crypto Assets API reachable", False, str(exc)))
            checks.append(("Tradable USD pair exists", False, "API unavailable"))

        try:
            from broker.crypto_order_manager import AlpacaCryptoPaperOrderManager

            manager = AlpacaCryptoPaperOrderManager(settings.alpaca_api_key, settings.alpaca_secret_key)
            account = manager.get_account_summary()
            active = str(account.get("status", "")).upper() in ("ACTIVE", "ACCOUNTSTATUS.ACTIVE")
            checks.append(("Alpaca account active", active, str(account.get("status"))))
            checks.append(("Trading API reachable", True, "Connected"))
        except Exception as exc:
            checks.append(("Alpaca account active", False, str(exc)))
            checks.append(("Trading API reachable", False, str(exc)))
    else:
        checks.append(("Crypto Assets API reachable", False, "Missing credentials"))
        checks.append(("Alpaca account active", False, "Missing credentials"))

    checks.append(
        (
            "Crypto limits valid",
            settings.max_crypto_paper_order_notional > 0 and settings.max_crypto_total_allocation > 0,
            "Limits configured",
        )
    )
    checks.append(
        (
            "Unknown crypto orders",
            db.count_unknown_crypto_orders() == 0,
            f"count={db.count_unknown_crypto_orders()}",
        )
    )

    passed = [name for name, ok, _ in checks if ok]
    failed = [f"{name}: {detail}" for name, ok, detail in checks if not ok]

    print("Crypto Paper Readiness")
    print("=" * 40)
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")

    if failed:
        print("\nCRYPTO PAPER NOT READY")
        for item in failed:
            print(f" - {item}")
        return 1

    print("\nCRYPTO PAPER READY")
    print(f"Passed checks: {len(passed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

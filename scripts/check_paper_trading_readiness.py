"""Read-only paper trading readiness checks."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings
from data.database import DatabaseManager
from portfolio.allocation_manager import AllocationManager


def main() -> int:
    settings = get_settings()
    checks: list[tuple[str, bool, str]] = []

    checks.append(("Credentials configured", settings.alpaca_configured, ""))
    checks.append(("Trading mode is paper", settings.trading_mode == "paper", settings.trading_mode))
    checks.append(("Live trading disabled", not settings.live_trading_enabled, str(settings.live_trading_enabled)))
    checks.append(
        ("Paper submission enabled", settings.paper_order_submission_enabled, ""),
    )
    checks.append(
        ("Manual confirmation required", settings.manual_order_confirmation_required, ""),
    )

    db = DatabaseManager(settings.database_full_path)
    checks.append(("Database healthy", db.database_exists(), f"schema v{db.schema_version}"))

    allocation = AllocationManager(db, settings)
    checks.append(
        (
            "Local allocations valid",
            allocation.get_total_allocated() <= allocation.capital_pool,
            f"allocated={allocation.get_total_allocated()} pool={allocation.capital_pool}",
        )
    )
    checks.append(("No unknown local orders", db.count_unknown_orders() == 0, str(db.count_unknown_orders())))

    active_symbols: dict[str, int] = {}
    for strategy in db.list_strategies():
        if strategy.status.value == "ACTIVE":
            active_symbols[strategy.symbol] = active_symbols.get(strategy.symbol, 0) + 1
    dupes = [s for s, c in active_symbols.items() if c > 1]
    checks.append(("No duplicate active strategy symbols", not dupes, str(dupes)))

    if settings.alpaca_configured:
        try:
            from broker.alpaca_order_manager import AlpacaPaperOrderManager

            manager = AlpacaPaperOrderManager(settings.alpaca_api_key, settings.alpaca_secret_key)
            account = manager.get_account_summary()
            checks.append(("Alpaca account active", "ACTIVE" in str(account["status"]).upper(), str(account["status"])))
            checks.append(("Trading not blocked", not account["trading_blocked"], str(account["trading_blocked"])))
            clock = manager.get_market_clock()
            checks.append(("Market clock available", clock.get("timestamp") is not None, ""))
        except Exception as exc:
            checks.append(("Alpaca connectivity", False, str(exc)))

    print("Quant Strategy Lab - Paper Trading Readiness")
    print("=" * 50)
    failed = 0
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        line = f"[{status}] {name}"
        if detail:
            line += f" ({detail})"
        print(line)
        if not ok:
            failed += 1

    print("=" * 50)
    if failed:
        print(f"{failed} check(s) failed.")
        return 1
    print("All readiness checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

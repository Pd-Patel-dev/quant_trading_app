"""Read-only health checks for Crypto EMA trend strategy."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings
from data.database import DatabaseManager
from strategies.crypto_ema_trend_following import MINIMUM_HISTORY_BARS, CryptoEMATrendFollowingStrategy
from strategies.registry import get_registry


def main() -> int:
    settings = get_settings()
    registry = get_registry()
    checks: list[tuple[str, bool, str]] = []
    strategy_type = CryptoEMATrendFollowingStrategy.STRATEGY_TYPE

    try:
        registry.get_strategy_class(strategy_type)
        checks.append(("registered", True, strategy_type))
    except Exception as exc:
        checks.append(("registered", False, str(exc)))
        return _report(checks)

    meta = registry.get_metadata(strategy_type)
    checks.append(("metadata_valid", bool(meta.display_name), meta.display_name))
    checks.append(("btc_supported", "BTC/USD" in meta.supported_symbols, "BTC/USD"))
    checks.append(("eth_supported", "ETH/USD" in meta.supported_symbols, "ETH/USD"))
    checks.append(
        ("daily_timeframe", "Daily" in meta.supported_timeframes or "1Day" in meta.supported_timeframes, str(meta.supported_timeframes))
    )
    checks.append(("min_history_250", meta.minimum_history_bars >= MINIMUM_HISTORY_BARS, str(meta.minimum_history_bars)))

    impl = registry.build(strategy_type, {})
    checks.append(("fast_ema_20", impl._fast == 20, str(impl._fast)))
    checks.append(("medium_ema_50", impl._medium == 50, str(impl._medium)))
    checks.append(("long_ema_200", impl._long == 200, str(impl._long)))
    checks.append(("stop_8pct", impl._stop_loss_percent == __import__("decimal").Decimal("0.08"), "0.08"))
    checks.append(("risk_1pct", impl._risk_per_trade_percent == __import__("decimal").Decimal("0.01"), "0.01"))
    checks.append(("manual_paper", meta.supports_manual_paper_trading, "yes"))
    checks.append(("automation_default_off", settings.crypto_automation_enabled is False, "global flag"))
    checks.append(("no_live_trading", settings.live_trading_enabled is False, "live disabled"))
    checks.append(("paper_mode", settings.trading_mode == "paper", settings.trading_mode))

    db_path = settings.database_full_path
    db = DatabaseManager(str(db_path))
    db.initialize()
    with db.connect() as conn:
        version = conn.execute("SELECT MAX(version) AS v FROM schema_versions").fetchone()["v"]
        checks.append(("schema_v8", version >= 8, str(version)))
        pos_cols = {row[1] for row in conn.execute("PRAGMA table_info(crypto_strategy_positions)")}
        for col in ("entry_price_text", "stop_price_text", "stop_loss_percent_text"):
            checks.append((f"column_{col}", col in pos_cols, col))

    return _report(checks)


def _report(checks: list[tuple[str, bool, str]]) -> int:
    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {name}: {detail}")
    if failed:
        print("\nCRYPTO EMA STRATEGY NEEDS ATTENTION")
        return 1
    print("\nCRYPTO EMA STRATEGY HEALTHY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

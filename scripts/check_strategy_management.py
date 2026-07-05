"""Read-only strategy lifecycle health validation."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings
from core.models import StrategyStatus
from data.database import DatabaseManager


def main() -> int:
    settings = get_settings()
    db = DatabaseManager(settings.database_path)
    issues: list[str] = []

    with db.connect() as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='strategies'"
        ).fetchone()
        if not table:
            issues.append("strategies table is missing")

        fk = conn.execute("PRAGMA foreign_keys").fetchone()
        if not fk or fk[0] != 1:
            issues.append("Foreign keys are not enabled")

        required_columns = {
            "status",
            "is_active",
            "paused_at",
            "stopped_at",
            "archived_at",
            "deactivated_reason",
            "updated_at",
        }
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(strategies)").fetchall()
        }
        missing = required_columns - columns
        if missing:
            issues.append(f"Missing strategy columns: {sorted(missing)}")

        valid_statuses = {s.value for s in StrategyStatus}
        rows = conn.execute("SELECT id, status, is_active, automation_enabled FROM strategies").fetchall()
        for row in rows:
            if row["status"] not in valid_statuses:
                issues.append(f"Strategy {row['id']} has invalid status {row['status']}")
            expected_active = 1 if row["status"] == StrategyStatus.ACTIVE.value else 0
            if row["is_active"] != expected_active:
                issues.append(
                    f"Strategy {row['id']} status/is_active mismatch "
                    f"({row['status']}, is_active={row['is_active']})"
                )
            if row["status"] in (
                StrategyStatus.ARCHIVED.value,
                StrategyStatus.STOPPED.value,
                StrategyStatus.PAUSED.value,
            ):
                if row["is_active"] == 1:
                    issues.append(f"Strategy {row['id']} is inactive status but is_active=1")
                if row["automation_enabled"] == 1:
                    issues.append(
                        f"Strategy {row['id']} ({row['status']}) has automation enabled while inactive"
                    )

        duplicates = conn.execute(
            """
            SELECT asset_type, symbol, COUNT(*) AS cnt
            FROM strategies WHERE status = 'ACTIVE'
            GROUP BY asset_type, symbol HAVING COUNT(*) > 1
            """
        ).fetchall()
        for row in duplicates:
            issues.append(
                f"Duplicate ACTIVE strategies for {row['asset_type']}/{row['symbol']} ({row['cnt']})"
            )

        orphan_orders = conn.execute(
            """
            SELECT COUNT(*) FROM paper_orders
            WHERE strategy_id IS NOT NULL
              AND strategy_id NOT IN (SELECT id FROM strategies)
            """
        ).fetchone()[0]
        if orphan_orders:
            issues.append(f"{orphan_orders} paper orders reference missing strategies")

        orphan_ledger = conn.execute(
            """
            SELECT COUNT(*) FROM strategy_ledger
            WHERE strategy_id NOT IN (SELECT id FROM strategies)
            """
        ).fetchone()[0]
        if orphan_ledger:
            issues.append(f"{orphan_ledger} ledger entries reference missing strategies")

    if issues:
        print("STRATEGY MANAGEMENT NEEDS ATTENTION")
        for issue in issues:
            print(f"- {issue}")
        return 1

    print("STRATEGY MANAGEMENT HEALTHY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

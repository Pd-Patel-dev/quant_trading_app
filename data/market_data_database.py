"""Database operations for Milestone 5 market data warehouse."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MarketDataDatabaseMixin:
    """Market data persistence mixed into DatabaseManager."""

    def list_cached_assets(
        self,
        asset_type: str | None = None,
        symbol_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT a.id, a.asset_type, a.symbol, c.timeframe, c.feed, c.adjustment,
                   c.coverage_start_utc, c.coverage_end_utc, c.row_count,
                   c.last_downloaded_at, c.data_quality_status, c.updated_at
            FROM assets a
            LEFT JOIN market_data_coverage c ON c.asset_id = a.id
            WHERE a.is_active = 1
        """
        params: list[Any] = []
        if asset_type:
            query += " AND a.asset_type = ?"
            params.append(asset_type)
        if symbol_filter:
            query += " AND a.symbol LIKE ?"
            params.append(f"%{symbol_filter.upper()}%")
        query += " ORDER BY a.asset_type, a.symbol"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_download_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, asset_type, symbol, timeframe, status, provider, feed,
                       rows_received, rows_inserted, rows_updated, error_message,
                       started_at, completed_at
                FROM market_data_download_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_quality_issues(self, unresolved_only: bool = True) -> list[dict[str, Any]]:
        query = """
            SELECT q.id, a.asset_type, a.symbol, q.timeframe, q.timestamp_utc,
                   q.issue_type, q.severity, q.description, q.detected_at, q.resolved_at
            FROM market_data_quality_issues q
            JOIN assets a ON a.id = q.asset_id
        """
        if unresolved_only:
            query += " WHERE q.resolved_at IS NULL"
        query += " ORDER BY q.detected_at DESC"
        with self.connect() as connection:
            rows = connection.execute(query).fetchall()
        return [dict(row) for row in rows]

    def acknowledge_quality_issue(self, issue_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE market_data_quality_issues
                SET resolved_at = ?
                WHERE id = ? AND issue_type NOT IN ('INVALID_OHLC', 'NEGATIVE_PRICE', 'MISSING_VALUE')
                """,
                (_utc_now(), issue_id),
            )

    def save_multi_asset_backtest_run(
        self,
        run_id: str,
        run_mode: str,
        strategy_type: str,
        asset_count: int,
        start_date: str,
        end_date: str,
        starting_capital: float,
        configuration: dict,
        successful_assets: int,
        failed_assets: int,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO multi_asset_backtest_runs (
                    run_id, run_mode, strategy_type, asset_count, start_date, end_date,
                    starting_capital, configuration_json, successful_assets, failed_assets,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    run_mode,
                    strategy_type,
                    asset_count,
                    start_date,
                    end_date,
                    float(starting_capital),
                    json.dumps(configuration),
                    successful_assets,
                    failed_assets,
                    _utc_now(),
                ),
            )

    def save_multi_asset_backtest_result(self, run_id: str, result: dict) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO multi_asset_backtest_results (
                    run_id, asset_type, symbol, strategy_type, parameters_json, allocation,
                    final_value, profit_loss, total_return_percent, annualized_return_percent,
                    maximum_drawdown_percent, annualized_volatility_percent, sharpe_ratio,
                    sortino_ratio, completed_trades, win_rate_percent, exposure_percent,
                    first_bar, last_bar, bar_count, data_source_status, error_message,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    result["asset_type"],
                    result["symbol"],
                    result["strategy_type"],
                    json.dumps(result.get("parameters", {})),
                    float(result.get("allocation", 0)),
                    float(result.get("final_value", 0)),
                    float(result.get("profit_loss", 0)),
                    float(result.get("total_return_percent", 0)),
                    float(result.get("annualized_return_percent", 0)),
                    float(result.get("maximum_drawdown_percent", 0)),
                    float(result.get("annualized_volatility_percent", 0)),
                    float(result.get("sharpe_ratio", 0)),
                    float(result.get("sortino_ratio", 0)),
                    int(result.get("completed_trades", 0)),
                    float(result.get("win_rate_percent", 0)),
                    float(result.get("exposure_percent", 0)),
                    result.get("first_bar", ""),
                    result.get("last_bar", ""),
                    int(result.get("bar_count", 0)),
                    result.get("data_source_status", ""),
                    result.get("error_message"),
                    _utc_now(),
                ),
            )

    def get_active_strategy_symbols(self) -> list[tuple[str, str]]:
        """Return (asset_type, symbol) pairs from active strategies."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT 'STOCK', symbol FROM strategies
                WHERE status = 'ACTIVE' AND is_active = 1
                """
            ).fetchall()
        return [(row[0], row[1].upper()) for row in rows]

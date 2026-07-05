"""Database operations for Milestone 4 research."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import numpy as np


class ResearchDatabaseMixin:
    """Research persistence methods mixed into DatabaseManager."""

    def save_strategy_definition(
        self,
        strategy_type: str,
        display_name: str,
        version: str,
        category: str,
        metadata_json: dict,
    ) -> None:
        now = _utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_definitions (
                    strategy_type, display_name, version, category,
                    metadata_json, is_available, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(strategy_type) DO UPDATE SET
                    display_name = excluded.display_name,
                    version = excluded.version,
                    category = excluded.category,
                    metadata_json = excluded.metadata_json,
                    is_available = 1,
                    updated_at = excluded.updated_at
                """,
                (strategy_type, display_name, version, category, json.dumps(metadata_json), now, now),
            )

    def create_research_run(
        self,
        run_id: str,
        run_type: str,
        symbol: str,
        start_date: str,
        end_date: str,
        starting_capital: float,
        configuration: dict,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_research_runs (
                    run_id, run_type, symbol, start_date, end_date,
                    starting_capital, configuration_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    run_type,
                    symbol.upper(),
                    start_date,
                    end_date,
                    float(starting_capital),
                    json.dumps(configuration),
                    _utc_now(),
                ),
            )

    def save_research_result(
        self,
        run_id: str,
        strategy_type: str,
        strategy_name: str,
        parameters: dict,
        metrics: dict,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_research_results (
                    run_id, strategy_type, strategy_name, parameters_json,
                    final_value, total_return_percent, annualized_return_percent,
                    maximum_drawdown_percent, annualized_volatility_percent,
                    sharpe_ratio, sortino_ratio, profit_factor,
                    completed_trades, win_rate_percent,
                    average_holding_period_days, exposure_percent,
                    result_summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    strategy_type,
                    strategy_name,
                    json.dumps(parameters),
                    _scalar(metrics.get("final_value", 0)),
                    _scalar(metrics.get("total_return_percent", 0)),
                    _scalar(metrics.get("annualized_return_percent", 0)),
                    _scalar(metrics.get("maximum_drawdown_percent", 0)),
                    _scalar(metrics.get("annualized_volatility_percent", 0)),
                    _scalar(metrics.get("sharpe_ratio", 0)),
                    _scalar(metrics.get("sortino_ratio", 0)),
                    _scalar(metrics.get("profit_factor", 0)),
                    int(metrics.get("completed_trades", 0)),
                    _scalar(metrics.get("win_rate_percent", 0)),
                    _scalar(metrics.get("average_holding_period_days", 0)),
                    _scalar(metrics.get("exposure_percent", 0)),
                    json.dumps(_sanitize_dict(metrics.get("summary", {}))),
                    _utc_now(),
                ),
            )

    def save_walk_forward_window(self, run_id: str, strategy_type: str, window: dict) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO walk_forward_windows (
                    run_id, strategy_type, window_number,
                    training_start, training_end, testing_start, testing_end,
                    training_return_percent, testing_return_percent,
                    testing_drawdown_percent, testing_sharpe_ratio,
                    testing_trade_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    strategy_type,
                    int(window["window_number"]),
                    window["training_start"],
                    window["training_end"],
                    window["testing_start"],
                    window["testing_end"],
                    _scalar(window["training_return_percent"]),
                    _scalar(window["testing_return_percent"]),
                    _scalar(window["testing_drawdown_percent"]),
                    _scalar(window["testing_sharpe_ratio"]),
                    int(window["testing_trade_count"]),
                    _utc_now(),
                ),
            )

    def has_matching_backtest(
        self,
        strategy_type: str,
        symbol: str,
        parameters_json: str,
    ) -> bool:
        from market_data.models import AssetType
        from market_data.symbol_normalizer import SymbolNormalizer

        normalizer = SymbolNormalizer()
        symbol_candidates = {symbol.strip().upper()}
        try:
            symbol_candidates.add(normalizer.normalize(AssetType.STOCK, symbol))
        except Exception:
            pass
        try:
            symbol_candidates.add(normalizer.normalize(AssetType.CRYPTO, symbol))
        except Exception:
            pass

        for candidate in symbol_candidates:
            with self.connect() as connection:
                row = connection.execute(
                    """
                    SELECT 1 FROM strategy_research_results r
                    JOIN strategy_research_runs run ON run.run_id = r.run_id
                    WHERE r.strategy_type = ? AND run.symbol = ?
                      AND r.parameters_json = ?
                    LIMIT 1
                    """,
                    (strategy_type, candidate, parameters_json),
                ).fetchone()
            if row:
                return True
            with self.connect() as connection:
                row = connection.execute(
                    """
                    SELECT 1 FROM backtest_runs
                    WHERE strategy_name LIKE ? AND symbol = ?
                    LIMIT 1
                    """,
                    (f"%{strategy_type}%", candidate),
                ).fetchone()
            if row:
                return True
        return False

    def update_strategy_paper_approval(
        self,
        strategy_id: int,
        *,
        approved: bool,
        approved_at: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE strategies
                SET paper_trading_approved = ?, paper_trading_approved_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (1 if approved else 0, approved_at, _utc_now(), strategy_id),
            )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scalar(value: Any) -> float:
    if isinstance(value, np.generic):
        return float(value.item())
    if value is None:
        return 0.0
    return float(value)


def _sanitize_dict(data: dict) -> dict:
    clean: dict = {}
    for key, value in data.items():
        if isinstance(value, (np.generic, np.ndarray)):
            clean[key] = float(value) if hasattr(value, "item") else str(value)
        elif isinstance(value, (int, float, str, bool)) or value is None:
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean

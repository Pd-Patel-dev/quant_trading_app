"""Multi-strategy comparison service."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from core.models import BacktestConfiguration
from research.backtest_utils import backtest_result_to_comparison, run_backtest
from research.models import StrategyComparisonResult
from strategies.registry import StrategyRegistry, get_registry


RANK_METRICS = {
    "Total Return": ("total_return_percent", True),
    "Maximum Drawdown": ("maximum_drawdown_percent", False),
    "Sharpe Ratio": ("sharpe_ratio", True),
    "Sortino Ratio": ("sortino_ratio", True),
    "Win Rate": ("win_rate_percent", True),
    "Profit Factor": ("profit_factor", True),
}


class StrategyComparisonService:
    """Run multiple strategies on identical data and assumptions."""

    RANK_METRICS = RANK_METRICS

    def __init__(self, registry: StrategyRegistry | None = None) -> None:
        self._registry = registry or get_registry()

    def compare(
        self,
        strategy_specs: list[tuple[str, dict[str, Any]]],
        data: pd.DataFrame,
        configuration: BacktestConfiguration,
        start_date: date,
        end_date: date,
    ) -> list[StrategyComparisonResult]:
        results: list[StrategyComparisonResult] = []
        for strategy_type, parameters in strategy_specs:
            strategy = self._registry.build(strategy_type, parameters)
            backtest = run_backtest(strategy, configuration, data)
            results.append(
                backtest_result_to_comparison(
                    backtest,
                    strategy_type,
                    configuration.allocation,
                    start_date,
                    end_date,
                )
            )
        return results

    def rank(
        self,
        results: list[StrategyComparisonResult],
        metric_name: str,
    ) -> list[StrategyComparisonResult]:
        if metric_name not in RANK_METRICS:
            raise ValueError(f"Unknown ranking metric: {metric_name}")
        field_name, higher_is_better = RANK_METRICS[metric_name]
        return sorted(
            results,
            key=lambda r: getattr(r, field_name),
            reverse=higher_is_better,
        )

    def comparison_warnings(self, results: list[StrategyComparisonResult]) -> list[str]:
        warnings: list[str] = []
        if len(results) < 2:
            return warnings
        trades = [r.completed_trades for r in results]
        exposures = [r.exposure_percent for r in results]
        if max(trades) - min(trades) > 5:
            warnings.append("Compared strategies have very different trade counts.")
        if max(exposures) - min(exposures) > 25:
            warnings.append("Compared strategies have different market exposure levels.")
        warnings.append("Ranking by one metric does not prove overall superiority.")
        return warnings

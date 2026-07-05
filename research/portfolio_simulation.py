"""Research-only multi-strategy portfolio simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from backtesting import metrics
from backtesting.engine import BacktestEngine
from core.models import BacktestConfiguration
from strategies.registry import StrategyRegistry, get_registry


@dataclass
class PortfolioSimulationResult:
    combined_equity: pd.Series
    strategy_equities: dict[str, pd.Series]
    unallocated_cash_series: pd.Series
    starting_capital: float
    final_value: float
    total_return_percent: float
    maximum_drawdown_percent: float
    sharpe_ratio: float
    strategy_contributions: dict[str, float] = field(default_factory=dict)


class PortfolioSimulator:
    """Simulate multiple strategies with separate virtual allocations."""

    def __init__(self, registry: StrategyRegistry | None = None) -> None:
        self._registry = registry or get_registry()

    def simulate(
        self,
        allocations: dict[str, float],
        unallocated_cash: float,
        strategy_specs: list[tuple[str, dict[str, Any]]],
        base_configuration: BacktestConfiguration,
        data: pd.DataFrame,
    ) -> PortfolioSimulationResult:
        total_allocated = sum(allocations.values()) + unallocated_cash
        if total_allocated > base_configuration.starting_capital + 0.01:
            raise ValueError("Allocations and unallocated cash exceed starting capital.")

        strategy_equities: dict[str, pd.Series] = {}
        contributions: dict[str, float] = {}

        for strategy_type, parameters in strategy_specs:
            alloc = allocations.get(strategy_type, 0.0)
            if alloc <= 0:
                continue
            strategy = self._registry.build(strategy_type, parameters)
            config = BacktestConfiguration(
                symbol=base_configuration.symbol,
                start_date=base_configuration.start_date,
                end_date=base_configuration.end_date,
                starting_capital=base_configuration.starting_capital,
                allocation=alloc,
                commission=base_configuration.commission,
                slippage_percent=base_configuration.slippage_percent,
                cash_reserve_percent=base_configuration.cash_reserve_percent,
            )
            result = BacktestEngine(strategy, config, data).run()
            equity = result.equity_curve["PortfolioValue"] - (config.starting_capital - config.allocation)
            strategy_equities[strategy_type] = equity
            contributions[strategy_type] = float(equity.iloc[-1] - alloc)

        if not strategy_equities:
            raise ValueError("At least one strategy allocation is required.")

        combined = pd.DataFrame(strategy_equities).sum(axis=1) + unallocated_cash
        unallocated_series = pd.Series(unallocated_cash, index=combined.index)
        daily_returns = combined.pct_change().fillna(0.0)

        return PortfolioSimulationResult(
            combined_equity=combined,
            strategy_equities=strategy_equities,
            unallocated_cash_series=unallocated_series,
            starting_capital=base_configuration.starting_capital,
            final_value=float(combined.iloc[-1]),
            total_return_percent=metrics.total_return_percent(
                base_configuration.starting_capital, float(combined.iloc[-1])
            ),
            maximum_drawdown_percent=metrics.maximum_drawdown_percent(combined),
            sharpe_ratio=metrics.sharpe_ratio(daily_returns),
            strategy_contributions=contributions,
        )

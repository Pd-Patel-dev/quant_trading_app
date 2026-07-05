"""Shared-capital multi-asset research portfolio simulation."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting import metrics
from market_data.models import AssetBacktestResult


@dataclass
class CombinedPortfolioResult:
    combined_portfolio_curve: pd.DataFrame | None
    portfolio_metrics: dict | None
    alignment_warnings: list[str]


class MultiAssetPortfolioService:
    """Align equity curves across stock and crypto calendars."""

    def combine(
        self,
        results: list[AssetBacktestResult],
        total_capital: float,
        allocation_map: dict[str, float],
    ) -> CombinedPortfolioResult:
        if not results:
            return CombinedPortfolioResult(None, None, [])

        warnings: list[str] = []
        invested = sum(allocation_map.values())
        unallocated = max(total_capital - invested, 0.0)

        curves: dict[str, pd.Series] = {}
        for item in results:
            if item.equity_curve is not None:
                curves[item.symbol] = item.equity_curve.copy()

        if not curves:
            return CombinedPortfolioResult(None, None, warnings)

        union_index = sorted(set().union(*[set(s.index) for s in curves.values()]))
        union_index = pd.DatetimeIndex(union_index)
        aligned: dict[str, pd.Series] = {}
        for symbol, series in curves.items():
            reindexed = series.reindex(union_index)
            first_valid = reindexed.first_valid_index()
            if first_valid is not None:
                reindexed = reindexed.ffill()
                reindexed.loc[:first_valid] = pd.NA
            aligned[symbol] = reindexed

        has_stock = any(r.asset_type.value == "STOCK" for r in results)
        has_crypto = any(r.asset_type.value == "CRYPTO" for r in results)
        if has_stock and has_crypto:
            warnings.append(
                "Stock positions are marked using their latest available close on "
                "non-stock trading days when combined with crypto."
            )

        component_sum = pd.Series(0.0, index=union_index)
        for symbol, series in aligned.items():
            component_sum = component_sum.add(series.fillna(0.0), fill_value=0.0)

        portfolio_value = component_sum + unallocated
        portfolio_value.name = "PortfolioValue"
        drawdown = metrics.compute_drawdown_series(portfolio_value)
        daily_returns = portfolio_value.pct_change().fillna(0.0)
        combined = pd.DataFrame(
            {
                "PortfolioValue": portfolio_value,
                "DailyReturn": daily_returns,
                "Drawdown": drawdown,
                "UnallocatedCash": unallocated,
            }
        )

        portfolio_metrics = {
            "total_starting_capital": total_capital,
            "invested_allocation": invested,
            "unallocated_cash": unallocated,
            "final_portfolio_value": float(portfolio_value.iloc[-1]),
            "portfolio_return_percent": metrics.total_return_percent(
                total_capital, float(portfolio_value.iloc[-1])
            ),
            "maximum_drawdown_percent": metrics.maximum_drawdown_percent(portfolio_value),
            "sharpe_ratio": metrics.sharpe_ratio(daily_returns),
        }
        return CombinedPortfolioResult(combined, portfolio_metrics, warnings)

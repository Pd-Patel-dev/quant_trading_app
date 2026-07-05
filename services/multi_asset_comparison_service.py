"""Cross-asset strategy comparison helpers."""

from __future__ import annotations

from market_data.models import AssetBacktestResult, MultiAssetBacktestResult

RANK_METRICS = {
    "Total Return": lambda r: r.total_return_percent,
    "Annualized Return": lambda r: r.annualized_return_percent,
    "Maximum Drawdown": lambda r: r.maximum_drawdown_percent,
    "Sharpe Ratio": lambda r: r.sharpe_ratio,
    "Sortino Ratio": lambda r: r.sortino_ratio,
    "Win Rate": lambda r: r.win_rate_percent,
}


class MultiAssetComparisonService:
    """Sort and rank multi-asset backtest results."""

    RANK_METRICS = RANK_METRICS

    def rank_results(
        self,
        result: MultiAssetBacktestResult,
        metric: str,
        *,
        ascending: bool = False,
    ) -> list[AssetBacktestResult]:
        if metric not in self.RANK_METRICS:
            raise ValueError(f"Unknown ranking metric: {metric}")
        key = self.RANK_METRICS[metric]
        if metric == "Maximum Drawdown":
            ascending = True
        return sorted(result.results, key=key, reverse=not ascending)

    def to_table_rows(self, result: MultiAssetBacktestResult) -> list[dict]:
        rows = []
        for item in result.results:
            rows.append(
                {
                    "Asset Type": item.asset_type.value,
                    "Symbol": item.symbol,
                    "Strategy": item.strategy_name,
                    "Final Value": float(item.final_value),
                    "Profit/Loss": float(item.profit_loss),
                    "Total Return %": item.total_return_percent,
                    "Annualized Return %": item.annualized_return_percent,
                    "Max Drawdown %": item.maximum_drawdown_percent,
                    "Sharpe": item.sharpe_ratio,
                    "Sortino": item.sortino_ratio,
                    "Trades": item.completed_trades,
                    "Win Rate %": item.win_rate_percent,
                    "Exposure %": item.exposure_percent,
                    "Bar Count": item.bar_count,
                    "Cache Source": item.data_source_status,
                }
            )
        return rows

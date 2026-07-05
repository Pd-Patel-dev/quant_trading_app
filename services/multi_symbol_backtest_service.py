"""Multi-symbol backtesting service."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd

from backtesting import metrics
from backtesting.engine import BacktestEngine
from core.models import BacktestConfiguration
from market_data.batch_service import BatchHistoricalDataService
from market_data.models import (
    AssetBacktestFailure,
    AssetBacktestResult,
    AssetRequest,
    AssetType,
    BacktestRunMode,
    DataTimeframe,
    MultiAssetBacktestResult,
    QuantityMode,
)
from research.backtest_utils import calendar_days_between
from strategies.registry import StrategyRegistry, get_registry


class MultiSymbolBacktestService:
    """Run the same strategy independently across multiple assets."""

    def __init__(
        self,
        batch_service: BatchHistoricalDataService,
        registry: StrategyRegistry | None = None,
    ) -> None:
        self._batch = batch_service
        self._registry = registry or get_registry()

    def run_independent_comparison(
        self,
        assets: list[tuple[AssetType, str]],
        start: datetime,
        end: datetime,
        strategy_type: str,
        parameters: dict[str, Any],
        starting_capital_per_asset: float,
        commission: float,
        slippage_percent: float,
        cash_reserve_percent: float,
        quantity_modes: dict[AssetType, QuantityMode] | None = None,
    ) -> MultiAssetBacktestResult:
        quantity_modes = quantity_modes or {
            AssetType.STOCK: QuantityMode.WHOLE_UNITS,
            AssetType.CRYPTO: QuantityMode.FRACTIONAL_RESEARCH,
        }
        requests = [
            AssetRequest(asset_type=asset_type, symbol=symbol, start=start, end=end)
            for asset_type, symbol in assets
        ]
        sync = self._batch.get_or_download_many(requests)
        results: list[AssetBacktestResult] = []
        failures: list[AssetBacktestFailure] = []
        curves: dict[str, pd.Series] = {}

        strategy = self._registry.build(strategy_type, parameters)
        start_date = start.date() if isinstance(start, datetime) else start
        end_date = end.date() if isinstance(end, datetime) else end

        sync_by_symbol = {r.symbol: r for r in sync.results}
        for asset_type, symbol in assets:
            sync_result = sync_by_symbol.get(symbol)
            if sync_result is None or sync_result.data.empty:
                failures.append(
                    AssetBacktestFailure(
                        asset_type=asset_type,
                        symbol=symbol,
                        error=f"No data for {symbol}",
                    )
                )
                continue
            try:
                data = _strip_timezone_for_engine(sync_result.data)
                config = BacktestConfiguration(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    starting_capital=starting_capital_per_asset,
                    allocation=starting_capital_per_asset,
                    commission=commission,
                    slippage_percent=slippage_percent,
                    cash_reserve_percent=cash_reserve_percent,
                    quantity_mode=quantity_modes.get(
                        asset_type, QuantityMode.WHOLE_UNITS
                    ),
                )
                result = BacktestEngine(strategy, config, data).run()
                asset_result = _to_asset_result(
                    asset_type,
                    symbol,
                    strategy_type,
                    strategy.name,
                    Decimal(str(starting_capital_per_asset)),
                    Decimal(str(starting_capital_per_asset)),
                    result,
                    start_date,
                    end_date,
                    sync_result.data_source_status,
                )
                asset_result.equity_curve = result.equity_curve["PortfolioValue"]
                results.append(asset_result)
                curves[symbol] = metrics.normalized_equity_curve(
                    result.equity_curve["PortfolioValue"]
                )
            except Exception as exc:
                failures.append(
                    AssetBacktestFailure(asset_type=asset_type, symbol=symbol, error=str(exc))
                )

        normalized = pd.DataFrame(curves) if curves else None
        return MultiAssetBacktestResult(
            results=results,
            failures=failures,
            normalized_equity_curves=normalized,
        )

    def run_shared_portfolio(
        self,
        allocations: list[tuple[AssetType, str, float]],
        start: datetime,
        end: datetime,
        strategy_type: str,
        parameters: dict[str, Any],
        total_capital: float,
        commission: float,
        slippage_percent: float,
        cash_reserve_percent: float,
    ) -> MultiAssetBacktestResult:
        from services.multi_asset_portfolio_service import MultiAssetPortfolioService

        quantity_modes = {
            AssetType.STOCK: QuantityMode.WHOLE_UNITS,
            AssetType.CRYPTO: QuantityMode.FRACTIONAL_RESEARCH,
        }
        requests = [
            AssetRequest(asset_type=asset_type, symbol=symbol, start=start, end=end)
            for asset_type, symbol, _ in allocations
        ]
        sync = self._batch.get_or_download_many(requests)
        results: list[AssetBacktestResult] = []
        failures: list[AssetBacktestFailure] = []
        curves: dict[str, pd.Series] = {}
        strategy = self._registry.build(strategy_type, parameters)
        start_date = start.date() if isinstance(start, datetime) else start
        end_date = end.date() if isinstance(end, datetime) else end
        sync_by_symbol = {r.symbol: r for r in sync.results}

        for asset_type, symbol, allocation in allocations:
            sync_result = sync_by_symbol.get(symbol)
            if sync_result is None or sync_result.data.empty:
                failures.append(
                    AssetBacktestFailure(asset_type, symbol, f"No data for {symbol}")
                )
                continue
            try:
                data = _strip_timezone_for_engine(sync_result.data)
                config = BacktestConfiguration(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    starting_capital=allocation,
                    allocation=allocation,
                    commission=commission,
                    slippage_percent=slippage_percent,
                    cash_reserve_percent=cash_reserve_percent,
                    quantity_mode=quantity_modes.get(
                        asset_type, QuantityMode.WHOLE_UNITS
                    ),
                )
                bt = BacktestEngine(strategy, config, data).run()
                asset_result = _to_asset_result(
                    asset_type,
                    symbol,
                    strategy_type,
                    strategy.name,
                    Decimal(str(allocation)),
                    Decimal(str(allocation)),
                    bt,
                    start_date,
                    end_date,
                    sync_result.data_source_status,
                )
                asset_result.equity_curve = bt.equity_curve["PortfolioValue"]
                results.append(asset_result)
                curves[symbol] = bt.equity_curve["PortfolioValue"]
            except Exception as exc:
                failures.append(AssetBacktestFailure(asset_type, symbol, str(exc)))

        portfolio_service = MultiAssetPortfolioService()
        allocation_map = {symbol: amount for _, symbol, amount in allocations}
        combined = portfolio_service.combine(results, total_capital, allocation_map)
        normalized = pd.DataFrame(
            {symbol: metrics.normalized_equity_curve(series) for symbol, series in curves.items()}
        ) if curves else None
        return MultiAssetBacktestResult(
            results=results,
            failures=failures,
            normalized_equity_curves=normalized,
            combined_portfolio_curve=combined.combined_portfolio_curve,
            portfolio_metrics=combined.portfolio_metrics,
            alignment_warnings=combined.alignment_warnings,
        )


def _strip_timezone_for_engine(data: pd.DataFrame) -> pd.DataFrame:
    frame = data.copy()
    if isinstance(frame.index, pd.DatetimeIndex) and frame.index.tz is not None:
        frame.index = frame.index.tz_convert("UTC").tz_localize(None)
    return frame


def _to_asset_result(
    asset_type: AssetType,
    symbol: str,
    strategy_type: str,
    strategy_name: str,
    starting_capital: Decimal,
    allocation: Decimal,
    result,
    start_date: date,
    end_date: date,
    data_source_status: str,
) -> AssetBacktestResult:
    days = calendar_days_between(start_date, end_date)
    daily_returns = result.equity_curve["DailyReturn"]
    position_qty = result.equity_curve.get("PositionQuantity", pd.Series(0))
    first_bar = pd.Timestamp(result.equity_curve.index[0]).to_pydatetime()
    last_bar = pd.Timestamp(result.equity_curve.index[-1]).to_pydatetime()
    if first_bar.tzinfo is None:
        first_bar = first_bar.replace(tzinfo=timezone.utc)
    if last_bar.tzinfo is None:
        last_bar = last_bar.replace(tzinfo=timezone.utc)
    final_value = Decimal(str(result.final_value))
    return AssetBacktestResult(
        asset_type=asset_type,
        symbol=symbol,
        strategy_type=strategy_type,
        strategy_name=strategy_name,
        starting_capital=starting_capital,
        allocation=allocation,
        final_value=final_value,
        profit_loss=final_value - starting_capital,
        total_return_percent=result.total_return_percent,
        annualized_return_percent=metrics.annualized_return_percent(
            result.starting_capital, result.final_value, days
        ),
        maximum_drawdown_percent=result.maximum_drawdown_percent,
        annualized_volatility_percent=result.annualized_volatility_percent,
        sharpe_ratio=result.sharpe_ratio,
        sortino_ratio=metrics.sortino_ratio(daily_returns),
        completed_trades=result.completed_trades,
        win_rate_percent=result.win_rate_percent,
        exposure_percent=metrics.exposure_percent(position_qty),
        first_bar=first_bar,
        last_bar=last_bar,
        bar_count=len(result.equity_curve),
        data_source_status=data_source_status,
    )

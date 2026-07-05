"""Shared portfolio simulation tests."""

from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from market_data.models import AssetBacktestResult, AssetType
from services.multi_asset_portfolio_service import MultiAssetPortfolioService


def _result(symbol: str, asset_type: AssetType, values: list[float]) -> AssetBacktestResult:
    index = pd.date_range("2024-01-01", periods=len(values), freq="D", tz="UTC")
    series = pd.Series(values, index=index)
    return AssetBacktestResult(
        asset_type=asset_type,
        symbol=symbol,
        strategy_type="moving_average_crossover",
        strategy_name="MA",
        starting_capital=Decimal("10000"),
        allocation=Decimal("10000"),
        final_value=Decimal(str(values[-1])),
        profit_loss=Decimal(str(values[-1] - values[0])),
        total_return_percent=0.0,
        annualized_return_percent=0.0,
        maximum_drawdown_percent=0.0,
        annualized_volatility_percent=0.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        completed_trades=0,
        win_rate_percent=0.0,
        exposure_percent=0.0,
        first_bar=index[0].to_pydatetime(),
        last_bar=index[-1].to_pydatetime(),
        bar_count=len(values),
        data_source_status="Local cache",
        equity_curve=series,
    )


def test_unallocated_cash_preserved() -> None:
    service = MultiAssetPortfolioService()
    results = [_result("AAPL", AssetType.STOCK, [10000, 10100, 10200])]
    combined = service.combine(results, total_capital=30000, allocation_map={"AAPL": 10000})
    assert combined.portfolio_metrics["unallocated_cash"] == 20000


def test_combined_value_includes_cash() -> None:
    service = MultiAssetPortfolioService()
    results = [_result("AAPL", AssetType.STOCK, [10000, 10500])]
    combined = service.combine(results, total_capital=20000, allocation_map={"AAPL": 10000})
    final = combined.portfolio_metrics["final_portfolio_value"]
    assert final == pytest.approx(20500.0)


def test_stock_crypto_alignment_warning() -> None:
    service = MultiAssetPortfolioService()
    stock = _result("AAPL", AssetType.STOCK, [10000, 10100, 10200])
    crypto = _result("BTC/USD", AssetType.CRYPTO, [10000, 10050, 10100, 10150])
    combined = service.combine(
        [stock, crypto],
        total_capital=30000,
        allocation_map={"AAPL": 10000, "BTC/USD": 10000},
    )
    assert combined.alignment_warnings

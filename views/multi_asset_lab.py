"""Multi-asset research lab page."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from config.settings import get_settings
from data.database import DatabaseManager
from market_data.factory import build_market_data_stack
from market_data.models import AssetType
from market_data.symbol_normalizer import SymbolNormalizer
from services.multi_asset_comparison_service import MultiAssetComparisonService
from services.multi_symbol_backtest_service import MultiSymbolBacktestService
from strategies.registry import get_registry
from ui import components


def render(database: DatabaseManager) -> None:
    settings = get_settings()
    st.title("Multi-Asset Lab")
    st.warning(
        "RESEARCH SIMULATION — NOT THE ALPACA PAPER ACCOUNT. "
        "Crypto backtesting is research-only and does not enable order submission."
    )

    tabs = st.tabs(["Batch Backtest", "Compare Assets", "Shared Portfolio", "Cache Inspector"])
    registry = get_registry()

    with tabs[0]:
        _render_batch_backtest(database, settings, registry)
    with tabs[1]:
        _render_compare_tab()
    with tabs[2]:
        _render_shared_portfolio(database, settings, registry)
    with tabs[3]:
        _render_cache_inspector(database, settings)


def _render_batch_backtest(database, settings, registry) -> None:
    strategy_types = registry.list_strategy_types()
    with st.form("multi_backtest"):
        mode = st.selectbox("Asset mix", ["Stocks", "Crypto", "Mixed"])
        stock_symbols = st.text_input("Stock symbols", value="AAPL, MSFT")
        crypto_symbols = st.text_input("Crypto symbols", value="BTC/USD")
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Start", value=date.today() - timedelta(days=365 * 2), key="ma_start")
        with col2:
            end_date = st.date_input("End", value=date.today(), key="ma_end")
        strategy_type = st.selectbox("Strategy", strategy_types)
        starting_capital = st.number_input("Starting capital per asset", value=10000.0, min_value=1.0)
        commission = st.number_input("Commission", value=settings.default_commission, min_value=0.0)
        slippage = st.number_input("Slippage %", value=settings.default_slippage_percent * 100) / 100
        cash_reserve = st.number_input("Cash reserve %", value=settings.default_cash_reserve_percent * 100) / 100
        submitted = st.form_submit_button("Run Multi-Asset Backtest", type="primary")

    if not submitted:
        return

    assets = _parse_assets(mode, stock_symbols, crypto_symbols)
    if not assets:
        st.error("No valid symbols.")
        return

    _, _, batch = build_market_data_stack(database, settings)
    service = MultiSymbolBacktestService(batch, registry)
    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc)
    params = registry.build(strategy_type).metadata.default_parameters
    result = service.run_independent_comparison(
        assets, start_dt, end_dt, strategy_type, params,
        starting_capital, commission, slippage, cash_reserve,
    )
    st.session_state["multi_asset_result"] = result
    st.subheader("Synchronization & Results")
    if result.failures:
        for failure in result.failures:
            st.error(f"{failure.symbol}: {failure.error}")
    if result.results:
        st.dataframe(pd.DataFrame(MultiAssetComparisonService().to_table_rows(result)), use_container_width=True)


def _render_compare_tab() -> None:
    result = st.session_state.get("multi_asset_result")
    if not result or not result.results:
        st.info("Run a batch backtest first.")
        return
    metric = st.selectbox("Rank by", list(MultiAssetComparisonService.RANK_METRICS.keys()))
    ranked = MultiAssetComparisonService().rank_results(result, metric)
    st.dataframe(pd.DataFrame(MultiAssetComparisonService().to_table_rows(
        type("R", (), {"results": ranked, "failures": []})()
    )), use_container_width=True)
    if result.normalized_equity_curves is not None:
        st.line_chart(result.normalized_equity_curves)
    st.caption("Historical returns are not guaranteed future performance.")


def _render_shared_portfolio(database, settings, registry) -> None:
    st.markdown("**RESEARCH SIMULATION — NOT THE ALPACA PAPER ACCOUNT**")
    with st.form("shared_portfolio"):
        stock_symbols = st.text_input("Stock symbols", value="AAPL", key="sp_stocks")
        crypto_symbols = st.text_input("Crypto symbols", value="BTC/USD", key="sp_crypto")
        total_capital = st.number_input("Total starting capital", value=30000.0, min_value=1.0)
        alloc_aapl = st.number_input("AAPL allocation", value=10000.0, min_value=0.0, key="alloc_aapl")
        strategy_type = st.selectbox("Strategy", registry.list_strategy_types(), key="sp_strategy")
        submitted = st.form_submit_button("Run Shared Portfolio Simulation")

    if not submitted:
        return

    normalizer = SymbolNormalizer()
    allocations: list[tuple[AssetType, str, float]] = []
    for symbol in normalizer.parse_input(AssetType.STOCK, stock_symbols).normalized:
        allocations.append((AssetType.STOCK, symbol, alloc_aapl if symbol == "AAPL" else 0.0))
    for symbol in normalizer.parse_input(AssetType.CRYPTO, crypto_symbols).normalized:
        allocations.append((AssetType.CRYPTO, symbol, 10000.0))
    invested = sum(a for _, _, a in allocations)
    if invested > total_capital:
        st.error("Total allocations exceed starting capital.")
        return

    _, _, batch = build_market_data_stack(database, settings)
    service = MultiSymbolBacktestService(batch, registry)
    start_dt = datetime.combine(date.today() - timedelta(days=365), datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(date.today(), datetime.max.time(), tzinfo=timezone.utc)
    params = registry.build(strategy_type).metadata.default_parameters
    result = service.run_shared_portfolio(
        allocations, start_dt, end_dt, strategy_type, params,
        total_capital, 0.0, settings.default_slippage_percent, settings.default_cash_reserve_percent,
    )
    if result.portfolio_metrics:
        metrics = result.portfolio_metrics
        cols = st.columns(4)
        cols[0].metric("Invested", components.format_currency(metrics["invested_allocation"]))
        cols[1].metric("Unallocated Cash", components.format_currency(metrics["unallocated_cash"]))
        cols[2].metric("Final Value", components.format_currency(metrics["final_portfolio_value"]))
        cols[3].metric("Return", components.format_percent(metrics["portfolio_return_percent"]))
    for warning in result.alignment_warnings:
        st.warning(warning)
    if result.combined_portfolio_curve is not None:
        st.line_chart(result.combined_portfolio_curve["PortfolioValue"])


def _render_cache_inspector(database, settings) -> None:
    rows = database.list_cached_assets()
    if not rows:
        st.info("No cached assets.")
        return
    options = [f"{r['asset_type']} {r['symbol']}" for r in rows]
    selected = st.selectbox("Cached asset", options)
    row = rows[options.index(selected)]
    st.write(row)
    if st.button("Refresh missing data"):
        _, cache, _ = build_market_data_stack(database, settings)
        asset_type = AssetType(row["asset_type"])
        start = datetime.fromisoformat(row["coverage_start_utc"]) if row.get("coverage_start_utc") else datetime.now(timezone.utc) - timedelta(days=365)
        end = datetime.now(timezone.utc)
        result = cache.get_or_download(asset_type, row["symbol"], DataTimeframe.DAY, start, end)
        st.success(f"Synchronized: {result.data_source_status}, {result.final_rows} rows")


def _parse_assets(mode: str, stocks: str, cryptos: str) -> list[tuple[AssetType, str]]:
    normalizer = SymbolNormalizer()
    assets: list[tuple[AssetType, str]] = []
    if mode in ("Stocks", "Mixed"):
        for symbol in normalizer.parse_input(AssetType.STOCK, stocks).normalized:
            assets.append((AssetType.STOCK, symbol))
    if mode in ("Crypto", "Mixed"):
        for symbol in normalizer.parse_input(AssetType.CRYPTO, cryptos).normalized:
            assets.append((AssetType.CRYPTO, symbol))
    return assets


from market_data.models import DataTimeframe  # noqa: E402

"""Strategy Lab research page."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from config.settings import get_settings
from core.exceptions import ConfigurationError, MarketDataError, QuantTradingError
from core.models import BacktestConfiguration
from data.database import DatabaseManager
from market_data.factory import build_market_data_stack
from market_data.models import AssetType, DataTimeframe, QuantityMode
from market_data.symbol_normalizer import SymbolNormalizer
from research.backtest_utils import backtest_result_to_comparison, run_backtest
from research.comparison_service import StrategyComparisonService
from research.portfolio_simulation import PortfolioSimulator
from research.train_test import TrainTestEvaluator
from research.walk_forward import WalkForwardEvaluator
from strategies.registry import get_registry
from ui import charts, components
from ui.crypto_ema_chart import price_and_emas_chart
from ui.strategy_forms import render_parameter_inputs


def render(database: DatabaseManager) -> None:
    settings = get_settings()
    registry = get_registry()

    st.title("Strategy Lab")
    st.caption("Research Simulation — results here do not affect your Paper Brokerage Account.")
    st.info(
        "Historical backtests and paper trading are simulations. They do not guarantee future "
        "profits or reproduce every live-market condition."
    )

    if not settings.alpaca_configured:
        st.warning("Configure Alpaca credentials in `.env` to download market data.")
        return

    tabs = st.tabs(
        ["Single Strategy", "Compare Strategies", "Train/Test", "Walk-Forward", "Portfolio Simulation"]
    )

    default_start = date.today() - timedelta(days=365 * 5)
    default_end = date.today()

    with tabs[0]:
        _render_single(database, registry, settings, default_start, default_end)
    with tabs[1]:
        _render_compare(database, registry, settings, default_start, default_end)
    with tabs[2]:
        _render_train_test(database, registry, settings, default_start, default_end)
    with tabs[3]:
        _render_walk_forward(database, registry, settings, default_start, default_end)
    with tabs[4]:
        _render_portfolio(database, registry, settings, default_start, default_end)


def _common_config_form(prefix: str, settings, default_start, default_end):
    asset_type_label = st.selectbox(
        "Asset Type",
        ["STOCK", "CRYPTO"],
        key=f"{prefix}_asset_type",
        help="Use CRYPTO for pairs such as BTC/USD. Crypto backtests use fractional research sizing.",
    )
    default_symbol = "BTC/USD" if asset_type_label == "CRYPTO" else "SPY"
    symbol = st.text_input("Symbol", value=default_symbol, key=f"{prefix}_symbol").strip()
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("Start Date", value=default_start, key=f"{prefix}_start")
    with c2:
        end_date = st.date_input("End Date", value=default_end, key=f"{prefix}_end")
    cap1, cap2 = st.columns(2)
    with cap1:
        starting_capital = st.number_input("Starting Capital ($)", value=settings.default_starting_capital, key=f"{prefix}_cap")
    with cap2:
        allocation = st.number_input("Strategy Allocation ($)", value=settings.default_starting_capital, key=f"{prefix}_alloc")
    cost1, cost2, cost3 = st.columns(3)
    with cost1:
        commission = st.number_input("Commission ($)", value=settings.default_commission, key=f"{prefix}_comm")
    with cost2:
        slippage = st.number_input("Slippage (%)", value=settings.default_slippage_percent * 100, key=f"{prefix}_slip")
    with cost3:
        reserve = st.number_input("Cash Reserve (%)", value=settings.default_cash_reserve_percent * 100, key=f"{prefix}_res")
    crypto_fee = 0.0
    if asset_type_label == "CRYPTO":
        crypto_fee = st.number_input(
            "Crypto Fee (%)",
            value=float(getattr(settings, "crypto_estimated_fee_percent", 0.0025)) * 100.0,
            min_value=0.0,
            key=f"{prefix}_cfee",
            help="Simulated percentage fee for crypto backtests.",
        )
    return asset_type_label, symbol, start_date, end_date, starting_capital, allocation, commission, slippage, reserve, crypto_fee


def _resolve_asset(symbol_input: str, asset_type_label: str) -> tuple[AssetType, str]:
    asset_type = AssetType.CRYPTO if asset_type_label == "CRYPTO" else AssetType.STOCK
    normalizer = SymbolNormalizer()
    return asset_type, normalizer.normalize(asset_type, symbol_input)


def _load_research_data(database, settings, asset_type_label: str, symbol: str, start_date, end_date):
    from datetime import datetime, timezone

    try:
        asset_type, canonical = _resolve_asset(symbol, asset_type_label)
    except ConfigurationError as exc:
        st.error(str(exc))
        return None, None

    _, cache, _ = build_market_data_stack(database, settings)
    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc)
    try:
        sync = cache.get_or_download(asset_type, canonical, DataTimeframe.DAY, start_dt, end_dt)
    except MarketDataError as exc:
        st.error(str(exc))
        return None, None
    except Exception as exc:
        st.error(f"Unable to load market data: {exc}")
        return None, None

    if sync.data is None or sync.data.empty:
        st.error(f"No historical data available for {canonical}.")
        return None, None

    data = sync.data.copy()
    if hasattr(data.index, "tz") and data.index.tz is not None:
        data.index = data.index.tz_convert("UTC").tz_localize(None)
    return data, canonical


def _backtest_config(
    symbol,
    start_date,
    end_date,
    starting_capital,
    allocation,
    commission,
    slippage,
    reserve,
    *,
    asset_type_label: str = "STOCK",
    crypto_fee_percent: float = 0.0,
):
    quantity_mode = (
        QuantityMode.FRACTIONAL_RESEARCH
        if asset_type_label == "CRYPTO"
        else QuantityMode.WHOLE_UNITS
    )
    return BacktestConfiguration(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        starting_capital=float(starting_capital),
        allocation=float(allocation),
        commission=float(commission),
        slippage_percent=float(slippage) / 100.0,
        cash_reserve_percent=float(reserve) / 100.0,
        quantity_mode=quantity_mode,
        crypto_fee_percent=float(crypto_fee_percent) / 100.0,
        max_order_notional=float(allocation),
    )


def _render_single(database, registry, settings, default_start, default_end):
    st.subheader("Single Strategy Backtest")
    strategy_type = st.selectbox(
        "Strategy",
        registry.list_strategy_types(),
        format_func=lambda t: registry.get_metadata(t).display_name,
        key="single_type",
    )
    meta = registry.get_metadata(strategy_type)
    st.write(meta.description)
    st.caption(f"Category: {meta.category.value} | Min history: {meta.minimum_history_bars} bars")
    st.warning(meta.risk_notes)
    if strategy_type == "crypto_ema_trend_following":
        st.info(
            "The strategy buys after the fast EMA crosses above the medium EMA while "
            "price is above the long EMA. It exits after a bearish EMA crossover or "
            "when a completed daily Close falls below the configured entry-price stop."
        )
        st.warning(
            "This strategy is designed for trending markets. It may perform poorly during "
            "sideways markets. The stop-loss is evaluated once per completed daily candle."
        )
    params = render_parameter_inputs(meta.parameter_definitions, "single")
    asset_type_label, symbol, start_date, end_date, starting_capital, allocation, commission, slippage, reserve, crypto_fee = _common_config_form(
        "single", settings, default_start, default_end
    )
    if st.button("Run Backtest", key="single_run", type="primary"):
        loaded = _load_research_data(database, settings, asset_type_label, symbol, start_date, end_date)
        if loaded[0] is None:
            return
        data, canonical = loaded
        try:
            strategy = registry.build(strategy_type, params)
            config = _backtest_config(
                canonical, start_date, end_date, starting_capital, allocation, commission, slippage, reserve,
                asset_type_label=asset_type_label,
                crypto_fee_percent=crypto_fee,
            )
            result = run_backtest(strategy, config, data)
            comparison = backtest_result_to_comparison(result, strategy_type, config.allocation, start_date, end_date)
            run_id = str(uuid.uuid4())
            database.create_research_run(
                run_id, "SINGLE_BACKTEST", canonical, str(start_date), str(end_date), starting_capital,
                {"strategy_type": strategy_type, "parameters": params, "asset_type": asset_type_label},
            )
            database.save_research_result(
                run_id, strategy_type, result.strategy_name, params,
                comparison.__dict__,
            )
            st.session_state["lab_single_result"] = result
            st.session_state["lab_single_comparison"] = comparison
            st.session_state["lab_single_type"] = strategy_type
            st.session_state["lab_single_params"] = params
        except QuantTradingError as exc:
            st.error(str(exc))

    result = st.session_state.get("lab_single_result")
    comparison = st.session_state.get("lab_single_comparison")
    if result and comparison:
        _display_metrics(comparison)
        if st.session_state.get("lab_single_type") == "rsi_mean_reversion":
            _rsi_chart(result.processed_data, st.session_state.get("lab_single_params", {}))
        elif st.session_state.get("lab_single_type") == "crypto_ema_trend_following":
            st.plotly_chart(
                price_and_emas_chart(result.processed_data, result.trades, result.equity_curve),
                use_container_width=True,
            )
            if result.extended_metrics:
                st.caption(
                    f"Stop-loss exits: {result.extended_metrics.get('stop_loss_exit_count', 0)} | "
                    f"EMA exits: {result.extended_metrics.get('ema_exit_count', 0)} | "
                    f"Stop slippage impact: "
                    f"{result.extended_metrics.get('stop_slippage_impact_percent', 0):.2f}%"
                )
        else:
            st.plotly_chart(charts.price_and_moving_averages_chart(result.processed_data, result.trades), use_container_width=True)
        st.plotly_chart(charts.equity_curve_chart(result.equity_curve, result.starting_capital), use_container_width=True)
        st.plotly_chart(charts.drawdown_chart(result.equity_curve), use_container_width=True)
        if result.trades:
            st.dataframe(pd.DataFrame([t.__dict__ for t in result.trades]), use_container_width=True)


def _render_compare(database, registry, settings, default_start, default_end):
    st.subheader("Compare Strategies")
    selected = st.multiselect(
        "Strategies",
        registry.list_strategy_types(),
        default=registry.list_strategy_types(),
        format_func=lambda t: registry.get_metadata(t).display_name,
        key="cmp_types",
    )
    asset_type_label, symbol, start_date, end_date, starting_capital, allocation, commission, slippage, reserve, crypto_fee = _common_config_form(
        "cmp", settings, default_start, default_end
    )
    param_sets = {}
    for stype in selected:
        with st.expander(registry.get_metadata(stype).display_name):
            param_sets[stype] = render_parameter_inputs(registry.get_metadata(stype).parameter_definitions, f"cmp_{stype}")

    rank_by = st.selectbox("Rank by", list(StrategyComparisonService.RANK_METRICS.keys()), key="cmp_rank")
    if st.button("Compare", key="cmp_run", type="primary") and selected:
        loaded = _load_research_data(database, settings, asset_type_label, symbol, start_date, end_date)
        if loaded[0] is None:
            return
        data, canonical = loaded
        config = _backtest_config(
            canonical, start_date, end_date, starting_capital, allocation, commission, slippage, reserve,
            asset_type_label=asset_type_label,
            crypto_fee_percent=crypto_fee,
        )
        service = StrategyComparisonService(registry)
        specs = [(t, param_sets[t]) for t in selected]
        results = service.compare(specs, data, config, start_date, end_date)
        ranked = service.rank(results, rank_by)
        for w in service.comparison_warnings(results):
            st.warning(w)
        st.dataframe(
            [{k: getattr(r, k) for k in (
                "strategy_name", "total_return_percent", "maximum_drawdown_percent",
                "sharpe_ratio", "sortino_ratio", "win_rate_percent", "profit_factor",
                "completed_trades", "exposure_percent",
            )} for r in ranked],
            use_container_width=True,
        )
        fig = go.Figure()
        for r in results:
            if r.normalized_equity is not None:
                fig.add_trace(go.Scatter(x=r.normalized_equity.index, y=r.normalized_equity.values, name=r.strategy_name))
        fig.update_layout(title="Normalized Equity (Base = 100)", yaxis_title="Index")
        st.plotly_chart(fig, use_container_width=True)


def _render_train_test(database, registry, settings, default_start, default_end):
    st.subheader("Train / Test Evaluation")
    st.caption(
        "Training performance is used to study and configure a strategy. "
        "Testing performance estimates how it behaved on later unseen historical data."
    )
    strategy_type = st.selectbox("Strategy", registry.list_strategy_types(), format_func=lambda t: registry.get_metadata(t).display_name, key="tt_type")
    params = render_parameter_inputs(registry.get_metadata(strategy_type).parameter_definitions, "tt")
    asset_type_label, symbol, start_date, end_date, starting_capital, allocation, commission, slippage, reserve, crypto_fee = _common_config_form("tt", settings, default_start, default_end)
    train_pct = st.slider("Training %", 50, 85, 70, key="tt_pct")
    if st.button("Run Train/Test", key="tt_run"):
        loaded = _load_research_data(database, settings, asset_type_label, symbol, start_date, end_date)
        if loaded[0] is None:
            return
        data, canonical = loaded
        strategy = registry.build(strategy_type, params)
        config = _backtest_config(
            canonical, start_date, end_date, starting_capital, allocation, commission, slippage, reserve,
            asset_type_label=asset_type_label,
            crypto_fee_percent=crypto_fee,
        )
        evaluation = TrainTestEvaluator().evaluate(strategy, data, config, train_fraction=train_pct / 100.0)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Training Metrics**")
            _display_period(evaluation.training)
        with c2:
            st.markdown("**Testing Metrics**")
            _display_period(evaluation.testing)
        for warning in evaluation.overfitting_warnings:
            st.warning(warning)


def _render_walk_forward(database, registry, settings, default_start, default_end):
    st.subheader("Walk-Forward Evaluation")
    strategy_type = st.selectbox("Strategy", registry.list_strategy_types(), format_func=lambda t: registry.get_metadata(t).display_name, key="wf_type")
    params = render_parameter_inputs(registry.get_metadata(strategy_type).parameter_definitions, "wf")
    asset_type_label, symbol, start_date, end_date, starting_capital, allocation, commission, slippage, reserve, crypto_fee = _common_config_form("wf", settings, default_start, default_end)
    if st.button("Run Walk-Forward", key="wf_run"):
        loaded = _load_research_data(database, settings, asset_type_label, symbol, start_date, end_date)
        if loaded[0] is None:
            return
        data, canonical = loaded
        strategy = registry.build(strategy_type, params)
        config = _backtest_config(
            canonical, start_date, end_date, starting_capital, allocation, commission, slippage, reserve,
            asset_type_label=asset_type_label,
            crypto_fee_percent=crypto_fee,
        )
        result = WalkForwardEvaluator().evaluate(strategy, data, config)
        if result.summary_message:
            st.info(result.summary_message)
        if result.windows:
            st.dataframe([w.__dict__ for w in result.windows], use_container_width=True)
            st.metric("Consistency (% positive windows)", f"{result.consistency_percent:.1f}%")
            if result.combined_oos_equity is not None:
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=result.combined_oos_equity.index, y=result.combined_oos_equity.values, name="Out-of-sample"))
                st.plotly_chart(fig, use_container_width=True)


def _render_portfolio(database, registry, settings, default_start, default_end):
    st.subheader("Portfolio Simulation")
    st.caption("Research Simulation — separate virtual allocations per strategy.")
    asset_type_label, symbol, start_date, end_date, starting_capital, allocation, commission, slippage, reserve, crypto_fee = _common_config_form("pf", settings, default_start, default_end)
    ma_alloc = st.number_input("MA Crossover Allocation ($)", value=float(allocation) / 2, key="pf_ma")
    rsi_alloc = st.number_input("RSI Mean Reversion Allocation ($)", value=float(allocation) / 2, key="pf_rsi")
    unallocated = st.number_input("Unallocated Cash ($)", value=0.0, key="pf_cash")
    if st.button("Simulate Portfolio", key="pf_run"):
        loaded = _load_research_data(database, settings, asset_type_label, symbol, start_date, end_date)
        if loaded[0] is None:
            return
        data, canonical = loaded
        config = _backtest_config(
            canonical, start_date, end_date, starting_capital, float(allocation), commission, slippage, reserve,
            asset_type_label=asset_type_label,
        )
        simulator = PortfolioSimulator(registry)
        try:
            result = simulator.simulate(
                {"moving_average_crossover": float(ma_alloc), "rsi_mean_reversion": float(rsi_alloc)},
                float(unallocated),
                [("moving_average_crossover", {}), ("rsi_mean_reversion", {})],
                config,
                data,
            )
            st.metric("Combined Final Value", components.format_currency(result.final_value))
            st.metric("Combined Return", components.format_percent(result.total_return_percent))
            st.metric("Combined Max Drawdown", components.format_percent(result.maximum_drawdown_percent))
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=result.combined_equity.index, y=result.combined_equity.values, name="Combined"))
            for name, series in result.strategy_equities.items():
                fig.add_trace(go.Scatter(x=series.index, y=series.values, name=name))
            st.plotly_chart(fig, use_container_width=True)
        except ValueError as exc:
            st.error(str(exc))


def _display_metrics(comparison) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Return", components.format_percent(comparison.total_return_percent))
    c2.metric("Sharpe", f"{comparison.sharpe_ratio:.2f}")
    c3.metric("Sortino", f"{comparison.sortino_ratio:.2f}")
    c4.metric("Max Drawdown", components.format_percent(comparison.maximum_drawdown_percent))


def _display_period(period) -> None:
    st.write(f"Return: {components.format_percent(period.total_return_percent)}")
    st.write(f"Sharpe: {period.sharpe_ratio:.2f}")
    st.write(f"Drawdown: {components.format_percent(period.maximum_drawdown_percent)}")
    st.write(f"Trades: {period.completed_trades}")


def _rsi_chart(processed: pd.DataFrame, params: dict) -> None:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4], vertical_spacing=0.05)
    fig.add_trace(go.Scatter(x=processed.index, y=processed["Close"], name="Close"), row=1, col=1)
    fig.add_trace(go.Scatter(x=processed.index, y=processed["RSI"], name="RSI"), row=2, col=1)
    for level, name in [
        (params.get("oversold_threshold", 30), "Oversold"),
        (params.get("exit_threshold", 55), "Exit"),
        (params.get("overbought_threshold", 70), "Overbought"),
    ]:
        fig.add_hline(y=level, line_dash="dash", annotation_text=name, row=2, col=1)
    fig.update_yaxes(range=[0, 100], row=2, col=1)
    buys = processed[processed["Signal"] == "BUY"]
    sells = processed[processed["Signal"] == "SELL"]
    fig.add_trace(go.Scatter(x=buys.index, y=buys["Close"], mode="markers", name="BUY", marker=dict(color="green", size=8)), row=1, col=1)
    fig.add_trace(go.Scatter(x=sells.index, y=sells["Close"], mode="markers", name="SELL", marker=dict(color="red", size=8)), row=1, col=1)
    st.plotly_chart(fig, use_container_width=True)

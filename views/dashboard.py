"""Dashboard overview page."""

from __future__ import annotations

import streamlit as st

from config.settings import get_settings
from data.database import DatabaseManager
from ui.components import format_currency, format_percent, render_empty_state, render_status_banner


def render(database: DatabaseManager) -> None:
    """Render the dashboard page."""
    settings = get_settings()
    st.title("Dashboard")
    st.markdown(
        "Welcome to **Quant Strategy Lab**. This milestone focuses on research: "
        "downloading historical data, running backtests, and reviewing performance."
    )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Trading Mode", settings.trading_mode.title())
    with col2:
        st.metric("Available Strategies", "1")
    with col3:
        st.metric("Live Trading", "Disabled")
    with col4:
        db_status = "Connected" if database.database_exists() else "Not initialized"
        st.metric("Database", db_status)

    render_status_banner(
        "Research Stage",
        "Backtesting is enabled. Paper account viewing is read-only. Order submission is disabled.",
        banner_type="info",
    )

    st.subheader("Recent Backtest Runs")
    recent = database.get_recent_backtests(limit=10)
    if not recent:
        render_empty_state(
            "No backtests yet",
            "Run your first backtest from the **Run Backtest** page to see results here.",
        )
        return

    for run in recent:
        with st.expander(
            f"{run['symbol']} — {run['strategy_name']} ({run['start_date']} to {run['end_date']})"
        ):
            metric_cols = st.columns(4)
            metric_cols[0].metric("Final Value", format_currency(run["final_value"]))
            metric_cols[1].metric("Total Return", format_percent(run["total_return_percent"]))
            metric_cols[2].metric("Win Rate", format_percent(run["win_rate_percent"]))
            metric_cols[3].metric("Sharpe Ratio", f"{run['sharpe_ratio']:.2f}")

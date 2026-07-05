"""Dashboard overview page."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from automation.models import AutomationRunType
from config.settings import get_settings
from data.database import DatabaseManager
from portfolio.allocation_manager import AllocationManager
from ui.components import format_currency, format_percent, render_empty_state, render_status_banner


def render(database: DatabaseManager) -> None:
    """Render the dashboard page."""
    settings = get_settings()
    allocation_manager = AllocationManager(database, settings)
    status_counts = database.count_strategies_by_status()

    st.title("Dashboard")
    st.markdown("Welcome to **Quant Strategy Lab** — research backtesting and manual paper trading.")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Trading Mode", settings.trading_mode.title())
    with col2:
        st.metric("Draft Strategies", status_counts.get("DRAFT", 0))
    with col3:
        st.metric("Active Strategies", status_counts.get("ACTIVE", 0))
    with col4:
        st.metric("Live Trading", "Disabled")

    col5, col6, col7, col8 = st.columns(4)
    with col5:
        st.metric("Allocated Capital", format_currency(float(allocation_manager.get_total_allocated())))
    with col6:
        st.metric("Unallocated Capital", format_currency(float(allocation_manager.get_unallocated_capital())))
    with col7:
        st.metric("Open Paper Orders", len(database.list_open_paper_orders()))
    with col8:
        st.metric("Unknown Orders", database.count_unknown_orders())
    st.caption(f"Capital source: {allocation_manager.capital_source_label}")

    auto_settings = database.get_automation_settings()
    st.subheader("Automation Status")
    ac1, ac2, ac3, ac4 = st.columns(4)
    with ac1:
        st.metric("Automated Trading", "Enabled" if auto_settings.automated_paper_trading_enabled else "Disabled")
    with ac2:
        st.metric("Kill Switch", "Engaged" if auto_settings.kill_switch_engaged else "Off")
    with ac3:
        automated_count = sum(
            1 for s in database.list_strategies() if getattr(s, "automation_enabled", False)
        )
        st.metric("Automated Strategies", automated_count)
    with ac4:
        last_eval = database.get_last_automation_run(AutomationRunType.AFTER_CLOSE_EVALUATION)
        st.metric("Last Evaluation", last_eval["status"] if last_eval else "None")

    today = datetime.now(ZoneInfo(settings.market_timezone)).date().isoformat()
    dc1, dc2, dc3 = st.columns(3)
    with dc1:
        st.metric("Orders Submitted Today", database.count_automated_orders_submitted_today(today))
    with dc2:
        st.metric("Automated Notional Today", format_currency(database.sum_automated_notional_submitted_today(today)))
    with dc3:
        recon = database.get_latest_reconciliation()
        warn_count = len(json.loads(recon["warnings_json"])) if recon else 0
        st.metric("Reconciliation Warnings", warn_count)

    managed_positions = database.list_strategy_positions()
    st.metric("Managed Positions", len(managed_positions))

    render_status_banner(
        "Paper Trading",
        "Manual confirmation required for all orders. No live trading.",
        banner_type="info",
    )

    st.subheader("Research Performance")
    recent = database.get_recent_backtests(limit=5)
    if not recent:
        render_empty_state("No backtests yet", "Run a backtest from Strategy Lab or Multi-Asset Lab.")
    else:
        for run in recent:
            with st.expander(f"{run['symbol']} - {run['strategy_name']}"):
                cols = st.columns(3)
                cols[0].metric("Return", format_percent(run["total_return_percent"]))
                cols[1].metric("Final Value", format_currency(run["final_value"]))
                cols[2].metric("Win Rate", format_percent(run["win_rate_percent"]))

    st.subheader("Paper Trading Performance")
    recent_orders = database.get_recent_paper_orders(limit=5)
    if not recent_orders:
        render_empty_state("No paper orders yet", "Evaluate a strategy on the Paper Trading page.")
    else:
        for order in recent_orders:
            st.write(
                f"{order.symbol} {order.side} x{order.quantity} — "
                f"{order.status} ({order.submitted_at or order.created_at})"
            )

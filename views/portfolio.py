"""Paper portfolio overview page."""

from __future__ import annotations

import streamlit as st

from broker.alpaca_order_manager import AlpacaPaperOrderManager
from config.settings import get_settings
from core.exceptions import QuantTradingError
from core.models import decimal_to_float
from data.database import DatabaseManager
from portfolio.allocation_manager import AllocationManager
from portfolio.portfolio_service import PortfolioService
from ui.components import format_currency, format_percent


def render(database: DatabaseManager) -> None:
    """Render managed portfolio and reconciliation warnings."""
    settings = get_settings()
    st.title("Paper Portfolio")

    allocation_manager = AllocationManager(database, settings)
    portfolio_service = PortfolioService(database)

    st.subheader("Managed Strategy Metrics")
    totals = portfolio_service.get_managed_totals()
    cols = st.columns(4)
    cols[0].metric("Total Allocated", format_currency(float(totals["total_allocated"])))
    cols[1].metric("Unallocated", format_currency(float(totals["unallocated"])))
    cols[2].metric("Managed Cash", format_currency(float(totals["managed_cash"])))
    cols[3].metric("Reserved Cash", format_currency(float(totals["reserved_cash"])))

    cols2 = st.columns(4)
    cols2[0].metric("Positions Value", format_currency(float(totals["positions_value"])))
    cols2[1].metric("Managed Portfolio", format_currency(float(totals["managed_portfolio_value"])))
    cols2[2].metric("Realized P/L", format_currency(float(totals["realized_pl"])))
    cols2[3].metric("Total P/L", format_currency(float(totals["total_pl"])))

    if settings.alpaca_configured:
        st.subheader("Broker-Level Metrics")
        try:
            order_manager = AlpacaPaperOrderManager(
                settings.alpaca_api_key, settings.alpaca_secret_key
            )
            account = order_manager.get_account_summary()
            bcols = st.columns(4)
            bcols[0].metric("Alpaca Cash", format_currency(float(account["cash"])))
            bcols[1].metric("Alpaca Equity", format_currency(float(account["equity"])))
            bcols[2].metric("Portfolio Value", format_currency(float(account["portfolio_value"])))
            bcols[3].metric("Buying Power", format_currency(float(account["buying_power"])))

            alpaca_positions = {
                p["symbol"]: int(p["quantity"]) for p in order_manager.get_all_positions()
            }
            warnings = portfolio_service.get_reconciliation_warnings(alpaca_positions)
            if warnings:
                st.subheader("Reconciliation Warnings")
                for warning in warnings:
                    st.warning(warning)
        except QuantTradingError as exc:
            st.error(str(exc))

    st.subheader("Strategy Table")
    strategies = database.list_strategies()
    if not strategies:
        st.info("No strategies configured.")
        return

    rows = []
    for strategy in strategies:
        summary = portfolio_service.get_strategy_summary(strategy.id)
        pos = database.get_strategy_position(strategy.id, strategy.symbol)
        rows.append(
            {
                "Strategy": strategy.name,
                "Symbol": strategy.symbol,
                "Status": strategy.status.value,
                "Allocation": decimal_to_float(summary.allocated_funds),
                "Available Cash": decimal_to_float(summary.available_cash),
                "Reserved Cash": decimal_to_float(summary.reserved_cash),
                "Quantity": int(pos["quantity"]) if pos else 0,
                "Avg Entry": float(pos["average_entry_price"]) if pos else 0.0,
                "Market Value": decimal_to_float(summary.invested_value),
                "Realized P/L": decimal_to_float(summary.realized_profit_loss),
                "Unrealized P/L": decimal_to_float(summary.unrealized_profit_loss),
                "Total Return %": decimal_to_float(summary.total_return_percent),
            }
        )
    st.dataframe(rows, use_container_width=True)

    st.caption(
        "Alpaca positions are account-level. Quant Strategy Lab tracks strategy-level "
        "positions locally and does not assume all broker positions are managed by this app."
    )

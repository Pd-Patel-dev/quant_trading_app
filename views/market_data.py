"""Historical market data management page."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from config.settings import get_settings
from data.database import DatabaseManager
from market_data.factory import build_market_data_stack
from market_data.models import AssetRequest, AssetType, DataTimeframe
from market_data.symbol_normalizer import SymbolNormalizer
from ui import components


def render(database: DatabaseManager) -> None:
    settings = get_settings()
    st.title("Market Data")
    st.markdown(
        "Manage local historical data cache for stocks and crypto research. "
        "Crypto data is for backtesting only — not order submission."
    )

    tabs = st.tabs(
        ["Download Data", "Cached Assets", "Data Coverage", "Quality Issues", "Download History"]
    )

    with tabs[0]:
        _render_download_tab(database, settings)
    with tabs[1]:
        _render_cached_assets_tab(database)
    with tabs[2]:
        _render_coverage_tab(database)
    with tabs[3]:
        _render_quality_tab(database)
    with tabs[4]:
        _render_history_tab(database)


def _render_download_tab(database: DatabaseManager, settings) -> None:
    normalizer = SymbolNormalizer()
    with st.form("download_form"):
        asset_type_label = st.selectbox("Asset Type", ["STOCK", "CRYPTO"])
        symbols_text = st.text_area(
            "Symbols (comma or newline separated)",
            value="AAPL, MSFT" if asset_type_label == "STOCK" else "BTC/USD, ETH/USD",
        )
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Start Date", value=date.today() - timedelta(days=365 * 2))
        with col2:
            end_date = st.date_input("End Date", value=date.today())
        st.caption("Timeframe: Daily")
        force_refresh = st.checkbox("Force refresh complete interval")
        repair_gaps = st.checkbox("Repair internal gaps", value=True)
        submitted = st.form_submit_button("Search Cache and Download Missing Data", type="primary")

    if not submitted:
        return

    asset_type = AssetType(asset_type_label)
    parsed = normalizer.parse_input(asset_type, symbols_text)
    if parsed.invalid:
        for msg in parsed.invalid:
            st.error(msg)
        return
    if not parsed.normalized:
        st.warning("Enter at least one valid symbol.")
        return

    if not settings.alpaca_configured and asset_type == AssetType.STOCK:
        components.render_status_banner(
            "Alpaca Credentials Missing",
            "Stock downloads require ALPACA_API_KEY and ALPACA_SECRET_KEY.",
            banner_type="warning",
        )
        return

    _, cache, batch = build_market_data_stack(database, settings)
    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc)
    requests = [
        AssetRequest(
            asset_type=asset_type,
            symbol=symbol,
            start=start_dt,
            end=end_dt,
            force_refresh=force_refresh,
            repair_gaps=repair_gaps,
        )
        for symbol in parsed.normalized
    ]
    progress = st.progress(0.0, text="Synchronizing...")
    batch_result = batch.get_or_download_many(requests)
    progress.progress(1.0, text="Complete")

    for idx, result in enumerate(batch_result.results):
        summary = (
            f"**{result.asset_type.value} {result.symbol}** — "
            f"Cache: {result.data_source_status} | "
            f"Cached before: {result.cached_rows_before} | "
            f"Downloaded: {result.downloaded_rows} | "
            f"Inserted: {result.inserted_rows} | "
            f"Updated: {result.updated_rows} | "
            f"Final rows: {result.final_rows}"
        )
        st.write(summary)
        if result.warnings:
            for warning in result.warnings:
                st.warning(warning)
    for error in batch_result.errors:
        st.error(error)


def _render_cached_assets_tab(database: DatabaseManager) -> None:
    asset_filter = st.selectbox("Filter by asset type", ["All", "STOCK", "CRYPTO"], key="cached_filter")
    symbol_filter = st.text_input("Symbol contains", value="")
    asset_type = None if asset_filter == "All" else asset_filter
    rows = database.list_cached_assets(asset_type, symbol_filter or None)
    if not rows:
        st.info("No cached assets yet.")
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def _render_coverage_tab(database: DatabaseManager) -> None:
    rows = database.list_cached_assets()
    if not rows:
        st.info("No coverage data.")
        return
    display = []
    for row in rows:
        status = row.get("data_quality_status") or "UNKNOWN"
        if row.get("row_count", 0) == 0:
            status = "No data"
        elif status == "VALID":
            status = "Complete"
        display.append({**row, "coverage_status": status})
    st.dataframe(pd.DataFrame(display), use_container_width=True)


def _render_quality_tab(database: DatabaseManager) -> None:
    issues = database.list_quality_issues(unresolved_only=True)
    if not issues:
        st.success("No unresolved quality issues.")
        return
    for issue in issues:
        cols = st.columns([4, 1])
        cols[0].write(
            f"{issue['asset_type']} {issue['symbol']} — {issue['issue_type']}: {issue['description']}"
        )
        if issue["issue_type"] not in ("INVALID_OHLC", "NEGATIVE_PRICE", "MISSING_VALUE"):
            if cols[1].button("Acknowledge", key=f"ack_{issue['id']}"):
                database.acknowledge_quality_issue(issue["id"])
                st.rerun()


def _render_history_tab(database: DatabaseManager) -> None:
    runs = database.list_download_runs(limit=100)
    if not runs:
        st.info("No download runs recorded.")
        return
    st.dataframe(pd.DataFrame(runs), use_container_width=True)

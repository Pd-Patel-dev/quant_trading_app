"""Strategy management page."""

from __future__ import annotations

import json
import logging
from decimal import Decimal

import streamlit as st

from config.settings import get_settings
from core.exceptions import AllocationError, QuantTradingError, StrategyError
from core.models import EntryPolicy, StrategyStatus, decimal_to_float, to_decimal
from data.database import DatabaseManager
from portfolio.allocation_manager import AllocationManager
from services.strategy_service import StrategyService
from ui.components import format_currency, format_percent, render_status_banner

logger = logging.getLogger(__name__)


def render(database: DatabaseManager) -> None:
    """Render strategy creation and lifecycle management."""
    settings = get_settings()
    allocation_manager = AllocationManager(database, settings)
    strategy_service = StrategyService(database)

    st.title("Strategies")
    st.markdown(
        "Create and manage moving-average crossover strategies with virtual fund allocation. "
        "Only **one active strategy** may trade a given symbol."
    )

    unallocated = allocation_manager.get_unallocated_capital()
    st.info(
        f"Local paper capital pool: {format_currency(float(allocation_manager.capital_pool))} | "
        f"Unallocated: {format_currency(float(unallocated))}"
    )

    with st.expander("Create Moving Average Strategy", expanded=False):
        with st.form("create_strategy_form"):
            name = st.text_input("Strategy Name", placeholder="SPY Trend Follower")
            symbol = st.text_input("Symbol", value="SPY").strip().upper()
            col1, col2 = st.columns(2)
            with col1:
                short_window = st.number_input("Short MA Window", min_value=2, value=50, step=1)
            with col2:
                long_window = st.number_input("Long MA Window", min_value=3, value=200, step=1)
            allocation = st.number_input(
                "Fund Allocation ($)",
                min_value=1.0,
                max_value=float(unallocated) if unallocated > 0 else 1.0,
                value=min(10000.0, float(unallocated)) if unallocated > 0 else 1.0,
                step=100.0,
            )
            cash_reserve = st.number_input(
                "Cash Reserve (%)",
                min_value=0.0,
                max_value=50.0,
                value=settings.default_cash_reserve_percent * 100,
                step=0.5,
            )
            entry_policy = st.selectbox(
                "Entry Policy",
                options=[
                    EntryPolicy.WAIT_FOR_NEXT_CROSSOVER.value,
                    EntryPolicy.ALIGN_WITH_CURRENT_POSITION.value,
                ],
                format_func=lambda x: x.replace("_", " ").title(),
            )
            st.markdown(
                """
                **Entry policies**
                - *Wait For Next Crossover*: enter only after a new BUY crossover following activation.
                - *Align With Current Position*: may propose a BUY when the strategy already indicates
                  long exposure, even if the crossover happened before activation. Requires extra confirmation.
                """
            )
            save_draft = st.form_submit_button("Save as Draft")
            save_activate = st.form_submit_button("Save and Activate", type="primary")

        if save_draft or save_activate:
            try:
                strategy_id = strategy_service.create_strategy(
                    name=name,
                    symbol=symbol,
                    short_window=int(short_window),
                    long_window=int(long_window),
                    allocated_funds=to_decimal(allocation),
                    cash_reserve_percent=to_decimal(cash_reserve) / Decimal("100"),
                    entry_policy=EntryPolicy(entry_policy),
                    activate=save_activate,
                )
                st.success(f"Strategy created (ID {strategy_id}).")
            except (StrategyError, AllocationError, QuantTradingError) as exc:
                st.error(str(exc))

    st.subheader("Your Strategies")
    strategies = database.list_strategies()
    if not strategies:
        st.markdown("No strategies yet. Create one above.")
        return

    for strategy in strategies:
        params = json.loads(strategy.parameters_json)
        with st.expander(f"{strategy.name} ({strategy.symbol}) - {strategy.status.value}"):
            st.write(f"**Type:** {strategy.strategy_type}")
            st.write(f"**Windows:** {params.get('short_window')}/{params.get('long_window')}")
            st.write(f"**Allocation:** {format_currency(decimal_to_float(strategy.allocated_funds))}")
            st.write(f"**Cash Reserve:** {format_percent(decimal_to_float(strategy.cash_reserve_percent) * 100)}")
            st.write(f"**Entry Policy:** {strategy.entry_policy.value.replace('_', ' ').title()}")

            if strategy_service.has_open_position(strategy.id):
                render_status_banner(
                    "Open Position",
                    "This strategy still holds shares. Pausing or stopping does not auto-liquidate.",
                    banner_type="warning",
                )

            cols = st.columns(4)
            if strategy.status == StrategyStatus.DRAFT and cols[0].button("Activate", key=f"act_{strategy.id}"):
                try:
                    strategy_service.activate(strategy.id)
                    st.success("Strategy activated.")
                    st.rerun()
                except QuantTradingError as exc:
                    st.error(str(exc))
            if strategy.status == StrategyStatus.ACTIVE and cols[1].button("Pause", key=f"pau_{strategy.id}"):
                strategy_service.pause(strategy.id)
                st.rerun()
            if strategy.status == StrategyStatus.PAUSED and cols[2].button("Resume", key=f"res_{strategy.id}"):
                try:
                    strategy_service.resume(strategy.id)
                    st.rerun()
                except QuantTradingError as exc:
                    st.error(str(exc))
            if strategy.status in (StrategyStatus.ACTIVE, StrategyStatus.PAUSED) and cols[3].button(
                "Stop", key=f"stp_{strategy.id}"
            ):
                strategy_service.stop(strategy.id)
                st.rerun()

    st.subheader("Strategy Reference")
    st.markdown(
        """
        **Moving Average Crossover** uses daily closing bars. Golden cross = BUY, death cross = SELL.
        Paper orders require manual confirmation on the **Paper Trading** page.
        """
    )

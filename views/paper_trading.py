"""Paper trading workflow page."""

from __future__ import annotations

import json
import logging

import streamlit as st

from broker.alpaca_order_manager import AlpacaPaperOrderManager
from config.settings import get_settings
from core.exceptions import QuantTradingError
from core.models import ConfirmationData, OrderProposalStatus, StrategyStatus
from data.alpaca_data import AlpacaMarketDataProvider
from data.database import DatabaseManager
from portfolio.portfolio_service import PortfolioService
from services.order_proposal_service import OrderProposalService
from services.paper_trading_service import PaperTradingService
from ui.components import format_currency, render_status_banner

logger = logging.getLogger(__name__)


def render(database: DatabaseManager) -> None:
    """Render the paper trading page."""
    settings = get_settings()
    render_status_banner(
        "PAPER TRADING ONLY - NO REAL MONEY",
        "All orders require manual confirmation. Streamlit reruns will not submit orders.",
        banner_type="warning",
    )

    if not settings.alpaca_configured:
        st.warning("Configure Alpaca credentials in `.env` to use paper trading.")
        return

    order_manager = AlpacaPaperOrderManager(settings.alpaca_api_key, settings.alpaca_secret_key)
    data_provider = AlpacaMarketDataProvider(settings.alpaca_api_key, settings.alpaca_secret_key)
    paper_service = PaperTradingService(database, order_manager, data_provider, settings)
    portfolio_service = PortfolioService(database)

    _render_broker_status(database, order_manager, portfolio_service)

    st.subheader("Strategy Evaluation")
    active_strategies = database.list_strategies(StrategyStatus.ACTIVE)
    if not active_strategies:
        st.info("No active strategies. Activate a strategy on the Strategies page.")
        return

    strategy_options = {f"{s.name} ({s.symbol})": s.id for s in active_strategies}
    selected_label = st.selectbox("Select Active Strategy", list(strategy_options.keys()))
    strategy_id = strategy_options[selected_label]
    strategy = database.get_strategy(strategy_id)

    if strategy:
        summary = paper_service.get_strategy_paper_summary(strategy_id)
        cols = st.columns(4)
        cols[0].metric("Allocation", format_currency(summary["allocated_funds"]))
        cols[1].metric("Available Cash", format_currency(summary["available_cash"]))
        cols[2].metric("Reserved Cash", format_currency(summary["reserved_cash"]))
        pos = database.get_strategy_position(strategy_id, strategy.symbol)
        cols[3].metric("Local Position", str(int(pos["quantity"]) if pos else 0))

        st.caption(
            f"Entry policy: {strategy.entry_policy.value.replace('_', ' ').title()} | "
            "Daily strategy based on completed closing bars."
        )

    if st.button("Evaluate Strategy", type="primary"):
        try:
            evaluation = paper_service.evaluate_strategy(strategy_id)
            st.session_state["latest_evaluation"] = evaluation
            proposal = paper_service.build_order_proposal(strategy_id)
            st.session_state["latest_proposal_id"] = proposal.proposal_id
            st.session_state[f"submitted_{proposal.proposal_id}"] = False
        except QuantTradingError as exc:
            st.error(str(exc))

    evaluation = st.session_state.get("latest_evaluation")
    if evaluation:
        st.markdown("**Latest Evaluation**")
        st.write(f"Desired position: {evaluation.current_desired_position}")
        st.write(f"Latest signal: {evaluation.latest_signal.value}")
        st.write(f"Signal timestamp: {evaluation.signal_timestamp}")
        st.write(f"Data timestamp: {evaluation.data_timestamp}")
        st.write(f"Short SMA: {evaluation.short_sma}")
        st.write(f"Long SMA: {evaluation.long_sma}")
        st.write(f"Reference price: {evaluation.close_price}")
        st.write(f"Explanation: {evaluation.explanation}")

    proposal_id = st.session_state.get("latest_proposal_id")
    if proposal_id:
        _render_proposal_section(database, paper_service, proposal_id)


def _render_broker_status(
    database: DatabaseManager,
    order_manager: AlpacaPaperOrderManager,
    portfolio_service: PortfolioService,
) -> None:
    st.subheader("Broker Status")
    try:
        account = order_manager.get_account_summary()
        clock = order_manager.get_market_clock()
        positions = order_manager.get_all_positions()
    except QuantTradingError as exc:
        st.error(str(exc))
        return

    cols = st.columns(4)
    cols[0].metric("Account Status", str(account["status"]))
    cols[1].metric("Market", "Open" if clock["is_open"] else "Closed")
    cols[2].metric("Paper Cash", format_currency(float(account["cash"])))
    cols[3].metric("Buying Power", format_currency(float(account["buying_power"])))

    st.caption(
        f"Market time: {clock.get('timestamp')} | Next open: {clock.get('next_open')} | "
        f"Next close: {clock.get('next_close')} | Trading blocked: {account.get('trading_blocked')}"
    )

    if positions:
        st.markdown("**Alpaca Account Positions** (may include unmanaged positions)")
        st.dataframe(
            [{"symbol": p["symbol"], "quantity": p["quantity"], "market_value": p["market_value"]} for p in positions],
            use_container_width=True,
        )

    managed = portfolio_service.list_managed_positions()
    if managed:
        st.markdown("**Quant Strategy Lab Managed Positions**")
        st.dataframe(managed, use_container_width=True)

    _render_order_management(database, order_manager)


def _render_proposal_section(database: DatabaseManager, paper_service: PaperTradingService, proposal_id: str) -> None:
    row = database.get_proposal(proposal_id)
    if not row:
        return

    st.subheader("Proposal Review")
    blocking = json.loads(row.get("blocking_reasons_json") or "[]")
    messages = json.loads(row.get("validation_json") or "[]")

    cols = st.columns(3)
    cols[0].metric("Side", row["side"])
    cols[1].metric("Quantity", row["quantity"])
    cols[2].metric("Est. Notional", format_currency(row["estimated_notional"]))

    st.write(f"Symbol: {row['symbol']} | Client Order ID: `{row['client_order_id']}`")
    st.write(f"Status: {row['status']} | Expires: {row.get('expires_at')}")

    if messages:
        st.success("Validations passed: " + "; ".join(messages))
    if blocking:
        for reason in blocking:
            st.error(reason)

    if row["status"] in (OrderProposalStatus.PROPOSED.value, OrderProposalStatus.BLOCKED.value) and not blocking:
        with st.form(f"confirm_{proposal_id}"):
            ack = st.checkbox("I understand this is paper trading")
            reviewed = st.checkbox("I reviewed the symbol, side, quantity, and estimated amount")
            paper_text = st.text_input("Type PAPER to confirm")
            align_text = st.text_input("Type ALIGN if this is an alignment entry (otherwise leave blank)")
            if st.form_submit_button("Confirm Paper Order"):
                try:
                    paper_service.confirm_proposal(
                        proposal_id,
                        ConfirmationData(
                            paper_text=paper_text,
                            paper_trading_acknowledged=ack,
                            details_reviewed=reviewed,
                            alignment_text=align_text,
                        ),
                    )
                    st.success("Proposal confirmed. Use Submit button below.")
                    st.rerun()
                except QuantTradingError as exc:
                    st.error(str(exc))

    if row["status"] == OrderProposalStatus.CONFIRMED.value:
        submit_key = f"submitted_{proposal_id}"
        already_submitted = st.session_state.get(submit_key, False)
        if st.button("Submit Confirmed Paper Order", disabled=already_submitted):
            try:
                result = paper_service.submit_confirmed_proposal(proposal_id)
                st.session_state[submit_key] = True
                st.success(f"Order submitted. Status: {result.get('status')}")
            except QuantTradingError as exc:
                st.error(str(exc))


def _render_order_management(database: DatabaseManager | None, order_manager: AlpacaPaperOrderManager) -> None:
    if database is None:
        return
    st.subheader("Order Management")
    orders = database.list_paper_orders(limit=20)
    if orders:
        st.dataframe(
            [
                {
                    "id": o.id,
                    "strategy_id": o.strategy_id,
                    "symbol": o.symbol,
                    "side": o.side,
                    "quantity": o.quantity,
                    "status": o.status,
                    "client_order_id": o.client_order_id,
                    "alpaca_order_id": o.alpaca_order_id,
                    "filled_qty": o.filled_quantity,
                    "submitted_at": o.submitted_at,
                }
                for o in orders
            ],
            use_container_width=True,
        )
    if st.button("Refresh All Open Orders"):
        paper_service = PaperTradingService(
            database,
            order_manager,
            None,
            get_settings(),
        )
        try:
            paper_service.synchronize_all_open_orders()
            st.success("Open orders synchronized.")
            st.rerun()
        except QuantTradingError as exc:
            st.error(str(exc))

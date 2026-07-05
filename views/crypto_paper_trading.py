"""Crypto paper trading page."""

from __future__ import annotations

import json

import streamlit as st

from broker.crypto_asset_service import CryptoAssetService
from broker.crypto_order_manager import AlpacaCryptoPaperOrderManager
from config.settings import get_settings
from core.models import CryptoConfirmationData, StrategyStatus
from data.database import DatabaseManager
from portfolio.crypto_ledger import CryptoStrategyLedger
from services.crypto_paper_trading_service import CryptoPaperTradingService
from ui import components


def render(database: DatabaseManager) -> None:
    settings = get_settings()
    st.title("Crypto Paper Trading")
    st.error("CRYPTO PAPER TRADING ONLY — NO REAL MONEY")
    st.info("Crypto trading schedule: Continuous")

    if not settings.alpaca_configured:
        components.render_status_banner(
            "Alpaca Credentials Missing",
            "Configure ALPACA_API_KEY and ALPACA_SECRET_KEY.",
            banner_type="warning",
        )
        return

    order_manager = AlpacaCryptoPaperOrderManager(settings.alpaca_api_key, settings.alpaca_secret_key)
    service = CryptoPaperTradingService(database, order_manager)
    ledger = CryptoStrategyLedger(database)
    status = service.get_status_summary()

    cols = st.columns(4)
    cols[0].metric("Crypto Trading", "Enabled" if status["crypto_paper_trading_enabled"] else "Disabled")
    cols[1].metric("Kill Switch", "Engaged" if status["crypto_kill_switch_engaged"] else "Disengaged")
    cols[2].metric("USD Pairs", str(len(status["supported_pairs"])))
    cols[3].metric("Critical Issues", str(len(status["reconciliation_critical"])))

    strategies = [
        s
        for s in database.list_strategies(StrategyStatus.ACTIVE)
        if getattr(s, "asset_type", "STOCK") == "CRYPTO"
    ]
    if not strategies:
        drafts = [
            s
            for s in database.list_strategies(StrategyStatus.DRAFT)
            if getattr(s, "asset_type", "STOCK") == "CRYPTO"
        ]
        st.warning("No active crypto strategies.")
        st.markdown(
            "1. Run a **CRYPTO** backtest in **Strategy Lab** (e.g. `BTC/USD`).\n"
            "2. Create a crypto strategy on the **Strategies** page (Asset Class: Crypto).\n"
            "3. Approve it for crypto paper trading, then **Activate** it from the Drafts tab."
        )
        if drafts:
            st.info(
                "Draft crypto strategies waiting for approval/activation: "
                + ", ".join(f"{s.name} ({s.symbol})" for s in drafts)
            )
        return

    selected = st.selectbox(
        "Active Crypto Strategy",
        strategies,
        format_func=lambda s: f"{s.name} ({s.symbol})",
    )

    available = ledger.get_available_usd(selected.id)
    reserved = ledger.get_reserved_usd(selected.id)
    position = database.get_crypto_position(selected.id, selected.symbol)
    st.write(
        f"Allocation: {components.format_currency(float(selected.allocated_funds))} | "
        f"Available USD: {components.format_currency(float(available))} | "
        f"Reserved: {components.format_currency(float(reserved))}"
    )
    if position:
        st.write(f"Local quantity: {position['quantity_text']} {selected.symbol}")

    if st.button("Evaluate Crypto Strategy"):
        evaluation = service.evaluate_strategy(selected.id)
        st.session_state["crypto_evaluation"] = evaluation
        st.write(evaluation)

    if st.button("Generate Crypto Order Proposal"):
        proposal = service.build_order_proposal(selected.id)
        st.session_state["crypto_proposal"] = proposal
        st.json(proposal)

    proposal = st.session_state.get("crypto_proposal")
    if proposal and proposal.get("status") != "BLOCKED":
        st.subheader("Confirm Crypto Paper Order")
        st.write(proposal)
        with st.form("crypto_confirm"):
            ack1 = st.checkbox("I understand this is a simulated crypto paper order.")
            ack2 = st.checkbox("I reviewed the pair, side, amount, estimated fee, and strategy signal.")
            ack3 = st.checkbox("I understand crypto markets operate continuously and prices may move before execution.")
            confirm_text = st.text_input("Type PAPER CRYPTO to confirm")
            confirmed = st.form_submit_button("Confirm Crypto Paper Order")
        if confirmed:
            service.confirm_proposal(
                proposal["proposal_id"],
                CryptoConfirmationData(
                    paper_text=confirm_text,
                    paper_trading_acknowledged=ack1,
                    details_reviewed=ack2,
                    continuous_market_acknowledged=ack3,
                ),
            )
            st.session_state["crypto_proposal_confirmed"] = proposal["proposal_id"]
            st.success("Proposal confirmed.")

        if st.session_state.get("crypto_proposal_confirmed") == proposal.get("proposal_id"):
            if st.button("Submit Confirmed Crypto Paper Order"):
                if "crypto_submitted" not in st.session_state:
                    result = service.submit_confirmed_proposal(proposal["proposal_id"])
                    st.session_state["crypto_submitted"] = True
                    st.success(f"Submitted: {result}")

    if status["reconciliation_warnings"]:
        st.warning("\n".join(status["reconciliation_warnings"]))
    if status["reconciliation_critical"]:
        st.error("\n".join(status["reconciliation_critical"]))

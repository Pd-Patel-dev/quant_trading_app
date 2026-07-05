"""Automation monitoring and configuration page."""

from __future__ import annotations

import json

import streamlit as st

from automation.automation_service import (
    GLOBAL_ENABLE_PHRASE,
    KILL_SWITCH_DISENGAGE_PHRASE,
    STRATEGY_ENABLE_PHRASE,
    AutomationService,
)
from broker.alpaca_order_manager import AlpacaPaperOrderManager
from config.settings import get_settings
from core.models import StrategyStatus
from data.alpaca_data import AlpacaMarketDataProvider
from data.database import DatabaseManager
from portfolio.ledger import StrategyLedger
from ui.automation_status import explain_strategy_automation
from ui.components import format_currency, render_status_banner
from strategies.registry import get_registry


def _build_service(database: DatabaseManager) -> AutomationService:
    settings = get_settings()
    order_manager = None
    data_provider = None
    if settings.alpaca_configured:
        order_manager = AlpacaPaperOrderManager(settings.alpaca_api_key, settings.alpaca_secret_key)
        data_provider = AlpacaMarketDataProvider(settings.alpaca_api_key, settings.alpaca_secret_key)
    return AutomationService(database, order_manager, data_provider, settings)


def render(database: DatabaseManager) -> None:
    """Render the automation page."""
    settings = get_settings()
    service = _build_service(database)
    status = service.get_dashboard_status()
    auto_settings = database.get_automation_settings()
    ledger = StrategyLedger(database)

    render_status_banner(
        "AUTOMATED PAPER TRADING ONLY — NO REAL MONEY",
        "Workers run outside Streamlit. Manual confirmation still required for manual proposals.",
        banner_type="warning",
    )

    st.title("Automation")
    st.caption("Configure and monitor automated daily paper-trading workers.")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Trading Mode", status["trading_mode"].title())
    col2.metric("Live Trading", "Disabled" if not status["live_trading_enabled"] else "Enabled")
    col3.metric(
        "Global Automation",
        "Enabled" if status["automated_paper_trading_enabled"] else "Disabled",
    )
    col4.metric(
        "Kill Switch",
        "ENGAGED" if status["kill_switch_engaged"] else "Disengaged",
    )

    if status["kill_switch_engaged"]:
        st.error("Emergency kill switch is ENGAGED. Automated order submission is blocked.")

    if auto_settings.automated_paper_trading_enabled:
        st.info(
            "**Global automation is enabled.** This is only the master switch. "
            "Each strategy still shows automation **Off** until you enable it under "
            "**Strategy Automation** below (per-strategy approval required)."
        )

    st.subheader("Global Controls")

    if not auto_settings.automated_paper_trading_enabled:
        st.markdown("Global automation is **disabled** by default.")
        ack1 = st.checkbox("I understand automated paper orders may be submitted without reviewing each order.")
        ack2 = st.checkbox("I understand paper results do not guarantee live results.")
        confirm = st.text_input("Type confirmation phrase", key="global_enable_phrase")
        if st.button("Enable Automated Paper Trading"):
            if not (ack1 and ack2):
                st.error("Both acknowledgment checkboxes are required.")
            elif confirm.strip() != GLOBAL_ENABLE_PHRASE:
                st.error(f"Type exactly: {GLOBAL_ENABLE_PHRASE}")
            else:
                try:
                    service.enable_global_automation(confirm)
                    st.success("Global automation enabled.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    else:
        if st.button("Disable Automated Paper Trading"):
            service.disable_global_automation()
            st.warning("Global automation disabled immediately.")
            st.rerun()

    st.subheader("Kill Switch")
    if not auto_settings.kill_switch_engaged:
        if st.button("ENGAGE EMERGENCY KILL SWITCH", type="primary"):
            service.engage_kill_switch()
            st.warning("Kill switch engaged.")
            st.rerun()
    else:
        disengage_text = st.text_input("Type to disengage kill switch", key="kill_disengage_phrase")
        if st.button("Disengage Kill Switch"):
            if disengage_text.strip() != KILL_SWITCH_DISENGAGE_PHRASE:
                st.error(f"Type exactly: {KILL_SWITCH_DISENGAGE_PHRASE}")
            else:
                try:
                    service.disengage_kill_switch(disengage_text)
                    st.success("Kill switch disengaged.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    st.subheader("Worker Status")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Unknown Orders", status["unknown_orders"])
    c2.metric("Pending Orders", status["pending_orders"])
    c3.metric("Orders Today", status["orders_submitted_today"])
    c4.metric("Notional Today", format_currency(status["notional_submitted_today"]))

    def _fmt_run(run: dict | None) -> str:
        if not run:
            return "Never"
        return f"{run['status']} @ {run['started_at'][:19]}"

    st.write(f"Last after-close evaluation: {_fmt_run(status['last_after_close'])}")
    st.write(f"Last market-open execution: {_fmt_run(status['last_market_open'])}")
    st.write(f"Last order sync: {_fmt_run(status['last_sync'])}")
    st.write(f"Last reconciliation: {_fmt_run(status['last_reconciliation'])}")
    st.write(f"Reconciliation warnings: {status['reconciliation_warnings']}")

    st.subheader("Strategy Automation")
    registry = get_registry()
    strategies = database.list_strategies(StrategyStatus.ACTIVE)
    if not strategies:
        st.info("No strategies configured.")
    else:
        for strategy in strategies:
            pos = database.get_strategy_position(strategy.id, strategy.symbol)
            local_qty = int(pos["quantity"]) if pos else 0
            latest_signal = database.get_latest_signal(strategy.id)
            pending = any(
                o.strategy_id == strategy.id
                for o in database.list_open_paper_orders()
            )
            with st.expander(f"{strategy.name} ({strategy.symbol})"):
                cols = st.columns(4)
                cols[0].write(f"Status: {strategy.status.value}")
                auto_label, auto_detail = explain_strategy_automation(
                    strategy, auto_settings, registry
                )
                cols[1].write(auto_label.replace("Per-strategy: ", "Automation: "))
                if auto_detail:
                    st.caption(auto_detail)
                cols[2].write(f"Position: {local_qty}")
                cols[3].write(f"Cash: {format_currency(float(ledger.get_available_cash(strategy.id)))}")
                if latest_signal:
                    st.caption(f"Latest signal: {latest_signal['signal']} @ {latest_signal['signal_timestamp']}")
                st.caption(f"Pending order: {'Yes' if pending else 'No'}")

                if not strategy.automation_enabled:
                    if strategy.status == StrategyStatus.ACTIVE and auto_settings.automated_paper_trading_enabled:
                        ack = st.checkbox(
                            "I understand this strategy may place paper orders automatically.",
                            key=f"strat_ack_{strategy.id}",
                        )
                        phrase = st.text_input("Confirmation phrase", key=f"strat_phrase_{strategy.id}")
                        if st.button(f"Enable automation for {strategy.name}", key=f"enable_{strategy.id}"):
                            if not ack:
                                st.error("Acknowledgment required.")
                            elif phrase.strip() != STRATEGY_ENABLE_PHRASE:
                                st.error(f"Type exactly: {STRATEGY_ENABLE_PHRASE}")
                            else:
                                try:
                                    service.enable_strategy_automation(strategy.id, phrase)
                                    st.success("Strategy automation enabled.")
                                    st.rerun()
                                except ValueError as exc:
                                    st.error(str(exc))
                    else:
                        st.caption("Requires active strategy and global automation enabled.")
                else:
                    if st.button(f"Disable automation for {strategy.name}", key=f"disable_{strategy.id}"):
                        service.disable_strategy_automation(strategy.id)
                        st.info("Strategy automation disabled.")
                        st.rerun()

    st.subheader("Manual Worker Execution (Development)")
    st.caption("These buttons invoke the same service layer as CLI workers.")
    w1, w2, w3, w4 = st.columns(4)
    with w1:
        if st.button("Run After-Close Evaluation"):
            result = service.run_after_close_evaluation()
            st.write(f"Status: {result.status.value}")
    with w2:
        if st.button("Run Market-Open Execution"):
            result = service.run_market_open_execution()
            st.write(f"Status: {result.status.value}")
    with w3:
        if st.button("Synchronize Orders"):
            result = service.run_order_synchronization()
            st.write(f"Status: {result.status.value}")
    with w4:
        if st.button("Run Reconciliation"):
            result = service.run_daily_reconciliation()
            st.write(f"Status: {result.status.value}")

    st.subheader("Automation History")
    runs = database.list_automation_runs(limit=15)
    if runs:
        st.dataframe(
            [
                {
                    "Type": r["run_type"],
                    "Status": r["status"],
                    "Started": r["started_at"][:19],
                    "Completed": (r["completed_at"] or "")[:19],
                    "Strategies": r["strategies_checked"],
                    "Proposals": r["proposals_created"],
                    "Orders": r["orders_submitted"],
                    "Warnings": r["warnings_count"],
                    "Errors": r["errors_count"],
                }
                for r in runs
            ],
            use_container_width=True,
        )

    st.subheader("Recent Audit Log")
    audit = database.list_audit_log(limit=20)
    for entry in audit:
        details = json.loads(entry.get("details_json") or "{}")
        st.write(f"**{entry['created_at'][:19]}** — {entry['event_type']}: {entry['message']}")
        if details:
            st.caption(str(details))

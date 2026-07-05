"""Strategy management page."""

from __future__ import annotations

import json
from decimal import Decimal

import streamlit as st

from config.settings import get_settings
from core.exceptions import AllocationError, QuantTradingError, StrategyDeletionBlockedError, StrategyError
from core.models import EntryPolicy, StrategyStatus, decimal_to_float, to_decimal
from data.database import DatabaseManager
from portfolio.allocation_manager import AllocationManager
from portfolio.crypto_ledger import CryptoStrategyLedger
from portfolio.ledger import StrategyLedger
from services.strategy_lifecycle_service import StrategyLifecycleService
from services.strategy_service import (
    CRYPTO_PAPER_APPROVAL_PHRASE,
    PAPER_APPROVAL_PHRASE,
    StrategyService,
)
from strategies.registry import get_registry
from ui.components import format_currency
from ui.automation_status import can_enable_strategy_automation, explain_strategy_automation
from ui.strategy_forms import render_parameter_inputs

_TAB_STATUSES = {
    "Active": [StrategyStatus.ACTIVE],
    "Drafts": [StrategyStatus.DRAFT],
    "Paused": [StrategyStatus.PAUSED],
    "Stopped": [StrategyStatus.STOPPED],
    "Archived": [StrategyStatus.ARCHIVED],
}


def _clear_lifecycle_session(strategy_id: int | None = None) -> None:
    keys = [k for k in st.session_state if k.startswith("lifecycle_")]
    for key in keys:
        if strategy_id is None or str(strategy_id) in key:
            st.session_state.pop(key, None)
    st.session_state.pop("lifecycle_pending", None)
    st.session_state.pop("lifecycle_details_id", None)


def _lifecycle_success(message: str) -> None:
    _clear_lifecycle_session()
    st.session_state["lifecycle_flash"] = message


def _render_flash() -> None:
    message = st.session_state.pop("lifecycle_flash", None)
    if message:
        st.success(message)


def _position_quantity_value(quantity) -> float:
    try:
        return float(quantity)
    except (TypeError, ValueError):
        return 0.0


def _has_open_position(metrics: dict) -> bool:
    return _position_quantity_value(metrics.get("position_quantity", 0)) > 0


def _strategy_metrics(database: DatabaseManager, strategy_id: int, symbol: str, asset_type: str = "STOCK") -> dict:
    if asset_type == "CRYPTO":
        pos = database.get_crypto_position(strategy_id, symbol)
        qty = pos["quantity_text"] if pos else "0"
    else:
        pos = database.get_strategy_position(strategy_id, symbol)
        qty = int(pos["quantity"]) if pos else 0
    open_orders = database.count_open_orders_for_strategy(strategy_id)
    return {"position_quantity": qty, "open_order_count": open_orders, "is_crypto": asset_type == "CRYPTO"}


def _render_adjust_allocation(
    strategy,
    strategy_service: StrategyService,
    allocation_manager: AllocationManager,
    database: DatabaseManager,
) -> None:
    if strategy.status == StrategyStatus.ARCHIVED:
        return

    is_crypto = getattr(strategy, "asset_type", "STOCK") == "CRYPTO"
    if is_crypto:
        crypto_ledger = CryptoStrategyLedger(database)
        available = crypto_ledger.get_available_usd(strategy.id)
        pool_remaining = allocation_manager.get_crypto_unallocated_capital()
    else:
        available = allocation_manager.get_strategy_available_cash(strategy.id)
        pool_remaining = allocation_manager.get_unallocated_capital()

    max_add = max(float(pool_remaining), 1.0)
    max_remove = max(float(min(available, strategy.allocated_funds)), 1.0)

    with st.expander("Adjust allocation", expanded=False):
        st.caption(
            f"Current allocation: {format_currency(decimal_to_float(strategy.allocated_funds))} | "
            f"Available cash: {format_currency(float(available))} | "
            f"Pool remaining: {format_currency(float(pool_remaining))}"
        )
        action = st.radio(
            "Action",
            ["Add funds", "Remove funds"],
            horizontal=True,
            key=f"alloc_action_{strategy.id}",
        )
        amount = st.number_input(
            "Amount ($)",
            min_value=1.0,
            max_value=max_add if action == "Add funds" else max_remove,
            value=min(1000.0, max_add if action == "Add funds" else max_remove),
            step=100.0 if not is_crypto else 10.0,
            key=f"alloc_amount_{strategy.id}",
        )
        if st.button("Apply allocation change", key=f"apply_alloc_{strategy.id}"):
            try:
                delta = to_decimal(amount)
                if action == "Add funds":
                    strategy_service.increase_allocation(strategy.id, delta)
                    _lifecycle_success(f"Added {format_currency(float(delta))} to {strategy.name}.")
                else:
                    strategy_service.decrease_allocation(strategy.id, delta)
                    _lifecycle_success(f"Removed {format_currency(float(delta))} from {strategy.name}.")
                st.rerun()
            except AllocationError as exc:
                st.error(str(exc))


def _render_strategy_row(
    database: DatabaseManager,
    lifecycle: StrategyLifecycleService,
    strategy_service: StrategyService,
    registry,
    ledger: StrategyLedger,
    strategy,
    tab_name: str,
    *,
    auto_settings=None,
    automation_service=None,
    allocation_manager: AllocationManager | None = None,
) -> None:
    meta = registry.get_metadata(strategy.strategy_type)
    asset_type = getattr(strategy, "asset_type", "STOCK")
    metrics = _strategy_metrics(database, strategy.id, strategy.symbol, asset_type)
    params = json.loads(strategy.parameters_json)

    st.markdown(
        f"**{strategy.name}** · ID `{strategy.id}` · {strategy.symbol} · "
        f"{getattr(strategy, 'asset_type', 'STOCK')} · {meta.display_name} · "
        f"**{strategy.status.value}**"
    )
    cols = st.columns(4)
    cols[0].caption(f"Allocation: {format_currency(decimal_to_float(strategy.allocated_funds))}")
    cols[1].caption(f"Position: {metrics['position_quantity']}")
    cols[2].caption(f"Open orders: {metrics['open_order_count']}")
    if auto_settings is not None:
        auto_label, auto_detail = explain_strategy_automation(strategy, auto_settings, registry)
        cols[3].caption(f"{auto_label} · Updated: {strategy.updated_at[:19]}")
        if auto_detail:
            st.caption(auto_detail)
    else:
        cols[3].caption(
            f"Automation: {'On' if strategy.automation_enabled else 'Off'} · "
            f"Updated: {strategy.updated_at[:19]}"
        )

    if st.button("View Details", key=f"view_details_{strategy.id}"):
        st.session_state["lifecycle_details_id"] = strategy.id
        st.rerun()

    is_crypto = asset_type == "CRYPTO"
    is_approved = (
        strategy.crypto_paper_trading_approved if is_crypto else strategy.paper_trading_approved
    )

    if not is_approved and strategy.status in (StrategyStatus.DRAFT, StrategyStatus.PAUSED):
        title = "Approve for Crypto Paper Trading" if is_crypto else "Approve for Paper Trading"
        phrase = CRYPTO_PAPER_APPROVAL_PHRASE if is_crypto else PAPER_APPROVAL_PHRASE
        with st.expander(title, expanded=False):
            r1 = st.checkbox("I reviewed the strategy rules", key=f"apr1_{strategy.id}")
            r2 = st.checkbox("I reviewed the historical backtest", key=f"apr2_{strategy.id}")
            r3 = st.checkbox(
                "I understand paper trading does not guarantee future performance",
                key=f"apr3_{strategy.id}",
            )
            r4 = st.checkbox(
                "I understand crypto markets operate continuously",
                key=f"apr4_{strategy.id}",
            ) if is_crypto else True
            r5 = st.checkbox(
                "I understand crypto volatility and partial-fill risk",
                key=f"apr5_{strategy.id}",
            ) if is_crypto else True
            phrase_input = st.text_input("Type approval phrase", key=f"apr_phrase_{strategy.id}")
            if st.button(f"Approve {strategy.name}", key=f"approve_{strategy.id}"):
                try:
                    if is_crypto:
                        strategy_service.approve_for_crypto_paper_trading(
                            strategy.id,
                            phrase_input,
                            reviewed_rules=r1,
                            reviewed_backtest=r2,
                            understood_disclaimer=r3,
                            understood_continuous=bool(r4),
                            understood_volatility=bool(r5),
                        )
                    else:
                        strategy_service.approve_for_paper_trading(
                            strategy.id,
                            phrase_input,
                            reviewed_rules=r1,
                            reviewed_backtest=r2,
                            understood_disclaimer=r3,
                        )
                    _lifecycle_success("Paper trading approved.")
                    st.rerun()
                except StrategyError as exc:
                    st.error(str(exc))
            st.caption(f"Type exactly: {phrase}")
            if is_crypto:
                st.caption("Run a matching backtest in Strategy Lab (CRYPTO, same symbol/parameters) first.")

    if (
        tab_name == "Active"
        and auto_settings is not None
        and automation_service is not None
        and can_enable_strategy_automation(strategy, auto_settings, registry)
    ):
        from automation.automation_service import STRATEGY_ENABLE_PHRASE

        with st.expander("Enable per-strategy automation", expanded=False):
            st.markdown(
                "Global automation is already **on**. Each strategy still requires its own "
                "approval before workers may submit orders for it."
            )
            ack = st.checkbox(
                "I understand this strategy may place paper orders automatically.",
                key=f"strat_auto_ack_{strategy.id}",
            )
            phrase = st.text_input("Confirmation phrase", key=f"strat_auto_phrase_{strategy.id}")
            if st.button(f"Enable automation for {strategy.name}", key=f"enable_auto_{strategy.id}"):
                if not ack:
                    st.error("Acknowledgment required.")
                elif phrase.strip() != STRATEGY_ENABLE_PHRASE:
                    st.error(f"Type exactly: {STRATEGY_ENABLE_PHRASE}")
                else:
                    try:
                        automation_service.enable_strategy_automation(strategy.id, phrase)
                        _lifecycle_success(f"Automation enabled for {strategy.name}.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
            st.caption(f"Type exactly: {STRATEGY_ENABLE_PHRASE}")

    if allocation_manager is not None:
        _render_adjust_allocation(strategy, strategy_service, allocation_manager, database)

    pending = st.session_state.get("lifecycle_pending")
    if pending and pending[1] == strategy.id:
        action = pending[0]
        if action == "pause":
            _render_pause_confirm(lifecycle, strategy, metrics)
        elif action == "stop":
            _render_stop_confirm(lifecycle, strategy)
        elif action == "archive":
            _render_archive_confirm(lifecycle, strategy)
        elif action == "delete":
            _render_delete_confirm(lifecycle, strategy_service, strategy)
        return

    btn_cols = st.columns(6)
    if tab_name == "Active":
        if btn_cols[0].button("Pause", key=f"pause_strategy_{strategy.id}"):
            st.session_state["lifecycle_pending"] = ("pause", strategy.id)
            st.rerun()
        if btn_cols[1].button("Stop", key=f"stop_strategy_{strategy.id}"):
            st.session_state["lifecycle_pending"] = ("stop", strategy.id)
            st.rerun()
    elif tab_name == "Drafts":
        if is_approved and btn_cols[0].button(
            "Activate", key=f"activate_strategy_{strategy.id}"
        ):
            try:
                lifecycle.activate_strategy(strategy.id)
                _lifecycle_success(f"Strategy {strategy.name} activated.")
                st.rerun()
            except QuantTradingError as exc:
                st.error(str(exc))
        if btn_cols[1].button("Archive", key=f"archive_strategy_{strategy.id}"):
            st.session_state["lifecycle_pending"] = ("archive", strategy.id)
            st.rerun()
        eligibility = strategy_service.get_deletion_eligibility(strategy.id)
        if eligibility.can_delete and btn_cols[2].button(
            "Delete Permanently", key=f"delete_strategy_{strategy.id}"
        ):
            st.session_state["lifecycle_pending"] = ("delete", strategy.id)
            st.rerun()
        elif not eligibility.can_delete:
            with st.expander("Why permanent delete is blocked"):
                for reason in eligibility.blocking_reasons:
                    st.write(f"- {reason}")
                st.info("Archive this strategy instead to preserve history.")
    elif tab_name == "Paused":
        if btn_cols[0].button("Resume", key=f"resume_strategy_{strategy.id}"):
            try:
                lifecycle.resume_strategy(strategy.id)
                _lifecycle_success(f"Strategy {strategy.name} resumed.")
                st.rerun()
            except QuantTradingError as exc:
                st.error(str(exc))
        if btn_cols[1].button("Stop", key=f"stop_strategy_{strategy.id}"):
            st.session_state["lifecycle_pending"] = ("stop", strategy.id)
            st.rerun()
        if btn_cols[2].button("Archive", key=f"archive_strategy_{strategy.id}"):
            st.session_state["lifecycle_pending"] = ("archive", strategy.id)
            st.rerun()
    elif tab_name == "Stopped":
        if btn_cols[0].button("Archive", key=f"archive_strategy_{strategy.id}"):
            st.session_state["lifecycle_pending"] = ("archive", strategy.id)
            st.rerun()
    elif tab_name == "Archived":
        if btn_cols[0].button("Restore as Draft", key=f"restore_strategy_{strategy.id}"):
            try:
                lifecycle.restore_strategy(strategy.id)
                _lifecycle_success(f"Strategy {strategy.name} restored as DRAFT.")
                st.rerun()
            except QuantTradingError as exc:
                st.error(str(exc))
        if btn_cols[1].button("View History", key=f"history_strategy_{strategy.id}"):
            st.session_state["lifecycle_details_id"] = strategy.id
            st.rerun()

    if _has_open_position(metrics) and strategy.status in (
        StrategyStatus.ACTIVE,
        StrategyStatus.PAUSED,
    ):
        st.caption("Open position present — pausing or stopping does not liquidate holdings.")


def _render_pause_confirm(lifecycle, strategy, metrics) -> None:
    st.warning(
        "Pausing this strategy stops new trading decisions. It does not close "
        "existing positions or cancel already submitted orders."
    )
    st.write(f"**Strategy:** {strategy.name}")
    st.write(f"**Symbol:** {strategy.symbol}")
    st.write(f"**Position quantity:** {metrics['position_quantity']}")
    st.write(f"**Open orders:** {metrics['open_order_count']}")
    c1, c2 = st.columns(2)
    if c1.button("Confirm Pause", key=f"confirm_pause_{strategy.id}"):
        try:
            lifecycle.pause_strategy(strategy.id)
            _lifecycle_success(f"Strategy {strategy.name} paused.")
            st.rerun()
        except QuantTradingError as exc:
            st.error(str(exc))
    if c2.button("Cancel", key=f"cancel_pause_{strategy.id}"):
        _clear_lifecycle_session(strategy.id)
        st.rerun()


def _render_stop_confirm(lifecycle, strategy) -> None:
    st.warning(
        "Stopping prevents future strategy activity. Existing positions and orders remain unchanged."
    )
    confirm = st.text_input("Type STOP to confirm", key=f"stop_confirm_text_{strategy.id}")
    c1, c2 = st.columns(2)
    if c1.button("Confirm Stop", key=f"confirm_stop_{strategy.id}"):
        if confirm.strip() != "STOP":
            st.error("Type STOP exactly to confirm.")
        else:
            try:
                lifecycle.stop_strategy(strategy.id)
                _lifecycle_success(f"Strategy {strategy.name} stopped.")
                st.rerun()
            except QuantTradingError as exc:
                st.error(str(exc))
    if c2.button("Cancel", key=f"cancel_stop_{strategy.id}"):
        _clear_lifecycle_session(strategy.id)
        st.rerun()


def _render_archive_confirm(lifecycle, strategy) -> None:
    confirm = st.text_input("Type ARCHIVE to confirm", key=f"archive_confirm_text_{strategy.id}")
    c1, c2 = st.columns(2)
    if c1.button("Confirm Archive", key=f"confirm_archive_{strategy.id}"):
        if confirm.strip() != "ARCHIVE":
            st.error("Type ARCHIVE exactly to confirm.")
        else:
            try:
                lifecycle.archive_strategy(strategy.id)
                _lifecycle_success(f"Strategy {strategy.name} archived.")
                st.rerun()
            except QuantTradingError as exc:
                st.error(str(exc))
    if c2.button("Cancel", key=f"cancel_archive_{strategy.id}"):
        _clear_lifecycle_session(strategy.id)
        st.rerun()


def _render_delete_confirm(lifecycle_service, strategy_service, strategy) -> None:
    eligibility = strategy_service.get_deletion_eligibility(strategy.id)
    if not eligibility.can_delete:
        st.error("This strategy has related history and cannot be permanently deleted. Archive it instead.")
        for reason in eligibility.blocking_reasons:
            st.write(f"- {reason}")
        if st.button("Cancel", key=f"cancel_delete_{strategy.id}"):
            _clear_lifecycle_session(strategy.id)
            st.rerun()
        return

    st.error(
        "This permanently deletes the unused strategy definition. This action cannot be undone."
    )
    confirm = st.text_input(
        "Type DELETE or the exact strategy name",
        key=f"delete_confirm_text_{strategy.id}",
    )
    c1, c2 = st.columns(2)
    if c1.button("Confirm Permanent Delete", key=f"confirm_delete_{strategy.id}"):
        if confirm.strip() not in ("DELETE", strategy.name):
            st.error("Type DELETE or the exact strategy name to confirm.")
        else:
            try:
                lifecycle_service.permanently_delete_strategy(strategy.id)
                _lifecycle_success(f"Strategy {strategy.name} permanently deleted.")
                st.rerun()
            except StrategyDeletionBlockedError as exc:
                st.error(str(exc))
    if c2.button("Cancel", key=f"cancel_delete_{strategy.id}"):
        _clear_lifecycle_session(strategy.id)
        st.rerun()


def _render_strategy_details(
    database: DatabaseManager,
    lifecycle: StrategyLifecycleService,
    strategy_service: StrategyService,
    registry,
    ledger: StrategyLedger,
    strategy_id: int,
    allocation_manager: AllocationManager,
) -> None:
    summary = lifecycle.get_strategy_summary(strategy_id)
    strategy = summary["strategy"]
    meta = registry.get_metadata(strategy.strategy_type)
    eligibility = summary["deletion_eligibility"]

    st.subheader(f"Strategy Details — {strategy.name}")
    st.write(f"**ID:** {strategy.id}")
    st.write(f"**Status:** {strategy.status.value}")
    st.write(f"**Type:** {meta.display_name}")
    st.write(f"**Symbol:** {strategy.symbol} ({getattr(strategy, 'asset_type', 'STOCK')})")
    st.write(f"**Parameters:** {json.loads(strategy.parameters_json)}")
    st.write(f"**Allocation:** {format_currency(decimal_to_float(strategy.allocated_funds))}")
    is_crypto = getattr(strategy, "asset_type", "STOCK") == "CRYPTO"
    if is_crypto:
        available = CryptoStrategyLedger(database).get_available_usd(strategy.id)
    else:
        available = ledger.get_available_cash(strategy.id)
    st.write(f"**Available cash:** {format_currency(float(available))}")
    st.write(f"**Paper approved:** {'Yes' if strategy.paper_trading_approved else 'No'}")
    if getattr(strategy, "asset_type", "STOCK") == "CRYPTO":
        st.write(
            f"**Crypto paper approved:** "
            f"{'Yes' if strategy.crypto_paper_trading_approved else 'No'}"
        )
    st.write(f"**Automation:** {'Yes' if strategy.automation_enabled else 'No'}")
    st.write(f"**Position quantity:** {summary['position_quantity']}")
    st.write(f"**Open orders:** {summary['open_order_count']}")

    _render_adjust_allocation(strategy, strategy_service, allocation_manager, database)

    st.markdown("**Status timestamps**")
    for label, value in (
        ("Created", strategy.created_at),
        ("Activated", strategy.activated_at),
        ("Paused", strategy.paused_at),
        ("Stopped", strategy.stopped_at),
        ("Archived", strategy.archived_at),
    ):
        if value:
            st.caption(f"{label}: {value}")

    orders = database.list_paper_orders(limit=20)
    strategy_orders = [o for o in orders if o.strategy_id == strategy.id]
    if strategy_orders:
        st.markdown("**Recent orders**")
        for order in strategy_orders[:5]:
            st.caption(f"{order.side} {order.quantity} {order.symbol} — {order.status}")

    latest = database.get_latest_signal(strategy.id)
    if latest:
        st.markdown("**Latest signal**")
        st.caption(f"{latest['signal']} @ {latest['signal_timestamp']}")

    events = summary["lifecycle_events"]
    if events:
        st.markdown("**Lifecycle timeline**")
        for event in reversed(events):
            st.caption(
                f"{event['created_at'][:19]} · {event['event_type']} · "
                f"{event.get('previous_status')} → {event.get('new_status')}"
            )

    st.markdown("**Deletion eligibility**")
    if eligibility.can_delete:
        st.success("This unused draft may be permanently deleted.")
    else:
        st.warning("Permanent deletion blocked:")
        for reason in eligibility.blocking_reasons:
            st.write(f"- {reason}")

    if st.button("Close Details", key="close_strategy_details"):
        st.session_state.pop("lifecycle_details_id", None)
        st.rerun()


def render(database: DatabaseManager) -> None:
    """Render strategy creation and lifecycle management."""
    settings = get_settings()
    allocation_manager = AllocationManager(database, settings)
    strategy_service = StrategyService(database)
    lifecycle = StrategyLifecycleService(database)
    registry = get_registry()
    ledger = StrategyLedger(database)
    auto_settings = database.get_automation_settings()
    automation_service = None
    try:
        from automation.automation_service import AutomationService
        from broker.alpaca_order_manager import AlpacaPaperOrderManager
        from data.alpaca_data import AlpacaMarketDataProvider

        order_manager = None
        data_provider = None
        if settings.alpaca_configured:
            order_manager = AlpacaPaperOrderManager(
                settings.alpaca_api_key, settings.alpaca_secret_key
            )
            data_provider = AlpacaMarketDataProvider(
                settings.alpaca_api_key, settings.alpaca_secret_key
            )
        automation_service = AutomationService(
            database, order_manager, data_provider, settings
        )
    except Exception:
        automation_service = None

    st.title("Strategies")
    _render_flash()
    st.markdown(
        "Manage strategy lifecycle: **Draft → Active ↔ Paused → Stop/Archive**. "
        "Only **one ACTIVE strategy** may trade a given asset and symbol."
    )
    if auto_settings.automated_paper_trading_enabled:
        st.info(
            "**Global automation is ON** — that only allows workers to run. Each **ACTIVE** stock "
            "strategy still shows **Per-strategy: Off** until you enable it separately "
            "(below or on the Automation page)."
        )

    unallocated = allocation_manager.get_unallocated_capital()
    crypto_remaining = float(allocation_manager.get_crypto_unallocated_capital())
    pool_label = allocation_manager.capital_source_label
    st.info(
        f"Capital source: **{pool_label}** | "
        f"Available for allocation: {format_currency(float(allocation_manager.get_unallocated_capital()))} | "
        f"Crypto remaining: {format_currency(max(crypto_remaining, 0.0))}"
    )
    if allocation_manager.uses_alpaca_capital:
        st.caption(
            "Strategy allocations are capped by your Alpaca paper account cash. "
            "Orders submit to Alpaca paper; the local ledger tracks each strategy's slice."
        )
    else:
        st.caption(
            "Using local virtual pool. Set PAPER_CAPITAL_SOURCE=alpaca in .env to use Alpaca paper cash instead."
        )

    details_id = st.session_state.get("lifecycle_details_id")
    if details_id:
        _render_strategy_details(
            database, lifecycle, strategy_service, registry, ledger, int(details_id), allocation_manager
        )
        return

    with st.expander("Create Strategy", expanded=False):
        strategy_type = st.selectbox(
            "Strategy Type",
            registry.list_strategy_types(),
            format_func=lambda t: registry.get_metadata(t).display_name,
        )
        meta = registry.get_metadata(strategy_type)
        st.write(meta.description)
        st.caption(f"Category: {meta.category.value}")

        with st.form("create_strategy_form"):
            asset_class = st.selectbox("Asset Class", ["Stock", "Crypto"])
            name = st.text_input(
                "Strategy Name",
                placeholder="BTC MA Crossover" if asset_class == "Crypto" else "SPY RSI Recovery",
            )
            symbol_default = "BTC/USD" if asset_class == "Crypto" else "SPY"
            symbol = st.text_input("Symbol", value=symbol_default).strip()
            params = render_parameter_inputs(meta.parameter_definitions, "create")
            if asset_class == "Crypto":
                allocation = st.number_input(
                    "Fund Allocation ($)",
                    min_value=1.0,
                    max_value=max(crypto_remaining, 1.0),
                    value=min(500.0, max(crypto_remaining, 1.0)),
                    step=10.0,
                )
            else:
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
            save_draft = st.form_submit_button("Save as Draft")

        if save_draft:
            try:
                if asset_class == "Crypto":
                    strategy_id = strategy_service.create_crypto_strategy(
                        name=name,
                        symbol=symbol,
                        strategy_type=strategy_type,
                        parameters=params,
                        allocated_funds=to_decimal(allocation),
                        cash_reserve_percent=to_decimal(cash_reserve) / Decimal("100"),
                        entry_policy=EntryPolicy(entry_policy),
                    )
                else:
                    strategy_id = strategy_service.create_strategy(
                        name=name,
                        symbol=symbol.upper(),
                        strategy_type=strategy_type,
                        parameters=params,
                        allocated_funds=to_decimal(allocation),
                        cash_reserve_percent=to_decimal(cash_reserve) / Decimal("100"),
                        entry_policy=EntryPolicy(entry_policy),
                        activate=False,
                    )
                _lifecycle_success(f"Strategy created as DRAFT (ID {strategy_id}).")
                st.rerun()
            except (StrategyError, QuantTradingError) as exc:
                st.error(str(exc))

    tabs = st.tabs(list(_TAB_STATUSES.keys()))
    for tab, (tab_name, statuses) in zip(tabs, _TAB_STATUSES.items()):
        with tab:
            strategies = lifecycle.list_strategies(statuses=statuses, include_archived=True)
            if not strategies:
                st.info(f"No {tab_name.lower()} strategies.")
                continue
            for strategy in strategies:
                with st.container(border=True):
                    _render_strategy_row(
                        database,
                        lifecycle,
                        strategy_service,
                        registry,
                        ledger,
                        strategy,
                        tab_name,
                        auto_settings=auto_settings,
                        automation_service=automation_service,
                        allocation_manager=allocation_manager,
                    )

    st.subheader("Strategy Reference")
    for stype in registry.list_strategy_types():
        m = registry.get_metadata(stype)
        st.markdown(f"**{m.display_name}** ({m.category.value}): {m.description}")

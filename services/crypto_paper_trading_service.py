"""Crypto paper trading orchestration."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from broker.crypto_asset_service import CryptoAssetService
from broker.crypto_order_manager import AlpacaCryptoPaperOrderManager
from config.settings import Settings, get_settings
from core.asset_models import AssetType, CryptoFeeStatus, CryptoOrderSizingMode
from core.client_order_id import build_crypto_client_order_id
from core.crypto_decimal import floor_to_increment, format_decimal, parse_decimal
from core.exceptions import PaperTradingError
from core.models import CryptoConfirmationData, OrderProposalStatus, SignalType, StrategyStatus, to_decimal
from data.database import DatabaseManager
from market_data.cache_service import HistoricalDataCacheService
from market_data.factory import build_market_data_stack
from market_data.models import DataTimeframe
from portfolio.allocation_manager import AllocationManager
from portfolio.crypto_ledger import CryptoStrategyLedger
from services.crypto_ema_evaluation import (
    build_risk_sizing_context,
    evaluate_crypto_ema_strategy,
)
from services.crypto_fee_service import CryptoFeeService
from services.crypto_reconciliation_service import CryptoReconciliationService
from strategies.capabilities import get_risk_overlay, has_risk_overlay
from strategies.registry import get_registry

logger = logging.getLogger(__name__)

_TERMINAL = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}


class CryptoPaperTradingService:
    """Manual crypto paper trading workflow."""

    def __init__(
        self,
        database: DatabaseManager,
        order_manager: AlpacaCryptoPaperOrderManager,
        asset_service: CryptoAssetService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._db = database
        self._orders = order_manager
        self._settings = settings or get_settings()
        self._assets = asset_service or CryptoAssetService(
            self._settings.alpaca_api_key,
            self._settings.alpaca_secret_key,
            self._settings,
        )
        self._ledger = CryptoStrategyLedger(database)
        self._fees = CryptoFeeService(self._settings)
        self._reconciliation = CryptoReconciliationService(database, order_manager, self._assets)
        self._registry = get_registry()
        _, self._cache, _ = build_market_data_stack(database, self._settings)

    def evaluate_strategy(self, strategy_id: int) -> dict:
        strategy = self._require_crypto_strategy(strategy_id)
        bars = self._load_bars(strategy.symbol)
        if bars.empty:
            raise PaperTradingError("No cached crypto data available for signal evaluation.")

        local_position = self._db.get_crypto_position(strategy.id, strategy.symbol)
        params = json.loads(strategy.parameters_json)

        if strategy.strategy_type == "crypto_ema_trend_following":
            evaluation = evaluate_crypto_ema_strategy(
                strategy.strategy_type,
                params,
                bars,
                local_position,
            )
            evaluation["strategy_id"] = strategy_id
            evaluation["symbol"] = strategy.symbol
            return evaluation

        strategy_impl = self._registry.build(strategy.strategy_type, params)
        if bars.index.tz is not None:
            bars = bars.copy()
            bars.index = bars.index.tz_localize(None)
        processed = strategy_impl.generate_signals(bars)
        latest = processed.iloc[-1]
        signal = SignalType(latest["Signal"])
        eval_result = strategy_impl.get_current_evaluation(processed)
        explanation = getattr(eval_result, "explanation", str(eval_result))
        return {
            "strategy_id": strategy_id,
            "symbol": strategy.symbol,
            "signal": signal.value,
            "signal_reason": str(latest.get("SignalReason", "HOLD")),
            "signal_timestamp": processed.index[-1].isoformat(),
            "close_price": float(latest["Close"]),
            "explanation": explanation,
            "is_actionable": signal in (SignalType.BUY, SignalType.SELL),
        }

    def build_order_proposal(self, strategy_id: int) -> dict:
        strategy = self._require_crypto_strategy(strategy_id)
        evaluation = self.evaluate_strategy(strategy_id)
        blocking = self._common_blockers(strategy)
        asset_validation = self._assets.validate_pair(strategy.symbol)
        if not asset_validation.is_valid:
            blocking.extend(asset_validation.messages)

        side = evaluation["signal"]
        signal_reason = evaluation.get("signal_reason", "HOLD")
        if side not in ("BUY", "SELL"):
            blocking.append("Latest signal is HOLD.")
        blocking.extend(evaluation.get("blocking", []))

        rules = asset_validation.rules
        local_position = self._db.get_crypto_position(strategy.id, strategy.symbol)
        local_qty = parse_decimal(local_position["quantity_text"]) if local_position else Decimal("0")
        available_usd = self._ledger.get_available_usd(strategy.id)
        close_price = parse_decimal(evaluation["close_price"])
        estimated_price = close_price * (Decimal("1") + self._settings.crypto_estimated_price_buffer_percent)
        sizing_mode = CryptoOrderSizingMode.NOTIONAL if side == "BUY" else CryptoOrderSizingMode.QUANTITY
        notional = Decimal("0")
        quantity = Decimal("0")
        estimated_base_qty = Decimal("0")
        estimated_fee, fee_currency = self._fees.estimate_fee(Decimal("0"))
        risk_info = None

        if side == "BUY":
            blocking.extend(self._validate_buy(strategy, local_qty, available_usd))
            strategy_impl = self._registry.build(
                strategy.strategy_type, json.loads(strategy.parameters_json)
            )
            if has_risk_overlay(strategy_impl):
                position_value = local_qty * close_price if local_qty > 0 else Decimal("0")
                strategy_equity = self._ledger.get_usd_balance(strategy.id) + position_value
                broker_bp = None
                try:
                    acct = self._orders.get_account_summary()
                    broker_bp = parse_decimal(acct.get("buying_power", 0))
                except Exception:
                    broker_bp = None
                sizing_ctx = build_risk_sizing_context(
                    strategy_impl,
                    strategy_equity=strategy_equity,
                    available_usd=available_usd,
                    cash_reserve_percent=strategy.cash_reserve_percent,
                    allocation_limit=strategy.allocated_funds,
                    application_max_notional=to_decimal(self._settings.max_crypto_paper_order_notional),
                    broker_buying_power=broker_bp,
                    minimum_order_notional=rules.minimum_order_size if rules else None,
                )
                blocking.extend(sizing_ctx["blocking_reasons"])
                notional = parse_decimal(sizing_ctx["final_notional"])
                risk_info = sizing_ctx
            else:
                spendable = available_usd * (Decimal("1") - strategy.cash_reserve_percent)
                notional = min(
                    spendable,
                    self._settings.max_crypto_paper_order_notional,
                )
                risk_info = None
            if rules and rules.minimum_order_size and notional < rules.minimum_order_size:
                blocking.append("Proposed notional below minimum order size.")
            if estimated_price > 0:
                estimated_base_qty = notional / estimated_price
                if rules and rules.minimum_trade_increment:
                    estimated_base_qty = floor_to_increment(estimated_base_qty, rules.minimum_trade_increment)
            estimated_fee, fee_currency = self._fees.estimate_fee(notional)
            if notional <= 0:
                blocking.append("Insufficient USD cash for crypto BUY.")
        else:
            blocking.extend(self._validate_sell(strategy, local_qty))
            quantity = local_qty
            if rules and rules.minimum_trade_increment:
                quantity = floor_to_increment(quantity, rules.minimum_trade_increment)
            if quantity <= 0:
                blocking.append("No sellable crypto quantity.")
            broker_pos = self._orders.get_crypto_position(strategy.symbol)
            broker_qty = parse_decimal(broker_pos["quantity"]) if broker_pos else Decimal("0")
            if quantity > broker_qty:
                blocking.append("Sell quantity exceeds broker position.")

        signal_ts = datetime.fromisoformat(evaluation["signal_timestamp"])
        client_order_id = build_crypto_client_order_id(
            self._settings.client_order_id_prefix,
            strategy.id,
            strategy.symbol,
            side,
            signal_ts,
            signal_reason=signal_reason if side in ("BUY", "SELL") else None,
        )
        if self._db.get_proposal_by_client_order_id(client_order_id):
            blocking.append("Duplicate proposal for this signal.")

        expires_at = datetime.now(timezone.utc) + timedelta(hours=self._settings.proposal_expiry_hours)
        status = OrderProposalStatus.PROPOSED.value if not blocking else OrderProposalStatus.BLOCKED.value
        total_cash_required = notional + estimated_fee if side == "BUY" else Decimal("0")
        proposal = {
            "proposal_id": str(uuid.uuid4()),
            "strategy_id": strategy.id,
            "strategy_name": strategy.name,
            "symbol": strategy.symbol,
            "signal": side,
            "signal_timestamp": signal_ts.isoformat(),
            "side": side,
            "status": status,
            "blocking_reasons": blocking,
            "validation_messages": [],
            "client_order_id": client_order_id,
            "sizing_mode": sizing_mode.value,
            "notional_text": format_decimal(notional),
            "quantity_text": format_decimal(quantity),
            "estimated_price_text": format_decimal(estimated_price),
            "estimated_base_quantity_text": format_decimal(estimated_base_qty),
            "estimated_fee_text": format_decimal(estimated_fee),
            "estimated_fee_currency": fee_currency,
            "estimated_notional": float(notional),
            "estimated_price": float(estimated_price),
            "estimated_total_cash_required": format_decimal(total_cash_required),
            "time_in_force": self._settings.crypto_default_time_in_force,
            "expires_at": expires_at.isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "explanation": evaluation.get("explanation", ""),
            "signal_reason": signal_reason,
        }
        if side == "BUY" and risk_info is not None:
            proposal["risk_sizing"] = {
                k: risk_info[k]
                for k in (
                    "strategy_equity",
                    "risk_percent",
                    "risk_budget",
                    "stop_loss_percent",
                    "risk_based_notional",
                    "available_cash",
                    "cash_reserve",
                    "maximum_order_notional",
                    "final_notional",
                    "warnings",
                )
            }
            overlay = get_risk_overlay(
                self._registry.build(strategy.strategy_type, json.loads(strategy.parameters_json))
            )
            est_entry = estimated_price
            proposal["planned_stop_price_text"] = format_decimal(
                overlay.stop_loss.calculate_stop_price(est_entry)
            )
        self._db.save_crypto_proposal(proposal)
        return proposal

    def confirm_proposal(self, proposal_id: str, confirmation: CryptoConfirmationData) -> None:
        row = self._db.get_proposal(proposal_id)
        if row is None or row.get("asset_type") != "CRYPTO":
            raise PaperTradingError("Crypto proposal not found.")
        blocking = json.loads(row.get("blocking_reasons_json") or "[]")
        if blocking:
            raise PaperTradingError("Proposal has blocking reasons.")
        if row["status"] not in (OrderProposalStatus.PROPOSED.value, OrderProposalStatus.BLOCKED.value):
            raise PaperTradingError(f"Proposal status is {row['status']}.")
        if not (
            confirmation.paper_trading_acknowledged
            and confirmation.details_reviewed
            and confirmation.continuous_market_acknowledged
        ):
            raise PaperTradingError("All confirmation checkboxes are required.")
        if confirmation.paper_text.strip().upper() != "PAPER CRYPTO":
            raise PaperTradingError("Confirmation text must be exactly PAPER CRYPTO.")
        self._assert_kill_switch_off()
        self._db.update_proposal_status(
            proposal_id,
            OrderProposalStatus.CONFIRMED,
            confirmed_at=datetime.now(timezone.utc).isoformat(),
        )

    def submit_confirmed_proposal(self, proposal_id: str) -> dict:
        self._assert_trading_enabled()
        self._assert_kill_switch_off()
        row = self._db.get_proposal(proposal_id)
        if row is None or row.get("asset_type") != "CRYPTO":
            raise PaperTradingError("Crypto proposal not found.")
        if row["status"] != OrderProposalStatus.CONFIRMED.value:
            raise PaperTradingError("Proposal must be CONFIRMED before submission.")
        if self._reconciliation.has_critical_issues():
            raise PaperTradingError("Critical crypto reconciliation issues block submission.")

        client_order_id = row["client_order_id"]
        existing = self._db.get_paper_order_by_client_id(client_order_id)
        if existing:
            return self.synchronize_order(existing.id)

        existing_alpaca = self._orders.get_crypto_order_by_client_order_id(client_order_id)
        if existing_alpaca:
            order_id = self._db.save_crypto_paper_order(
                {
                    "strategy_id": row["strategy_id"],
                    "proposal_id": proposal_id,
                    "client_order_id": client_order_id,
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "status": str(existing_alpaca["status"]).upper(),
                    "alpaca_order_id": existing_alpaca["alpaca_order_id"],
                    "submitted_at": existing_alpaca.get("submitted_at"),
                    "notional_text": row.get("notional_text"),
                    "requested_quantity_text": row.get("quantity_text"),
                    "time_in_force": row.get("time_in_force", "gtc"),
                }
            )
            return self.synchronize_order(order_id)

        side = row["side"].upper()
        if side == "BUY":
            result = self._orders.submit_crypto_market_buy(
                row["symbol"],
                parse_decimal(row.get("notional_text", "0")),
                client_order_id,
                time_in_force=row.get("time_in_force", "gtc"),
            )
            notional = row.get("notional_text")
            qty_text = None
        else:
            result = self._orders.submit_crypto_market_sell(
                row["symbol"],
                parse_decimal(row.get("quantity_text", "0")),
                client_order_id,
                time_in_force=row.get("time_in_force", "gtc"),
            )
            notional = None
            qty_text = row.get("quantity_text")

        order_id = self._db.save_crypto_paper_order(
            {
                "strategy_id": row["strategy_id"],
                "proposal_id": proposal_id,
                "client_order_id": client_order_id,
                "symbol": row["symbol"],
                "side": side,
                "status": str(result["status"]).upper(),
                "alpaca_order_id": result["alpaca_order_id"],
                "submitted_at": result.get("submitted_at"),
                "notional_text": notional,
                "requested_quantity_text": qty_text,
                "time_in_force": row.get("time_in_force", "gtc"),
            }
        )
        if side == "BUY":
            reserve = parse_decimal(row.get("notional_text", "0")) + parse_decimal(
                row.get("estimated_fee_text", "0")
            )
            self._ledger.reserve_usd(row["strategy_id"], row["symbol"], reserve, proposal_id)
        self._db.update_proposal_status(
            proposal_id,
            OrderProposalStatus.SUBMITTED,
            submitted_at=datetime.now(timezone.utc).isoformat(),
        )
        return self.synchronize_order(order_id)

    def synchronize_order(self, order_id: int) -> dict:
        with self._db.connect() as connection:
            row = connection.execute("SELECT * FROM paper_orders WHERE id = ?", (order_id,)).fetchone()
        if not row:
            raise PaperTradingError("Order not found.")
        row = dict(row)
        alpaca_id = row.get("alpaca_order_id")
        if not alpaca_id:
            existing = self._orders.get_crypto_order_by_client_order_id(row["client_order_id"])
            alpaca_id = existing["alpaca_order_id"] if existing else None
        if not alpaca_id:
            raise PaperTradingError("No Alpaca order ID for synchronization.")
        broker = self._orders.synchronize_crypto_order(alpaca_id)
        self._apply_fill(order_id, broker, row)
        return broker

    def get_status_summary(self) -> dict:
        recon = self._reconciliation.run_reconciliation(persist=False)
        return {
            "crypto_paper_trading_enabled": self._settings.crypto_paper_trading_enabled,
            "crypto_kill_switch_engaged": self._settings.crypto_kill_switch_engaged,
            "supported_pairs": [a["symbol"] for a in self._orders.get_active_crypto_assets()],
            "reconciliation_warnings": recon.warnings,
            "reconciliation_critical": recon.critical,
        }

    def _apply_fill(self, order_id: int, broker: dict, row: dict) -> None:
        status = str(broker["status"]).upper().replace("ORDERSTATUS.", "")
        filled_qty = parse_decimal(broker.get("filled_quantity", "0"))
        prev_processed = parse_decimal(row.get("last_processed_filled_quantity_text") or "0")
        delta = filled_qty - prev_processed
        avg_price = parse_decimal(broker.get("filled_average_price") or "0")
        connection_fields = {
            "status": status,
            "filled_quantity_text": format_decimal(filled_qty),
            "filled_average_price_text": format_decimal(avg_price) if avg_price > 0 else None,
            "last_processed_filled_quantity_text": format_decimal(filled_qty),
        }
        self._db.update_paper_order(order_id, **{k: v for k, v in connection_fields.items() if v is not None})

        if delta <= 0:
            if status in ("CANCELED", "REJECTED") and row["side"].upper() == "BUY":
                reserve = parse_decimal(row.get("notional_text") or "0")
                self._ledger.release_reserved_usd(row["strategy_id"], row["symbol"], reserve, str(order_id))
            return

        fill_ref = f"{order_id}-{format_decimal(filled_qty)}"
        if row["side"].upper() == "BUY":
            notional = delta * avg_price if avg_price > 0 else parse_decimal(row.get("notional_text") or "0")
            self._ledger.record_buy_fill(row["strategy_id"], row["symbol"], notional, delta, fill_ref)
            reserve = parse_decimal(row.get("notional_text") or "0")
            self._ledger.release_reserved_usd(row["strategy_id"], row["symbol"], reserve, str(order_id))
            self._update_position_buy(row["strategy_id"], row["symbol"], delta, avg_price, row)
        else:
            proceeds = delta * avg_price
            self._ledger.record_sell_fill(row["strategy_id"], row["symbol"], delta, proceeds, fill_ref)
            self._update_position_sell(row["strategy_id"], row["symbol"], delta, avg_price, proceeds)

    def _update_position_buy(
        self, strategy_id: int, symbol: str, qty: Decimal, price: Decimal, order_row: dict | None = None
    ) -> None:
        pos = self._db.get_crypto_position(strategy_id, symbol)
        old_qty = parse_decimal(pos["quantity_text"]) if pos else Decimal("0")
        old_cost = parse_decimal(pos["cost_basis_usd_text"]) if pos else Decimal("0")
        new_qty = old_qty + qty
        new_cost = old_cost + qty * price
        avg = new_cost / new_qty if new_qty > 0 else Decimal("0")
        realized = parse_decimal(pos["realized_profit_loss_usd_text"]) if pos else Decimal("0")

        entry_price_text = format_decimal(price)
        stop_price_text = None
        stop_loss_percent_text = None
        risk_budget_text = None
        initial_notional_text = format_decimal(qty * price)
        entry_filled_at = datetime.now(timezone.utc).isoformat()

        strategy = self._db.get_strategy(strategy_id)
        if strategy and strategy.strategy_type == "crypto_ema_trend_following":
            params = json.loads(strategy.parameters_json)
            impl = self._registry.build(strategy.strategy_type, params)
            if has_risk_overlay(impl):
                overlay = get_risk_overlay(impl)
                stop_price_text = format_decimal(overlay.stop_loss.calculate_stop_price(price))
                stop_loss_percent_text = format_decimal(overlay.stop_loss.stop_loss_percent)
                equity = self._ledger.get_usd_balance(strategy_id) + new_qty * price
                sizing = overlay.position_sizer.calculate(
                    strategy_equity=equity,
                    available_usd=self._ledger.get_available_usd(strategy_id),
                    cash_reserve_percent=strategy.cash_reserve_percent,
                    strategy_allocation_limit=strategy.allocated_funds,
                    application_max_order_notional=to_decimal(
                        self._settings.max_crypto_paper_order_notional
                    ),
                )
                risk_budget_text = format_decimal(sizing.risk_budget)

        self._db.upsert_crypto_position(
            strategy_id,
            symbol,
            new_qty,
            avg,
            new_cost,
            realized,
            entry_price_text=entry_price_text,
            stop_price_text=stop_price_text,
            stop_loss_percent_text=stop_loss_percent_text,
            risk_budget_text=risk_budget_text,
            initial_position_notional_text=initial_notional_text,
            entry_filled_at=entry_filled_at,
        )

    def _update_position_sell(
        self, strategy_id: int, symbol: str, qty: Decimal, price: Decimal, proceeds: Decimal
    ) -> None:
        pos = self._db.get_crypto_position(strategy_id, symbol)
        if not pos:
            return
        old_qty = parse_decimal(pos["quantity_text"])
        old_cost = parse_decimal(pos["cost_basis_usd_text"])
        avg = parse_decimal(pos["average_entry_price_text"])
        cost_removed = avg * qty
        new_qty = old_qty - qty
        new_cost = max(old_cost - cost_removed, Decimal("0"))
        realized = parse_decimal(pos["realized_profit_loss_usd_text"]) + (proceeds - cost_removed)
        self._db.upsert_crypto_position(
            strategy_id,
            symbol,
            max(new_qty, Decimal("0")),
            avg if new_qty > 0 else Decimal("0"),
            new_cost,
            realized,
            clear_risk_fields=new_qty <= 0,
        )

    def _common_blockers(self, strategy) -> list[str]:
        blocking: list[str] = []
        if self._settings.trading_mode != "paper":
            blocking.append("Trading mode is not paper.")
        if self._settings.live_trading_enabled:
            blocking.append("Live trading is enabled (blocked).")
        if not self._settings.crypto_paper_trading_enabled:
            blocking.append("Crypto paper trading is disabled.")
        if self._settings.crypto_kill_switch_engaged:
            blocking.append("Crypto kill switch is engaged.")
        if not self._settings.alpaca_configured:
            blocking.append("Alpaca credentials are not configured.")
        if strategy.status != StrategyStatus.ACTIVE:
            blocking.append(f"Strategy status is {strategy.status.value}.")
        if not strategy.crypto_paper_trading_approved:
            blocking.append("Strategy is not approved for crypto paper trading.")
        if (
            strategy.automation_enabled
            and not self._settings.crypto_automation_enabled
        ):
            blocking.append("Strategy automation is enabled but global crypto automation is off.")
        if self._db.get_active_proposal_for_strategy(strategy.id):
            blocking.append("An active proposal already exists.")
        if self._db.count_unknown_crypto_orders() > 0:
            blocking.append("Unknown crypto order exists.")
        active = self._db.get_active_crypto_strategy_for_symbol(strategy.symbol)
        if active and active["id"] != strategy.id:
            blocking.append("Another active strategy manages this crypto pair.")
        total_alloc = self._db.get_total_allocated_funds()
        pool = AllocationManager(self._db, self._settings).capital_pool
        if total_alloc > pool:
            blocking.append("Total strategy allocations exceed available paper capital.")
        return blocking

    def _validate_buy(self, strategy, local_qty: Decimal, available_usd: Decimal) -> list[str]:
        errors: list[str] = []
        if local_qty > 0:
            errors.append("Strategy already holds a local crypto position.")
        if available_usd <= 0:
            errors.append("No available strategy USD cash.")
        return errors

    def _validate_sell(self, strategy, local_qty: Decimal) -> list[str]:
        if local_qty <= 0:
            return ["No local crypto position to sell."]
        return []

    def _require_crypto_strategy(self, strategy_id: int):
        strategy = self._db.get_strategy(strategy_id)
        if strategy is None:
            raise PaperTradingError("Strategy not found.")
        if getattr(strategy, "asset_type", "STOCK") != AssetType.CRYPTO.value:
            raise PaperTradingError("Strategy is not a crypto strategy.")
        return strategy

    def _load_bars(self, symbol: str):
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=365)
        result = self._cache.get_or_download(
            AssetType.CRYPTO, symbol, DataTimeframe.DAY, start, end
        )
        return result.data

    def _assert_trading_enabled(self) -> None:
        if not self._settings.crypto_paper_trading_enabled:
            raise PaperTradingError("Crypto paper trading is disabled.")

    def _assert_kill_switch_off(self) -> None:
        if self._settings.crypto_kill_switch_engaged:
            raise PaperTradingError("Crypto kill switch is engaged.")

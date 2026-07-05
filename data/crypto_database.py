"""Database operations for crypto paper trading."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.asset_models import CryptoFeeStatus, CryptoLedgerEntryType
from core.crypto_decimal import format_decimal, parse_decimal


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CryptoDatabaseMixin:
    """Crypto ledger, positions, and reconciliation persistence."""

    def append_crypto_ledger_entry(
        self,
        strategy_id: int,
        entry_type: CryptoLedgerEntryType,
        symbol: str,
        currency: str,
        amount: Decimal,
        balance_after: Decimal,
        description: str,
        *,
        reference_type: str | None = None,
        reference_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO crypto_strategy_ledger (
                    strategy_id, entry_type, symbol, currency, amount_text,
                    balance_after_text, reference_type, reference_id,
                    idempotency_key, description, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_id,
                    entry_type.value,
                    symbol,
                    currency,
                    format_decimal(amount),
                    format_decimal(balance_after),
                    reference_type,
                    reference_id,
                    idempotency_key,
                    description,
                    _utc_now(),
                ),
            )

    def get_crypto_ledger_entries(self, strategy_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM crypto_strategy_ledger
                WHERE strategy_id = ?
                ORDER BY created_at, id
                """,
                (strategy_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_crypto_position(self, strategy_id: int, symbol: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM crypto_strategy_positions
                WHERE strategy_id = ? AND symbol = ?
                """,
                (strategy_id, symbol),
            ).fetchone()
        return dict(row) if row else None

    def upsert_crypto_position(
        self,
        strategy_id: int,
        symbol: str,
        quantity: Decimal,
        average_entry_price: Decimal,
        cost_basis_usd: Decimal,
        realized_profit_loss_usd: Decimal,
        *,
        entry_price_text: str | None = None,
        stop_price_text: str | None = None,
        stop_loss_percent_text: str | None = None,
        risk_budget_text: str | None = None,
        initial_position_notional_text: str | None = None,
        entry_filled_at: str | None = None,
        clear_risk_fields: bool = False,
    ) -> None:
        now = _utc_now()
        existing = self.get_crypto_position(strategy_id, symbol)
        with self.connect() as connection:
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO crypto_strategy_positions (
                        strategy_id, symbol, quantity_text, average_entry_price_text,
                        cost_basis_usd_text, realized_profit_loss_usd_text, updated_at,
                        entry_price_text, stop_price_text, stop_loss_percent_text,
                        risk_budget_text, initial_position_notional_text, entry_filled_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        strategy_id,
                        symbol,
                        format_decimal(quantity),
                        format_decimal(average_entry_price),
                        format_decimal(cost_basis_usd),
                        format_decimal(realized_profit_loss_usd),
                        now,
                        entry_price_text,
                        stop_price_text,
                        stop_loss_percent_text,
                        risk_budget_text,
                        initial_position_notional_text,
                        entry_filled_at,
                    ),
                )
                return

            if clear_risk_fields:
                entry_price_text = None
                stop_price_text = None
                stop_loss_percent_text = None
                risk_budget_text = None
                initial_position_notional_text = None
                entry_filled_at = None
            else:
                entry_price_text = (
                    entry_price_text if entry_price_text is not None else existing.get("entry_price_text")
                )
                stop_price_text = (
                    stop_price_text if stop_price_text is not None else existing.get("stop_price_text")
                )
                stop_loss_percent_text = (
                    stop_loss_percent_text
                    if stop_loss_percent_text is not None
                    else existing.get("stop_loss_percent_text")
                )
                risk_budget_text = (
                    risk_budget_text if risk_budget_text is not None else existing.get("risk_budget_text")
                )
                initial_position_notional_text = (
                    initial_position_notional_text
                    if initial_position_notional_text is not None
                    else existing.get("initial_position_notional_text")
                )
                entry_filled_at = (
                    entry_filled_at if entry_filled_at is not None else existing.get("entry_filled_at")
                )

            connection.execute(
                """
                UPDATE crypto_strategy_positions SET
                    quantity_text = ?,
                    average_entry_price_text = ?,
                    cost_basis_usd_text = ?,
                    realized_profit_loss_usd_text = ?,
                    updated_at = ?,
                    entry_price_text = ?,
                    stop_price_text = ?,
                    stop_loss_percent_text = ?,
                    risk_budget_text = ?,
                    initial_position_notional_text = ?,
                    entry_filled_at = ?
                WHERE strategy_id = ? AND symbol = ?
                """,
                (
                    format_decimal(quantity),
                    format_decimal(average_entry_price),
                    format_decimal(cost_basis_usd),
                    format_decimal(realized_profit_loss_usd),
                    now,
                    entry_price_text,
                    stop_price_text,
                    stop_loss_percent_text,
                    risk_budget_text,
                    initial_position_notional_text,
                    entry_filled_at,
                    strategy_id,
                    symbol,
                ),
            )

    def list_crypto_positions(self, strategy_id: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM crypto_strategy_positions"
        params: tuple = ()
        if strategy_id is not None:
            query += " WHERE strategy_id = ?"
            params = (strategy_id,)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def save_crypto_reconciliation_run(
        self,
        run_id: str,
        status: str,
        local_position_count: int,
        alpaca_position_count: int,
        warning_count: int,
        critical_count: int,
        summary: dict,
        *,
        completed: bool = True,
    ) -> None:
        now = _utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO crypto_reconciliation_runs (
                    run_id, status, local_position_count, alpaca_position_count,
                    warning_count, critical_count, summary_json, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status = excluded.status,
                    warning_count = excluded.warning_count,
                    critical_count = excluded.critical_count,
                    summary_json = excluded.summary_json,
                    completed_at = excluded.completed_at
                """,
                (
                    run_id,
                    status,
                    local_position_count,
                    alpaca_position_count,
                    warning_count,
                    critical_count,
                    json.dumps(summary),
                    now,
                    now if completed else None,
                ),
            )

    def save_crypto_fee_record(
        self,
        order_id: int,
        symbol: str,
        fee_amount: Decimal,
        fee_currency: str,
        *,
        alpaca_activity_id: str | None = None,
        activity_timestamp: str | None = None,
        processing_status: CryptoFeeStatus = CryptoFeeStatus.CONFIRMED,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO crypto_fee_records (
                    order_id, alpaca_activity_id, symbol, fee_amount_text,
                    fee_currency, activity_timestamp, processing_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alpaca_activity_id) DO NOTHING
                """,
                (
                    order_id,
                    alpaca_activity_id,
                    symbol,
                    format_decimal(fee_amount),
                    fee_currency,
                    activity_timestamp,
                    processing_status.value,
                    _utc_now(),
                ),
            )

    def count_unknown_crypto_orders(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM paper_orders
                WHERE asset_type = 'CRYPTO' AND status = 'UNKNOWN'
                """
            ).fetchone()
        return int(row[0])

    def list_open_crypto_paper_orders(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM paper_orders
                WHERE asset_type = 'CRYPTO'
                  AND status IN ('SUBMITTED', 'ACCEPTED', 'PARTIALLY_FILLED', 'UNKNOWN')
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_active_crypto_strategy_for_symbol(self, symbol: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM strategies
                WHERE asset_type = 'CRYPTO' AND symbol = ? AND status = 'ACTIVE' AND is_active = 1
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
        return dict(row) if row else None

    def sum_crypto_allocations(self) -> Decimal:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(allocated_funds), 0)
                FROM strategies
                WHERE asset_type = 'CRYPTO'
                """
            ).fetchone()
        return parse_decimal(row[0])

    def list_crypto_strategies(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM strategies WHERE asset_type = 'CRYPTO'"
        params: tuple = ()
        if status:
            query += " AND status = ?"
            params = (status,)
        query += " ORDER BY id"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def update_crypto_paper_approval(
        self,
        strategy_id: int,
        *,
        approved: bool,
        approved_at: str | None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE strategies
                SET crypto_paper_trading_approved = ?, crypto_approved_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (1 if approved else 0, approved_at, _utc_now(), strategy_id),
            )

    def create_crypto_strategy(
        self,
        name: str,
        strategy_type: str,
        symbol: str,
        quote_currency: str,
        parameters_json: str,
        allocated_funds: float,
        cash_reserve_percent: float,
        entry_policy: str,
        status: str = "DRAFT",
    ) -> int:
        now = _utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO strategies (
                    name, strategy_type, symbol, parameters_json,
                    allocated_funds, cash_reserve_percent, entry_policy,
                    status, is_active, asset_type, quote_currency,
                    crypto_paper_trading_approved, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'CRYPTO', ?, 0, ?, ?)
                """,
                (
                    name,
                    strategy_type,
                    symbol,
                    parameters_json,
                    allocated_funds,
                    cash_reserve_percent,
                    entry_policy,
                    status,
                    quote_currency,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def save_crypto_proposal(self, proposal: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO order_proposals (
                    proposal_id, strategy_id, symbol, signal, signal_timestamp,
                    side, quantity, estimated_price, estimated_notional,
                    client_order_id, status, validation_json, blocking_reasons_json,
                    expires_at, created_at, updated_at,
                    asset_type, sizing_mode, notional_text, quantity_text,
                    estimated_price_text, estimated_base_quantity_text,
                    estimated_fee_text, estimated_fee_currency, time_in_force
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal["proposal_id"],
                    proposal["strategy_id"],
                    proposal["symbol"],
                    proposal["signal"],
                    proposal["signal_timestamp"],
                    proposal["side"],
                    0,
                    float(proposal.get("estimated_price", 0)),
                    float(proposal.get("estimated_notional", 0)),
                    proposal["client_order_id"],
                    proposal["status"],
                    json.dumps(proposal.get("validation_messages", [])),
                    json.dumps(proposal.get("blocking_reasons", [])),
                    proposal.get("expires_at"),
                    proposal["created_at"],
                    _utc_now(),
                    "CRYPTO",
                    proposal.get("sizing_mode"),
                    proposal.get("notional_text"),
                    proposal.get("quantity_text"),
                    proposal.get("estimated_price_text"),
                    proposal.get("estimated_base_quantity_text"),
                    proposal.get("estimated_fee_text"),
                    proposal.get("estimated_fee_currency"),
                    proposal.get("time_in_force"),
                ),
            )

    def save_crypto_paper_order(self, order: dict[str, Any]) -> int:
        now = _utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO paper_orders (
                    strategy_id, proposal_id, alpaca_order_id, client_order_id,
                    symbol, side, quantity, order_type, time_in_force, status,
                    submitted_at, filled_quantity, last_processed_filled_qty,
                    submission_source, asset_type, notional_text, requested_quantity_text,
                    fee_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 'market', ?, ?, ?, 0, 0, 'MANUAL', 'CRYPTO', ?, ?, ?, ?, ?)
                """,
                (
                    order["strategy_id"],
                    order["proposal_id"],
                    order.get("alpaca_order_id"),
                    order["client_order_id"],
                    order["symbol"],
                    order["side"],
                    order.get("time_in_force", "gtc"),
                    order["status"],
                    order.get("submitted_at") or now,
                    order.get("notional_text"),
                    order.get("requested_quantity_text"),
                    order.get("fee_status", "NOT_AVAILABLE"),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

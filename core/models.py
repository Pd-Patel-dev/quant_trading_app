"""Core data models for Quant Strategy Lab."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

import pandas as pd

from core.exceptions import ConfigurationError


class SignalType(str, Enum):
    """Trading signal types produced by strategies."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class StrategyStatus(str, Enum):
    """Lifecycle status for a managed strategy."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class EntryPolicy(str, Enum):
    """How a strategy may enter after activation."""

    WAIT_FOR_NEXT_CROSSOVER = "WAIT_FOR_NEXT_CROSSOVER"
    ALIGN_WITH_CURRENT_POSITION = "ALIGN_WITH_CURRENT_POSITION"


class OrderProposalStatus(str, Enum):
    """Lifecycle status for an order proposal."""

    PROPOSED = "PROPOSED"
    BLOCKED = "BLOCKED"
    CONFIRMED = "CONFIRMED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class LedgerEntryType(str, Enum):
    """Append-only strategy ledger entry types."""

    ALLOCATION = "ALLOCATION"
    ALLOCATION_INCREASE = "ALLOCATION_INCREASE"
    ALLOCATION_DECREASE = "ALLOCATION_DECREASE"
    BUY_DEBIT = "BUY_DEBIT"
    SELL_CREDIT = "SELL_CREDIT"
    COMMISSION_DEBIT = "COMMISSION_DEBIT"
    RESERVE = "RESERVE"
    RELEASE_RESERVE = "RELEASE_RESERVE"
    ADJUSTMENT = "ADJUSTMENT"


@dataclass(frozen=True)
class Trade:
    """Record of a single executed order during a backtest."""

    timestamp: datetime
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int
    execution_price: float
    gross_value: float
    commission: float
    cash_after_trade: float
    position_after_trade: int
    reason: str


@dataclass(frozen=True)
class BacktestConfiguration:
    """User-defined parameters for a single backtest run."""

    symbol: str
    start_date: date
    end_date: date
    starting_capital: float
    allocation: float
    commission: float
    slippage_percent: float
    cash_reserve_percent: float

    def __post_init__(self) -> None:
        if self.starting_capital <= 0:
            raise ConfigurationError("Starting capital must be greater than zero.")
        if self.allocation <= 0:
            raise ConfigurationError("Allocation must be greater than zero.")
        if self.allocation > self.starting_capital:
            raise ConfigurationError("Allocation cannot exceed starting capital.")
        if self.slippage_percent < 0:
            raise ConfigurationError("Slippage cannot be negative.")
        if self.commission < 0:
            raise ConfigurationError("Commission cannot be negative.")
        if not 0 <= self.cash_reserve_percent <= 1:
            raise ConfigurationError("Cash reserve must be between zero and one.")
        if self.start_date >= self.end_date:
            raise ConfigurationError("Start date must be before end date.")


@dataclass
class BacktestResult:
    """Complete output from a backtest run."""

    symbol: str
    strategy_name: str
    starting_capital: float
    final_value: float
    total_return_percent: float
    buy_and_hold_return_percent: float
    total_trades: int
    completed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_percent: float
    maximum_drawdown_percent: float
    annualized_volatility_percent: float
    sharpe_ratio: float
    equity_curve: pd.DataFrame
    processed_data: pd.DataFrame
    trades: list[Trade] = field(default_factory=list)


@dataclass
class StrategyRecord:
    """Persisted strategy configuration."""

    id: int
    name: str
    strategy_type: str
    symbol: str
    parameters_json: str
    allocated_funds: Decimal
    cash_reserve_percent: Decimal
    entry_policy: EntryPolicy
    status: StrategyStatus
    is_active: bool
    created_at: str
    updated_at: str
    activated_at: str | None = None
    paused_at: str | None = None
    automation_enabled: bool = False
    automation_approved_at: str | None = None
    automation_paused_reason: str | None = None


@dataclass
class SignalEvaluation:
    """Result of evaluating a strategy against latest market data."""

    strategy_id: int
    symbol: str
    current_desired_position: int
    latest_signal: SignalType
    signal_timestamp: datetime | None
    short_sma: Decimal | None
    long_sma: Decimal | None
    close_price: Decimal | None
    data_timestamp: datetime | None
    is_actionable: bool
    requires_alignment: bool
    explanation: str
    saved_signal_id: int | None = None


@dataclass
class OrderProposal:
    """Proposed paper order awaiting validation and confirmation."""

    proposal_id: str
    strategy_id: int
    strategy_name: str
    symbol: str
    signal: SignalType
    signal_timestamp: datetime
    side: Literal["BUY", "SELL"]
    proposed_quantity: int
    estimated_price: Decimal
    estimated_notional: Decimal
    allocated_funds: Decimal
    strategy_cash_available: Decimal
    strategy_position_quantity: int
    cash_reserve_percent: Decimal
    client_order_id: str
    status: OrderProposalStatus
    validation_messages: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    requires_alignment_confirmation: bool = False
    proposal_source: str = "MANUAL"
    confirmation_mode: str = "MANUAL"
    automation_eligible: bool = False
    automation_validation_json: str | None = None
    automation_validated_at: str | None = None

    @property
    def is_executable(self) -> bool:
        """True when the proposal has no blockers and a positive quantity."""
        return not self.blocking_reasons and self.proposed_quantity > 0


@dataclass
class StrategyPaperPosition:
    """Locally tracked strategy position."""

    strategy_id: int
    symbol: str
    quantity: int
    average_entry_price: Decimal
    cost_basis: Decimal
    market_price: Decimal
    market_value: Decimal
    unrealized_profit_loss: Decimal
    updated_at: datetime


@dataclass
class StrategyLedgerSummary:
    """Aggregated strategy ledger and position metrics."""

    strategy_id: int
    allocated_funds: Decimal
    available_cash: Decimal
    reserved_cash: Decimal
    invested_value: Decimal
    current_value: Decimal
    realized_profit_loss: Decimal
    unrealized_profit_loss: Decimal
    total_profit_loss: Decimal
    total_return_percent: Decimal


@dataclass
class ConfirmationData:
    """User confirmation inputs for a paper order proposal."""

    paper_text: str
    paper_trading_acknowledged: bool
    details_reviewed: bool
    alignment_text: str = ""


@dataclass
class PaperOrderRecord:
    """Locally persisted Alpaca paper order."""

    id: int
    strategy_id: int
    proposal_id: str | None
    alpaca_order_id: str | None
    client_order_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    time_in_force: str
    status: str
    submitted_at: str | None
    filled_at: str | None
    filled_quantity: int
    filled_average_price: Decimal | None
    failure_message: str | None
    raw_status: str | None
    last_synced_at: str | None
    created_at: str
    updated_at: str
    last_processed_filled_qty: int = 0
    submission_source: str = "MANUAL"
    automation_run_id: str | None = None


def decimal_to_float(value: Decimal | None) -> float:
    """Convert Decimal to float safely for UI and charts."""
    if value is None:
        return 0.0
    return float(value)


def to_decimal(value: Any) -> Decimal:
    """Convert numeric values to Decimal safely."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    return Decimal(str(value))

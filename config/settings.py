"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

APP_NAME = "Quant Strategy Lab"
TRADING_MODE = "paper"
LIVE_TRADING_ENABLED = False
PAPER_ORDER_SUBMISSION_ENABLED = True
MANUAL_ORDER_CONFIRMATION_REQUIRED = True
MAX_PAPER_ORDER_NOTIONAL = 500.0
AUTOMATED_PAPER_TRADING_ENABLED = False
AUTOMATION_KILL_SWITCH_ENGAGED = True
MAX_AUTOMATED_ORDER_NOTIONAL = 500.0
MAX_AUTOMATED_ORDERS_PER_DAY = 3
MAX_AUTOMATED_DAILY_NOTIONAL = 1_000.0
MAX_ACTIVE_MANAGED_POSITIONS = 3
AUTOMATION_PROPOSAL_EXPIRATION_HOURS = 20
AUTOMATION_PRICE_BUFFER_PERCENT = 0.005
MARKET_OPEN_EXECUTION_DELAY_MINUTES = 5
MARKET_TIMEZONE = "America/New_York"
DEFAULT_STARTING_CAPITAL = 10_000.0
DEFAULT_COMMISSION = 0.0
DEFAULT_SLIPPAGE_PERCENT = 0.0005
DEFAULT_CASH_RESERVE_PERCENT = 0.05
LOCAL_PAPER_CAPITAL_POOL = 100_000.0
PRICE_ESTIMATE_BUFFER_PERCENT = 0.005
PROPOSAL_EXPIRY_HOURS = 24
DATABASE_PATH = "storage/trading_app.db"
CLIENT_ORDER_ID_PREFIX = "qslab"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    return float(value) if value else default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    """Frozen application settings."""

    app_name: str = APP_NAME
    database_path: str = DATABASE_PATH
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    trading_mode: str = TRADING_MODE
    live_trading_enabled: bool = LIVE_TRADING_ENABLED
    paper_order_submission_enabled: bool = PAPER_ORDER_SUBMISSION_ENABLED
    manual_order_confirmation_required: bool = MANUAL_ORDER_CONFIRMATION_REQUIRED
    max_paper_order_notional: float = MAX_PAPER_ORDER_NOTIONAL
    automated_paper_trading_enabled: bool = AUTOMATED_PAPER_TRADING_ENABLED
    automation_kill_switch_engaged: bool = AUTOMATION_KILL_SWITCH_ENGAGED
    max_automated_order_notional: float = MAX_AUTOMATED_ORDER_NOTIONAL
    max_automated_orders_per_day: int = MAX_AUTOMATED_ORDERS_PER_DAY
    max_automated_daily_notional: float = MAX_AUTOMATED_DAILY_NOTIONAL
    max_active_managed_positions: int = MAX_ACTIVE_MANAGED_POSITIONS
    automation_proposal_expiration_hours: int = AUTOMATION_PROPOSAL_EXPIRATION_HOURS
    automation_price_buffer_percent: float = AUTOMATION_PRICE_BUFFER_PERCENT
    market_open_execution_delay_minutes: int = MARKET_OPEN_EXECUTION_DELAY_MINUTES
    market_timezone: str = MARKET_TIMEZONE
    default_starting_capital: float = DEFAULT_STARTING_CAPITAL
    default_commission: float = DEFAULT_COMMISSION
    default_slippage_percent: float = DEFAULT_SLIPPAGE_PERCENT
    default_cash_reserve_percent: float = DEFAULT_CASH_RESERVE_PERCENT
    local_paper_capital_pool: float = LOCAL_PAPER_CAPITAL_POOL
    price_estimate_buffer_percent: float = PRICE_ESTIMATE_BUFFER_PERCENT
    proposal_expiry_hours: int = PROPOSAL_EXPIRY_HOURS
    client_order_id_prefix: str = CLIENT_ORDER_ID_PREFIX

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpaca_api_key", os.getenv("ALPACA_API_KEY", "").strip())
        object.__setattr__(self, "alpaca_secret_key", os.getenv("ALPACA_SECRET_KEY", "").strip())
        object.__setattr__(
            self,
            "automated_paper_trading_enabled",
            _env_bool("AUTOMATED_PAPER_TRADING_ENABLED", AUTOMATED_PAPER_TRADING_ENABLED),
        )
        object.__setattr__(
            self,
            "automation_kill_switch_engaged",
            _env_bool("AUTOMATION_KILL_SWITCH_ENGAGED", AUTOMATION_KILL_SWITCH_ENGAGED),
        )
        object.__setattr__(
            self,
            "max_automated_order_notional",
            _env_float("MAX_AUTOMATED_ORDER_NOTIONAL", MAX_AUTOMATED_ORDER_NOTIONAL),
        )
        object.__setattr__(
            self,
            "max_automated_orders_per_day",
            _env_int("MAX_AUTOMATED_ORDERS_PER_DAY", MAX_AUTOMATED_ORDERS_PER_DAY),
        )
        object.__setattr__(
            self,
            "max_automated_daily_notional",
            _env_float("MAX_AUTOMATED_DAILY_NOTIONAL", MAX_AUTOMATED_DAILY_NOTIONAL),
        )
        object.__setattr__(
            self,
            "max_active_managed_positions",
            _env_int("MAX_ACTIVE_MANAGED_POSITIONS", MAX_ACTIVE_MANAGED_POSITIONS),
        )

    @property
    def alpaca_configured(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_secret_key)

    @property
    def database_full_path(self) -> Path:
        return _PROJECT_ROOT / self.database_path


def get_settings() -> Settings:
    return Settings()

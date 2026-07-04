"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

APP_NAME = "Quant Strategy Lab"
TRADING_MODE = "paper"
DEFAULT_STARTING_CAPITAL = 10_000.0
DEFAULT_COMMISSION = 0.0
DEFAULT_SLIPPAGE_PERCENT = 0.0005
DEFAULT_CASH_RESERVE_PERCENT = 0.05
DATABASE_PATH = "storage/trading_app.db"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    """Frozen application settings."""

    app_name: str = APP_NAME
    database_path: str = DATABASE_PATH
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    trading_mode: str = TRADING_MODE
    default_starting_capital: float = DEFAULT_STARTING_CAPITAL
    default_commission: float = DEFAULT_COMMISSION
    default_slippage_percent: float = DEFAULT_SLIPPAGE_PERCENT
    default_cash_reserve_percent: float = DEFAULT_CASH_RESERVE_PERCENT

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "alpaca_api_key",
            os.getenv("ALPACA_API_KEY", "").strip(),
        )
        object.__setattr__(
            self,
            "alpaca_secret_key",
            os.getenv("ALPACA_SECRET_KEY", "").strip(),
        )

    @property
    def alpaca_configured(self) -> bool:
        """Return True when both Alpaca API credentials are present."""
        return bool(self.alpaca_api_key and self.alpaca_secret_key)

    @property
    def database_full_path(self) -> Path:
        """Return the absolute path to the SQLite database file."""
        return _PROJECT_ROOT / self.database_path


def get_settings() -> Settings:
    """Return the application settings singleton."""
    return Settings()

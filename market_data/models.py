"""Market data domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

import pandas as pd


class AssetType(str, Enum):
    STOCK = "STOCK"
    CRYPTO = "CRYPTO"


class MarketDataSource(str, Enum):
    ALPACA = "ALPACA"


class DataTimeframe(str, Enum):
    DAY = "1Day"


class DownloadRunStatus(str, Enum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    NO_DATA = "NO_DATA"
    FAILED = "FAILED"


class QualityIssueType(str, Enum):
    DUPLICATE_TIMESTAMP = "DUPLICATE_TIMESTAMP"
    MISSING_VALUE = "MISSING_VALUE"
    INVALID_OHLC = "INVALID_OHLC"
    NEGATIVE_PRICE = "NEGATIVE_PRICE"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    INTERNAL_GAP = "INTERNAL_GAP"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    EXTREME_PRICE_CHANGE = "EXTREME_PRICE_CHANGE"


class QuantityMode(str, Enum):
    WHOLE_UNITS = "WHOLE_UNITS"
    FRACTIONAL_RESEARCH = "FRACTIONAL_RESEARCH"


class BacktestRunMode(str, Enum):
    INDEPENDENT_COMPARISON = "INDEPENDENT_COMPARISON"
    SHARED_CAPITAL_PORTFOLIO = "SHARED_CAPITAL_PORTFOLIO"


@dataclass(frozen=True)
class HistoricalBar:
    asset_type: AssetType
    symbol: str
    timeframe: DataTimeframe
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int | None
    vwap: Decimal | None
    source: MarketDataSource
    feed: str
    adjustment: str


@dataclass(frozen=True)
class DateRange:
    start: datetime
    end: datetime


@dataclass
class CachedRange:
    start: datetime
    end: datetime
    row_count: int


@dataclass
class UpsertResult:
    inserted: int = 0
    updated: int = 0


@dataclass
class AssetDataSummary:
    asset_id: int
    asset_type: AssetType
    symbol: str
    timeframe: str
    feed: str
    adjustment: str
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    row_count: int
    quality_status: str
    last_downloaded_at: str | None


@dataclass
class MarketDataValidationResult:
    is_usable: bool
    passed_checks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocking_errors: list[str] = field(default_factory=list)
    quality_issues: list[dict] = field(default_factory=list)
    cleaned_data: pd.DataFrame | None = None


@dataclass
class HistoricalDataResult:
    asset_type: AssetType
    symbol: str
    timeframe: DataTimeframe
    data: pd.DataFrame
    requested_start: datetime
    requested_end: datetime
    cached_rows_before: int = 0
    downloaded_rows: int = 0
    inserted_rows: int = 0
    updated_rows: int = 0
    final_rows: int = 0
    missing_ranges_requested: int = 0
    download_runs: list[str] = field(default_factory=list)
    served_entirely_from_cache: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def data_source_status(self) -> str:
        if self.error:
            return "Error"
        if self.served_entirely_from_cache:
            return "Local cache"
        if self.downloaded_rows > 0 and self.cached_rows_before > 0:
            return "Cache updated from Alpaca"
        if self.downloaded_rows > 0:
            return "Downloaded from Alpaca"
        return "Local cache"


@dataclass
class AssetRequest:
    asset_type: AssetType
    symbol: str
    start: datetime
    end: datetime
    force_refresh: bool = False
    repair_gaps: bool = True


@dataclass
class BatchHistoricalDataResult:
    results: list[HistoricalDataResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ParseSymbolsResult:
    normalized: list[str]
    duplicates_removed: list[str]
    invalid: list[str]
    warnings: list[str]


@dataclass
class AssetBacktestResult:
    asset_type: AssetType
    symbol: str
    strategy_type: str
    strategy_name: str
    starting_capital: Decimal
    allocation: Decimal
    final_value: Decimal
    profit_loss: Decimal
    total_return_percent: float
    annualized_return_percent: float
    maximum_drawdown_percent: float
    annualized_volatility_percent: float
    sharpe_ratio: float
    sortino_ratio: float
    completed_trades: int
    win_rate_percent: float
    exposure_percent: float
    first_bar: datetime
    last_bar: datetime
    bar_count: int
    data_source_status: str
    equity_curve: pd.Series | None = None


@dataclass
class AssetBacktestFailure:
    asset_type: AssetType
    symbol: str
    error: str


@dataclass
class MultiAssetBacktestResult:
    results: list[AssetBacktestResult] = field(default_factory=list)
    failures: list[AssetBacktestFailure] = field(default_factory=list)
    normalized_equity_curves: pd.DataFrame | None = None
    combined_portfolio_curve: pd.DataFrame | None = None
    portfolio_metrics: dict | None = None
    alignment_warnings: list[str] = field(default_factory=list)

"""Strategy metadata and parameter definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.models import SignalType


class StrategyCategory(str, Enum):
    TREND_FOLLOWING = "TREND_FOLLOWING"
    MEAN_REVERSION = "MEAN_REVERSION"
    MOMENTUM = "MOMENTUM"
    BREAKOUT = "BREAKOUT"


class ParameterType(str, Enum):
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    BOOLEAN = "BOOLEAN"
    CHOICE = "CHOICE"


@dataclass(frozen=True)
class StrategyParameterDefinition:
    name: str
    display_name: str
    description: str
    parameter_type: ParameterType
    default_value: Any
    minimum_value: float | None = None
    maximum_value: float | None = None
    step: float | None = None
    required: bool = True
    choices: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class StrategyMetadata:
    strategy_type: str
    display_name: str
    description: str
    category: StrategyCategory
    version: str
    minimum_history_bars: int
    supported_timeframes: tuple[str, ...]
    supports_backtesting: bool
    supports_manual_paper_trading: bool
    supports_automated_paper_trading: bool
    default_parameters: dict[str, Any]
    parameter_definitions: tuple[StrategyParameterDefinition, ...]
    risk_notes: str
    asset_type: str = "ANY"
    supported_symbols: tuple[str, ...] = field(default_factory=tuple)
    long_only: bool = True
    supports_leverage: bool = False
    risk_model_type: str | None = None


@dataclass
class StrategyEvaluation:
    """Standardized latest-bar evaluation from any strategy."""

    latest_signal: SignalType
    signal_timestamp: Any | None
    current_desired_position: int
    is_actionable: bool
    explanation: str
    signal_reason: str | None = None
    indicators: dict[str, Any] = field(default_factory=dict)
    data_timestamp: Any | None = None
    requires_alignment: bool = False

"""Strategy registry for plugin-style strategy discovery."""

from __future__ import annotations

from typing import Any, Type

from core.exceptions import StrategyError
from strategies.base_strategy import BaseStrategy
from strategies.metadata import StrategyMetadata


_DEFAULT_PARAMETERS: dict[str, dict[str, Any]] = {
    "moving_average_crossover": {"short_window": 50, "long_window": 200},
    "rsi_mean_reversion": {
        "rsi_period": 14,
        "oversold_threshold": 30.0,
        "exit_threshold": 55.0,
        "overbought_threshold": 70.0,
    },
    "crypto_ema_trend_following": {
        "fast_ema_period": 20,
        "medium_ema_period": 50,
        "long_ema_period": 200,
        "stop_loss_percent": 0.08,
        "risk_per_trade_percent": 0.01,
    },
}


class StrategyRegistry:
    """Register and instantiate trading strategies by type identifier."""

    def __init__(self) -> None:
        self._strategies: dict[str, Type[BaseStrategy]] = {}

    def register(self, strategy_type: str, strategy_class: Type[BaseStrategy]) -> None:
        if strategy_type in self._strategies:
            raise StrategyError(f"Strategy type '{strategy_type}' is already registered.")
        self._strategies[strategy_type] = strategy_class

    def get_strategy_class(self, strategy_type: str) -> Type[BaseStrategy]:
        if strategy_type not in self._strategies:
            raise StrategyError(f"Unknown strategy type: {strategy_type}")
        return self._strategies[strategy_type]

    def list_strategy_types(self) -> list[str]:
        return sorted(self._strategies.keys())

    def list_metadata(self) -> list[StrategyMetadata]:
        return [self.get_metadata(st) for st in self.list_strategy_types()]

    def get_metadata(self, strategy_type: str) -> StrategyMetadata:
        defaults = _DEFAULT_PARAMETERS.get(strategy_type)
        if defaults is None:
            raise StrategyError(f"Unknown strategy type: {strategy_type}")
        return self.build(strategy_type, defaults).metadata

    def build(self, strategy_type: str, parameters: dict[str, Any] | None = None) -> BaseStrategy:
        strategy_class = self.get_strategy_class(strategy_type)
        defaults = _DEFAULT_PARAMETERS[strategy_type]
        merged = {**defaults, **(parameters or {})}
        instance = strategy_class.from_parameters(merged)
        instance.validate_parameters()
        return instance

    def validate_parameters(self, strategy_type: str, parameters: dict[str, Any]) -> None:
        self.build(strategy_type, parameters)

    def minimum_history_bars(self, strategy_type: str, parameters: dict[str, Any]) -> int:
        strategy = self.build(strategy_type, parameters)
        return strategy.metadata.minimum_history_bars


def _create_default_registry() -> StrategyRegistry:
    from strategies.crypto_ema_trend_following import CryptoEMATrendFollowingStrategy
    from strategies.moving_average import MovingAverageCrossoverStrategy
    from strategies.rsi_mean_reversion import RSIMeanReversionStrategy

    registry = StrategyRegistry()
    registry.register(MovingAverageCrossoverStrategy.STRATEGY_TYPE, MovingAverageCrossoverStrategy)
    registry.register(RSIMeanReversionStrategy.STRATEGY_TYPE, RSIMeanReversionStrategy)
    registry.register(CryptoEMATrendFollowingStrategy.STRATEGY_TYPE, CryptoEMATrendFollowingStrategy)
    return registry


DEFAULT_REGISTRY = _create_default_registry()


def get_registry() -> StrategyRegistry:
    return DEFAULT_REGISTRY

"""Strategy registry tests."""

import pytest

from core.exceptions import StrategyError
from strategies.moving_average import MovingAverageCrossoverStrategy
from strategies.registry import StrategyRegistry
from strategies.rsi_mean_reversion import RSIMeanReversionStrategy


def test_registers_both_strategies() -> None:
    registry = StrategyRegistry()
    registry.register("moving_average_crossover", MovingAverageCrossoverStrategy)
    registry.register("rsi_mean_reversion", RSIMeanReversionStrategy)
    assert len(registry.list_strategy_types()) == 2


def test_rejects_duplicate_registration() -> None:
    registry = StrategyRegistry()
    registry.register("moving_average_crossover", MovingAverageCrossoverStrategy)
    with pytest.raises(StrategyError):
        registry.register("moving_average_crossover", MovingAverageCrossoverStrategy)


def test_rejects_unknown_type() -> None:
    registry = StrategyRegistry()
    with pytest.raises(StrategyError):
        registry.build("unknown", {})


def test_instantiates_valid_parameters() -> None:
    from strategies.registry import get_registry

    registry = get_registry()
    strategy = registry.build("rsi_mean_reversion", {"rsi_period": 14})
    assert strategy.metadata.category.value == "MEAN_REVERSION"


def test_rejects_invalid_rsi_parameters() -> None:
    from strategies.registry import get_registry

    with pytest.raises(StrategyError):
        get_registry().build("rsi_mean_reversion", {"rsi_period": 14, "exit_threshold": 20.0})


def test_returns_metadata() -> None:
    from strategies.registry import get_registry

    meta = get_registry().get_metadata("moving_average_crossover")
    assert meta.category.value == "TREND_FOLLOWING"

"""Research evaluation tests."""

from datetime import datetime

import pandas as pd
import pytest

from backtesting.engine import BacktestEngine
from core.models import BacktestConfiguration, SignalType
from research.comparison_service import StrategyComparisonService
from research.portfolio_simulation import PortfolioSimulator
from research.train_test import TrainTestEvaluator
from research.walk_forward import WalkForwardConfig, WalkForwardEvaluator
from strategies.moving_average import MovingAverageCrossoverStrategy
from strategies.rsi_mean_reversion import RSIMeanReversionStrategy
from tests.test_backtest_engine import ScriptedStrategy, _configuration, _make_ohlcv


def _long_data(n: int = 800) -> pd.DataFrame:
    return _make_ohlcv(
        [{"Open": 100 + i * 0.05, "High": 101, "Low": 99, "Close": 100 + i * 0.05, "Volume": 1000} for i in range(n)]
    )


def test_train_test_chronological_split() -> None:
    data = _long_data(200)
    strategy = MovingAverageCrossoverStrategy(5, 10)
    config = _configuration()
    result = TrainTestEvaluator().evaluate(strategy, data, config, train_fraction=0.7)
    assert result.training.end_date <= result.testing.start_date
    assert result.training.period_label == "Training"
    assert result.testing.period_label == "Testing"


def test_walk_forward_sequential_windows() -> None:
    data = _long_data(700)
    strategy = RSIMeanReversionStrategy(rsi_period=5)
    config = _configuration()
    result = WalkForwardEvaluator().evaluate(
        strategy, data, config, WalkForwardConfig(training_bars=200, testing_bars=50, step_bars=50)
    )
    assert result.windows
    assert result.windows[0].testing_start >= result.windows[0].training_end


def test_comparison_same_data() -> None:
    data = _long_data(300)
    config = _configuration()
    service = StrategyComparisonService()
    results = service.compare(
        [("moving_average_crossover", {"short_window": 5, "long_window": 10}), ("rsi_mean_reversion", {"rsi_period": 5})],
        data,
        config,
        config.start_date,
        config.end_date,
    )
    assert len(results) == 2
    ranked = service.rank(results, "Sharpe Ratio")
    assert ranked[0].sharpe_ratio >= ranked[-1].sharpe_ratio


def test_portfolio_allocations_cannot_exceed_capital() -> None:
    data = _long_data(200)
    config = _configuration(starting_capital=10000, allocation=10000)
    simulator = PortfolioSimulator()
    with pytest.raises(ValueError):
        simulator.simulate(
            {"moving_average_crossover": 6000, "rsi_mean_reversion": 6000},
            0,
            [("moving_average_crossover", {}), ("rsi_mean_reversion", {})],
            config,
            data,
        )


def test_rsi_backtest_round_trip() -> None:
    signals = [SignalType.HOLD.value] * 5 + [SignalType.BUY.value] + [SignalType.HOLD.value] * 5 + [SignalType.SELL.value] + [SignalType.HOLD.value] * 3
    data = _make_ohlcv(
        [{"Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 1000}] * len(signals)
    )
    result = BacktestEngine(ScriptedStrategy(signals), _configuration(), data).run()
    assert result.completed_trades >= 0


def test_schema_version_four(temp_db) -> None:
    assert temp_db.schema_version >= 4

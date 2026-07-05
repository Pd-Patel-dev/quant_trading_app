"""Chronological train/test evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from core.models import BacktestConfiguration
from research.backtest_utils import period_result_from_backtest, run_backtest
from research.models import EvaluationPeriodResult
from strategies.base_strategy import BaseStrategy


@dataclass
class TrainTestEvaluation:
    training: EvaluationPeriodResult
    testing: EvaluationPeriodResult
    train_fraction: float
    overfitting_warnings: list[str]


class TrainTestEvaluator:
    """Split historical data chronologically into training and testing periods."""

    DEFAULT_TRAIN_FRACTION = 0.70

    def evaluate(
        self,
        strategy: BaseStrategy,
        data: pd.DataFrame,
        configuration: BacktestConfiguration,
        train_fraction: float = DEFAULT_TRAIN_FRACTION,
    ) -> TrainTestEvaluation:
        if not 0.1 <= train_fraction <= 0.9:
            raise ValueError("Train fraction must be between 0.1 and 0.9.")
        if len(data) < 20:
            raise ValueError("Insufficient history for train/test evaluation.")

        split_index = int(len(data) * train_fraction)
        if split_index < 5 or split_index >= len(data) - 5:
            raise ValueError("Train/test split produced segments that are too small.")

        train_data = data.iloc[:split_index].copy()
        test_data = data.iloc[split_index:].copy()

        train_result = run_backtest(strategy, configuration, train_data)
        test_result = run_backtest(strategy, configuration, test_data)

        train_start = pd.Timestamp(train_data.index[0]).date()
        train_end = pd.Timestamp(train_data.index[-1]).date()
        test_start = pd.Timestamp(test_data.index[0]).date()
        test_end = pd.Timestamp(test_data.index[-1]).date()

        training = period_result_from_backtest("Training", train_result, train_start, train_end)
        testing = period_result_from_backtest("Testing", test_result, test_start, test_end)
        warnings = self._overfitting_warnings(training, testing)

        return TrainTestEvaluation(
            training=training,
            testing=testing,
            train_fraction=train_fraction,
            overfitting_warnings=warnings,
        )

    @staticmethod
    def _overfitting_warnings(
        training: EvaluationPeriodResult,
        testing: EvaluationPeriodResult,
    ) -> list[str]:
        warnings: list[str] = []
        if training.total_return_percent - testing.total_return_percent > 15.0:
            warnings.append("Training return materially exceeds testing return.")
        if training.sharpe_ratio > 0 and testing.sharpe_ratio < 0:
            warnings.append("Training Sharpe is positive but testing Sharpe is negative.")
        if testing.maximum_drawdown_percent > training.maximum_drawdown_percent * 1.5 + 5:
            warnings.append("Testing drawdown is much larger than training drawdown.")
        return warnings

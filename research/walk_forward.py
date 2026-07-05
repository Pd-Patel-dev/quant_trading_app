"""Sequential walk-forward evaluation."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting import metrics
from core.models import BacktestConfiguration
from research.backtest_utils import run_backtest
from research.models import WalkForwardResult, WalkForwardWindowResult
from strategies.base_strategy import BaseStrategy


@dataclass
class WalkForwardConfig:
    training_bars: int = 504
    testing_bars: int = 126
    step_bars: int = 126


class WalkForwardEvaluator:
    """Evaluate fixed-parameter strategy across sequential walk-forward windows."""

    def evaluate(
        self,
        strategy: BaseStrategy,
        data: pd.DataFrame,
        configuration: BacktestConfiguration,
        config: WalkForwardConfig | None = None,
    ) -> WalkForwardResult:
        cfg = config or WalkForwardConfig()
        required = cfg.training_bars + cfg.testing_bars
        if len(data) < required:
            return WalkForwardResult(
                symbol=configuration.symbol,
                strategy_type=strategy.metadata.strategy_type,
                strategy_name=strategy.name,
                summary_message=(
                    f"Insufficient history: need at least {required} bars, have {len(data)}."
                ),
            )

        windows: list[WalkForwardWindowResult] = []
        oos_parts: list[pd.Series] = []
        start = 0
        window_num = 0

        while start + cfg.training_bars + cfg.testing_bars <= len(data):
            window_num += 1
            train_slice = data.iloc[start : start + cfg.training_bars]
            test_slice = data.iloc[start + cfg.training_bars : start + cfg.training_bars + cfg.testing_bars]

            train_result = run_backtest(strategy, configuration, train_slice)
            test_result = run_backtest(strategy, configuration, test_slice)

            test_equity = test_result.equity_curve["PortfolioValue"]
            if oos_parts:
                scale = float(oos_parts[-1].iloc[-1]) / float(test_equity.iloc[0])
                test_equity = test_equity * scale
            oos_parts.append(test_equity)

            windows.append(
                WalkForwardWindowResult(
                    window_number=window_num,
                    training_start=pd.Timestamp(train_slice.index[0]).date(),
                    training_end=pd.Timestamp(train_slice.index[-1]).date(),
                    testing_start=pd.Timestamp(test_slice.index[0]).date(),
                    testing_end=pd.Timestamp(test_slice.index[-1]).date(),
                    training_return_percent=train_result.total_return_percent,
                    testing_return_percent=test_result.total_return_percent,
                    testing_drawdown_percent=test_result.maximum_drawdown_percent,
                    testing_sharpe_ratio=test_result.sharpe_ratio,
                    testing_trade_count=test_result.completed_trades,
                )
            )
            start += cfg.step_bars

        if not windows:
            return WalkForwardResult(
                symbol=configuration.symbol,
                strategy_type=strategy.metadata.strategy_type,
                strategy_name=strategy.name,
                summary_message="No walk-forward windows could be generated.",
            )

        combined = pd.concat(oos_parts) if oos_parts else None
        test_returns = [w.testing_return_percent for w in windows]
        positive = sum(1 for r in test_returns if r > 0)
        negative = sum(1 for r in test_returns if r <= 0)
        consistency = (positive / len(windows)) * 100.0 if windows else 0.0

        return WalkForwardResult(
            symbol=configuration.symbol,
            strategy_type=strategy.metadata.strategy_type,
            strategy_name=strategy.name,
            windows=windows,
            combined_oos_equity=combined,
            positive_windows=positive,
            negative_windows=negative,
            consistency_percent=consistency,
            best_testing_return_percent=max(test_returns),
            worst_testing_return_percent=min(test_returns),
            summary_message=f"Completed {len(windows)} out-of-sample windows.",
        )

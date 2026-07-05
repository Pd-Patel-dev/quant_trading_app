"""Crypto strategy creation and backtest matching tests."""

import json
from datetime import datetime, timezone
from decimal import Decimal

from core.models import EntryPolicy
from market_data.models import AssetType
from market_data.symbol_normalizer import SymbolNormalizer
from portfolio.crypto_ledger import CryptoStrategyLedger
from services.strategy_service import StrategyService


def test_create_crypto_strategy_draft(temp_db) -> None:
    service = StrategyService(temp_db)
    strategy_id = service.create_crypto_strategy(
        name="BTC MA",
        symbol="BTC/USD",
        strategy_type="moving_average_crossover",
        parameters={"short_window": 50, "long_window": 200},
        allocated_funds=Decimal("500"),
        cash_reserve_percent=Decimal("0.05"),
        entry_policy=EntryPolicy.WAIT_FOR_NEXT_CROSSOVER,
    )
    strategy = temp_db.get_strategy(strategy_id)
    assert strategy is not None
    assert strategy.asset_type == "CRYPTO"
    assert strategy.symbol == "BTC/USD"
    assert strategy.status.value == "DRAFT"


def test_has_matching_backtest_accepts_crypto_symbol(temp_db) -> None:
    normalizer = SymbolNormalizer()
    symbol = normalizer.normalize(AssetType.CRYPTO, "btc-usd")
    params = {"short_window": 50, "long_window": 200}
    params_json = json.dumps(params)
    now = datetime.now(timezone.utc).isoformat()
    with temp_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO strategy_research_runs (
                run_id, run_type, symbol, start_date, end_date,
                starting_capital, configuration_json, created_at
            ) VALUES (?, 'SINGLE_BACKTEST', ?, '2020-01-01', '2024-01-01', 10000, '{}', ?)
            """,
            ("run-crypto-1", symbol, now),
        )
        conn.execute(
            """
            INSERT INTO strategy_research_results (
                run_id, strategy_type, strategy_name, parameters_json,
                final_value, total_return_percent, annualized_return_percent,
                maximum_drawdown_percent, annualized_volatility_percent,
                sharpe_ratio, sortino_ratio, profit_factor,
                completed_trades, win_rate_percent,
                average_holding_period_days, exposure_percent,
                result_summary_json, created_at
            ) VALUES (?, 'moving_average_crossover', 'MA', ?, 11000, 10, 10, 5, 10, 1, 1, 1, 1, 50, 1, 50, '{}', ?)
            """,
            ("run-crypto-1", params_json, now),
        )
    assert temp_db.has_matching_backtest("moving_average_crossover", "BTC/USD", params_json)


def test_increase_crypto_allocation(temp_db) -> None:
    service = StrategyService(temp_db)
    strategy_id = service.create_crypto_strategy(
        name="BTC MA",
        symbol="BTC/USD",
        strategy_type="moving_average_crossover",
        parameters={"short_window": 50, "long_window": 200},
        allocated_funds=Decimal("500"),
        cash_reserve_percent=Decimal("0.05"),
        entry_policy=EntryPolicy.WAIT_FOR_NEXT_CROSSOVER,
    )
    service.increase_allocation(strategy_id, Decimal("200"))
    strategy = temp_db.get_strategy(strategy_id)
    assert strategy.allocated_funds == Decimal("700")
    assert CryptoStrategyLedger(temp_db).get_available_usd(strategy_id) == Decimal("700")

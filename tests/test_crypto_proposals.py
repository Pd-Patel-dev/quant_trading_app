"""Crypto proposal and safety tests."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from config.settings import Settings
from core.asset_models import AssetType
from core.models import CryptoConfirmationData, StrategyStatus
from data.database import DatabaseManager
from portfolio.crypto_ledger import CryptoStrategyLedger
from services.crypto_paper_trading_service import CryptoPaperTradingService


def _seed_crypto_strategy(db: DatabaseManager) -> int:
    strategy_id = db.create_crypto_strategy(
        "BTC Test",
        "moving_average_crossover",
        "BTC/USD",
        "USD",
        '{"short_window": 5, "long_window": 20}',
        500.0,
        0.05,
        "WAIT_FOR_NEXT_CROSSOVER",
        status="ACTIVE",
    )
    db.update_crypto_paper_approval(strategy_id, approved=True, approved_at="2026-01-01")
    CryptoStrategyLedger(db).allocate(strategy_id, "BTC/USD", Decimal("500"), "seed")
    return strategy_id


@patch("services.crypto_paper_trading_service.build_market_data_stack")
def test_blocks_kill_switch(mock_stack, temp_db) -> None:
    strategy_id = _seed_crypto_strategy(temp_db)
    mock_cache = MagicMock()
    mock_stack.return_value = (None, mock_cache, None)
    bars = pd.DataFrame(
        {
            "Open": [100.0] * 30,
            "High": [101.0] * 30,
            "Low": [99.0] * 30,
            "Close": [100.0] * 30,
            "Volume": [1000.0] * 30,
        },
        index=pd.date_range("2025-01-01", periods=30, freq="D"),
    )
    mock_cache.get_or_download.return_value = MagicMock(data=bars)
    order_manager = MagicMock()
    asset_service = MagicMock()
    asset_service.validate_pair.return_value = MagicMock(is_valid=True, messages=(), rules=MagicMock(
        minimum_order_size=Decimal("1"),
        minimum_trade_increment=Decimal("0.00000001"),
        tradable=True,
        fractionable=True,
    ))
    settings = Settings()
    object.__setattr__(settings, "crypto_kill_switch_engaged", True)
    object.__setattr__(settings, "crypto_paper_trading_enabled", True)
    service = CryptoPaperTradingService(temp_db, order_manager, asset_service, settings)
    proposal = service.build_order_proposal(strategy_id)
    assert proposal["status"] == "BLOCKED"
    assert any("kill switch" in b.lower() for b in proposal["blocking_reasons"])


def test_confirmation_requires_paper_crypto(temp_db) -> None:
    strategy_id = _seed_crypto_strategy(temp_db)
    temp_db.save_crypto_proposal(
        {
            "proposal_id": "p1",
            "strategy_id": strategy_id,
            "symbol": "BTC/USD",
            "signal": "BUY",
            "signal_timestamp": datetime.now(timezone.utc).isoformat(),
            "side": "BUY",
            "status": "PROPOSED",
            "blocking_reasons": [],
            "validation_messages": [],
            "client_order_id": "cid-1",
            "sizing_mode": "NOTIONAL",
            "notional_text": "50",
            "quantity_text": "0",
            "estimated_price_text": "100",
            "estimated_base_quantity_text": "0.5",
            "estimated_fee_text": "0.05",
            "estimated_fee_currency": "USD",
            "estimated_notional": 50.0,
            "estimated_price": 100.0,
            "time_in_force": "gtc",
            "expires_at": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    service = CryptoPaperTradingService(temp_db, MagicMock(), MagicMock())
    with pytest.raises(Exception):
        service.confirm_proposal(
            "p1",
            CryptoConfirmationData("WRONG", True, True, True),
        )


def test_deterministic_client_order_id() -> None:
    from core.client_order_id import build_crypto_client_order_id

    ts = datetime(2026, 7, 4, tzinfo=timezone.utc)
    a = build_crypto_client_order_id("qslab", 14, "BTC/USD", "BUY", ts)
    b = build_crypto_client_order_id("qslab", 14, "BTC/USD", "BUY", ts)
    assert a == b
    assert "crypto" in a

"""Paper capital source tests."""

from decimal import Decimal
from unittest.mock import patch

from portfolio.allocation_manager import AllocationManager
from services.paper_capital_service import PaperCapitalService


def test_local_capital_pool_by_default_in_tests(temp_db) -> None:
    manager = AllocationManager(temp_db)
    assert not manager.uses_alpaca_capital
    assert manager.capital_pool == Decimal("100000")


def test_alpaca_capital_pool_when_configured() -> None:
    class _Settings:
        paper_capital_source = "alpaca"
        local_paper_capital_pool = 100_000.0
        max_crypto_total_allocation = Decimal("1000")

        @property
        def alpaca_configured(self) -> bool:
            return True

        alpaca_api_key = "key"
        alpaca_secret_key = "secret"

    service = PaperCapitalService(_Settings())  # type: ignore[arg-type]
    with patch.object(
        service,
        "_fetch_alpaca_account",
        return_value={"cash": 25000.0, "buying_power": 50000.0},
    ):
        assert service.uses_alpaca_capital
        assert service.get_capital_pool() == Decimal("25000")

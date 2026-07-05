"""Route paper orders by asset class."""

from __future__ import annotations

from core.asset_models import AssetType
from core.models import StrategyRecord


class PaperOrderRouter:
    """Select stock or crypto execution path."""

    @staticmethod
    def asset_type_for_strategy(strategy: StrategyRecord) -> AssetType:
        asset_type = getattr(strategy, "asset_type", "STOCK")
        return AssetType(asset_type)

    @staticmethod
    def is_crypto(strategy: StrategyRecord) -> bool:
        return PaperOrderRouter.asset_type_for_strategy(strategy) == AssetType.CRYPTO

    @staticmethod
    def is_stock(strategy: StrategyRecord) -> bool:
        return PaperOrderRouter.asset_type_for_strategy(strategy) == AssetType.STOCK

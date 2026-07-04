"""Custom application exceptions for Quant Strategy Lab."""


class QuantTradingError(Exception):
    """Base exception for all application errors."""


class ConfigurationError(QuantTradingError):
    """Raised when configuration or user inputs are invalid."""


class MarketDataError(QuantTradingError):
    """Raised when market data cannot be retrieved or parsed."""


class StrategyError(QuantTradingError):
    """Raised when a strategy fails validation or signal generation."""


class BacktestError(QuantTradingError):
    """Raised when the backtesting engine encounters an error."""


class AlpacaConnectionError(QuantTradingError):
    """Raised when Alpaca API connectivity fails."""

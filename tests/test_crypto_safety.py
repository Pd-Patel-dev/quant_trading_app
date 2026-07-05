"""Crypto safety separation tests."""

from config.settings import Settings, TRADING_MODE, LIVE_TRADING_ENABLED


def test_live_trading_disabled() -> None:
    settings = Settings()
    assert settings.trading_mode == TRADING_MODE == "paper"
    assert settings.live_trading_enabled is LIVE_TRADING_ENABLED is False


def test_crypto_automation_disabled_by_default() -> None:
    settings = Settings()
    assert settings.crypto_automation_enabled is False


def test_crypto_paper_disabled_by_default() -> None:
    settings = Settings()
    assert settings.crypto_paper_trading_enabled is False


def test_crypto_kill_switch_default_engaged() -> None:
    settings = Settings()
    assert settings.crypto_kill_switch_engaged is True

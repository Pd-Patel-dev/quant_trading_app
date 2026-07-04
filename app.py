"""Quant Strategy Lab — Streamlit entry point."""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

# Project root must be on sys.path before any local imports.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Streamlit can cache outdated project modules across reruns.
_STALE_MODULE_CHECKS = (
    ("core.exceptions", "PaperTradingError"),
    ("data.database", "list_strategy_positions"),
)


def _clear_stale_project_modules() -> None:
    needs_clear = False
    for module_name, attribute in _STALE_MODULE_CHECKS:
        module = sys.modules.get(module_name)
        if module is not None and not hasattr(module, attribute):
            needs_clear = True
            break
    if not needs_clear:
        return
    prefixes = (
        "core",
        "data",
        "views",
        "services",
        "portfolio",
        "broker",
        "config",
        "strategies",
        "backtesting",
        "automation",
        "workers",
    )
    for module_name in list(sys.modules):
        if module_name in prefixes or any(module_name.startswith(f"{prefix}.") for prefix in prefixes):
            del sys.modules[module_name]
    importlib.invalidate_caches()


_clear_stale_project_modules()

import streamlit as st

from config.settings import get_settings
from data.database import DatabaseManager
from views import about, automation, backtest, dashboard, paper_account, paper_trading, portfolio, strategies

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    """Configure and run the Streamlit multipage application."""
    settings = get_settings()
    st.set_page_config(
        page_title=settings.app_name,
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    database = DatabaseManager(settings.database_full_path)

    def dashboard_page() -> None:
        dashboard.render(database)

    def backtest_page() -> None:
        backtest.render(database)

    def strategies_page() -> None:
        strategies.render(database)

    def paper_trading_page() -> None:
        paper_trading.render(database)

    def portfolio_page() -> None:
        portfolio.render(database)

    def automation_page() -> None:
        automation.render(database)

    pages = {
        "Overview": [
            st.Page(
                dashboard_page,
                title="Dashboard",
                icon="🏠",
                url_path="dashboard",
                default=True,
            ),
        ],
        "Research": [
            st.Page(
                backtest_page,
                title="Run Backtest",
                icon="🧪",
                url_path="backtest",
            ),
            st.Page(
                strategies_page,
                title="Strategies",
                icon="📐",
                url_path="strategies",
            ),
        ],
        "Trading": [
            st.Page(
                paper_trading_page,
                title="Paper Trading",
                icon="📝",
                url_path="paper-trading",
            ),
            st.Page(
                portfolio_page,
                title="Paper Portfolio",
                icon="📊",
                url_path="portfolio",
            ),
            st.Page(
                paper_account.render,
                title="Paper Account",
                icon="💼",
                url_path="paper-account",
            ),
            st.Page(
                automation_page,
                title="Automation",
                icon="🤖",
                url_path="automation",
            ),
        ],
        "Help": [
            st.Page(
                about.render,
                title="About",
                icon="ℹ️",
                url_path="about",
            ),
        ],
    }

    navigation = st.navigation(pages)
    navigation.run()


if __name__ == "__main__":
    main()

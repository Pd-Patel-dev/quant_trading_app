"""Quant Strategy Lab — Streamlit entry point."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings
from data.database import DatabaseManager
from views import about, backtest, dashboard, paper_account, strategies

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
                strategies.render,
                title="Strategies",
                icon="📐",
                url_path="strategies",
            ),
        ],
        "Trading": [
            st.Page(
                paper_account.render,
                title="Paper Account",
                icon="💼",
                url_path="paper-account",
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

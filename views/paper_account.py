"""Read-only paper account page."""

from __future__ import annotations

import logging

import streamlit as st

from broker.alpaca_account import AlpacaPaperAccountClient
from config.settings import get_settings
from core.exceptions import AlpacaConnectionError, ConfigurationError
from ui.components import format_currency, render_status_banner

logger = logging.getLogger(__name__)


def render() -> None:
    """Render the paper account page."""
    settings = get_settings()
    st.title("Paper Account")
    st.markdown(
        "This page connects to your Alpaca **paper** account in read-only mode. "
        "No orders can be submitted in milestone 1."
    )

    if not settings.alpaca_configured:
        render_status_banner(
            "Credentials Required",
            "Add your Alpaca paper credentials to a local `.env` file before testing the connection.",
            banner_type="warning",
        )
        st.markdown(
            """
            ### Setup instructions
            1. Copy `.env.example` to `.env`
            2. Add your Alpaca paper credentials:
               - `ALPACA_API_KEY`
               - `ALPACA_SECRET_KEY`
            3. Restart the Streamlit app
            4. Return to this page and click **Test Paper Connection**

            Never commit your `.env` file or share your secret key.
            """
        )
        return

    st.info("Order submission: **Disabled in milestone 1**")

    if st.button("Test Paper Connection", type="primary"):
        try:
            client = AlpacaPaperAccountClient(
                settings.alpaca_api_key,
                settings.alpaca_secret_key,
            )
            summary = client.get_account_summary()
            st.success("Connected to Alpaca paper account.")

            cols = st.columns(3)
            cols[0].metric("Cash", format_currency(float(summary["cash"])))
            cols[1].metric("Portfolio Value", format_currency(float(summary["portfolio_value"])))
            cols[2].metric("Buying Power", format_currency(float(summary["buying_power"])))

            cols2 = st.columns(3)
            cols2[0].metric("Equity", format_currency(float(summary["equity"])))
            cols2[1].metric("Account Status", str(summary["status"]))
            cols2[2].metric(
                "Trading Blocked",
                "Yes" if summary["trading_blocked"] else "No",
            )

            with st.expander("Additional Account Details"):
                st.write(f"Account Number: {summary['account_number']}")
                st.write(f"Currency: {summary['currency']}")
                st.write(f"Last Equity: {format_currency(float(summary['last_equity']))}")
                st.write(
                    "Pattern Day Trader: "
                    f"{'Yes' if summary['pattern_day_trader'] else 'No'}"
                )
        except (ConfigurationError, AlpacaConnectionError) as exc:
            st.error(str(exc))
        except Exception as exc:
            logger.exception("Unexpected paper account error.")
            st.error(f"Unable to retrieve account information: {exc}")

"""About page."""

from __future__ import annotations

import streamlit as st


def render() -> None:
    """Render the about page."""
    st.title("About Quant Strategy Lab")
    st.markdown(
        """
        **Quant Strategy Lab** is a beginner-friendly algorithmic trading application focused on
        research, learning, and safe paper-trading preparation.

        ### Architecture
        ```
        Market data
            ↓
        Strategy
            ↓
        Signals
            ↓
        Backtesting engine
            ↓
        Metrics
            ↓
        Streamlit dashboard
        ```

        ### Backtesting vs paper trading vs live trading
        - **Backtesting** simulates a strategy on historical data. It helps you learn and compare ideas,
          but past performance does not guarantee future results.
        - **Paper trading** uses real market data and a simulated brokerage account with fake money.
          It helps validate workflow and connectivity without financial risk.
        - **Live trading** uses real money. It is disabled in this milestone and should only be considered
          after extensive testing and risk controls.

        ### Safety notice
        Algorithmic trading involves risk. Backtesting and paper trading cannot guarantee future profit.
        Always understand a strategy before risking capital.

        ### Current milestone
        Milestone 1 provides project foundation, Alpaca historical data, a moving-average strategy,
        a backtesting engine, Streamlit UI, SQLite storage, and automated tests.
        """
    )

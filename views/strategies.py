"""Strategy documentation page."""

from __future__ import annotations

import streamlit as st


def render() -> None:
    """Render the strategies page."""
    st.title("Strategies")
    st.markdown(
        "Quant Strategy Lab uses a modular strategy interface. "
        "Each strategy calculates indicators, generates signals, and plugs into the same backtesting engine."
    )

    st.subheader("Moving Average Crossover")
    st.markdown(
        """
        **Moving Average Crossover** is a beginner-friendly trend-following strategy.

        ### Short moving average
        The short moving average (default: 50 days) reacts quickly to recent price changes.
        It represents the recent trend.

        ### Long moving average
        The long moving average (default: 200 days) reacts slowly and represents the longer-term trend.

        ### Golden cross
        A **golden cross** happens when the short average crosses **above** the long average.
        This strategy treats it as a **BUY** signal.

        ### Death cross
        A **death cross** happens when the short average crosses **below** the long average.
        This strategy treats it as a **SELL** signal.

        ### Important concepts
        - Moving averages are **lagging indicators**. They follow price rather than predict it.
        - In sideways markets, crossovers can produce false signals (**whipsaws**).
        - **Position** tells you whether the strategy wants to be invested (1) or in cash (0).
        - **Signal** tells you when a crossover happened (BUY, SELL, or HOLD).

        ### Rules
        1. Calculate short and long simple moving averages of the closing price.
        2. Stay invested when the short average is above the long average.
        3. Buy only on a valid golden cross (previous short ≤ previous long, current short > current long).
        4. Sell only on a valid death cross (previous short ≥ previous long, current short < current long).
        5. Trades execute at the **next day's open** to reduce look-ahead bias.

        One strategy is available in this milestone. Additional strategies will be added through the same interface.
        """
    )

    st.info("Available now: Moving Average Crossover (50/200 default windows)")

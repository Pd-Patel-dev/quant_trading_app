"""Reusable Plotly chart builders."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from core.models import Trade


def price_and_moving_averages_chart(
    processed_data: pd.DataFrame,
    trades: list[Trade],
    short_column: str = "SMA_Short",
    long_column: str = "SMA_Long",
) -> go.Figure:
    """Plot closing price, moving averages, and executed trade markers."""
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=processed_data.index,
            y=processed_data["Close"],
            mode="lines",
            name="Close",
            line={"color": "#1f77b4"},
        )
    )
    if short_column in processed_data.columns:
        figure.add_trace(
            go.Scatter(
                x=processed_data.index,
                y=processed_data[short_column],
                mode="lines",
                name="Short SMA",
                line={"color": "#ff7f0e"},
            )
        )
    if long_column in processed_data.columns:
        figure.add_trace(
            go.Scatter(
                x=processed_data.index,
                y=processed_data[long_column],
                mode="lines",
                name="Long SMA",
                line={"color": "#9467bd"},
            )
        )

    buy_trades = [trade for trade in trades if trade.side == "BUY"]
    sell_trades = [trade for trade in trades if trade.side == "SELL"]

    if buy_trades:
        figure.add_trace(
            go.Scatter(
                x=[trade.timestamp for trade in buy_trades],
                y=[trade.execution_price for trade in buy_trades],
                mode="markers",
                name="Buy",
                marker={"symbol": "triangle-up", "size": 12, "color": "#2ca02c"},
            )
        )
    if sell_trades:
        figure.add_trace(
            go.Scatter(
                x=[trade.timestamp for trade in sell_trades],
                y=[trade.execution_price for trade in sell_trades],
                mode="markers",
                name="Sell",
                marker={"symbol": "triangle-down", "size": 12, "color": "#d62728"},
            )
        )

    figure.update_layout(
        title="Price and Moving Averages",
        xaxis_title="Date",
        yaxis_title="Price",
        hovermode="x unified",
        height=500,
    )
    return figure


def equity_curve_chart(equity_curve: pd.DataFrame, starting_capital: float) -> go.Figure:
    """Plot portfolio value against the initial capital reference line."""
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=equity_curve.index,
            y=equity_curve["PortfolioValue"],
            mode="lines",
            name="Portfolio Value",
            line={"color": "#1f77b4"},
        )
    )
    figure.add_hline(
        y=starting_capital,
        line_dash="dash",
        line_color="#888888",
        annotation_text="Initial Capital",
    )
    figure.update_layout(
        title="Portfolio Equity Curve",
        xaxis_title="Date",
        yaxis_title="Portfolio Value",
        hovermode="x unified",
        height=400,
    )
    return figure


def drawdown_chart(equity_curve: pd.DataFrame) -> go.Figure:
    """Plot drawdown percentage over time."""
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=equity_curve.index,
            y=equity_curve["Drawdown"],
            mode="lines",
            name="Drawdown",
            fill="tozeroy",
            line={"color": "#d62728"},
        )
    )
    figure.update_layout(
        title="Drawdown",
        xaxis_title="Date",
        yaxis_title="Drawdown (%)",
        hovermode="x unified",
        height=350,
    )
    return figure


def strategy_vs_buy_hold_chart(
    equity_curve: pd.DataFrame,
    close_prices: pd.Series,
    starting_capital: float,
) -> go.Figure:
    """Compare normalized strategy performance against buy-and-hold."""
    strategy_normalized = (equity_curve["PortfolioValue"] / starting_capital) * 100.0
    valid_close = close_prices.dropna()
    first_close = float(valid_close.iloc[0]) if not valid_close.empty else 1.0
    buy_hold_normalized = (close_prices / first_close) * 100.0

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=equity_curve.index,
            y=strategy_normalized,
            mode="lines",
            name="Strategy",
            line={"color": "#1f77b4"},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=buy_hold_normalized.index,
            y=buy_hold_normalized,
            mode="lines",
            name="Buy and Hold",
            line={"color": "#ff7f0e", "dash": "dot"},
        )
    )
    figure.update_layout(
        title="Strategy vs Buy-and-Hold (Normalized to 100)",
        xaxis_title="Date",
        yaxis_title="Indexed Value",
        hovermode="x unified",
        height=400,
    )
    return figure

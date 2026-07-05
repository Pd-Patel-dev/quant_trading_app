"""Crypto EMA trend strategy chart."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from core.models import Trade


def price_and_emas_chart(
    processed_data: pd.DataFrame,
    trades: list[Trade],
    equity_curve: pd.DataFrame | None = None,
) -> go.Figure:
    """Plot closing price, EMAs, stop threshold, and execution markers."""
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
    for column, label, color in (
        ("EMA_Fast", "Fast EMA", "#ff7f0e"),
        ("EMA_Medium", "Medium EMA", "#2ca02c"),
        ("EMA_Long", "Long EMA", "#9467bd"),
    ):
        if column in processed_data.columns:
            figure.add_trace(
                go.Scatter(
                    x=processed_data.index,
                    y=processed_data[column],
                    mode="lines",
                    name=label,
                    line={"color": color},
                )
            )

    if equity_curve is not None and "StopPrice" in equity_curve.columns:
        stop_series = equity_curve["StopPrice"].replace(0, pd.NA).dropna()
        if not stop_series.empty:
            figure.add_trace(
                go.Scatter(
                    x=stop_series.index,
                    y=stop_series.values,
                    mode="lines",
                    name="Stop threshold",
                    line={"color": "#d62728", "dash": "dot"},
                )
            )

    for trade in trades:
        is_stop = getattr(trade, "signal_reason", None) == "STOP_LOSS"
        marker = {
            "symbol": "triangle-down" if trade.side == "SELL" else "triangle-up",
            "size": 12,
            "color": "#8c564b" if is_stop else ("#d62728" if trade.side == "SELL" else "#2ca02c"),
        }
        figure.add_trace(
            go.Scatter(
                x=[trade.timestamp],
                y=[trade.execution_price],
                mode="markers",
                name=f"{trade.side} ({getattr(trade, 'signal_reason', None) or 'signal'})",
                marker=marker,
                showlegend=True,
            )
        )

    figure.update_layout(
        title="Crypto EMA Trend — Price, EMAs, and Executions",
        xaxis_title="Date",
        yaxis_title="Price",
        hovermode="x unified",
        height=520,
    )
    return figure

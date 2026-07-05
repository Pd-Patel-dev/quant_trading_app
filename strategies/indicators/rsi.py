"""Wilder-style RSI indicator."""

from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_rsi(close: pd.Series, period: int) -> pd.Series:
    """Calculate RSI using Wilder-style exponential smoothing.

    Returns a new Series; does not modify the input.
    RSI values are clipped to [0, 100].
    """
    if period < 2:
        raise ValueError("RSI period must be at least 2.")

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    alpha = 1.0 / period

    avg_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss > 0)), 0.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss == 0)), np.nan)

    rsi = rsi.clip(lower=0.0, upper=100.0)
    return rsi

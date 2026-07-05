"""Validation service tests."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from market_data.validation_service import MarketDataValidationService


def _valid_frame() -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc)]
    )
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1000.0, 1100.0],
        },
        index=index,
    )


def test_valid_ohlc_accepted() -> None:
    result = MarketDataValidationService().validate(_valid_frame())
    assert result.is_usable


def test_high_below_close_blocked() -> None:
    frame = _valid_frame()
    frame.loc[frame.index[0], "High"] = 100.0
    result = MarketDataValidationService().validate(frame)
    assert not result.is_usable


def test_negative_price_blocked() -> None:
    frame = _valid_frame()
    frame.loc[frame.index[0], "Open"] = -1.0
    result = MarketDataValidationService().validate(frame)
    assert not result.is_usable


def test_negative_volume_blocked() -> None:
    frame = _valid_frame()
    frame.loc[frame.index[0], "Volume"] = -1.0
    result = MarketDataValidationService().validate(frame)
    assert not result.is_usable


def test_nan_ohlc_blocked() -> None:
    frame = _valid_frame()
    frame.loc[frame.index[0], "Close"] = np.nan
    result = MarketDataValidationService().validate(frame)
    assert not result.is_usable


def test_duplicate_timestamp_handled() -> None:
    frame = _valid_frame()
    frame = pd.concat([frame, frame.iloc[[0]]])
    result = MarketDataValidationService().validate(frame)
    assert result.is_usable
    assert result.cleaned_data is not None
    assert not result.cleaned_data.index.duplicated().any()

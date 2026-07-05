"""Validate downloaded market data before persistence."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from market_data.models import MarketDataValidationResult, QualityIssueType

OHLC_COLUMNS = ["Open", "High", "Low", "Close"]
REQUIRED_COLUMNS = OHLC_COLUMNS + ["Volume"]


class MarketDataValidationService:
    """Validate OHLCV DataFrames prior to database insertion."""

    def validate(
        self,
        data: pd.DataFrame,
        requested_start: datetime | None = None,
        requested_end: datetime | None = None,
    ) -> MarketDataValidationResult:
        passed: list[str] = []
        warnings: list[str] = []
        blocking: list[str] = []
        issues: list[dict] = []

        if data is None or data.empty:
            blocking.append("DataFrame is empty.")
            return MarketDataValidationResult(
                is_usable=False,
                passed_checks=passed,
                warnings=warnings,
                blocking_errors=blocking,
                quality_issues=issues,
            )

        frame = data.copy()
        passed.append("DataFrame is not empty.")

        if not isinstance(frame.index, pd.DatetimeIndex):
            blocking.append("Index is not a DatetimeIndex.")
            return MarketDataValidationResult(False, passed, warnings, blocking, issues)

        if frame.index.tz is None:
            frame.index = frame.index.tz_localize("UTC")
        else:
            frame.index = frame.index.tz_convert("UTC")
        passed.append("Timezone-aware UTC index.")

        if frame.index.duplicated().any():
            dup_count = int(frame.index.duplicated().sum())
            warnings.append(f"Removed {dup_count} duplicate timestamps.")
            issues.append(
                _issue(
                    QualityIssueType.DUPLICATE_TIMESTAMP,
                    f"{dup_count} duplicate timestamps detected.",
                )
            )
            frame = frame[~frame.index.duplicated(keep="last")]

        frame = frame.sort_index()

        if not frame.index.is_monotonic_increasing:
            blocking.append("Index is not sorted oldest to newest.")
            issues.append(
                _issue(QualityIssueType.OUT_OF_ORDER, "Timestamps are out of order.")
            )
        else:
            passed.append("Index is sorted.")

        missing_cols = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
        if missing_cols:
            blocking.append(f"Missing required columns: {missing_cols}")
            return MarketDataValidationResult(False, passed, warnings, blocking, issues, frame)

        passed.append("Required OHLCV columns exist.")

        for column in REQUIRED_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

        for column in OHLC_COLUMNS:
            if frame[column].isna().any():
                blocking.append(f"NaN values in {column}.")
                issues.append(
                    _issue(QualityIssueType.MISSING_VALUE, f"NaN in {column}.", "ERROR")
                )
            if np.isinf(frame[column]).any():
                blocking.append(f"Infinity in {column}.")
                issues.append(
                    _issue(QualityIssueType.MISSING_VALUE, f"Infinity in {column}.", "ERROR")
                )

        if frame["Volume"].isna().any():
            warnings.append("Volume contains NaN; filling with zero.")
            frame["Volume"] = frame["Volume"].fillna(0)

        for column in OHLC_COLUMNS:
            negatives = frame[column] <= 0
            if negatives.any():
                blocking.append(f"Non-positive values in {column}.")
                issues.append(
                    _issue(
                        QualityIssueType.NEGATIVE_PRICE,
                        f"Non-positive {column} values.",
                        "ERROR",
                    )
                )

        if (frame["Volume"] < 0).any():
            blocking.append("Negative volume detected.")
            issues.append(
                _issue(QualityIssueType.NEGATIVE_VOLUME, "Negative volume.", "ERROR")
            )

        invalid_high = (
            (frame["High"] < frame["Open"])
            | (frame["High"] < frame["Close"])
            | (frame["High"] < frame["Low"])
        )
        if invalid_high.any():
            blocking.append("High is below Open, Close, or Low.")
            issues.append(
                _issue(QualityIssueType.INVALID_OHLC, "Invalid high/low relationship.", "ERROR")
            )

        invalid_low = (
            (frame["Low"] > frame["Open"])
            | (frame["Low"] > frame["Close"])
            | (frame["Low"] > frame["High"])
        )
        if invalid_low.any():
            blocking.append("Low is above Open, Close, or High.")
            issues.append(
                _issue(QualityIssueType.INVALID_OHLC, "Invalid low/high relationship.", "ERROR")
            )

        if requested_start and requested_end:
            start = _ensure_utc(requested_start)
            end = _ensure_utc(requested_end)
            out_of_range = (frame.index < pd.Timestamp(start)) | (frame.index > pd.Timestamp(end))
            if out_of_range.any():
                blocking.append("Bars fall outside requested date interval.")
                issues.append(
                    _issue(
                        QualityIssueType.MISSING_VALUE,
                        "Timestamps outside requested interval.",
                        "ERROR",
                    )
                )
            else:
                passed.append("Bars within requested interval.")

        is_usable = len(blocking) == 0
        if is_usable:
            passed.append("All blocking checks passed.")

        return MarketDataValidationResult(
            is_usable=is_usable,
            passed_checks=passed,
            warnings=warnings,
            blocking_errors=blocking,
            quality_issues=issues,
            cleaned_data=frame.sort_index(),
        )


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _issue(issue_type: QualityIssueType, description: str, severity: str = "WARNING") -> dict:
    return {
        "issue_type": issue_type.value,
        "description": description,
        "severity": severity,
    }

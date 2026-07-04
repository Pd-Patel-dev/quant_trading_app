"""Reusable Streamlit UI components."""

from __future__ import annotations

import streamlit as st


def format_currency(value: float) -> str:
    """Format a number as USD currency."""
    return f"${value:,.2f}"


def format_percent(value: float, decimals: int = 2) -> str:
    """Format a number as a percentage."""
    return f"{value:.{decimals}f}%"


def metric_card(label: str, value: str, help_text: str | None = None) -> None:
    """Render a single metric card."""
    st.metric(label=label, value=value, help=help_text)


def render_status_banner(title: str, message: str, banner_type: str = "info") -> None:
    """Render an application status banner."""
    if banner_type == "success":
        st.success(f"**{title}** — {message}")
    elif banner_type == "warning":
        st.warning(f"**{title}** — {message}")
    elif banner_type == "error":
        st.error(f"**{title}** — {message}")
    else:
        st.info(f"**{title}** — {message}")


def render_empty_state(title: str, message: str) -> None:
    """Render a friendly empty-state message."""
    st.markdown(f"### {title}")
    st.markdown(message)

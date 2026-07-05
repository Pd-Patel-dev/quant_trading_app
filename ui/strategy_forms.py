"""Dynamic Streamlit inputs from strategy parameter definitions."""

from __future__ import annotations

from typing import Any

import streamlit as st

from strategies.metadata import ParameterType, StrategyParameterDefinition


def render_parameter_inputs(
    definitions: tuple[StrategyParameterDefinition, ...],
    key_prefix: str,
) -> dict[str, Any]:
    """Build parameter dict from Streamlit widgets."""
    values: dict[str, Any] = {}
    for param in definitions:
        widget_key = f"{key_prefix}_{param.name}"
        if param.parameter_type == ParameterType.INTEGER:
            values[param.name] = st.number_input(
                param.display_name,
                min_value=int(param.minimum_value or 0),
                max_value=int(param.maximum_value or 1000),
                value=int(param.default_value),
                step=int(param.step or 1),
                help=param.description,
                key=widget_key,
            )
        elif param.parameter_type == ParameterType.FLOAT:
            values[param.name] = st.number_input(
                param.display_name,
                min_value=float(param.minimum_value or 0),
                max_value=float(param.maximum_value or 100),
                value=float(param.default_value),
                step=float(param.step or 0.5),
                help=param.description,
                key=widget_key,
            )
        elif param.parameter_type == ParameterType.BOOLEAN:
            values[param.name] = st.checkbox(
                param.display_name,
                value=bool(param.default_value),
                help=param.description,
                key=widget_key,
            )
        elif param.parameter_type == ParameterType.CHOICE:
            choices = param.choices or (str(param.default_value),)
            values[param.name] = st.selectbox(
                param.display_name,
                options=list(choices),
                help=param.description,
                key=widget_key,
            )
    return values

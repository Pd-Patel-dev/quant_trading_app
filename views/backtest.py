"""Backtest execution page."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from backtesting.engine import BacktestEngine
from config.settings import get_settings
from core.exceptions import ConfigurationError, MarketDataError, QuantTradingError
from core.models import BacktestConfiguration, BacktestResult, SignalType
from data.alpaca_data import AlpacaMarketDataProvider
from data.database import DatabaseManager
from strategies.moving_average import MovingAverageCrossoverStrategy
from ui import charts, components

logger = logging.getLogger(__name__)

SESSION_KEY = "latest_backtest_result"


def _init_session_state() -> None:
    if SESSION_KEY not in st.session_state:
        st.session_state[SESSION_KEY] = None


def render(database: DatabaseManager) -> None:
    """Render the backtest page."""
    settings = get_settings()
    _init_session_state()

    st.title("Run Backtest")
    st.markdown(
        "Configure a moving-average crossover backtest, download historical data from Alpaca, "
        "and review performance metrics and charts."
    )

    default_start = date.today() - timedelta(days=365 * 5)
    default_end = date.today()

    with st.form("backtest_form"):
        symbol = st.text_input("Ticker Symbol", value="SPY").strip().upper()
        date_col1, date_col2 = st.columns(2)
        with date_col1:
            start_date = st.date_input("Start Date", value=default_start)
        with date_col2:
            end_date = st.date_input("End Date", value=default_end)

        capital_col1, capital_col2 = st.columns(2)
        with capital_col1:
            starting_capital = st.number_input(
                "Starting Capital ($)",
                min_value=1.0,
                value=settings.default_starting_capital,
                step=100.0,
            )
        with capital_col2:
            allocation = st.number_input(
                "Strategy Allocation ($)",
                min_value=1.0,
                value=settings.default_starting_capital,
                step=100.0,
            )

        ma_col1, ma_col2 = st.columns(2)
        with ma_col1:
            short_window = st.number_input("Short Moving Average", min_value=2, value=50, step=1)
        with ma_col2:
            long_window = st.number_input("Long Moving Average", min_value=3, value=200, step=1)

        cost_col1, cost_col2, cost_col3 = st.columns(3)
        with cost_col1:
            commission = st.number_input(
                "Commission per Order ($)",
                min_value=0.0,
                value=settings.default_commission,
                step=0.01,
            )
        with cost_col2:
            slippage_percent = st.number_input(
                "Slippage (%)",
                min_value=0.0,
                value=settings.default_slippage_percent * 100.0,
                step=0.01,
                format="%.4f",
            )
        with cost_col3:
            cash_reserve_percent = st.number_input(
                "Cash Reserve (%)",
                min_value=0.0,
                max_value=100.0,
                value=settings.default_cash_reserve_percent * 100.0,
                step=0.5,
            )

        submitted = st.form_submit_button("Run Backtest", type="primary")

    if submitted:
        _run_backtest(
            database=database,
            settings=settings,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            starting_capital=starting_capital,
            allocation=allocation,
            short_window=int(short_window),
            long_window=int(long_window),
            commission=commission,
            slippage_percent=slippage_percent / 100.0,
            cash_reserve_percent=cash_reserve_percent / 100.0,
        )

    result: BacktestResult | None = st.session_state.get(SESSION_KEY)
    if result is not None:
        _display_results(result, int(short_window), int(long_window))


def _run_backtest(
    database: DatabaseManager,
    settings,
    symbol: str,
    start_date: date,
    end_date: date,
    starting_capital: float,
    allocation: float,
    short_window: int,
    long_window: int,
    commission: float,
    slippage_percent: float,
    cash_reserve_percent: float,
) -> None:
    if not settings.alpaca_configured:
        components.render_status_banner(
            "Alpaca Credentials Missing",
            "Copy `.env.example` to `.env` and add your paper API keys before running a backtest.",
            banner_type="warning",
        )
        return

    try:
        configuration = BacktestConfiguration(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            starting_capital=starting_capital,
            allocation=allocation,
            commission=commission,
            slippage_percent=slippage_percent,
            cash_reserve_percent=cash_reserve_percent,
        )
        strategy = MovingAverageCrossoverStrategy(
            short_window=short_window,
            long_window=long_window,
        )
    except QuantTradingError as exc:
        st.error(str(exc))
        return

    try:
        with st.spinner("Downloading historical data and running backtest..."):
            provider = AlpacaMarketDataProvider(
                settings.alpaca_api_key,
                settings.alpaca_secret_key,
            )
            data = provider.get_daily_bars(symbol, start_date, end_date)
            engine = BacktestEngine(strategy, configuration, data)
            result = engine.run()

            database.save_backtest_summary(
                result=result,
                configuration_start=start_date.isoformat(),
                configuration_end=end_date.isoformat(),
                allocation=allocation,
            )
            st.session_state[SESSION_KEY] = result
    except (ConfigurationError, MarketDataError, QuantTradingError) as exc:
        st.error(str(exc))
    except Exception as exc:
        logger.exception("Unexpected backtest failure.")
        st.error(f"An unexpected error occurred: {exc}")


def _display_results(result: BacktestResult, short_window: int, long_window: int) -> None:
    st.subheader("Backtest Results")
    profit_loss = result.final_value - result.starting_capital

    metrics = st.columns(4)
    metrics[0].metric("Final Portfolio Value", components.format_currency(result.final_value))
    metrics[1].metric("Total Return", components.format_percent(result.total_return_percent))
    metrics[2].metric("Buy-and-Hold Return", components.format_percent(result.buy_and_hold_return_percent))
    metrics[3].metric("Profit / Loss", components.format_currency(profit_loss))

    metrics2 = st.columns(4)
    metrics2[0].metric("Completed Trades", str(result.completed_trades))
    metrics2[1].metric("Win Rate", components.format_percent(result.win_rate_percent))
    metrics2[2].metric("Max Drawdown", components.format_percent(result.maximum_drawdown_percent))
    metrics2[3].metric("Sharpe Ratio", f"{result.sharpe_ratio:.2f}")

    _show_warnings(result, long_window)

    st.plotly_chart(
        charts.price_and_moving_averages_chart(result.processed_data, result.trades),
        use_container_width=True,
    )
    st.plotly_chart(
        charts.equity_curve_chart(result.equity_curve, result.starting_capital),
        use_container_width=True,
    )
    st.plotly_chart(charts.drawdown_chart(result.equity_curve), use_container_width=True)
    st.plotly_chart(
        charts.strategy_vs_buy_hold_chart(
            result.equity_curve,
            result.processed_data["Close"],
            result.starting_capital,
        ),
        use_container_width=True,
    )

    if result.trades:
        st.subheader("Trade History")
        trade_rows = [
            {
                "Timestamp": trade.timestamp,
                "Side": trade.side,
                "Quantity": trade.quantity,
                "Execution Price": trade.execution_price,
                "Gross Value": trade.gross_value,
                "Commission": trade.commission,
                "Cash After Trade": trade.cash_after_trade,
                "Position After Trade": trade.position_after_trade,
                "Reason": trade.reason,
            }
            for trade in result.trades
        ]
        trade_df = pd.DataFrame(trade_rows)
        st.dataframe(trade_df, use_container_width=True)
        st.download_button(
            "Download Trade History (CSV)",
            data=trade_df.to_csv(index=False),
            file_name=f"{result.symbol}_trades.csv",
            mime="text/csv",
        )

    st.download_button(
        "Download Processed Data (CSV)",
        data=result.processed_data.to_csv(),
        file_name=f"{result.symbol}_processed.csv",
        mime="text/csv",
    )
    st.download_button(
        "Download Equity Curve (CSV)",
        data=result.equity_curve.to_csv(),
        file_name=f"{result.symbol}_equity_curve.csv",
        mime="text/csv",
    )


def _show_warnings(result: BacktestResult, long_window: int) -> None:
    data_length = len(result.processed_data)
    if data_length < long_window:
        st.warning(
            f"Historical data contains {data_length} rows, which is shorter than the "
            f"long moving-average window ({long_window}). Signals may be limited."
        )

    buy_sell_trades = [trade for trade in result.trades if trade.side in ("BUY", "SELL")]
    if not buy_sell_trades:
        st.warning("No crossover trades were executed during this backtest.")

    final_position = int(result.equity_curve["PositionQuantity"].iloc[-1])
    if final_position > 0:
        st.warning(
            f"The strategy finished with an open position of {final_position} share(s). "
            "This is not counted as a completed trade."
        )

    buy_signals = result.processed_data["Signal"] == SignalType.BUY.value
    if buy_signals.any():
        sample_price = float(result.processed_data.loc[buy_signals, "Open"].iloc[0])
        allocation_estimate = result.starting_capital
        if sample_price > allocation_estimate:
            st.warning(
                "The strategy allocation may be too small to purchase even one whole share."
            )

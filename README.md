# Quant Strategy Lab

A modular Python application for researching algorithmic trading strategies. Quant Strategy Lab connects to Alpaca for historical market data and paper account access, runs backtests with realistic execution assumptions, and supports manual paper-order workflows with strategy-level virtual fund allocation.

**Current release:** Milestone 8 - Crypto Daily EMA Trend Following  
**Trading mode:** Paper only (live trading disabled)

---

## Overview

Quant Strategy Lab helps you move from idea to evidence in a structured way:

1. Download historical OHLCV data from Alpaca
2. Apply a strategy to generate signals
3. Simulate trades with a backtesting engine
4. Create and activate strategies with virtual fund allocation
5. Evaluate daily signals and generate paper order proposals
6. Manually confirm and submit paper orders to Alpaca
7. Track strategy-level positions and cash in a local ledger

The architecture is intentionally modular so new strategies can be added without changing the backtesting engine or UI framework.

---

## Milestone 2 Features

| Area | Capability |
|------|------------|
| Strategy management | Create, draft, activate, pause, resume, stop, archive, restore, and permanently delete unused drafts |
| Virtual allocation | Local paper capital pool with per-strategy fund allocation |
| Signal evaluation | Daily completed-bar signal evaluation with entry policies |
| Order proposals | Risk-validated BUY/SELL proposals (not auto-submitted) |
| Manual confirmation | Requires PAPER text and checkboxes before submission |
| Paper orders | Alpaca market orders (paper=True, whole shares, DAY) |
| Order sync | Fill, partial fill, reject, and cancel synchronization |
| Strategy ledger | Append-only cash ledger and local position tracking |
| Portfolio view | Managed vs unmanaged position reconciliation warnings |
| Safety script | Read-only readiness checks before trading |
| **Automation (M3)** | One-shot CLI workers for after-close evaluation, market-open execution, order sync, and reconciliation |
| **Kill switch** | Global emergency block on automated submissions (engaged by default) |
| **Audit log** | Append-only record of every automation event |
| **Strategy Lab (M4)** | Registry-based multi-strategy research, comparison, train/test, walk-forward, portfolio simulation |
| **RSI Mean Reversion** | Second strategy plugin (manual paper only; automation disabled) |

---

## Milestone 4 — Multi-Strategy Research Lab

### Strategy Plugin Architecture

```text
Create strategy class
    ↓
Define metadata and parameters
    ↓
Register strategy
    ↓
Write unit tests
    ↓
Run historical backtest
    ↓
Run train/test evaluation
    ↓
Run walk-forward evaluation
    ↓
Approve for paper trading
    ↓
Observe paper performance
```

Registered strategies:

| Type | Category | Automation |
|------|----------|------------|
| `moving_average_crossover` | TREND_FOLLOWING | Supported |
| `rsi_mean_reversion` | MEAN_REVERSION | Disabled |

Add a new strategy by implementing `BaseStrategy`, defining `StrategyMetadata`, registering in `strategies/registry.py`, and adding tests.

### RSI Mean Reversion Rules

- **BUY:** Previous RSI ≤ oversold threshold AND current RSI > oversold threshold, from cash
- **SELL:** Previous RSI < exit threshold AND current RSI ≥ exit threshold, from long
- Long-only; overbought threshold is research-only (no shorts)
- Wilder-style RSI calculated with Pandas (no TA-Lib)

### Trend Following vs Mean Reversion

Moving-average crossover follows trends; RSI mean reversion buys oversold recoveries and exits at a recovery threshold.

### Research vs Paper Trading

**Strategy Lab** results are labeled **Research Simulation**. They never write to the paper ledger or submit orders. **Paper Brokerage Account** values remain separate.

### Paper Trading Approval

New strategies default to `DRAFT`, `paper_trading_approved = False`, `automation_enabled = False`. To approve:

1. Complete at least one backtest for the same strategy type, symbol, and parameters
2. Check all approval boxes on the Strategies page
3. Type exactly: `APPROVE PAPER STRATEGY`

Approval does not activate the strategy. RSI automated trading remains disabled.

### Strategy Status Lifecycle

```text
DRAFT → ACTIVE ↔ PAUSED → STOPPED
              ↓              ↓
           ARCHIVED ←────────┘
              ↓
           DRAFT (restore)
```

| Status | Meaning |
|--------|---------|
| **DRAFT** | Configured but not trading. May be edited, approved, activated, stopped, archived, or permanently deleted if unused. |
| **ACTIVE** | Approved and eligible for paper trading and automation (when separately enabled). Only one ACTIVE strategy per asset type and symbol. |
| **PAUSED** | Temporarily halted. Existing positions and open orders remain. Order sync and fill processing continue. No new proposals or automated submissions. |
| **STOPPED** | Permanently disabled until restored via archive workflow. Positions and history preserved. Does not liquidate. |
| **ARCHIVED** | Hidden from normal selectors. All financial history preserved. Restore as DRAFT to reconfigure. |

**Pause**
- Stops new trading decisions
- Keeps positions and history
- Can be resumed

**Stop**
- Permanently disables future activity
- Keeps positions and history
- Does not liquidate

**Archive**
- Hides the strategy from normal management
- Preserves all financial history

**Delete**
- Available only for unused drafts (no signals, orders, trading ledger entries, etc.)
- Permanently removes the strategy definition
- If deletion is blocked, archive instead

Pausing or stopping a strategy with an open position does **not** close the position or cancel submitted orders. A paused strategy with a pending order continues to synchronize with Alpaca.

Restoring an archived strategy returns it to **DRAFT** with automation and approvals reset — you must approve and activate again before trading.

### Troubleshooting Deletion Failures

Permanent delete is blocked when related history exists (signals, proposals, orders, non-allocation ledger entries, positions, audit records). The Strategies page lists each blocking reason. Use **Archive** instead to retain history.

Validate lifecycle health:

```powershell
python scripts/check_strategy_management.py
```

Historical backtests and paper trading are simulations. They do not guarantee future profits or reproduce every live-market condition.

---

## Milestone 3 — Automated Daily Paper Trading

Streamlit is the **monitoring and configuration interface only**. Automated trading runs through **one-shot CLI workers** that start, perform one job, record results, and exit. Schedule them with Windows Task Scheduler (tasks are created **disabled** by default).

### Daily Timeline

```text
Market closes
    ↓
After-close worker evaluates completed bars
    ↓
New signal and proposal are saved
    ↓
Next market day begins
    ↓
Market-open worker revalidates proposal
    ↓
Paper order is submitted
    ↓
Synchronization worker processes order updates
    ↓
Ledger and position are updated
```

### Why Workers Run Outside Streamlit

Streamlit reruns the entire script on every interaction. A background scheduler or infinite loop inside Streamlit would be unreliable and unsafe for order submission. Workers are independent processes with database-backed locks.

### Global Automation Approval

Global automation is **disabled by default**. To enable:

1. Readiness checks must pass (no unknown orders, no critical reconciliation issues)
2. Check both acknowledgment boxes on the Automation page
3. Type exactly: `ENABLE AUTOMATED PAPER TRADING`

Enabling global automation does **not** enable individual strategies.

### Per-Strategy Automation Approval

Each strategy has `automation_enabled = false` by default. To enable for a strategy:

1. Strategy must be active with valid allocation
2. Global automation must be enabled
3. Check: `I understand this strategy may place paper orders automatically`
4. Type exactly: `ENABLE PAPER AUTOMATION`

Disabling automation (global or per-strategy) does **not** cancel open orders or liquidate positions.

### Kill Switch

The kill switch defaults to **engaged** after a fresh database install. When engaged:

- No automated order may be submitted
- Signal evaluation, sync, and reconciliation still run
- Manual paper orders remain available with existing confirmation

Engage: one click on the Automation page.  
Disengage: type exactly `DISENGAGE PAPER KILL SWITCH`

The kill switch is checked when proposals are generated, before approval, and immediately before Alpaca submission.

### Safety Limits

| Setting | Default |
|---------|---------|
| `MAX_AUTOMATED_ORDER_NOTIONAL` | 500.0 |
| `MAX_AUTOMATED_ORDERS_PER_DAY` | 3 |
| `MAX_AUTOMATED_DAILY_NOTIONAL` | 1,000.0 |
| `MAX_ACTIVE_MANAGED_POSITIONS` | 3 |

Daily limits count only orders **successfully submitted** to Alpaca on the current trading day. Blocked or rejected proposals do not count.

### Worker Commands

```powershell
python -m workers.evaluate_daily_strategies      # After close (~4:15 PM ET)
python -m workers.execute_market_open_orders     # Market open (~9:35 AM ET)
python -m workers.synchronize_paper_orders       # Every 5 min during market hours
python -m workers.daily_reconciliation           # After close reconciliation
python -m workers.automation_readiness           # Read-only readiness check
```

### PowerShell Scripts

```powershell
.\scripts\run_after_close_evaluation.ps1
.\scripts\run_market_open_execution.ps1
.\scripts\run_order_sync.ps1
.\scripts\run_daily_reconciliation.ps1
```

Logs are written to `storage/logs/`.

### Windows Task Scheduler

```powershell
.\scripts\install_windows_tasks.ps1
```

Creates disabled tasks: `QuantStrategyLab-AfterCloseEvaluation`, `QuantStrategyLab-MarketOpenExecution`, `QuantStrategyLab-OrderSync`, `QuantStrategyLab-DailyReconciliation`.

**Important:** Task Scheduler uses your computer's local timezone. Convert from `America/New_York` before enabling tasks. Workers use Alpaca's market clock internally to skip weekends and holidays.

### Disable Automation Immediately

1. Click **ENGAGE EMERGENCY KILL SWITCH** on the Automation page, or
2. Click **Disable Automated Paper Trading**, or
3. Disable individual strategy automation

### Troubleshooting

| Issue | Action |
|-------|--------|
| Worker blocked by lock | Wait for TTL expiry (30 min) or delete stale row in `automation_worker_locks` |
| Readiness NOT READY | Run `python -m workers.automation_readiness` and fix failed checks |
| Unknown orders | Synchronize orders manually; resolve before enabling automation |
| Reconciliation warnings | Review Automation page; mismatches are not auto-repaired |

Paper trading is a simulation. Simulated fills and performance can differ from live trading because of liquidity, latency, slippage, market impact, queue position, and other market conditions.

---

## Strategy Allocation and Paper Capital

**Orders always submit to your Alpaca paper account** when credentials are configured. The app also maintains a **local per-strategy ledger** in SQLite to track each strategy's slice of capital, positions, and cash.

### Capital source (default: Alpaca)

| Setting | Meaning |
|---------|---------|
| `PAPER_CAPITAL_SOURCE=alpaca` | Strategy allocation limits use **Alpaca paper account cash** (recommended) |
| `PAPER_CAPITAL_SOURCE=local` | Use the offline virtual pool (`LOCAL_PAPER_CAPITAL_POOL`, default $100,000) |

When Alpaca credentials are present, `alpaca` is the default. Add to `.env`:

```text
PAPER_CAPITAL_SOURCE=alpaca
ALPACA_API_KEY=your_paper_api_key
ALPACA_SECRET_KEY=your_paper_secret_key
```

The Strategies page shows **Capital source: Alpaca paper account cash** and how much is still available to assign across strategies.

Alpaca maintains positions at the **account level**, not per strategy. Quant Strategy Lab:

- Assigns each strategy a virtual allocation (cannot exceed Alpaca cash when using Alpaca capital source)
- Records cash, reserves, buys, sells, and commissions as append-only ledger entries
- Tracks strategy positions locally in SQLite
- Enforces only one **ACTIVE** strategy per asset/symbol
- Validates Alpaca buying power before order submission

Corrections are made via new ledger adjustment entries. Existing ledger rows are never deleted.

---

## Manual Order Workflow

1. Create and **activate** a strategy on the Strategies page
2. Open **Paper Trading** and select the active strategy
3. Click **Evaluate Strategy** (generates a proposal, does not submit)
4. Review validations, warnings, and blocking reasons
5. Confirm with checkboxes and type **PAPER**
6. For alignment entries, also type **ALIGN**
7. Click **Submit Confirmed Paper Order** (separate step, idempotent)
8. Refresh order status to synchronize fills

Streamlit reruns will **not** submit orders. Submission is blocked after the first successful submit for a proposal.

---

## Order Status Lifecycle

```text
PROPOSED -> CONFIRMED -> SUBMITTED -> ACCEPTED -> FILLED
                                   \-> PARTIALLY_FILLED -> FILLED
                                   \-> REJECTED / CANCELED
                                   \-> UNKNOWN (requires reconciliation, no retry)
```

---

## Tech Stack

- Python 3.12+
- Streamlit - multipage web interface
- Pandas / NumPy - data processing and simulation
- Plotly - interactive charts
- alpaca-py - market data and paper trading API
- SQLite - local persistence
- pytest - automated testing

---

## Getting Started

```powershell
git clone https://github.com/Pd-Patel-dev/quant_trading_app.git
cd quant_trading_app
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Add Alpaca paper credentials to `.env`, then:

```powershell
streamlit run app.py
pytest -v
python scripts/check_paper_trading_readiness.py
python -m workers.automation_readiness
python -m workers.refresh_historical_data
```

---

## Milestone 5 — Multi-Asset Market Data Warehouse

### Architecture

```text
Enter symbol
    ↓
Normalize symbol
    ↓
Search local database
    ↓
Find missing history
    ↓
Download missing ranges
    ↓
Validate and store bars
    ↓
Load complete cached dataset
    ↓
Run backtest
```

### Asset types

| Type | Alpaca client | Symbol format | Research backtest |
|------|---------------|---------------|-------------------|
| `STOCK` | `StockHistoricalDataClient` | `AAPL`, `BRK.B` | Yes |
| `CRYPTO` | `CryptoHistoricalDataClient` | `BTC/USD`, `ETH/USD` | Yes (research only) |

**Warning:** Crypto functionality in this milestone is for historical research and backtesting only. It does not enable crypto order submission.

### Local historical-data cache

- SQLite is the source of truth after synchronization
- Missing ranges are detected and downloaded incrementally from Alpaca
- Upserts prevent duplicate bars (`INSERT ... ON CONFLICT DO UPDATE`)
- Data-quality validation runs before persistence
- Force refresh re-downloads the complete requested interval
- Recent overlap refresh: 5 stock sessions / 5 crypto UTC days

### Multi-symbol backtesting

- **Independent comparison:** Same starting capital per symbol
- **Shared-capital portfolio:** Allocations across assets with unallocated cash preserved
- **Calendar alignment:** Union of stock and crypto dates with forward-fill for combined portfolios

### Fractional crypto research

```python
QuantityMode.WHOLE_UNITS          # Stocks (default)
QuantityMode.FRACTIONAL_RESEARCH  # Crypto backtests (8 decimal places, Decimal math)
```

Fractional crypto research never reaches paper-trading or order-submission services.

### Streamlit pages

| Navigation | Page |
|------------|------|
| Data → Market Data | Download, cache inspector, coverage, quality issues |
| Research → Multi-Asset Lab | Batch backtest, compare assets, shared portfolio |

### Historical-data refresh worker

```powershell
python -m workers.refresh_historical_data
```

Uses worker lock `historical-data-refresh`. Refreshes active strategy symbols and optional watchlist. Never submits orders.

### Database tables (schema v5)

- `assets`, `market_bars`, `market_data_coverage`
- `market_data_download_runs`, `market_data_quality_issues`
- `multi_asset_backtest_runs`, `multi_asset_backtest_results`

### Configuration limits

```python
MAX_SYMBOLS_PER_BATCH = 20
MAX_BACKTEST_YEARS = 15
MAX_DATABASE_EXPORT_ROWS = 500_000
RECENT_STOCK_REFRESH_SESSIONS = 5
RECENT_CRYPTO_REFRESH_DAYS = 5
```

### Troubleshooting missing data

1. Check **Data → Market Data → Download History** for failed runs
2. Verify symbol format (`BTC/USD` not ambiguous concatenations)
3. Use **Repair internal gaps** for crypto daily sequences
4. Use **Force refresh** if provider revised recent bars
5. Confirm Alpaca credentials for stock downloads

---

## Milestone 6 — Crypto Paper Trading (Manual Only)

### Staged rollout defaults

```python
CRYPTO_PAPER_TRADING_ENABLED = False
CRYPTO_AUTOMATION_ENABLED = False
CRYPTO_KILL_SWITCH_ENGAGED = True
TRADING_MODE = "paper"
LIVE_TRADING_ENABLED = False
```

Crypto paper trading is **manual confirmation only** in this milestone. No crypto automation workers submit orders.

### Workflow

```text
Crypto strategy
    ↓
Completed crypto market data
    ↓
BUY, SELL, or HOLD signal
    ↓
Asset and allocation validation
    ↓
Crypto order proposal
    ↓
Manual PAPER CRYPTO confirmation
    ↓
Alpaca paper order
    ↓
Order synchronization
    ↓
Fee processing
    ↓
Crypto ledger and position update
```

### Order behavior

| Side | Sizing | Time-in-force |
|------|--------|---------------|
| BUY | USD notional | GTC |
| SELL | Base quantity (fractional) | GTC |

Only **USD-quoted** pairs are supported for order submission (`BTC/USD`, etc.). Pairs are discovered from Alpaca's Assets API — not hardcoded.

### Confirmation

Requires checkboxes plus exact text: `PAPER CRYPTO`

Crypto strategy approval requires: `APPROVE CRYPTO PAPER STRATEGY`

### Readiness check

```powershell
python scripts/check_crypto_paper_readiness.py
```

### Warning

Crypto paper trading is simulated. Paper fills, fees, liquidity, spread, latency, and price movement may differ from live cryptocurrency trading. Cryptocurrency prices can change rapidly and trading may result in substantial losses.

---

## Milestone 8 — Crypto Daily EMA Trend Following

Strategy type: `crypto_ema_trend_following`  
Supported pairs: **BTC/USD**, **ETH/USD** (daily candles only)

### Logic flow

```text
Completed daily crypto candle
    ↓
Calculate EMA Fast, Medium, and Long
    ↓
Detect bullish or bearish crossover
    ↓
Apply Long EMA market filter
    ↓
Calculate risk-based position size
    ↓
Execute at next daily Open
    ↓
Track actual entry price
    ↓
Calculate 8% stop level
    ↓
Exit after bearish crossover or stop-loss
```

### Rules

| Element | Default |
|---------|---------|
| Fast / Medium / Long EMA | 20 / 50 / 200 |
| Stop-loss | 8% from **actual entry fill** (daily close evaluation) |
| Risk per trade | 1% of strategy equity |
| Minimum history | 250 completed daily bars |

**BUY:** Fast EMA crosses above Medium EMA while Close is above Long EMA (flat only).  
**SELL:** Bearish EMA crossover or daily close-based stop (stop has priority).  
**Sizing:** `risk_budget = equity × 1%`, `notional = risk_budget ÷ 8%`, capped by cash, reserve, allocation, and order limits.

### Warning

This strategy is not guaranteed to be profitable. Crypto markets are highly volatile, and trend-following strategies can experience repeated losses during sideways conditions. The daily stop-loss does not guarantee an exit exactly 8% below entry because execution occurs at the next available daily Open.

### Health check

```powershell
python scripts/check_crypto_ema_strategy.py
```

---

## Paper Trading Limitations

- Paper trading is a simulation. Simulated fills and performance may differ from live trading because of liquidity, latency, market impact, slippage, queue position, and other real-market conditions.
- Live trading is permanently disabled in this application
- Automated submission is disabled by default; kill switch engaged by default
- No short selling, leverage, fractional shares, options, or crypto **order submission**
- Manual confirmation required for manual order proposals
- Automated proposals use policy-based validation at market open (never manually confirmed)
- Unknown order status blocks new submissions until reconciled

---

## Safety Configuration

```python
TRADING_MODE = "paper"
LIVE_TRADING_ENABLED = False
PAPER_ORDER_SUBMISSION_ENABLED = True
MANUAL_ORDER_CONFIRMATION_REQUIRED = True
MAX_PAPER_ORDER_NOTIONAL = 500.0
AUTOMATED_PAPER_TRADING_ENABLED = False
AUTOMATION_KILL_SWITCH_ENGAGED = True
MAX_AUTOMATED_ORDER_NOTIONAL = 500.0
MAX_AUTOMATED_ORDERS_PER_DAY = 3
MAX_AUTOMATED_DAILY_NOTIONAL = 1_000.0
MAX_ACTIVE_MANAGED_POSITIONS = 3
```

---

## Disclaimer

This software is for educational and research purposes only. Algorithmic trading involves substantial risk of loss. Past backtest or paper results do not guarantee future performance.

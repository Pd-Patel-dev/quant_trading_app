# Quant Strategy Lab

A modular Python application for researching algorithmic trading strategies. Quant Strategy Lab connects to Alpaca for historical market data and paper account access, runs backtests with realistic execution assumptions, and supports manual paper-order workflows with strategy-level virtual fund allocation.

**Current release:** Milestone 3 - Automated Daily Paper Trading Workflow  
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
| Strategy management | Create, draft, activate, pause, resume, and stop MA crossover strategies |
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

## Strategy Allocation and Local Ledger

Alpaca maintains positions at the **account level**, not per strategy. Quant Strategy Lab maintains its own **local strategy ledger**:

- Each strategy has a virtual allocation from the local paper capital pool
- Cash, reserves, buys, sells, and commissions are recorded as append-only ledger entries
- Strategy positions are tracked locally in SQLite
- Only one active strategy may trade a given symbol
- Alpaca buying power is used only as an additional broker-level validation

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
```

---

## Paper Trading Limitations

- Paper trading is a simulation. Simulated fills and performance may differ from live trading because of liquidity, latency, market impact, slippage, queue position, and other real-market conditions.
- Live trading is permanently disabled in this application
- Automated submission is disabled by default; kill switch engaged by default
- No short selling, leverage, fractional shares, options, or crypto
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

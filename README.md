---
title: DhanML Trading Engine
emoji: 📈
colorFrom: green
colorTo: blue
sdk: docker
pinned: false
---

# DhanML — Multi-Agent Algo Trading Engine
### BankNifty / Nifty / Sensex · Futures & Options · Live + Backtest

---

## Project Structure

```
dhan_app/
├── backend/
│   ├── main.py           # FastAPI app (REST + WebSocket)
│   ├── broker.py         # Dhan/Tradehull connection & order execution
│   ├── strategy.py       # Indicators, rule engine, ML signal
│   ├── trade_manager.py  # Position tracking & risk limits
│   └── config.py         # Settings (persisted to settings.json)
├── frontend/
│   ├── src/
│   │   ├── App.jsx       # Full React dashboard
│   │   └── main.jsx      # Entry point
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── models/
│   └── ml_model.pkl      # ← Copy your trained model here
├── data/                 # Local data cache
├── logs/                 # App logs
├── requirements.txt
├── settings.json         # Auto-created on first settings save
└── README.md
```

---

## Prerequisites

- Python 3.10+
- Node.js 18+ (for frontend build)
- `uv` installed (`pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Dhan trading account with API access enabled

---

## Setup — One Time

### 1. Create virtual environment with uv

```bash
cd dhan_app
uv venv .venv --python 3.11
```

### 2. Activate the environment

```bash
# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install Python dependencies

```bash
uv pip install -r requirements.txt
```

### 4. Copy your ML model

```bash
# Copy the trained model from the backtesting project
cp /path/to/banknifty_engine/model.pkl models/ml_model.pkl
```

> If you don't have the model yet, the system runs in **rule-only mode** (still functional, just without ML filter).

### 5. Build the React frontend

```bash
cd frontend
npm install
npm run build
cd ..
```

---

## Run the App

### Single command (recommended)

```bash
# From dhan_app/ directory, with venv active:
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### Or use the run script

```bash
python run.py
```

Then open: **http://localhost:8000**

---

## Daily Workflow (Every Trading Day)

### Step 1 — Update Dhan Access Token
Dhan access tokens expire daily. Every morning before 9:15 AM:

1. Log into **Dhan** → My Profile → API Access
2. Copy your new **Access Token**
3. In the app: click **"Connect"** button (top right)
4. Enter your **Client Code** + new **Access Token**
5. Click Connect — you'll see your balance if successful

### Step 2 — Verify Settings
Click ⚙️ Settings and confirm:
- Instrument (NIFTY / BANKNIFTY / SENSEX)
- Trade Mode (FUTURES / OPTIONS)
- Expiry selection
- Lot multiplier
- Daily loss/profit limits

### Step 3 — Trading
- **Manual mode**: Click "Refresh Signal" → if signal fires, click "Execute" button
- **Auto mode**: Toggle "Auto ON" — engine will execute automatically on every confirmed signal
- Chart updates automatically every 60 seconds during market hours

---

## Settings Reference

| Setting | Default | Description |
|---------|---------|-------------|
| instrument | BANKNIFTY | NIFTY / BANKNIFTY / SENSEX |
| trade_mode | FUTURES | FUTURES or OPTIONS |
| futures_expiry | 0 | 0=current month, 1=next, 2=far |
| options_expiry | 0 | 0=current, 1=next |
| strike_type | ATM | ATM / ITM / OTM |
| strike_offset | 0 | +N for OTM, -N for ITM from ATM |
| lot_multiplier | 1 | Number of lots per trade |
| max_daily_loss | 5000 | Stop trading after ₹5000 loss |
| max_daily_profit | 15000 | Stop trading after ₹15000 profit |
| ml_threshold | 0.55 | Minimum ML probability to trade |
| atr_sl_mult | 1.2 | Stop loss = 1.2 × ATR(5min) |
| atr_t1_mult | 2.5 | Target 1 = 2.5 × ATR(5min) |
| atr_t2_mult | 4.0 | Target 2 = 4.0 × ATR(5min) |
| chart_timeframe | 5 | Chart display timeframe |

---

## Backtest Tab

1. Select instrument, from/to date, capital
2. Click **Run Backtest**
3. Data is fetched live from Dhan (INDEX data, no options)
4. Results show: win rate, profit factor, drawdown, trade log

---

## API Endpoints (for automation/debugging)

```
GET  /api/status          → Connection status, balance, live P&L, position
POST /api/connect         → Connect to Dhan {client_code, access_token}
GET  /api/settings        → Current settings
POST /api/settings        → Update settings
GET  /api/signal          → Compute & return latest signal
GET  /api/chart/{tf}      → OHLCV + indicator data for chart
POST /api/backtest        → Run backtest {instrument, from_date, to_date, capital}
POST /api/trade/manual    → Manual trade {action: LONG|SHORT|EXIT}
GET  /api/signal_history  → Last 50 signals
POST /api/cancel_all      → Cancel all open orders
WS   /ws                  → WebSocket for real-time updates
```

---

## Lot Sizes (NSE/BSE)

| Instrument | Lot Size | Exchange |
|------------|----------|----------|
| NIFTY | 75 | NFO |
| BANKNIFTY | 15 | NFO |
| SENSEX | 10 | BFO |

---

## Risk Warnings

- **Paper trade first**: Run in manual mode for at least 2-4 weeks before enabling auto-trade
- **Token daily**: Dhan access token expires daily — app will fail silently if not updated
- **SL is not guaranteed**: Market orders during fast moves may slip beyond SL
- **Options liquidity**: ATM options have good liquidity; deep ITM/OTM may have slippage
- **Not financial advice**: This is a research/educational tool. Trade at your own risk.

---

## Troubleshooting

**"Connection failed"** → Check client code and access token are correct and not expired

**"Insufficient data for TF"** → Some timeframes need market hours data; run during 9:30 AM–3:30 PM IST

**"ML model not found"** → Copy model.pkl to models/ folder. System continues in rule-only mode.

**Chart not loading** → Ensure connected to broker; chart data comes from Dhan API

**Frontend not showing** → Run `npm run build` inside frontend/ folder first

# ⚡ Inevitable
### Algorithmic Trading Platform

A self-hosted intraday trading platform for Nifty 50 options — built for speed, discipline, and zero compromise.

---

## Features

| Module | Description |
|---|---|
| 🤖 **Auto-Trader** | Fully automated NIFTY short-options strategy with regime detection |
| 🛡 **SL Management** | Exchange-backed stop-loss with real-time trail and tick guard |
| 🛢 **Crude Trader** | MCX crude oil intraday strategy with auto entry/exit |
| 📊 **Live Monitor** | WebSocket tick stream from Zerodha Kite |
| 📈 **Backtester** | Historical replay with pattern scoring |
| 🧠 **Pattern Engine** | Chart pattern detector (HH/HL, breakdown, squeeze) |
| 📋 **P&L Dashboard** | Day / session / cumulative P&L with trade log |

---

## Quick Start

```bash
cd ~/inevitable
.venv/bin/python3 start.py
```

Then open **http://localhost:8000** and connect your Zerodha account.

---

## Stack

- **Backend:** Python · FastAPI · asyncio
- **Frontend:** HTMX · Tailwind CSS · Chart.js · Lightweight Charts
- **Broker:** Zerodha Kite Connect (OAuth 2.0)
- **Data:** Zerodha REST + WebSocket ticks (NFO, MCX)
- **State:** JSON snapshots (no external DB needed)

---

## Server management

```bash
# Start
nohup .venv/bin/python3 start.py >> /tmp/inevitable.log 2>&1 &

# Logs
tail -f /tmp/inevitable.log

# Stop
kill $(lsof -ti tcp:8000)
```

---

*"Discipline is destiny."*

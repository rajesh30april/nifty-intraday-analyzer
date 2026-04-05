"""fetch_tv_data.py — Fetch Nifty 5-min OHLCV from TradingView WebSocket API.

Uses TradingView's native WebSocket protocol (same as browser).
With a Premium plan → up to unlimited bars of history.
With Pro+ plan      → up to 20,000 bars (~14 months of 5-min data).

Setup (one time):
    Add to your .env file:
        TV_USERNAME=your@email.com
        TV_PASSWORD=yourpassword

Usage:
    .venv/bin/python3 fetch_tv_data.py

Output:
    data/nifty_5min_tv.csv
"""

import json
import random
import re
import string
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import websocket
from dotenv import load_dotenv
import os

load_dotenv()

# ── Config ────────────────────────────────────────────────────────
TV_USERNAME  = os.getenv("TV_USERNAME", "")
TV_PASSWORD  = os.getenv("TV_PASSWORD", "")
SYMBOL       = "NSE:NIFTY"
INTERVAL     = "5"          # 5-minute candles (TV uses "5" not "5m")
N_BARS       = 50000        # request max — TV will cap based on your plan
OUT_DIR      = Path(__file__).parent / "data"
OUT_FILE     = OUT_DIR / "nifty_5min_tv.csv"

TV_SIGNIN_URL  = "https://www.tradingview.com/accounts/signin/"
TV_WS_URL      = "wss://data.tradingview.com/socket.io/websocket?from=chart%2F&date=2024_03_17-12_57&type=chart"

HEADERS = {
    "User-Agent"   : "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer"      : "https://www.tradingview.com/",
    "Origin"       : "https://www.tradingview.com",
}


# ── TradingView WebSocket message format ──────────────────────────

def _rand_session(prefix: str = "cs") -> str:
    """Generate a random session ID like TradingView does."""
    return prefix + "_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def _wrap(msg: dict) -> str:
    """Wrap a dict into TradingView's ~m~N~m~{json} wire format."""
    raw = json.dumps(msg, separators=(",", ":"))
    return f"~m~{len(raw)}~m~{raw}"


def _send(ws, method: str, params: list):
    ws.send(_wrap({"m": method, "p": params}))


# ── Login → get auth token ────────────────────────────────────────

def _login(username: str, password: str) -> str | None:
    """POST to TradingView signin and return auth_token."""
    try:
        r = requests.post(
            TV_SIGNIN_URL,
            data={"username": username, "password": password,
                  "remember": "on", "code": ""},
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        data = r.json()
        token = data.get("user", {}).get("auth_token")
        if token:
            print(f"  ✅ Logged in as: {data['user'].get('username', username)}")
            plan = data.get("user", {}).get("pro_plan", "free")
            print(f"  📋 Plan: {plan}")
            return token
        else:
            print(f"  ❌ Login failed: {data.get('error', 'unknown error')}")
            return None
    except Exception as e:
        print(f"  ❌ Login error: {e}")
        return None


# ── WebSocket data collector ──────────────────────────────────────

class _TVDataCollector:
    def __init__(self, symbol: str, interval: str, n_bars: int):
        self.symbol   = symbol
        self.interval = interval
        self.n_bars   = n_bars
        self.candles: list[dict] = []
        self.done     = False
        self._cs      = _rand_session("cs")
        self._ss      = _rand_session("ss")

    def on_open(self, ws):
        print("  📡 WebSocket connected")
        _send(ws, "set_auth_token",       [self._token])
        _send(ws, "chart_create_session", [self._cs, ""])
        _send(ws, "switch_timezone",      [self._cs, "Asia/Kolkata"])
        _send(ws, "quote_create_session", [self._ss])
        _send(ws, "resolve_symbol", [
            self._cs, "sds_sym_1",
            f'={json.dumps({"symbol": self.symbol, "adjustment": "splits"})}',
        ])
        _send(ws, "create_series", [
            self._cs, "sds_1", "s1", "sds_sym_1",
            self.interval, self.n_bars, "",
        ])

    def on_message(self, ws, message):
        # TradingView sends heartbeats: ~m~N~m~~h~N
        if "~h~" in message:
            ws.send(f"~m~{len(message)}~m~{message}")  # pong
            return

        # Parse all JSON packets from the wire format
        packets = re.findall(r"~m~\d+~m~(\{.*?\})(?=~m~|$)", message, re.DOTALL)
        for raw in packets:
            try:
                pkt = json.loads(raw)
            except Exception:
                continue

            m = pkt.get("m", "")

            if m == "timescale_update":
                bars = (pkt.get("p", [{}])[1] or {}).get("sds_1", {}).get("s", [])
                for bar in bars:
                    v = bar.get("v", [])
                    if len(v) >= 5:
                        self.candles.append({
                            "timestamp": v[0],
                            "open"     : v[1],
                            "high"     : v[2],
                            "low"      : v[3],
                            "close"    : v[4],
                            "volume"   : v[5] if len(v) > 5 else 0,
                        })

            elif m == "series_completed":
                count = len(self.candles)
                print(f"  ✅ Series complete — {count:,} candles received")
                self.done = True
                ws.close()

            elif m == "symbol_error":
                print(f"  ❌ Symbol error: {pkt.get('p')}")
                self.done = True
                ws.close()

            elif m == "critical_error":
                print(f"  ❌ Critical error: {pkt.get('p')}")
                self.done = True
                ws.close()

    def on_error(self, ws, error):
        print(f"  ❌ WebSocket error: {error}")
        self.done = True

    def on_close(self, ws, code, msg):
        self.done = True

    def fetch(self, auth_token: str) -> list[dict]:
        self._token = auth_token
        ws = websocket.WebSocketApp(
            TV_WS_URL,
            header=[f"{k}: {v}" for k, v in HEADERS.items()],
            on_open    = self.on_open,
            on_message = self.on_message,
            on_error   = self.on_error,
            on_close   = self.on_close,
        )
        ws.run_forever(ping_interval=20, ping_timeout=10)
        return self.candles


# ── Main ──────────────────────────────────────────────────────────

def main():
    OUT_DIR.mkdir(exist_ok=True)

    if not TV_USERNAME or not TV_PASSWORD:
        print("❌ Missing credentials!")
        print("   Add to your .env file:")
        print("   TV_USERNAME=your@email.com")
        print("   TV_PASSWORD=yourpassword")
        return

    print(f"📈 TradingView → Nifty 5-min data fetcher")
    print(f"🎯 Symbol: {SYMBOL} | Interval: {INTERVAL}min | Bars: {N_BARS:,}\n")

    # Step 1: Login
    print("🔐 Logging in to TradingView...")
    token = _login(TV_USERNAME, TV_PASSWORD)
    if not token:
        return

    # Step 2: Fetch via WebSocket
    print(f"\n📡 Connecting to TradingView WebSocket...")
    collector = _TVDataCollector(SYMBOL, INTERVAL, N_BARS)
    candles   = collector.fetch(token)

    if not candles:
        print("\n💀 No data received. Check credentials and plan.")
        return

    # Step 3: Build DataFrame
    df = pd.DataFrame(candles)
    df["date"] = pd.to_datetime(df["timestamp"], unit="s", utc=True) \
                   .dt.tz_convert("Asia/Kolkata")
    df = df.drop(columns=["timestamp"]).sort_values("date").drop_duplicates("date")

    # Market hours only: 9:15 – 15:30 IST
    h, m = df["date"].dt.hour, df["date"].dt.minute
    df = df[((h > 9) | ((h == 9) & (m >= 15))) & ((h < 15) | ((h == 15) & (m <= 30)))]
    df = df.reset_index(drop=True)

    # Step 4: Save
    df.to_csv(OUT_FILE, index=False)

    trading_days  = df["date"].dt.date.nunique()
    candles_vol   = int((df["volume"] > 0).sum())
    date_from     = df["date"].min().strftime("%Y-%m-%d")
    date_to       = df["date"].max().strftime("%Y-%m-%d")

    print(f"""
╔══════════════════════════════════════════════════════╗
║       ✅  TRADINGVIEW FETCH COMPLETE                 ║
╠══════════════════════════════════════════════════════╣
║  Period        : {date_from} → {date_to}       ║
║  Total candles : {len(df):>10,}                         ║
║  Trading days  : {trading_days:>10,}                         ║
║  Volume > 0    : {candles_vol:>10,} candles                  ║
║  Nifty range   : {df['low'].min():>8,.0f} – {df['high'].max():,.0f}             ║
╠══════════════════════════════════════════════════════╣
║  📄 {str(OUT_FILE):<48} ║
╚══════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()

"""fetch_tv_data.py — Fetch Nifty 5-min OHLCV from TradingView WebSocket API.

Uses TradingView's native WebSocket protocol (same as your browser).

Works with ANY login method (Apple, Google, email — doesn't matter!)
because we use the session token directly from your browser, not your password.

Setup (one time — 30 seconds):
    1. Open tradingview.com in browser (logged in)
    2. Press Cmd+Option+I → Console tab
    3. Paste this and hit Enter:
           document.cookie.split(';').find(c=>c.trim().startsWith('auth_token'))?.trim()
    4. Copy the value after 'auth_token=' and add to .env:
           TV_AUTH_TOKEN=eyJhb...your_long_token_here

Usage:
    .venv/bin/python3 fetch_tv_data.py

Output:
    data/nifty_5min_tv.csv

Bar limits by plan (5-min candles):
    Guest / no token  →  ~5,000 bars  (~3.5 months)
    Essential         → ~10,000 bars  (~7 months)
    Essential+        → ~20,000 bars  (~14 months)
    Premium           → ~40,000 bars  (~2 years) ✅
"""

import json
import os
import random
import re
import ssl
import string
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import websocket
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────
TV_AUTH_TOKEN = os.getenv("TV_AUTH_TOKEN", "").strip()
TV_USERNAME   = os.getenv("TV_USERNAME",   "").strip()
TV_PASSWORD   = os.getenv("TV_PASSWORD",   "").strip()
SYMBOL        = "NSE:NIFTY"
INTERVAL      = "5"       # TradingView uses "5" for 5-minute
N_BARS        = 50000     # Request max — TV caps based on your plan
OUT_DIR       = Path(__file__).parent / "data"
OUT_FILE      = OUT_DIR / "nifty_5min_tv.csv"

TV_WS_URL    = "wss://data.tradingview.com/socket.io/websocket?from=chart/&type=chart"
TV_LOGIN_URL = "https://www.tradingview.com/accounts/signin/"
WS_HEADERS   = [
    "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Origin: https://www.tradingview.com",
    "Referer: https://www.tradingview.com/",
]


# ── Auth ─────────────────────────────────────────────────────────

def _login(username: str, password: str) -> str | None:
    """POST credentials to TradingView → return JWT auth token."""
    try:
        r = requests.post(
            TV_LOGIN_URL,
            data={"username": username, "password": password, "remember": "on"},
            headers={
                "User-Agent" : "Mozilla/5.0",
                "Referer"    : "https://www.tradingview.com/",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            verify=False,
            timeout=15,
        )
        data  = r.json()
        token = data.get("user", {}).get("auth_token")
        if token:
            plan = data["user"].get("pro_plan", "free")
            print(f"  ✅ Logged in as: {data['user'].get('username', username)}")
            print(f"  📋 Plan: {plan or 'free'}")
            return token
        print(f"  ❌ Login failed: {data.get('error', data)}")
        return None
    except Exception as e:
        print(f"  ❌ Login error: {e}")
        return None


# ── Helpers ───────────────────────────────────────────────────────

def _rand_id(n: int = 12) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _wrap(msg: dict) -> str:
    """Pack a dict into TradingView's ~m~N~m~{json} wire format."""
    raw = json.dumps(msg, separators=(",", ":"))
    return f"~m~{len(raw)}~m~{raw}"


def _send(ws, method: str, params: list) -> None:
    ws.send(_wrap({"m": method, "p": params}))


# ── WebSocket collector ───────────────────────────────────────────

class _Collector:
    def __init__(self, auth_token: str, symbol: str, interval: str, n_bars: int):
        self._token   = auth_token
        self._symbol  = symbol
        self._interval= interval
        self._n_bars  = n_bars
        self._cs      = "cs_" + _rand_id()
        self.candles: list[dict] = []
        self._done    = threading.Event()

    def on_open(self, ws):
        print("  📡 Connected to TradingView WebSocket")
        _send(ws, "set_auth_token",       [self._token])
        _send(ws, "chart_create_session", [self._cs, ""])
        _send(ws, "switch_timezone",      [self._cs, "Asia/Kolkata"])
        _send(ws, "resolve_symbol", [
            self._cs, "sds_sym_1",
            f'={json.dumps({"symbol": self._symbol, "adjustment": "splits"})}',
        ])
        _send(ws, "create_series", [
            self._cs, "sds_1", "s1", "sds_sym_1",
            self._interval, self._n_bars, "",
        ])

    def on_message(self, ws, message):
        # Heartbeat → pong immediately
        if "~h~" in message:
            ws.send(f"~m~{len(message)}~m~{message}")
            return

        for raw in re.findall(r"~m~\d+~m~(\{.*?\})(?=~m~|$)", message, re.DOTALL):
            try:
                pkt = json.loads(raw)
            except Exception:
                continue

            msg_type = pkt.get("m", "")

            if msg_type == "timescale_update":
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
                print(f"  📦 {len(self.candles):,} candles received...", end="\r")

            elif msg_type == "series_completed":
                print(f"  ✅ Series complete — {len(self.candles):,} candles total")
                self._done.set()
                ws.close()

            elif msg_type in ("symbol_error", "critical_error"):
                print(f"\n  ❌ TradingView error ({msg_type}): {pkt.get('p')}")
                self._done.set()
                ws.close()

    def on_error(self, ws, error):
        print(f"\n  ❌ WebSocket error: {error}")
        self._done.set()

    def on_close(self, ws, *_):
        self._done.set()

    def fetch(self) -> list[dict]:
        ws = websocket.WebSocketApp(
            TV_WS_URL,
            header     = WS_HEADERS,
            on_open    = self.on_open,
            on_message = self.on_message,
            on_error   = self.on_error,
            on_close   = self.on_close,
        )
        t = threading.Thread(
            target=ws.run_forever,
            kwargs={
                "ping_interval": 20,
                "ping_timeout" : 10,
                # SSL bypass — needed on Walmart VPN (corporate cert interception)
                "sslopt"       : {"cert_reqs": ssl.CERT_NONE},
            },
            daemon=True,
        )
        t.start()
        self._done.wait(timeout=90)
        ws.close()
        return self.candles


# ── Main ──────────────────────────────────────────────────────────

def main():
    OUT_DIR.mkdir(exist_ok=True)

    # ── Resolve auth token (priority: explicit token > login > guest) ──
    if TV_AUTH_TOKEN:
        token = TV_AUTH_TOKEN
        print("🔐 Using TV_AUTH_TOKEN from .env")
    elif TV_USERNAME and TV_PASSWORD:
        print("🔐 Logging in to TradingView...")
        token = _login(TV_USERNAME, TV_PASSWORD)
        if not token:
            return
    else:
        token = "unauthorized_user_token"
        print("⚠️  No credentials in .env — guest mode (~5,000 bars only)")

    print(f"📈 Fetching: {SYMBOL} | {INTERVAL}-min | up to {N_BARS:,} bars\n")

    # Fetch
    collector = _Collector(token, SYMBOL, INTERVAL, N_BARS)
    candles   = collector.fetch()

    if not candles:
        print("\n💀 No data received.")
        if not TV_AUTH_TOKEN:
            print("   Tip: Add TV_AUTH_TOKEN to .env for more data.")
        return

    # Build DataFrame
    df = pd.DataFrame(candles)
    df["date"] = (
        pd.to_datetime(df["timestamp"], unit="s", utc=True)
          .dt.tz_convert("Asia/Kolkata")
    )
    df = (df.drop(columns=["timestamp"])
            .sort_values("date")
            .drop_duplicates("date")
            .reset_index(drop=True))

    # Market hours only: 9:15 AM – 3:30 PM IST
    h, m = df["date"].dt.hour, df["date"].dt.minute
    df = df[
        ((h > 9) | ((h == 9) & (m >= 15))) &
        ((h < 15) | ((h == 15) & (m <= 30)))
    ].reset_index(drop=True)

    # Save
    df.to_csv(OUT_FILE, index=False)

    trading_days = df["date"].dt.date.nunique()
    vol_count    = int((df["volume"] > 0).sum())
    date_from    = df["date"].min().strftime("%Y-%m-%d")
    date_to      = df["date"].max().strftime("%Y-%m-%d")

    print(f"""
╔══════════════════════════════════════════════════════╗
║       ✅  TRADINGVIEW FETCH COMPLETE                 ║
╠══════════════════════════════════════════════════════╣
║  Period        : {date_from} → {date_to}       ║
║  Total candles : {len(df):>10,}                         ║
║  Trading days  : {trading_days:>10,}                         ║
║  Volume > 0    : {vol_count:>10,} / {len(df):,} candles        ║
║  Nifty range   : {df['low'].min():>8,.0f} – {df['high'].max():,.0f}             ║
╠══════════════════════════════════════════════════════╣
║  📄 {str(OUT_FILE):<48} ║
╚══════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()

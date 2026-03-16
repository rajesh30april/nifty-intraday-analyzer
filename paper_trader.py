"""Paper Trading Tracker for Nifty Options.

Records virtual option trades triggered by strategy signals.
Uses ATM strike, real premium estimates (Kite LTP or Black-Scholes).
Stores everything in SQLite — survives restarts.
"""

from __future__ import annotations

import sqlite3
import math
from datetime import datetime, date, time as dt_time, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from contextlib import contextmanager
from typing import Optional

DB_PATH    = Path(__file__).parent / "paper_trades.db"
LOT_SIZE   = 65          # Nifty lot size (as confirmed)
EXIT_TIME  = dt_time(15, 15)  # Auto-exit time
STRIKE_STEP = 50         # Nifty strikes are in multiples of 50

# ── DB setup ──────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT    NOT NULL,          -- YYYY-MM-DD
    signal_time   TEXT    NOT NULL,          -- HH:MM
    strategy      TEXT    NOT NULL,
    direction     TEXT    NOT NULL,          -- 'long' | 'short'
    option_type   TEXT    NOT NULL,          -- 'CE'   | 'PE'
    strike        INTEGER NOT NULL,          -- e.g. 23450
    expiry        TEXT    NOT NULL,          -- YYYY-MM-DD
    nifty_entry   REAL    NOT NULL,          -- Nifty spot at entry
    entry_premium REAL    NOT NULL,          -- option premium paid
    exit_premium  REAL,                      -- NULL while open
    lot_size      INTEGER NOT NULL DEFAULT 65,
    status        TEXT    NOT NULL DEFAULT 'open',  -- 'open'|'closed'
    exit_reason   TEXT,                      -- 'SL'|'Target'|'Time Exit'|'Manual'
    pnl_points    REAL,                      -- exit_premium - entry_premium (for CE)
    pnl_rupees    REAL,
    sl_premium    REAL,                      -- SL level in option premium
    target_premium REAL,                     -- Target level in option premium
    nifty_sl      REAL,                      -- Nifty SL (for reference)
    nifty_target  REAL,                      -- Nifty Target (for reference)
    opened_at     TEXT    NOT NULL,
    closed_at     TEXT,
    notes         TEXT
);
"""


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Domain helpers ─────────────────────────────────────────────────────────────

def atm_strike(nifty_price: float) -> int:
    """Round Nifty spot to nearest 50 → ATM strike."""
    return int(round(nifty_price / STRIKE_STEP) * STRIKE_STEP)


def next_expiry() -> str:
    """Return nearest Nifty weekly expiry as YYYY-MM-DD.

    Reads directly from NFO instruments — no hardcoded weekday.
    Falls back to nearest Tuesday if Kite is unavailable.
    """
    try:
        from auto_trader import _get_nfo_instruments
        from kite_integration import kite_manager
        today = kite_manager.get_market_date()
        instruments = _get_nfo_instruments()
        expiries = sorted({
            i["expiry"] for i in instruments
            if i["name"] == "NIFTY" and i["expiry"] >= today
        })
        if expiries:
            return expiries[0].strftime("%Y-%m-%d")
    except Exception:
        pass
    # fallback: nearest Tuesday
    today = date.today()
    days_ahead = (1 - today.weekday()) % 7 or 7
    return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def estimate_premium(
    nifty_price: float,
    strike: int,
    option_type: str,       # 'CE' or 'PE'
    sl_nifty: float = 30.0,
    rr: float = 2.0,
) -> dict:
    """Best-effort ATM option premium estimate + SL/Target in premium terms.

    Priority:
      1. Live Kite LTP for the exact contract  (if kite_manager is authenticated)
      2. Black-Scholes ATM approximation using India VIX cache

    Returns:
        premium, sl_premium, target_premium, source
    """
    premium = _bs_premium(nifty_price, strike, option_type)
    source  = "black-scholes"

    # Try Kite live LTP first
    try:
        from kite_integration import kite_manager as _km
        if _km.is_authenticated:
            expiry_str = next_expiry().replace("-", "")
            # Format: NSE:NIFTY{YYMMDD}{STRIKE}{CE/PE}
            ymd = datetime.strptime(next_expiry(), "%Y-%m-%d").strftime("%y%m%d")
            symbol = f"NSE:NIFTY{ymd}{strike}{option_type}"
            quotes = _km.kite.ltp([symbol])
            if quotes and symbol in quotes:
                ltp = quotes[symbol]["last_price"]
                if ltp and ltp > 0:
                    premium = float(ltp)
                    source  = "kite-ltp"
    except Exception:
        pass

    # Delta ≈ 0.5 for ATM; SL and target in premium terms
    delta      = 0.50
    sl_premium = round(max(5.0, premium - sl_nifty * delta), 1)
    tgt_premium = round(premium + sl_nifty * rr * delta, 1)

    return {
        "premium":        round(premium, 1),
        "sl_premium":     sl_premium,
        "target_premium": tgt_premium,
        "source":         source,
    }


def _bs_premium(spot: float, strike: int, option_type: str) -> float:
    """Black-Scholes ATM premium estimate using cached VIX."""
    try:
        from app import premium_estimate as _pe_fn
        cache = getattr(_pe_fn, "_cache", {})
        vix   = float(cache.get("vix", 15.0))
    except Exception:
        vix = 15.0

    iv  = vix / 100.0
    # Use Kite market date + nearest actual expiry for DTE
    exp_str = next_expiry()
    exp_date = date.fromisoformat(exp_str)
    try:
        from kite_integration import kite_manager as _km2
        today = _km2.get_market_date()
    except Exception:
        today = date.today()
    days_to_exp = max((exp_date - today).days, 1)
    T   = days_to_exp / 365.0
    r   = 0.065    # risk-free rate
    d1  = (math.log(spot / strike) + (r + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
    d2  = d1 - iv * math.sqrt(T)

    def _norm_cdf(x: float) -> float:
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    import math as _m
    e_rT = math.exp(-r * T)
    if option_type == "CE":
        px = spot * _norm_cdf(d1) - strike * e_rT * _norm_cdf(d2)
    else:   # PE
        px = strike * e_rT * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
    return max(1.0, round(px, 1))


# ── CRUD ───────────────────────────────────────────────────────────────────────

def open_trade(
    direction: str,          # 'long' | 'short'
    nifty_price: float,
    strategy: str = "smart_router",
    sl_nifty: float = 30.0,
    rr: float = 2.0,
    lot_size: int = LOT_SIZE,
    notes: str = "",
) -> dict:
    """Open a new paper trade. Returns the created trade as dict."""
    now        = datetime.now()
    option_type = "CE" if direction == "long" else "PE"
    strike      = atm_strike(nifty_price)
    expiry      = next_expiry()

    prem_data   = estimate_premium(nifty_price, strike, option_type, sl_nifty, rr)
    premium     = prem_data["premium"]
    sl_prem     = prem_data["sl_premium"]
    tgt_prem    = prem_data["target_premium"]

    nifty_sl     = (nifty_price - sl_nifty)  if direction == "long" else (nifty_price + sl_nifty)
    nifty_target = (nifty_price + sl_nifty * rr) if direction == "long" else (nifty_price - sl_nifty * rr)

    row = {
        "date":           now.strftime("%Y-%m-%d"),
        "signal_time":    now.strftime("%H:%M"),
        "strategy":       strategy,
        "direction":      direction,
        "option_type":    option_type,
        "strike":         strike,
        "expiry":         expiry,
        "nifty_entry":    round(nifty_price, 2),
        "entry_premium":  premium,
        "lot_size":       lot_size,
        "sl_premium":     sl_prem,
        "target_premium": tgt_prem,
        "nifty_sl":       round(nifty_sl, 2),
        "nifty_target":   round(nifty_target, 2),
        "opened_at":      now.isoformat(),
        "notes":          notes,
    }

    with _db() as conn:
        cur = conn.execute(
            """
            INSERT INTO paper_trades
              (date, signal_time, strategy, direction, option_type, strike, expiry,
               nifty_entry, entry_premium, lot_size, sl_premium, target_premium,
               nifty_sl, nifty_target, opened_at, notes)
            VALUES
              (:date,:signal_time,:strategy,:direction,:option_type,:strike,:expiry,
               :nifty_entry,:entry_premium,:lot_size,:sl_premium,:target_premium,
               :nifty_sl,:nifty_target,:opened_at,:notes)
            """,
            row,
        )
        row["id"]     = cur.lastrowid
        row["status"] = "open"
        row["premium_source"] = prem_data["source"]
    return row


def close_trade(
    trade_id: int,
    exit_premium: float,
    exit_reason: str = "Manual",
) -> dict:
    """Close an open trade. Returns updated trade dict."""
    now = datetime.now()
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM paper_trades WHERE id=? AND status='open'", (trade_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Trade {trade_id} not found or already closed")

        direction     = row["direction"]
        entry_premium = row["entry_premium"]
        lot_size      = row["lot_size"]

        if direction == "long":   # bought CE — profit when premium rises
            pnl_pts = round(exit_premium - entry_premium, 2)
        else:                     # bought PE — profit when premium rises
            pnl_pts = round(exit_premium - entry_premium, 2)

        pnl_rs = round(pnl_pts * lot_size, 2)

        conn.execute(
            """
            UPDATE paper_trades
            SET status='closed', exit_premium=?, pnl_points=?, pnl_rupees=?,
                exit_reason=?, closed_at=?
            WHERE id=?
            """,
            (exit_premium, pnl_pts, pnl_rs, exit_reason, now.isoformat(), trade_id),
        )
    return get_trade(trade_id)


def get_trade(trade_id: int) -> dict:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM paper_trades WHERE id=?", (trade_id,)
        ).fetchone()
        return dict(row) if row else {}


def get_open_trades() -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE status='open' ORDER BY opened_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_trades(days: int = 30) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE date >= ? ORDER BY opened_at DESC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_trade(trade_id: int) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "DELETE FROM paper_trades WHERE id=? AND status='open'", (trade_id,)
        )
        return cur.rowcount > 0


def summary(days: int = 30) -> dict:
    """Return P&L summary for the last N days."""
    trades    = get_trades(days)
    closed    = [t for t in trades if t["status"] == "closed"]
    open_     = [t for t in trades if t["status"] == "open"]
    wins      = [t for t in closed if (t["pnl_rupees"] or 0) > 0]
    losses    = [t for t in closed if (t["pnl_rupees"] or 0) <= 0]
    total_rs  = sum(t["pnl_rupees"] or 0 for t in closed)
    today_str = date.today().isoformat()
    today_rs  = sum(t["pnl_rupees"] or 0 for t in closed if t["date"] == today_str)

    # Daily breakdown
    daily: dict[str, float] = {}
    for t in closed:
        daily[t["date"]] = round(daily.get(t["date"], 0) + (t["pnl_rupees"] or 0), 2)

    return {
        "total_trades":  len(closed),
        "open_trades":   len(open_),
        "winners":       len(wins),
        "losers":        len(losses),
        "win_rate":      round(len(wins) / len(closed) * 100, 1) if closed else 0,
        "total_pnl_rs":  round(total_rs, 2),
        "today_pnl_rs":  round(today_rs, 2),
        "avg_win_rs":    round(sum(t["pnl_rupees"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss_rs":   round(sum(t["pnl_rupees"] for t in losses) / len(losses), 2) if losses else 0,
        "profit_factor": _profit_factor(wins, losses),
        "daily_pnl":     dict(sorted(daily.items())),
    }


def _profit_factor(wins: list, losses: list) -> float:
    gp = sum(t["pnl_rupees"] for t in wins)
    gl = abs(sum(t["pnl_rupees"] for t in losses))
    return round(gp / gl, 2) if gl else 0.0


def current_premium(trade: dict) -> dict:
    """Get current live premium for an open trade (for unrealized P&L)."""
    try:
        from kite_integration import kite_manager as _km
        if _km.is_authenticated:
            ymd    = datetime.strptime(trade["expiry"], "%Y-%m-%d").strftime("%y%m%d")
            symbol = f"NSE:NIFTY{ymd}{trade['strike']}{trade['option_type']}"
            quotes = _km.kite.ltp([symbol])
            if quotes and symbol in quotes:
                ltp = quotes[symbol]["last_price"]
                if ltp and ltp > 0:
                    prem = float(ltp)
                    pnl_pts = round(prem - trade["entry_premium"], 2)
                    return {
                        "current_premium": prem,
                        "unrealized_pts":  pnl_pts,
                        "unrealized_rs":   round(pnl_pts * trade["lot_size"], 2),
                        "source":          "kite-ltp",
                    }
    except Exception:
        pass

    # Fallback: BS estimate using latest Nifty tick
    try:
        from kite_integration import kite_manager as _km
        nifty = _km.get_nifty_ltp() or trade["nifty_entry"]
        prem  = _bs_premium(nifty, trade["strike"], trade["option_type"])
        pnl_pts = round(prem - trade["entry_premium"], 2)
        return {
            "current_premium": prem,
            "unrealized_pts":  pnl_pts,
            "unrealized_rs":   round(pnl_pts * trade["lot_size"], 2),
            "source":          "bs-estimate",
        }
    except Exception:
        return {"current_premium": None, "unrealized_pts": 0, "unrealized_rs": 0, "source": "unavailable"}
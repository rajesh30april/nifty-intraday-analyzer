"""Trade report analyser — parses Zerodha Console tradebook CSV + app trade log.

Zerodha Console tradebook CSV columns (typical):
  trade_date, order_id, trade_id, security_name, isin,
  quantity, price, trade_type, order_type, exchange, segment

We pair BUY/SELL legs by security + date to reconstruct full trades,
then run pattern analysis:
  - Win / loss rate by time-of-day, day-of-week, direction (CE/PE),
    option type, exit reason, holding duration
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
from typing import Any

import pandas as pd


# ── Zerodha Console CSV parsers ──────────────────────────────────────────────

ZERODHA_DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d",
]


def _parse_dt(val: str) -> datetime | None:
    for fmt in ZERODHA_DATE_FORMATS:
        try:
            return datetime.strptime(str(val).strip(), fmt)
        except ValueError:
            continue
    return None


def parse_zerodha_csv(content: str) -> list[dict]:
    """Parse Zerodha Console tradebook CSV → list of raw trade-leg dicts."""
    from io import StringIO
    df = pd.read_csv(StringIO(content))
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Normalise common column name variants
    rename = {
        "symbol": "security_name",
        "scrip": "security_name",
        "trade_type": "trade_type",
        "buy/sell": "trade_type",
        "b/s": "trade_type",
        "qty": "quantity",
        "trade_date": "trade_date",
        "date": "trade_date",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    required = {"security_name", "quantity", "price", "trade_type", "trade_date"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {missing}. Found: {list(df.columns)}")

    legs = []
    for _, row in df.iterrows():
        dt = _parse_dt(str(row["trade_date"]))
        legs.append({
            "security":   str(row["security_name"]).strip(),
            "qty":        abs(float(str(row["quantity"]).replace(",", ""))),
            "price":      float(str(row["price"]).replace(",", "")),
            "side":       str(row["trade_type"]).strip().upper()[0],   # B or S
            "dt":         dt,
            "date":       dt.date() if dt else None,
        })
    return legs


# ── Pair legs into trades ─────────────────────────────────────────────────────

def _option_meta(symbol: str) -> dict:
    """Extract CE/PE, strike, expiry from symbol like NIFTY2631723450PE."""
    m = re.search(r"(NIFTY|BANKNIFTY|FINNIFTY)(\d{5})(\d{5})(CE|PE)", symbol.upper())
    if m:
        return {
            "underlying": m.group(1),
            "expiry_code": m.group(2),
            "strike":      int(m.group(3)),
            "opt_type":    m.group(4),
        }
    return {"underlying": symbol, "expiry_code": "", "strike": 0, "opt_type": ""}


def pair_legs_into_trades(legs: list[dict]) -> list[dict]:
    """Match BUY legs with SELL legs (FIFO) per security per date."""
    by_sec: dict[str, list[dict]] = defaultdict(list)
    for leg in legs:
        by_sec[leg["security"]].append(leg)

    trades = []
    for security, sec_legs in by_sec.items():
        sec_legs.sort(key=lambda x: x["dt"] or datetime.min)
        buys:  list[dict] = []
        sells: list[dict] = []
        for leg in sec_legs:
            (buys if leg["side"] == "B" else sells).append(leg)

        # Simple FIFO pair: each BUY matched with a SELL
        for buy, sell in zip(buys, sells):
            pnl_per_unit = sell["price"] - buy["price"]
            qty          = min(buy["qty"], sell["qty"])
            pnl          = pnl_per_unit * qty
            meta         = _option_meta(security)
            entry_dt     = buy["dt"]
            exit_dt      = sell["dt"]
            dur_min      = (
                (exit_dt - entry_dt).total_seconds() / 60
                if entry_dt and exit_dt else None
            )
            trades.append({
                "security":    security,
                "opt_type":    meta["opt_type"] or "EQ",
                "strike":      meta["strike"],
                "underlying":  meta["underlying"],
                "direction":   "LONG",
                "entry_price": buy["price"],
                "exit_price":  sell["price"],
                "qty":         qty,
                "pnl":         round(pnl, 2),
                "entry_dt":    entry_dt.isoformat() if entry_dt else "",
                "exit_dt":     exit_dt.isoformat() if exit_dt else "",
                "entry_date":  entry_dt.date().isoformat() if entry_dt else "",
                "entry_time":  entry_dt.strftime("%H:%M") if entry_dt else "",
                "exit_time":   exit_dt.strftime("%H:%M") if exit_dt else "",
                "day_of_week": entry_dt.strftime("%A") if entry_dt else "",
                "hour":        entry_dt.hour if entry_dt else 0,
                "duration_min": round(dur_min, 1) if dur_min is not None else None,
                "won":         pnl > 0,
                "source":      "zerodha_csv",
            })
        # Handle SHORT legs (SELL first, BUY to close)
        for sell, buy in zip(sells[len(buys):], buys[len(sells):]):
            pnl_per_unit = sell["price"] - buy["price"]
            qty          = min(buy["qty"], sell["qty"])
            pnl          = pnl_per_unit * qty
            meta         = _option_meta(security)
            entry_dt     = sell["dt"]
            exit_dt      = buy["dt"]
            dur_min      = (
                (exit_dt - entry_dt).total_seconds() / 60
                if entry_dt and exit_dt else None
            )
            trades.append({
                "security":    security,
                "opt_type":    meta["opt_type"] or "EQ",
                "strike":      meta["strike"],
                "underlying":  meta["underlying"],
                "direction":   "SHORT",
                "entry_price": sell["price"],
                "exit_price":  buy["price"],
                "qty":         qty,
                "pnl":         round(pnl, 2),
                "entry_dt":    entry_dt.isoformat() if entry_dt else "",
                "exit_dt":     exit_dt.isoformat() if exit_dt else "",
                "entry_date":  entry_dt.date().isoformat() if entry_dt else "",
                "entry_time":  entry_dt.strftime("%H:%M") if entry_dt else "",
                "exit_time":   exit_dt.strftime("%H:%M") if exit_dt else "",
                "day_of_week": entry_dt.strftime("%A") if entry_dt else "",
                "hour":        entry_dt.hour if entry_dt else 0,
                "duration_min": round(dur_min, 1) if dur_min is not None else None,
                "won":         pnl > 0,
                "source":      "zerodha_csv",
            })
    return sorted(trades, key=lambda t: t["entry_dt"])


# ── App trade log loader ──────────────────────────────────────────────────────

def load_app_trade_log(path: Path) -> list[dict]:
    """Load trades from app's trade_log.json (today's trades)."""
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    trades = []
    for t in data.get("trades", []):
        if t.get("status") != "exited":
            continue
        entry_dt = _parse_dt(t.get("timestamp", ""))
        exit_dt  = _parse_dt(t.get("exit_time", ""))
        dur_min  = (
            (exit_dt - entry_dt).total_seconds() / 60
            if entry_dt and exit_dt else None
        )
        meta = _option_meta(t.get("instrument", ""))
        pnl  = t.get("pnl", 0) or 0
        trades.append({
            "security":    t.get("instrument", "").replace("NFO:", ""),
            "opt_type":    meta["opt_type"] or "EQ",
            "strike":      meta["strike"],
            "underlying":  meta["underlying"],
            "direction":   t.get("direction", "").upper(),
            "entry_price": t.get("entry_premium", t.get("entry_price", 0)),
            "exit_price":  t.get("exit_premium", t.get("exit_price", 0)),
            "qty":         t.get("quantity", 0),
            "pnl":         round(pnl, 2),
            "entry_dt":    t.get("timestamp", ""),
            "exit_dt":     t.get("exit_time", ""),
            "entry_date":  entry_dt.date().isoformat() if entry_dt else "",
            "entry_time":  entry_dt.strftime("%H:%M") if entry_dt else "",
            "exit_time":   exit_dt.strftime("%H:%M") if exit_dt else "",
            "day_of_week": entry_dt.strftime("%A") if entry_dt else "",
            "hour":        entry_dt.hour if entry_dt else 0,
            "duration_min": round(dur_min, 1) if dur_min is not None else None,
            "won":         pnl > 0,
            "exit_reason": t.get("exit_reason", ""),
            "source":      "app_log",
        })
    return trades


# ── Pattern analysis ──────────────────────────────────────────────────────────

def analyse(trades: list[dict]) -> dict:
    """Generate pattern analysis from trade list."""
    if not trades:
        return {}

    total       = len(trades)
    winners     = [t for t in trades if t["won"]]
    losers      = [t for t in trades if not t["won"]]
    win_rate    = len(winners) / total * 100
    total_pnl   = sum(t["pnl"] for t in trades)
    avg_win     = sum(t["pnl"] for t in winners) / len(winners) if winners else 0
    avg_loss    = sum(t["pnl"] for t in losers)  / len(losers)  if losers  else 0
    profit_factor = (
        abs(sum(t["pnl"] for t in winners)) /
        abs(sum(t["pnl"] for t in losers))
        if losers and any(t["pnl"] < 0 for t in trades) else None
    )

    def _group(key: str) -> dict:
        g: dict[str, list] = defaultdict(list)
        for t in trades:
            g[str(t.get(key, "?"))].append(t)
        return {
            k: {
                "total":    len(v),
                "wins":     sum(1 for t in v if t["won"]),
                "losses":   sum(1 for t in v if not t["won"]),
                "win_rate": round(sum(1 for t in v if t["won"]) / len(v) * 100, 1),
                "pnl":      round(sum(t["pnl"] for t in v), 2),
            }
            for k, v in sorted(g.items())
        }

    # Time-of-day buckets
    def _time_bucket(t: dict) -> str:
        h = t.get("hour", 0)
        if h < 10:  return "09:15–10:00 (Open)"
        if h < 11:  return "10:00–11:00"
        if h < 12:  return "11:00–12:00"
        if h < 13:  return "12:00–13:00"
        if h < 14:  return "13:00–14:00"
        return              "14:00–15:30 (Close)"

    time_groups: dict[str, list] = defaultdict(list)
    for t in trades:
        time_groups[_time_bucket(t)].append(t)
    time_analysis = {
        k: {
            "total":    len(v),
            "wins":     sum(1 for t in v if t["won"]),
            "win_rate": round(sum(1 for t in v if t["won"]) / len(v) * 100, 1),
            "pnl":      round(sum(t["pnl"] for t in v), 2),
        }
        for k, v in sorted(time_groups.items())
    }

    return {
        "summary": {
            "total_trades":   total,
            "winners":        len(winners),
            "losers":         len(losers),
            "win_rate":       round(win_rate, 1),
            "total_pnl":      round(total_pnl, 2),
            "avg_win":        round(avg_win, 2),
            "avg_loss":       round(avg_loss, 2),
            "profit_factor":  round(profit_factor, 2) if profit_factor else None,
            "best_trade":     round(max(t["pnl"] for t in trades), 2),
            "worst_trade":    round(min(t["pnl"] for t in trades), 2),
            "avg_duration":   round(
                sum(t["duration_min"] for t in trades if t["duration_min"] is not None) /
                sum(1 for t in trades if t["duration_min"] is not None), 1
            ) if any(t["duration_min"] for t in trades) else None,
        },
        "by_direction":  _group("direction"),
        "by_opt_type":   _group("opt_type"),
        "by_day":        _group("day_of_week"),
        "by_time":       time_analysis,
        "trades":        trades,
    }
"""Trade report analyser — Zerodha FO tradebook CSV parser + pattern analysis."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


# ── Zerodha FO Tradebook parser ──────────────────────────────────────────────
# Columns: symbol,isin,trade_date,exchange,segment,series,trade_type,auction,
#          quantity,price,trade_id,order_id,order_execution_time,expiry_date

def parse_zerodha_fo_csv(content: str) -> list[dict]:
    """Parse Zerodha FO tradebook CSV → list of matched trade dicts."""
    from io import StringIO
    df = pd.read_csv(StringIO(content))
    df.columns = [c.strip().lower() for c in df.columns]

    df["order_execution_time"] = pd.to_datetime(df["order_execution_time"], errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    df["price"]    = pd.to_numeric(df["price"],    errors="coerce").fillna(0)

    # Aggregate partial fills → one row per order
    orders = (
        df.groupby(["order_id", "symbol", "trade_type", "trade_date"])
        .agg(quantity=("quantity", "sum"),
             avg_price=("price", "mean"),
             time=("order_execution_time", "min"),
             expiry_date=("expiry_date", "first"))
        .reset_index()
    )

    trades: list[dict] = []

    for symbol, grp in orders.groupby("symbol"):
        grp    = grp.sort_values("time")
        meta   = _option_meta(symbol)
        buys   = grp[grp["trade_type"] == "buy"].copy()
        sells  = grp[grp["trade_type"] == "sell"].copy()

        buy_pool  = [(r.avg_price, r.quantity, r.time, r.trade_date) for r in buys.itertuples()]
        sell_pool = [(r.avg_price, r.quantity, r.time, r.trade_date) for r in sells.itertuples()]

        bi = si = 0
        while bi < len(buy_pool) and si < len(sell_pool):
            bp, bq, bt, bd = buy_pool[bi]
            sp, sq, st, sd = sell_pool[si]
            matched = min(bq, sq)
            pnl     = (sp - bp) * matched

            entry_dt = bt if bt <= st else st
            exit_dt  = st if bt <= st else bt
            direction = "LONG" if bt <= st else "SHORT"
            dur_min   = (exit_dt - entry_dt).total_seconds() / 60 if pd.notna(entry_dt) and pd.notna(exit_dt) else None

            trades.append({
                "security":     symbol,
                "opt_type":     meta["opt_type"],
                "strike":       meta["strike"],
                "underlying":   meta["underlying"],
                "direction":    direction,
                "entry_price":  round(bp if direction == "LONG" else sp, 2),
                "exit_price":   round(sp if direction == "LONG" else bp, 2),
                "qty":          matched,
                "pnl":          round(pnl, 2),
                "entry_dt":     entry_dt.isoformat() if pd.notna(entry_dt) else "",
                "exit_dt":      exit_dt.isoformat()  if pd.notna(exit_dt)  else "",
                "entry_date":   str(bd if direction == "LONG" else sd),
                "entry_time":   entry_dt.strftime("%H:%M") if pd.notna(entry_dt) else "",
                "exit_time":    exit_dt.strftime("%H:%M")  if pd.notna(exit_dt)  else "",
                "day_of_week":  entry_dt.strftime("%A") if pd.notna(entry_dt) else "",
                "hour":         entry_dt.hour if pd.notna(entry_dt) else 0,
                "duration_min": round(dur_min, 1) if dur_min is not None else None,
                "won":          pnl > 0,
                "exit_reason":  "",
                "source":       "zerodha_csv",
            })

            buy_pool[bi]  = (bp, bq  - matched, bt, bd)
            sell_pool[si] = (sp, sq  - matched, st, sd)
            if buy_pool[bi][1]  <= 0: bi += 1
            if sell_pool[si][1] <= 0: si += 1

    return sorted(trades, key=lambda t: t["entry_dt"])


# ── Legacy generic CSV parser (drag-drop fallback) ────────────────────────────
def parse_zerodha_csv(content: str) -> list[dict]:
    """Auto-detect Zerodha FO tradebook and parse it."""
    from io import StringIO
    df = pd.read_csv(StringIO(content), nrows=1)
    cols = [c.strip().lower() for c in df.columns]
    if "order_execution_time" in cols:
        return parse_zerodha_fo_csv(content)
    raise ValueError(f"Unrecognised CSV format. Columns found: {cols}")


def pair_legs_into_trades(legs: list[dict]) -> list[dict]:
    """No-op shim — FO parser already returns paired trades."""
    return legs


# ── Option metadata ───────────────────────────────────────────────────────────
def _option_meta(symbol: str) -> dict:
    m = re.search(r"(NIFTY|BANKNIFTY|FINNIFTY|SENSEX)(\d+)(\d{5})(CE|PE)", symbol.upper())
    if m:
        return {"underlying": m.group(1), "strike": int(m.group(3)), "opt_type": m.group(4)}
    return {"underlying": symbol, "strike": 0, "opt_type": ""}


# ── App trade log loader ──────────────────────────────────────────────────────
def load_app_trade_log(path: Path) -> list[dict]:
    import json
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    trades = []
    for t in data.get("trades", []):
        if t.get("status") != "exited":
            continue
        def _pdt(s):
            try: return datetime.fromisoformat(s)
            except: return None
        entry_dt = _pdt(t.get("timestamp", ""))
        exit_dt  = _pdt(t.get("exit_time", ""))
        dur_min  = (exit_dt - entry_dt).total_seconds() / 60 if entry_dt and exit_dt else None
        meta     = _option_meta(t.get("instrument", ""))
        pnl      = t.get("pnl", 0) or 0
        trades.append({
            "security":    t.get("instrument", "").replace("NFO:", ""),
            "opt_type":    meta["opt_type"] or "EQ",
            "strike":      meta["strike"],
            "underlying":  meta["underlying"],
            "direction":   t.get("direction", "").upper(),
            "entry_price": t.get("entry_premium", t.get("entry_price", 0)),
            "exit_price":  t.get("exit_premium",  t.get("exit_price",  0)),
            "qty":         t.get("quantity", 0),
            "pnl":         round(pnl, 2),
            "entry_dt":    t.get("timestamp", ""),
            "exit_dt":     t.get("exit_time",  ""),
            "entry_date":  entry_dt.date().isoformat() if entry_dt else "",
            "entry_time":  entry_dt.strftime("%H:%M") if entry_dt else "",
            "exit_time":   exit_dt.strftime("%H:%M")  if exit_dt  else "",
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
    if not trades:
        return {"summary": {}, "by_direction": {}, "by_opt_type": {}, "by_day": {}, "by_time": {}, "trades": []}

    total     = len(trades)
    winners   = [t for t in trades if t["won"]]
    losers    = [t for t in trades if not t["won"]]
    win_rate  = len(winners) / total * 100
    total_pnl = sum(t["pnl"] for t in trades)
    avg_win   = sum(t["pnl"] for t in winners) / len(winners) if winners else 0
    avg_loss  = sum(t["pnl"] for t in losers)  / len(losers)  if losers  else 0
    gross_win = abs(sum(t["pnl"] for t in winners))
    gross_loss= abs(sum(t["pnl"] for t in losers))
    pf        = round(gross_win / gross_loss, 2) if gross_loss else None

    # Monthly P&L
    monthly: dict[str, float] = defaultdict(float)
    for t in trades:
        m = t["entry_date"][:7] if t["entry_date"] else "unknown"
        monthly[m] += t["pnl"]

    # Daily P&L
    daily: dict[str, float] = defaultdict(float)
    for t in trades:
        daily[t["entry_date"]] += t["pnl"]

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

    def _time_bucket(t: dict) -> str:
        h = t.get("hour", 0)
        if h < 10:  return "09:15–10:00"
        if h < 11:  return "10:00–11:00"
        if h < 12:  return "11:00–12:00"
        if h < 13:  return "12:00–13:00"
        if h < 14:  return "13:00–14:00"
        return              "14:00–15:30"

    time_groups: dict[str, list] = defaultdict(list)
    for t in trades:
        time_groups[_time_bucket(t)].append(t)

    return {
        "summary": {
            "total_trades":  total,
            "winners":       len(winners),
            "losers":        len(losers),
            "win_rate":      round(win_rate, 1),
            "total_pnl":     round(total_pnl, 2),
            "avg_win":       round(avg_win, 2),
            "avg_loss":      round(avg_loss, 2),
            "profit_factor": pf,
            "best_trade":    round(max(t["pnl"] for t in trades), 2),
            "worst_trade":   round(min(t["pnl"] for t in trades), 2),
            "avg_duration":  round(
                sum(t["duration_min"] for t in trades if t["duration_min"] is not None) /
                max(1, sum(1 for t in trades if t["duration_min"] is not None)), 1
            ),
        },
        "by_direction": _group("direction"),
        "by_opt_type":  _group("opt_type"),
        "by_day":       _group("day_of_week"),
        "by_time": {
            k: {
                "total":    len(v),
                "wins":     sum(1 for t in v if t["won"]),
                "win_rate": round(sum(1 for t in v if t["won"]) / len(v) * 100, 1),
                "pnl":      round(sum(t["pnl"] for t in v), 2),
            }
            for k, v in sorted(time_groups.items())
        },
        "monthly": {k: round(v, 2) for k, v in sorted(monthly.items())},
        "daily":   {k: round(v, 2) for k, v in sorted(daily.items())},
        "trades":  trades,
    }
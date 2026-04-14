"""Live Performance Tracker — Auto-calibrating strategy weights.

The static calibration.json is a snapshot from months ago. Markets
change. This module maintains a rolling window of live trade results
per strategy and produces DYNAMIC weights that reflect recent reality.

Architecture:
  - Reads trade_log files from archives/ + live trade_log.json
  - Maintains a sliding window of last N trades per strategy
  - Merges with static calibration (static = prior, live = update)
  - Produces a blended win_rate + profit_factor for each strategy
  - Exposes get_live_stats() for meta router to use

The blend formula:
    live_weight = min(live_count / BLEND_SATURATION, 1.0)
    blended_wr  = static_wr * (1-live_weight) + live_wr * live_weight

At 0 live trades → fully trust static calibration.
At BLEND_SATURATION trades → fully trust live data.
In between → proportional blend (Bayesian update, basically).

Author: Code Puppy 🐶
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

ROLLING_WINDOW    = 30     # last N live trades per strategy to consider
BLEND_SATURATION  = 20     # at this many live trades, fully trust live data
MIN_LIVE_TRADES   = 5      # need at least this many live trades to blend at all
LOOKBACK_DAYS     = 30     # scan archives going back this many calendar days

_ROOT = Path(__file__).parent


@dataclass
class LiveTradeRecord:
    """Slim record of a single completed trade."""
    strategy_id: str
    direction: str     # 'long' | 'short'
    pnl: float         # rupees
    pts: float         # index points (entry vs exit underlying)
    won: bool
    date: str          # ISO date string


@dataclass
class LiveStrategyStats:
    """Per-strategy rolling stats from live trades."""
    strategy_id: str
    live_count: int        = 0
    live_wins: int         = 0
    live_losses: int       = 0
    live_win_rate: float   = 0.0
    live_avg_win_pts: float  = 0.0
    live_avg_loss_pts: float = 0.0
    live_profit_factor: float = 0.0
    recent_trades: list[dict] = field(default_factory=list)  # last 5 for UI


def _load_static_calibration() -> dict:
    """Load calibration.json as the static prior."""
    path = _ROOT / "calibration.json"
    try:
        with path.open() as f:
            return json.load(f)
    except Exception as e:
        logger.warning("calibration.json missing or broken: %s", e)
        return {}


def _parse_trade_log(log_path: Path) -> list[LiveTradeRecord]:
    """Extract completed trades from a trade_log JSON file."""
    try:
        with log_path.open() as f:
            data = json.load(f)
    except Exception:
        return []

    records: list[LiveTradeRecord] = []
    trades = data.get("trades", [])

    for t in trades:
        if t.get("status") != "exited":
            continue

        pnl    = float(t.get("pnl", 0))
        entry  = float(t.get("entry_price", 0))
        exit_p = float(t.get("exit_price", 0))

        if entry == 0:
            continue  # malformed

        direction = t.get("direction", "long")
        pts = (exit_p - entry) if direction == "long" else (entry - exit_p)

        # Strategy ID — trades should have a 'strategy' field.
        # Fall back to 'smart_router' if missing (older logs).
        strategy_id = t.get("strategy", t.get("signal_strategy", "smart_router"))

        records.append(LiveTradeRecord(
            strategy_id=strategy_id,
            direction=direction,
            pnl=pnl,
            pts=round(pts, 2),
            won=pnl > 0,
            date=data.get("date", "unknown"),
        ))

    return records


def _collect_all_live_trades(lookback_days: int = LOOKBACK_DAYS) -> list[LiveTradeRecord]:
    """Scan archives + live trade_log.json for recent trades."""
    all_records: list[LiveTradeRecord] = []
    cutoff = date.today() - timedelta(days=lookback_days)

    # Scan archives/
    archive_dir = _ROOT / "archives"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("trade_log_*.json")):
            # Parse date from filename: trade_log_YYYY-MM-DD.json
            try:
                file_date = date.fromisoformat(f.stem.replace("trade_log_", ""))
            except ValueError:
                continue
            if file_date < cutoff:
                continue
            all_records.extend(_parse_trade_log(f))

    # Also scan live trade_log.json if present
    live_log = _ROOT / "trade_log.json"
    if live_log.exists():
        all_records.extend(_parse_trade_log(live_log))

    return all_records


def compute_live_stats(
    lookback_days: int = LOOKBACK_DAYS,
    window: int = ROLLING_WINDOW,
) -> dict[str, LiveStrategyStats]:
    """Compute per-strategy rolling stats from live trade archive.

    Returns:
        Dict[strategy_id, LiveStrategyStats]
    """
    trades = _collect_all_live_trades(lookback_days)

    # Group by strategy, keep last `window` trades
    by_strategy: dict[str, list[LiveTradeRecord]] = defaultdict(list)
    for t in trades:
        by_strategy[t.strategy_id].append(t)

    result: dict[str, LiveStrategyStats] = {}

    for sid, all_trades in by_strategy.items():
        # Keep only the most recent N
        recent = all_trades[-window:]

        wins   = [t for t in recent if t.won]
        losses = [t for t in recent if not t.won]

        win_pts  = [t.pts for t in wins]
        loss_pts = [abs(t.pts) for t in losses]

        avg_win  = mean(win_pts)  if win_pts  else 0.0
        avg_loss = mean(loss_pts) if loss_pts else 0.0

        total_won  = sum(t.pnl for t in wins)
        total_lost = abs(sum(t.pnl for t in losses))
        pf = (total_won / total_lost) if total_lost > 0 else (2.0 if wins else 0.0)

        n       = len(recent)
        win_cnt = len(wins)

        result[sid] = LiveStrategyStats(
            strategy_id=sid,
            live_count=n,
            live_wins=win_cnt,
            live_losses=len(losses),
            live_win_rate=round(win_cnt / n * 100, 1) if n > 0 else 0.0,
            live_avg_win_pts=round(avg_win, 1),
            live_avg_loss_pts=round(avg_loss, 1),
            live_profit_factor=round(pf, 2),
            recent_trades=[{"date": t.date, "pts": t.pts, "won": t.won} for t in recent[-5:]],
        )

    return result


def get_blended_calibration(
    lookback_days: int = LOOKBACK_DAYS,
) -> dict[str, dict]:
    """Merge static calibration with live performance for each strategy.

    Returns a dict in the SAME shape as calibration.json so it's a
    drop-in replacement everywhere calibration is consumed.
    """
    static = _load_static_calibration()
    live   = compute_live_stats(lookback_days)

    blended: dict[str, dict] = {}

    # Start with all static entries
    for sid, s_stats in static.items():
        live_st = live.get(sid)

        if live_st is None or live_st.live_count < MIN_LIVE_TRADES:
            # Not enough live data — trust static
            blended[sid] = {**s_stats, "_source": "static"}
            continue

        n = live_st.live_count
        alpha = min(n / BLEND_SATURATION, 1.0)   # 0 → fully static, 1 → fully live

        b_wr  = (1 - alpha) * s_stats.get("win_rate", 50) + alpha * live_st.live_win_rate
        b_win = (1 - alpha) * s_stats.get("avg_win_pts", 30) + alpha * live_st.live_avg_win_pts
        b_los = (1 - alpha) * abs(s_stats.get("avg_loss_pts", 20)) + alpha * live_st.live_avg_loss_pts
        b_pf  = (1 - alpha) * s_stats.get("profit_factor", 1.5) + alpha * live_st.live_profit_factor

        blended[sid] = {
            **s_stats,
            "win_rate":      round(b_wr, 1),
            "avg_win_pts":   round(b_win, 1),
            "avg_loss_pts":  round(-b_los, 1),
            "profit_factor": round(b_pf, 2),
            "_source": f"blended(α={alpha:.2f}, n={n})",
            "_live_win_rate": live_st.live_win_rate,
            "_live_count": n,
        }

    # Add strategies that appear in live data but NOT in static calibration
    for sid, live_st in live.items():
        if sid not in blended and live_st.live_count >= MIN_LIVE_TRADES:
            blended[sid] = {
                "win_rate":      live_st.live_win_rate,
                "avg_win_pts":   live_st.live_avg_win_pts,
                "avg_loss_pts":  -live_st.live_avg_loss_pts,
                "profit_factor": live_st.live_profit_factor,
                "_source": "live_only",
                "_live_count": live_st.live_count,
            }

    return blended


# ── CLI demo ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🐶 Live Performance Tracker — Blended Calibration")
    print("=" * 80)

    live_stats = compute_live_stats()
    if not live_stats:
        print("  No live trades found in archives (or all trades missing 'strategy' field).")
        print("  Tip: Make sure your auto_trader.py tags each trade with the strategy ID.")
    else:
        print(f"\n  Found live stats for {len(live_stats)} strategy(ies):\n")
        for sid, s in live_stats.items():
            print(f"  {sid:<25}: {s.live_count} trades | WR={s.live_win_rate:.1f}% | PF={s.live_profit_factor:.2f}")

    print("\n  Blended calibration:")
    blended = get_blended_calibration()
    print(f"\n  {'Strategy':<25} {'WR%':>6} {'PF':>5}  Source")
    print("  " + "-" * 60)
    for sid, stats in blended.items():
        src = stats.get("_source", "static")
        print(f"  {sid:<25} {stats['win_rate']:>5.1f}% {stats['profit_factor']:>5.2f}  {src}")
    print("=" * 80)

"""Strategy Calibrator — compute actual win rates from backtests.

Instead of hand-coded confidence scores, this runs each strategy
independently over 60 days of real data and records:
  - win_rate       : % of trades that were profitable
  - avg_rr         : average realised risk-reward
  - trades_per_day : how active the strategy is
  - profit_factor  : total_wins / total_losses

These are saved to calibration.json and loaded by the meta-router
so scoring is grounded in actual evidence.
"""

from __future__ import annotations

import json
import time
from datetime import time as dt_time
from pathlib import Path

import pandas as pd

CALIBRATION_FILE = Path(__file__).parent / "calibration.json"

# Backtest params used for calibration — keep consistent
CAL_SL_POINTS   = 30.0
CAL_TRAIL_SL    = 15.0
CAL_RR          = 2.0
CAL_MAX_TRADES  = 4       # realistic daily cap during calibration
CAL_QUANTITY    = 1       # 1 unit — we only care about win rate, not rupees
ENTRY_START     = dt_time(9, 18)
EXIT_TIME       = dt_time(15, 15)


def _calc_pnl(direction: str, entry: float, exit_: float) -> float:
    return (exit_ - entry) if direction == "long" else (entry - exit_)


def _backtest_strategy_solo(
    strat_id: str,
    df: pd.DataFrame,
    sl_points: float = CAL_SL_POINTS,
    trailing_sl: float = CAL_TRAIL_SL,
    rr_ratio: float = CAL_RR,
    max_trades: int = CAL_MAX_TRADES,
) -> dict:
    """Run one strategy over the full df, return win-rate stats."""
    from strategies.registry import all_strategies  # noqa: PLC0415

    strat = next((s for s in all_strategies() if s.id == strat_id), None)
    if strat is None:
        return {"error": f"Strategy {strat_id!r} not found"}

    trades, winners = 0, 0
    total_win_pts, total_loss_pts = 0.0, 0.0

    # Walk day by day
    dates = sorted(set(df.index.date))
    for day in dates:
        day_df = df[df.index.date == day]
        if len(day_df) < 5:
            continue

        in_trade      = False
        trades_today  = 0
        entry_price   = 0.0
        direction     = ""
        stop_loss     = 0.0
        target        = 0.0
        highest       = 0.0
        lowest        = float("inf")
        last_exit_i   = None

        for i in range(1, len(day_df)):
            candle_time = day_df.index[i].time()
            candle      = day_df.iloc[i]
            price       = float(candle["close"])
            high        = float(candle["high"])
            low         = float(candle["low"])

            if candle_time < ENTRY_START:
                continue

            # Force exit at end of day
            if candle_time >= EXIT_TIME and in_trade:
                pnl = _calc_pnl(direction, entry_price, price)
                trades += 1
                if pnl > 0:
                    winners += 1; total_win_pts += pnl
                else:
                    total_loss_pts += abs(pnl)
                in_trade = False
                continue

            if candle_time >= EXIT_TIME:
                continue

            if in_trade:
                if direction == "long":
                    highest   = max(highest, high)
                    stop_loss = max(stop_loss, highest - trailing_sl)
                    if low <= stop_loss:
                        pnl = _calc_pnl(direction, entry_price, stop_loss)
                        trades += 1
                        if pnl > 0: winners += 1; total_win_pts += pnl
                        else: total_loss_pts += abs(pnl)
                        in_trade = False; last_exit_i = i
                    elif high >= target:
                        pnl = _calc_pnl(direction, entry_price, target)
                        trades += 1; winners += 1; total_win_pts += pnl
                        in_trade = False; last_exit_i = i
                else:  # short
                    lowest    = min(lowest, low)
                    stop_loss = min(stop_loss, lowest + trailing_sl)
                    if high >= stop_loss:
                        pnl = _calc_pnl(direction, entry_price, stop_loss)
                        trades += 1
                        if pnl > 0: winners += 1; total_win_pts += pnl
                        else: total_loss_pts += abs(pnl)
                        in_trade = False; last_exit_i = i
                    elif low <= target:
                        pnl = _calc_pnl(direction, entry_price, target)
                        trades += 1; winners += 1; total_win_pts += pnl
                        in_trade = False; last_exit_i = i
                continue

            if trades_today >= max_trades:
                continue
            if last_exit_i is not None and i - last_exit_i < 1:
                continue

            # Evaluate strategy with lookback up to this candle
            current_ts  = day_df.index[i]
            lookback_df = df[df.index <= current_ts]
            try:
                signal = strat.evaluate(lookback_df)
            except Exception:  # noqa: BLE001
                continue

            if not signal.should_enter or signal.direction is None:
                continue

            in_trade     = True
            trades_today += 1
            direction    = signal.direction.value
            entry_price  = price
            highest      = high
            lowest       = low

            if direction == "long":
                stop_loss = entry_price - sl_points
                target    = entry_price + sl_points * rr_ratio
            else:
                stop_loss = entry_price + sl_points
                target    = entry_price - sl_points * rr_ratio

    losers    = trades - winners
    win_rate  = winners / trades * 100 if trades > 0 else 0.0
    pf        = total_win_pts / total_loss_pts if total_loss_pts > 0 else 0.0
    tpd       = trades / max(len(dates), 1)
    avg_win   = total_win_pts / winners if winners > 0 else 0.0
    avg_loss  = total_loss_pts / losers if losers > 0 else 0.0

    return {
        "trades":         trades,
        "winners":        winners,
        "losers":         losers,
        "win_rate":       round(win_rate, 1),
        "profit_factor":  round(pf, 2),
        "trades_per_day": round(tpd, 2),
        "avg_win_pts":    round(avg_win, 1),
        "avg_loss_pts":   round(avg_loss, 1),
    }


def run_calibration(
    df: pd.DataFrame,
    strategy_ids: list[str] | None = None,
    verbose: bool = True,
) -> dict[str, dict]:
    """Run all strategies and save win rates to calibration.json.

    Args:
        df           : Full historical OHLCV DataFrame.
        strategy_ids : Subset of strategy IDs to calibrate. None = all.
        verbose      : Print progress.

    Returns:
        dict mapping strategy_id → stats dict.
    """
    import strategies.loader  # noqa: F401, PLC0415
    from strategies.registry import all_strategies  # noqa: PLC0415

    strats = [
        s for s in all_strategies()
        if s.id not in ("smart_router", "meta_router")
        and (strategy_ids is None or s.id in strategy_ids)
    ]

    results: dict[str, dict] = {}
    total = len(strats)

    for idx, strat in enumerate(strats, 1):
        if verbose:
            print(f"  [{idx}/{total}] {strat.emoji} {strat.name} ...", end=" ", flush=True)
        t0    = time.time()
        stats = _backtest_strategy_solo(strat.id, df)
        elapsed = time.time() - t0
        results[strat.id] = stats
        if verbose:
            if "error" in stats:
                print(f"ERROR: {stats['error']}")
            else:
                print(
                    f"{stats['win_rate']:.0f}% WR "
                    f"({stats['winners']}W/{stats['losers']}L) "
                    f"PF={stats['profit_factor']:.2f} "
                    f"{stats['trades_per_day']:.1f}/day "
                    f"[{elapsed:.1f}s]"
                )

    # Persist — merge with existing (don't wipe other strategies' data)
    existing = load_calibration()
    existing.update(results)
    CALIBRATION_FILE.write_text(json.dumps(existing, indent=2))
    if verbose:
        print(f"\n✅ Saved to {CALIBRATION_FILE}")

    return results


def load_calibration() -> dict[str, dict]:
    """Load saved calibration. Returns empty dict if not yet run."""
    if not CALIBRATION_FILE.exists():
        return {}
    try:
        return json.loads(CALIBRATION_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def win_rate_for(strategy_id: str) -> float:
    """Return calibrated win rate (0-100) for a strategy, or 50 if unknown."""
    cal = load_calibration()
    return cal.get(strategy_id, {}).get("win_rate", 50.0)


if __name__ == "__main__":
    print("🔬 Running strategy calibration...")
    print("   Fetching 60 days of Nifty Futures data...")
    try:
        from kite_fetcher import fetch_data  # noqa: PLC0415
        df, src = fetch_data(period="60d")
    except Exception:
        from data_fetcher import fetch_intraday_data  # noqa: PLC0415
        df  = fetch_intraday_data(period="60d")
        src = "Yahoo"
    print(f"   Source: {src}  |  {len(df)} candles\n")
    run_calibration(df)
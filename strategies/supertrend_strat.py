"""Supertrend Strategy.

Uses the Supertrend indicator for clear trend-following signals.
Flips direction when price crosses the Supertrend line.
"""

import pandas as pd
from strategy import StrategySignal, StrategyCondition, Direction
import indicators as ind
from strategies.registry import register, StrategyInfo


def evaluate_supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
) -> StrategySignal:
    """Supertrend Flip Strategy.

    Entry conditions:
    1. Supertrend just flipped direction (within last 2 candles)
    2. Price closed above/below the Supertrend line
    3. EMA-20 slope confirms the direction
    4. RSI is not at extreme (avoid chasing)
    5. Not within first 15 minutes
    """
    conditions = []

    if len(df) < 30:
        return StrategySignal(should_enter=False, reason="Insufficient data")

    close = df["close"]
    high = df["high"]
    low = df["low"]
    price = float(close.iloc[-1])

    st = ind.supertrend(high, low, close, period, multiplier)
    st_dir = st["direction"]
    st_line = st["supertrend"]

    # ── 1. Supertrend direction — flip OR sustained trend ────────
    # Original code required a flip within 2-3 candles, which blocked
    # valid continuation entries in an already-confirmed trend.
    # Fix: accept EITHER a recent flip (strong new-trend signal) OR
    # a consistent direction held for at most 20 candles (continuation).
    # If trend has been running > 20 candles it's stale — skip.
    curr_dir  = int(st_dir.iloc[-1])
    direction = Direction.LONG if curr_dir == 1 else Direction.SHORT

    # Count how many consecutive candles the trend has been in curr_dir
    lookback   = min(20, len(st_dir) - 1)
    trend_run  = 0
    for i in range(1, lookback + 1):
        if int(st_dir.iloc[-i]) == curr_dir:
            trend_run += 1
        else:
            break

    just_flipped      = trend_run <= 2           # flipped within last 2 candles
    trend_fresh       = trend_run <= 20          # trend is still fresh (< 20 candles old)
    direction_ok      = just_flipped or trend_fresh

    flip_label = (
        f"Flipped {trend_run} candle(s) ago"
        if just_flipped else
        f"Trending {'BEARISH' if curr_dir == -1 else 'BULLISH'} for {trend_run} candles"
        if trend_fresh else
        f"Stale — trend running {trend_run} candles (> 20)"
    )
    conditions.append(StrategyCondition(
        name="Supertrend Direction",
        met=direction_ok,
        detail=f"{flip_label} | ST line={float(st_line.iloc[-1]):.0f}",
    ))

    # ── 2. Price vs Supertrend line ──────────────────
    st_val = float(st_line.iloc[-1])
    if direction == Direction.LONG:
        price_ok = price > st_val
    else:
        price_ok = price < st_val

    conditions.append(StrategyCondition(
        name="Price Position",
        met=price_ok,
        detail=f"Price {price:.0f} {'above' if price > st_val else 'below'} ST line {st_val:.0f}",
    ))

    # ── 3. EMA slope confirmation ───────────────────
    ema_20 = ind.ema(close, 20)
    slope = float(ema_20.iloc[-1] - ema_20.iloc[-3]) / 3

    if direction == Direction.LONG:
        slope_ok = slope > 0
    else:
        slope_ok = slope < 0

    conditions.append(StrategyCondition(
        name="EMA Slope",
        met=slope_ok,
        detail=f"EMA-20 slope {slope:+.1f} ({'confirms' if slope_ok else 'against'} direction)",
    ))

    # ── 4. RSI not extreme (don't chase) ─────────────
    rsi_val = float(ind.rsi(close, 14).iloc[-1])
    if direction == Direction.LONG:
        rsi_ok = rsi_val < 75  # not overbought
    else:
        rsi_ok = rsi_val > 25  # not oversold

    conditions.append(StrategyCondition(
        name="RSI Check",
        met=rsi_ok,
        detail=f"RSI {rsi_val:.0f} ({'OK' if rsi_ok else 'extreme — avoid chasing'})",
    ))

    # ── 5. Time filter ─────────────────────────────
    last_time = df.index[-1]
    market_open = last_time.replace(hour=9, minute=15, second=0)
    mins_since = (last_time - market_open).total_seconds() / 60
    time_ok = mins_since >= 15

    conditions.append(StrategyCondition(
        name="Opening Filter",
        met=time_ok,
        detail=f"{mins_since:.0f} mins since open",
    ))

    # Score
    weighted = [c for c in conditions if c.weight > 0]
    total_w = sum(c.weight for c in weighted)
    met_w = sum(c.weight for c in weighted if c.met)
    confidence = (met_w / total_w * 100) if total_w > 0 else 0
    all_met = all(c.met for c in weighted)

    return StrategySignal(
        should_enter=all_met,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=f"Supertrend: {'ALL' if all_met else 'NOT all'} conditions ({confidence:.0f}%)",
    )


register(StrategyInfo(
    id="supertrend",
    name="Supertrend",
    emoji="💠",
    description=(
        "Follows the Supertrend indicator. When it flips from bearish to bullish "
        "(or vice versa), enter in the new direction. Very visual and easy to follow."
    ),
    category="trend",
    difficulty="beginner",
    market_condition="Trending markets. Avoid sideways/choppy conditions.",
    evaluate=evaluate_supertrend,
    entry_rules=[
        "Supertrend indicator flips direction (within last 2 candles)",
        "Price closes on the correct side of the Supertrend line",
        "EMA-20 slope confirms the direction",
        "RSI is not at extreme levels (avoid chasing)",
        "At least 15 minutes after market open",
    ],
    exit_rules=[
        "Stop-loss: At the Supertrend line itself",
        "Target: 2x stop-loss distance",
        "Exit when Supertrend flips against you",
    ],
    risk_tips=[
        "Supertrend parameters (10, 3.0) are standard. Don't over-optimize",
        "Combine with ADX > 25 for higher probability",
        "Avoid trading Supertrend flips in the first 15 minutes",
    ],
    pros=[
        "Very visual — easy to see on charts",
        "Gives clear entry and stop-loss levels",
        "Works well with the trend",
    ],
    cons=[
        "Whipsaws badly in sideways markets",
        "Lagging indicator — enters after the move starts",
        "Large stop-losses when ATR is high",
    ],
    example_scenario=(
        "Supertrend was bearish (red line above price). Price pushes through "
        "and Supertrend flips bullish (green line below price). EMA-20 slopes up. "
        "RSI is 55. \u2192 BUY with SL at the Supertrend line."
    ),
))

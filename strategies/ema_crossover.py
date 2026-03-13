"""EMA Crossover Strategy.

Classic 9/21 EMA crossover with volume and MACD confirmation.
Simplest momentum strategy for beginners.
"""

import pandas as pd
from strategy import StrategySignal, StrategyCondition, Direction
import indicators as ind
from strategies.registry import register, StrategyInfo


def evaluate_ema_crossover(
    df: pd.DataFrame,
    fast_period: int = 9,
    slow_period: int = 21,
) -> StrategySignal:
    """EMA Crossover with MACD Confirmation.

    Entry conditions:
    1. EMA-9 just crossed above EMA-21 (long) or below (short)
    2. MACD histogram confirms direction (positive for long, negative for short)
    3. Price closed in the direction of the cross
    4. Crossover happened within last 3 candles (fresh cross)
    5. Not within first 15 minutes
    """
    conditions = []

    if len(df) < 30:
        return StrategySignal(should_enter=False, reason="Insufficient data")

    close = df["close"]
    price = float(close.iloc[-1])

    ema_fast = ind.ema(close, fast_period)
    ema_slow = ind.ema(close, slow_period)

    # ── 1. EMA Cross detection ──────────────────────
    curr_fast = float(ema_fast.iloc[-1])
    curr_slow = float(ema_slow.iloc[-1])
    prev_fast = float(ema_fast.iloc[-2])
    prev_slow = float(ema_slow.iloc[-2])

    golden_cross = curr_fast > curr_slow and prev_fast <= prev_slow
    death_cross = curr_fast < curr_slow and prev_fast >= prev_slow

    # Check if cross happened within last 3 candles
    recent_cross = False
    cross_dir = None
    for lookback in range(1, min(4, len(df))):
        f_now = float(ema_fast.iloc[-lookback])
        s_now = float(ema_slow.iloc[-lookback])
        f_prev = float(ema_fast.iloc[-lookback - 1])
        s_prev = float(ema_slow.iloc[-lookback - 1])
        if f_now > s_now and f_prev <= s_prev:
            recent_cross = True
            cross_dir = Direction.LONG
            break
        if f_now < s_now and f_prev >= s_prev:
            recent_cross = True
            cross_dir = Direction.SHORT
            break

    conditions.append(StrategyCondition(
        name="EMA Crossover",
        met=recent_cross,
        detail=(
            f"EMA-{fast_period} {'crossed above' if cross_dir == Direction.LONG else 'crossed below' if cross_dir == Direction.SHORT else 'no cross'} "
            f"EMA-{slow_period} ({curr_fast:.0f} vs {curr_slow:.0f})"
        ),
    ))

    # ── 2. MACD histogram confirmation ──────────────
    macd_data = ind.macd(close)
    hist = float(macd_data["histogram"].iloc[-1])

    if cross_dir == Direction.LONG:
        macd_ok = hist > 0
    elif cross_dir == Direction.SHORT:
        macd_ok = hist < 0
    else:
        macd_ok = False

    conditions.append(StrategyCondition(
        name="MACD Confirms",
        met=macd_ok,
        detail=f"MACD histogram {hist:+.1f} ({'confirms' if macd_ok else 'diverges'})",
    ))

    # ── 3. Price confirmation ───────────────────────
    c_open = float(df.iloc[-1]["open"])
    if cross_dir == Direction.LONG:
        price_ok = price > c_open and price > curr_fast
    elif cross_dir == Direction.SHORT:
        price_ok = price < c_open and price < curr_fast
    else:
        price_ok = False

    conditions.append(StrategyCondition(
        name="Price Confirms",
        met=price_ok,
        detail=f"Close {'above' if price > c_open else 'below'} open, {'above' if price > curr_fast else 'below'} EMA-{fast_period}",
    ))

    # ── 4. Fresh cross (within 3 candles) ─────────────
    conditions.append(StrategyCondition(
        name="Fresh Signal",
        met=recent_cross,
        detail="Cross within last 3 candles" if recent_cross else "No recent cross",
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
        should_enter=all_met and cross_dir is not None,
        direction=cross_dir,
        confidence=confidence,
        conditions=conditions,
        reason=f"EMA Cross: {'ALL' if all_met else 'NOT all'} conditions ({confidence:.0f}%)",
    )


register(StrategyInfo(
    id="ema_crossover",
    name="EMA Crossover",
    emoji="✂️",
    description=(
        "The most beginner-friendly strategy. Buy when fast EMA crosses "
        "above slow EMA, sell when it crosses below. Confirmed by MACD."
    ),
    category="momentum",
    difficulty="beginner",
    market_condition="Works in any market but best in trending conditions.",
    evaluate=evaluate_ema_crossover,
    entry_rules=[
        "EMA-9 crosses above EMA-21 (bullish) or below (bearish)",
        "MACD histogram confirms the direction",
        "Current candle closes in the crossover direction",
        "Crossover happened within the last 3 candles (fresh signal)",
        "At least 15 minutes after market open",
    ],
    exit_rules=[
        "Stop-loss: Below the EMA-21 line",
        "Target: 2x stop-loss (1:2 R:R)",
        "Exit on reverse crossover",
    ],
    risk_tips=[
        "EMA crossovers lag — you'll always enter slightly late",
        "Many false crosses in sideways markets. Combine with ADX > 20",
        "Don't trade every cross — wait for MACD confirmation",
    ],
    pros=[
        "Simplest strategy to learn and execute",
        "Catches the middle of trends reliably",
        "Very clear, rule-based signals",
    ],
    cons=[
        "Late entries (lagging indicator)",
        "Many false signals in choppy markets",
        "Misses the first part of every move",
    ],
    example_scenario=(
        "EMA-9 crosses above EMA-21. MACD histogram turns positive. "
        "Current candle closes green above both EMAs. "
        "\u2192 BUY with SL below EMA-21."
    ),
))

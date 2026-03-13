"""Scalping Strategies for Nifty Options.

Optimized for 1m/5m charts with quick in-and-out trades.
Small targets (5-15 pts on options), tight SLs, high frequency.

For options premium approximation:
- ATM option delta ≈ 0.5
- 1 pt Nifty move ≈ 0.5 pt option premium move
- So 10 pt Nifty scalp ≈ 5 pt option premium gain
"""

import pandas as pd
import numpy as np
from strategy import StrategySignal, StrategyCondition, Direction
import indicators as ind
from strategies.registry import register, StrategyInfo


# ───────────────────────────────────────────────────────────
# 1. EMA SCALP — Fast EMA crossover on 1m/5m
# ───────────────────────────────────────────────────────────

def evaluate_ema_scalp(df: pd.DataFrame) -> StrategySignal:
    """EMA Scalp — 5/13 EMA cross on fast timeframes.

    Quick entries on EMA cross with RSI momentum filter.
    Designed for 1-5 candle hold time.

    Conditions:
    1. EMA-5 crosses EMA-13 (fast cross)
    2. RSI between 40-60 (not overbought/oversold — room to run)
    3. Price action confirms (candle closes in cross direction)
    4. Spread between EMAs is widening (momentum building)
    """
    conditions = []
    if len(df) < 20:
        return StrategySignal(should_enter=False, reason="Need 20+ candles")

    close = df["close"]
    price = float(close.iloc[-1])
    c_open = float(df.iloc[-1]["open"])

    ema5 = ind.ema(close, 5)
    ema13 = ind.ema(close, 13)
    e5 = float(ema5.iloc[-1])
    e13 = float(ema13.iloc[-1])
    e5_p = float(ema5.iloc[-2])
    e13_p = float(ema13.iloc[-2])

    # 1. EMA cross
    bull_cross = e5 > e13 and e5_p <= e13_p
    bear_cross = e5 < e13 and e5_p >= e13_p
    # Also accept if cross happened 1 candle ago
    if not (bull_cross or bear_cross) and len(df) > 3:
        e5_pp = float(ema5.iloc[-3])
        e13_pp = float(ema13.iloc[-3])
        bull_cross = e5_p > e13_p and e5_pp <= e13_pp and e5 > e13
        bear_cross = e5_p < e13_p and e5_pp >= e13_pp and e5 < e13

    cross_ok = bull_cross or bear_cross
    direction = Direction.LONG if bull_cross else Direction.SHORT if bear_cross else None

    conditions.append(StrategyCondition(
        name="EMA 5/13 Cross",
        met=cross_ok,
        detail=f"EMA-5 {e5:.1f} {'>' if e5 > e13 else '<'} EMA-13 {e13:.1f}",
    ))

    # 2. RSI sweet spot (room to run)
    rsi_val = float(ind.rsi(close, 7).iloc[-1])  # fast RSI for scalping
    if direction == Direction.LONG:
        rsi_ok = 35 < rsi_val < 65
    elif direction == Direction.SHORT:
        rsi_ok = 35 < rsi_val < 65
    else:
        rsi_ok = False

    conditions.append(StrategyCondition(
        name="RSI Room",
        met=rsi_ok,
        detail=f"RSI-7 = {rsi_val:.0f} ({'sweet spot' if rsi_ok else 'extreme'})",
    ))

    # 3. Price confirms
    if direction == Direction.LONG:
        price_ok = price > c_open  # green candle
    elif direction == Direction.SHORT:
        price_ok = price < c_open  # red candle
    else:
        price_ok = False

    conditions.append(StrategyCondition(
        name="Price Confirms",
        met=price_ok,
        detail=f"{'Green' if price > c_open else 'Red'} candle",
    ))

    # 4. EMA spread widening (momentum)
    spread_now = abs(e5 - e13)
    spread_prev = abs(e5_p - e13_p)
    widening = spread_now > spread_prev

    conditions.append(StrategyCondition(
        name="EMA Widening",
        met=widening,
        detail=f"Spread {spread_prev:.1f} \u2192 {spread_now:.1f} ({'widening' if widening else 'narrowing'})",
    ))

    # Time filter
    last_time = df.index[-1]
    market_open = last_time.replace(hour=9, minute=15, second=0)
    mins_since = (last_time - market_open).total_seconds() / 60
    time_ok = 5 <= mins_since <= 345  # after first 5 min, before 3:00 PM

    conditions.append(StrategyCondition(
        name="Scalp Window",
        met=time_ok,
        detail=f"{mins_since:.0f} min since open",
    ))

    weighted = [c for c in conditions if c.weight > 0]
    total_w = sum(c.weight for c in weighted)
    met_w = sum(c.weight for c in weighted if c.met)
    confidence = (met_w / total_w * 100) if total_w > 0 else 0
    all_met = all(c.met for c in weighted)

    return StrategySignal(
        should_enter=all_met and direction is not None,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=f"EMA Scalp: {confidence:.0f}% ({direction.value if direction else 'none'})",
    )


# ───────────────────────────────────────────────────────────
# 2. VWAP SCALP — Quick bounces off VWAP
# ───────────────────────────────────────────────────────────

def evaluate_vwap_scalp(df: pd.DataFrame) -> StrategySignal:
    """VWAP Scalp — Quick bounce trades off VWAP.

    Price touches VWAP and bounces. Enter on the bounce candle.
    Hold for 5-10 points on Nifty (≈2.5-5 pts on options).

    Conditions:
    1. Price within 10 pts of VWAP (touching zone)
    2. Bounce candle forms (reversal from VWAP)
    3. Volume on bounce candle above average
    4. Previous candle was moving toward VWAP (approach confirmed)
    """
    conditions = []
    if len(df) < 20:
        return StrategySignal(should_enter=False, reason="Need 20+ candles")

    today = df.index[-1].date()
    today_df = df[df.index.date == today]
    if len(today_df) < 5:
        return StrategySignal(should_enter=False, reason="Need more today's data")

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(curr["close"])
    c_open = float(curr["open"])
    c_low = float(curr["low"])
    c_high = float(curr["high"])

    vwap_val = float(ind.vwap(
        today_df["high"], today_df["low"],
        today_df["close"], today_df["volume"]
    ).iloc[-1])

    # 1. Near VWAP
    dist = abs(price - vwap_val)
    near_vwap = dist <= 15  # within 15 pts
    # Check if low/high touched VWAP zone
    touched_from_below = c_low <= vwap_val + 5 and price > vwap_val
    touched_from_above = c_high >= vwap_val - 5 and price < vwap_val

    conditions.append(StrategyCondition(
        name="Near VWAP",
        met=near_vwap,
        detail=f"{dist:.0f} pts from VWAP ({vwap_val:.0f})",
    ))

    # 2. Bounce candle
    if touched_from_below:
        direction = Direction.LONG
        bounce_ok = price > c_open  # green bounce
    elif touched_from_above:
        direction = Direction.SHORT
        bounce_ok = price < c_open  # red bounce
    else:
        direction = Direction.LONG if price > vwap_val else Direction.SHORT
        bounce_ok = False

    conditions.append(StrategyCondition(
        name="Bounce Candle",
        met=bounce_ok,
        detail=f"{'Bullish bounce from VWAP' if touched_from_below else 'Bearish bounce from VWAP' if touched_from_above else 'No clear bounce'}",
    ))

    # 3. Volume check
    vol = df["volume"]
    if vol.sum() > 0:
        avg_vol = float(vol.rolling(10).mean().iloc[-1])
        curr_vol = float(vol.iloc[-1])
        vol_ok = curr_vol > avg_vol * 0.8
    else:
        vol_ok = True  # skip for index data

    conditions.append(StrategyCondition(
        name="Volume OK",
        met=vol_ok,
        detail="Volume confirms" if vol_ok else "Low volume",
    ))

    # 4. Approach confirmed (prev candle was moving toward VWAP)
    prev_close = float(prev["close"])
    if direction == Direction.LONG:
        approach_ok = prev_close < price  # was lower, now bouncing up
    else:
        approach_ok = prev_close > price  # was higher, now bouncing down

    conditions.append(StrategyCondition(
        name="Approach",
        met=approach_ok,
        detail="Price approached VWAP and bounced",
    ))

    # Time filter
    last_time = df.index[-1]
    market_open = last_time.replace(hour=9, minute=15, second=0)
    mins_since = (last_time - market_open).total_seconds() / 60
    time_ok = 5 <= mins_since <= 345

    conditions.append(StrategyCondition(
        name="Scalp Window",
        met=time_ok,
        detail=f"{mins_since:.0f} min since open",
    ))

    weighted = [c for c in conditions if c.weight > 0]
    total_w = sum(c.weight for c in weighted)
    met_w = sum(c.weight for c in weighted if c.met)
    confidence = (met_w / total_w * 100) if total_w > 0 else 0
    all_met = all(c.met for c in weighted)

    return StrategySignal(
        should_enter=all_met and direction is not None,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=f"VWAP Scalp: {confidence:.0f}%",
    )


# ───────────────────────────────────────────────────────────
# 3. MOMENTUM SCALP — RSI + Candle momentum
# ───────────────────────────────────────────────────────────

def evaluate_momentum_scalp(df: pd.DataFrame) -> StrategySignal:
    """Momentum Scalp — Ride short bursts of momentum.

    Enter when 3 consecutive candles move in same direction
    with increasing body size (momentum building).

    Conditions:
    1. Last 3 candles all same color (3 green or 3 red)
    2. Each candle body >= previous (momentum building)
    3. RSI trending in direction (not extreme)
    4. EMA-5 slope confirms direction
    """
    conditions = []
    if len(df) < 20:
        return StrategySignal(should_enter=False, reason="Need 20+ candles")

    close = df["close"]
    price = float(close.iloc[-1])

    # Last 3 candles
    candles = [df.iloc[-3], df.iloc[-2], df.iloc[-1]]
    bodies = [abs(float(c["close"]) - float(c["open"])) for c in candles]
    greens = [float(c["close"]) > float(c["open"]) for c in candles]
    reds = [float(c["close"]) < float(c["open"]) for c in candles]

    # 1. All same color
    all_green = all(greens)
    all_red = all(reds)
    same_color = all_green or all_red
    direction = Direction.LONG if all_green else Direction.SHORT if all_red else None

    conditions.append(StrategyCondition(
        name="3 Same Color",
        met=same_color,
        detail=f"{'3 GREEN' if all_green else '3 RED' if all_red else 'Mixed'} candles",
    ))

    # 2. Increasing body size
    increasing = bodies[1] >= bodies[0] * 0.8 and bodies[2] >= bodies[1] * 0.8

    conditions.append(StrategyCondition(
        name="Growing Bodies",
        met=increasing,
        detail=f"Bodies: {bodies[0]:.1f} \u2192 {bodies[1]:.1f} \u2192 {bodies[2]:.1f}",
    ))

    # 3. RSI trending
    rsi_val = float(ind.rsi(close, 7).iloc[-1])
    if direction == Direction.LONG:
        rsi_ok = 45 < rsi_val < 75
    elif direction == Direction.SHORT:
        rsi_ok = 25 < rsi_val < 55
    else:
        rsi_ok = False

    conditions.append(StrategyCondition(
        name="RSI Momentum",
        met=rsi_ok,
        detail=f"RSI-7 = {rsi_val:.0f}",
    ))

    # 4. EMA-5 slope
    ema5 = ind.ema(close, 5)
    slope = float(ema5.iloc[-1] - ema5.iloc[-3])
    if direction == Direction.LONG:
        slope_ok = slope > 0
    elif direction == Direction.SHORT:
        slope_ok = slope < 0
    else:
        slope_ok = False

    conditions.append(StrategyCondition(
        name="EMA-5 Slope",
        met=slope_ok,
        detail=f"Slope {slope:+.1f}",
    ))

    # Time filter
    last_time = df.index[-1]
    market_open = last_time.replace(hour=9, minute=15, second=0)
    mins_since = (last_time - market_open).total_seconds() / 60
    time_ok = 5 <= mins_since <= 345

    conditions.append(StrategyCondition(
        name="Scalp Window",
        met=time_ok,
        detail=f"{mins_since:.0f} min since open",
    ))

    weighted = [c for c in conditions if c.weight > 0]
    total_w = sum(c.weight for c in weighted)
    met_w = sum(c.weight for c in weighted if c.met)
    confidence = (met_w / total_w * 100) if total_w > 0 else 0
    all_met = all(c.met for c in weighted)

    return StrategySignal(
        should_enter=all_met and direction is not None,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=f"Momentum Scalp: {confidence:.0f}%",
    )


# ── Register All Scalping Strategies ─────────────────────────

register(StrategyInfo(
    id="ema_scalp",
    name="EMA Scalp (5/13)",
    emoji="⚡",
    description=(
        "Lightning-fast EMA 5/13 crossover scalps. Enter on cross, "
        "exit in 1-5 candles. Best on 1m/5m charts for options scalping."
    ),
    category="scalping",
    difficulty="advanced",
    market_condition="Any market with decent movement. Avoid lunch hour dead zone.",
    evaluate=evaluate_ema_scalp,
    entry_rules=[
        "EMA-5 crosses EMA-13 (within last 2 candles)",
        "RSI-7 is between 35-65 (room to run, not at extremes)",
        "Current candle closes in the cross direction",
        "EMA spread is widening (momentum building)",
        "After 9:20 AM and before 3:00 PM",
    ],
    exit_rules=[
        "Target: 10-15 pts on Nifty (≈5-8 pts on ATM option)",
        "SL: 8-10 pts on Nifty",
        "Exit if EMA-5 crosses back against you",
        "Max hold time: 5 candles",
    ],
    risk_tips=[
        "Keep position size fixed — NEVER double down on a losing scalp",
        "Stop trading after 3 consecutive losses (take a break)",
        "Avoid 12:30-1:30 PM dead zone (low volume, whipsaws)",
        "Best time: 9:20-11:00 AM and 2:00-3:15 PM",
    ],
    pros=[
        "Many opportunities per day (8-15 trades)",
        "Small capital at risk per trade",
        "Quick feedback loop",
    ],
    cons=[
        "Transaction costs eat into profits",
        "Mentally exhausting (requires constant attention)",
        "Slippage can be significant on fast moves",
    ],
    example_scenario=(
        "EMA-5 crosses above EMA-13 on 5m chart. RSI-7 is 52. Green candle confirms. "
        "Buy ATM CE at \u20b9200. Target: \u20b9208 (+8 pts). SL: \u20b9194 (-6 pts). "
        "1040 units \u00d7 8 pts = \u20b98,320 profit per trade."
    ),
))

register(StrategyInfo(
    id="vwap_scalp",
    name="VWAP Bounce Scalp",
    emoji="🎯",
    description=(
        "Scalps quick bounces when price touches VWAP. "
        "VWAP is the most respected level by institutions — "
        "price bounces off it like a trampoline."
    ),
    category="scalping",
    difficulty="intermediate",
    market_condition="Range-bound days when price keeps touching VWAP.",
    evaluate=evaluate_vwap_scalp,
    entry_rules=[
        "Price is within 15 pts of VWAP",
        "Bounce candle forms (bullish from below, bearish from above)",
        "Volume is not declining",
        "Previous candle confirms approach toward VWAP",
        "Within scalp window (9:20 AM - 3:00 PM)",
    ],
    exit_rules=[
        "Target: 8-12 pts (quick scalp)",
        "SL: 8-10 pts (tight)",
        "Exit if price crosses through VWAP against you",
    ],
    risk_tips=[
        "VWAP bounces work best in the first 2 hours",
        "If price cuts through VWAP cleanly, DON'T fight it",
        "Best combined with ORB levels for double confirmation",
    ],
    pros=[
        "VWAP is a self-fulfilling prophecy (institutions watch it)",
        "Tight stop-losses possible",
        "Multiple touches = multiple opportunities",
    ],
    cons=[
        "Fails on trending days (price leaves VWAP and doesn't look back)",
        "Needs quick execution (seconds matter)",
        "Slippage risk on fast bounces",
    ],
    example_scenario=(
        "VWAP is at 22,500. Price dips to 22,490 and forms a green hammer. "
        "Buy ATM CE at \u20b9180. Target: \u20b9188 (+8 pts). SL: \u20b9174. "
        "1040 units \u00d7 8 pts = \u20b98,320 per scalp. 6 scalps = \u20b949,920."
    ),
))

register(StrategyInfo(
    id="momentum_scalp",
    name="Momentum Burst Scalp",
    emoji="🚀",
    description=(
        "Rides short bursts of momentum when 3 consecutive candles "
        "push in one direction with growing bodies. Catches the "
        "explosive moves."
    ),
    category="scalping",
    difficulty="intermediate",
    market_condition="Volatile sessions with strong directional moves.",
    evaluate=evaluate_momentum_scalp,
    entry_rules=[
        "Last 3 candles are all the same color (3 green or 3 red)",
        "Each candle body is similar or larger than the previous",
        "RSI-7 is trending in the direction (not extreme)",
        "EMA-5 slope confirms the direction",
        "Within scalp window",
    ],
    exit_rules=[
        "Target: 15-20 pts Nifty (≈8-10 pts options)",
        "SL: 10 pts",
        "Exit on first opposite-color candle",
    ],
    risk_tips=[
        "Momentum can reverse suddenly — always use strict SL",
        "Best during first hour and last hour",
        "Don't chase — if you missed the 3rd candle, wait for next setup",
    ],
    pros=[
        "Catches explosive moves for quick profits",
        "High conviction when all conditions align",
        "Works on any timeframe",
    ],
    cons=[
        "By the time 3 candles confirm, move may be exhausted",
        "Can enter at the tail end of a burst",
        "Needs fast execution",
    ],
    example_scenario=(
        "3 consecutive green candles with bodies 5\u21927\u219210 pts. RSI-7 at 58. "
        "EMA-5 slope positive. Buy ATM CE at \u20b9220. Target: \u20b9230. SL: \u20b9214. "
        "1040 \u00d7 10 = \u20b910,400 per trade."
    ),
))

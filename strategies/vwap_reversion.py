"""VWAP Mean Reversion Strategy.

Price tends to revert to VWAP after deviating too far.
Enter when price snaps back toward VWAP in sideways/low-vol markets.
"""

import pandas as pd
import numpy as np
from strategy import StrategySignal, StrategyCondition, Direction
import indicators as ind
from strategies.registry import register, StrategyInfo


def evaluate_vwap_reversion(
    df: pd.DataFrame,
    deviation_pct: float = 0.15,
    rsi_oversold: float = 35.0,
    rsi_overbought: float = 65.0,
) -> StrategySignal:
    """VWAP Mean Reversion.

    Entry conditions:
    1. Price deviated > 0.15% from VWAP
    2. RSI confirms: oversold (<35) for long, overbought (>65) for short
    3. Current candle starts reversing toward VWAP
    4. ADX < 25 (not a strong trend — reversion works in ranges)
    5. Not within first 15 minutes
    """
    conditions = []

    if len(df) < 30:
        return StrategySignal(should_enter=False, reason="Insufficient data")

    today = df.index[-1].date()
    today_df = df[df.index.date == today]
    if len(today_df) < 5:
        return StrategySignal(should_enter=False, reason="Need more today's data")

    curr = df.iloc[-1]
    price = float(curr["close"])
    c_open = float(curr["open"])

    # VWAP
    vwap_val = float(ind.vwap(
        today_df["high"], today_df["low"],
        today_df["close"], today_df["volume"]
    ).iloc[-1])

    # ── 1. Deviation from VWAP ─────────────────────
    dev_pct = (price - vwap_val) / vwap_val * 100
    dev_ok = abs(dev_pct) >= deviation_pct
    is_above_vwap = price > vwap_val
    is_below_vwap = price < vwap_val

    conditions.append(StrategyCondition(
        name="VWAP Deviation",
        met=dev_ok,
        detail=f"Price {dev_pct:+.2f}% from VWAP ({vwap_val:.0f})",
    ))

    # ── 2. RSI confirmation ───────────────────────
    rsi_val = float(ind.rsi(df["close"], 14).iloc[-1])

    if is_below_vwap:
        rsi_ok = rsi_val <= rsi_oversold
        direction = Direction.LONG
        rsi_detail = f"RSI {rsi_val:.0f} ≤ {rsi_oversold} (oversold → long)"
    elif is_above_vwap:
        rsi_ok = rsi_val >= rsi_overbought
        direction = Direction.SHORT
        rsi_detail = f"RSI {rsi_val:.0f} ≥ {rsi_overbought} (overbought → short)"
    else:
        rsi_ok = False
        direction = None
        rsi_detail = f"RSI {rsi_val:.0f} neutral"

    conditions.append(StrategyCondition(
        name="RSI Extreme",
        met=rsi_ok,
        detail=rsi_detail,
    ))

    # ── 3. Reversal candle (moving back toward VWAP) ────
    if direction == Direction.LONG:
        reversal_ok = price > c_open  # green candle
    elif direction == Direction.SHORT:
        reversal_ok = price < c_open  # red candle
    else:
        reversal_ok = False

    conditions.append(StrategyCondition(
        name="Reversal Candle",
        met=reversal_ok,
        detail=f"Candle {'reversing toward' if reversal_ok else 'continuing away from'} VWAP",
    ))

    # ── 4. Low trend (ADX < 25) ────────────────────
    adx_data = ind.adx(df["high"], df["low"], df["close"])
    adx_val = float(adx_data["adx"].iloc[-1])
    adx_ok = adx_val < 25

    conditions.append(StrategyCondition(
        name="No Strong Trend",
        met=adx_ok,
        detail=f"ADX {adx_val:.0f} ({'OK, ranging' if adx_ok else 'trending — avoid reversion'})",
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
        should_enter=all_met and direction is not None,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=f"VWAP Reversion: {'ALL' if all_met else 'NOT all'} conditions ({confidence:.0f}%)",
    )


register(StrategyInfo(
    id="vwap_reversion",
    name="VWAP Mean Reversion",
    emoji="🎯",
    description=(
        "Price snaps back to VWAP after over-extending. Works best in "
        "range-bound markets where VWAP acts as a magnet."
    ),
    category="reversal",
    difficulty="intermediate",
    market_condition="Sideways/range-bound markets with ADX < 25.",
    evaluate=evaluate_vwap_reversion,
    entry_rules=[
        "Price has deviated > 0.15% from VWAP",
        "RSI is oversold (<35) for long or overbought (>65) for short",
        "Current candle reverses toward VWAP direction",
        "ADX < 25 (no strong trend — reversion environment)",
        "At least 15 minutes after market open",
    ],
    exit_rules=[
        "Target: VWAP itself (price reverts to mean)",
        "Stop-loss: Beyond the recent swing extreme",
        "Exit if ADX rises above 30 (trend forming)",
    ],
    risk_tips=[
        "NEVER use this in trending markets — it will keep losing",
        "Best during 11AM-2PM lull when market is range-bound",
        "VWAP is strongest on high-volume days",
    ],
    pros=[
        "High win rate in range-bound markets",
        "VWAP is a well-respected institutional level",
        "Small stop-losses possible",
    ],
    cons=[
        "Gets destroyed in trending markets",
        "Target is limited (only to VWAP, not beyond)",
        "Doesn't work on low-volume/index data without real volume",
    ],
    example_scenario=(
        "VWAP is at 22,500. Price drops to 22,460 (−0.18% deviation). "
        "RSI hits 32 (oversold). A green hammer forms. ADX is 18. "
        "\u2192 BUY at 22,465, Target VWAP (22,500), SL at 22,440."
    ),
))

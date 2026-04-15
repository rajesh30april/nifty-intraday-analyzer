"""RSI Divergence / Reversal Strategy.

Enters when RSI reaches extreme levels and price shows reversal signs.
Classic mean-reversion approach using RSI as the primary filter.
"""

import pandas as pd
from strategy import StrategySignal, StrategyCondition, Direction
import indicators as ind
from strategies.registry import register, StrategyInfo


def evaluate_rsi_reversal(
    df: pd.DataFrame,
    oversold: float = 30.0,
    overbought: float = 70.0,
) -> StrategySignal:
    """RSI Reversal with Price Action Confirmation.

    Entry conditions:
    1. RSI was at extreme (below 30 or above 70) within last 3 candles
    2. RSI is now crossing back (exiting extreme zone)
    3. Reversal candle pattern (engulfing or hammer/shooting star)
    4. Price near a support/resistance level (EMA-50)
    5. Not within first 15 minutes
    """
    conditions = []

    if len(df) < 50:
        return StrategySignal(should_enter=False, reason="Need 50+ candles")

    close = df["close"]
    price = float(close.iloc[-1])
    curr = df.iloc[-1]
    c_open = float(curr["open"])
    c_high = float(curr["high"])
    c_low = float(curr["low"])

    rsi_series = ind.rsi(close, 14)
    rsi_now = float(rsi_series.iloc[-1])

    # ── 1. RSI was at extreme recently ─────────────────
    was_oversold = any(float(rsi_series.iloc[-i]) <= oversold for i in range(1, 4))
    was_overbought = any(float(rsi_series.iloc[-i]) >= overbought for i in range(1, 4))
    extreme_ok = was_oversold or was_overbought

    conditions.append(StrategyCondition(
        name="RSI Extreme",
        met=extreme_ok,
        detail=(
            f"RSI was {'oversold (<' + str(oversold) + ')' if was_oversold else 'overbought (>' + str(overbought) + ')' if was_overbought else 'normal'} recently"
        ),
    ))

    # ── 2. RSI crossing back out of extreme ────────────
    if was_oversold:
        crossing_back = rsi_now > oversold
        direction = Direction.LONG
        cross_detail = f"RSI {rsi_now:.0f} crossing back above {oversold}"
    elif was_overbought:
        crossing_back = rsi_now < overbought
        direction = Direction.SHORT
        cross_detail = f"RSI {rsi_now:.0f} crossing back below {overbought}"
    else:
        crossing_back = False
        direction = None
        cross_detail = f"RSI {rsi_now:.0f} — no extreme to cross back from"

    conditions.append(StrategyCondition(
        name="RSI Crossback",
        met=crossing_back,
        detail=cross_detail,
    ))

    # ── 3. Reversal candle ─────────────────────────
    body = abs(price - c_open)
    total_range = c_high - c_low
    lower_wick = min(c_open, price) - c_low
    upper_wick = c_high - max(c_open, price)

    if direction == Direction.LONG:
        # Bullish reversal: hammer (long lower wick) or bullish engulfing
        prev_body = abs(float(df.iloc[-2]["close"]) - float(df.iloc[-2]["open"]))
        is_hammer = total_range > 0 and (lower_wick / total_range) > 0.4
        is_engulfing = price > c_open and body > prev_body
        reversal_ok = is_hammer or is_engulfing
        rev_detail = f"{'Hammer' if is_hammer else 'Engulfing' if is_engulfing else 'No'} bullish pattern"
    elif direction == Direction.SHORT:
        prev_body = abs(float(df.iloc[-2]["close"]) - float(df.iloc[-2]["open"]))
        is_shooting = total_range > 0 and (upper_wick / total_range) > 0.4
        is_engulfing = price < c_open and body > prev_body
        reversal_ok = is_shooting or is_engulfing
        rev_detail = f"{'Shooting star' if is_shooting else 'Engulfing' if is_engulfing else 'No'} bearish pattern"
    else:
        reversal_ok = False
        rev_detail = "No direction"

    conditions.append(StrategyCondition(
        name="Reversal Candle",
        met=reversal_ok,
        detail=rev_detail,
    ))

    # ── 4. Near EMA-50 (support/resistance zone) ───────
    ema_50 = float(ind.ema(close, 50).iloc[-1])
    dist_pct = abs(price - ema_50) / ema_50 * 100
    near_ema = dist_pct <= 0.6   # loosened from 0.3% → 0.6% (~140pts at Nifty 23k)

    conditions.append(StrategyCondition(
        name="Near EMA-50",
        met=near_ema,
        detail=f"Price {dist_pct:.2f}% from EMA-50 ({ema_50:.0f}) — need ≤0.6%",
    ))

    # ── 5. Time filter — use wall clock, NOT candle timestamp ──────────
    from datetime import datetime as _dt
    _now = _dt.now()
    _market_open_dt = _now.replace(hour=9, minute=15, second=0, microsecond=0)
    mins_since = max(0, (_now - _market_open_dt).total_seconds() / 60)
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
        reason=f"RSI Reversal: {'ALL' if all_met else 'NOT all'} conditions ({confidence:.0f}%)",
    )


register(StrategyInfo(
    id="rsi_reversal",
    name="RSI Reversal",
    emoji="🔃",
    description=(
        "Catches reversals when RSI hits extreme levels and starts crossing "
        "back. Combined with reversal candle patterns for confirmation."
    ),
    category="reversal",
    difficulty="intermediate",
    market_condition="Range-bound markets with clear overbought/oversold zones.",
    evaluate=evaluate_rsi_reversal,
    entry_rules=[
        "RSI was below 30 (oversold) or above 70 (overbought) in last 3 candles",
        "RSI is now crossing back out of the extreme zone",
        "Reversal candle pattern (hammer, engulfing, shooting star)",
        "Price is near EMA-50 (within 0.3%)",
        "At least 15 minutes after market open",
    ],
    exit_rules=[
        "Target: RSI reaches 50 (midline)",
        "Stop-loss: Below/above the extreme candle",
        "Exit if RSI goes back into the extreme zone",
    ],
    risk_tips=[
        "RSI can stay overbought/oversold for extended periods in trends",
        "Always wait for the CROSSBACK — never enter while still in extreme",
        "Best combined with support/resistance levels",
    ],
    pros=[
        "Catches turning points with good R:R",
        "RSI is a leading indicator (warns before price)",
        "Works well with candlestick patterns",
    ],
    cons=[
        "Can be early — trend may continue after RSI extreme",
        "Requires patience and discipline",
        "False signals in strong trending markets",
    ],
    example_scenario=(
        "Nifty drops sharply, RSI hits 28 (oversold). Next candle, RSI recovers to 33 "
        "and a bullish hammer forms near EMA-50. "
        "\u2192 BUY with SL below the hammer low, Target at RSI 50 level."
    ),
))

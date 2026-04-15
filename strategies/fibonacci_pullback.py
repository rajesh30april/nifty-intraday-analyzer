"""Fibonacci Pullback Entry Strategy.

In a trending market, price tends to retrace to Fibonacci levels
(38.2%, 50%, 61.8%) before continuing the trend.

Wait for the pullback to a Fib zone, then enter with trend direction.
Uses the day's swing high/low as the reference for Fibonacci calculation.
"""

from __future__ import annotations

import pandas as pd
from strategy import StrategySignal, StrategyCondition, Direction
import indicators as ind
from strategies.registry import register, StrategyInfo

# Entry zone: 38.2% to 61.8% pullback — the "golden zone"
_FIB_ENTRY_DEEP  = 0.618   # deeper than this = trend may be reversing
_FIB_ENTRY_LIGHT = 0.382   # shallower than this = too shallow, not a real pullback
_MIN_SWING_PTS   = 30      # day's swing must be at least 30pts to be meaningful
_MIN_ADX         = 20      # need some trend to trade pullbacks


def evaluate_fibonacci_pullback(df: pd.DataFrame) -> StrategySignal:
    """Fibonacci Pullback — enter at 38.2–61.8% retracement in trend direction."""
    NO = lambda r, c=[]: StrategySignal(should_enter=False, reason=r, conditions=c)

    if len(df) < 30:
        return NO("Need 30+ candles")

    today    = df.index[-1].date()
    today_df = df[df.index.date == today]

    if len(today_df) < 6:
        return NO("Need 6+ today candles (30 min) for swing to form")

    price    = float(df["close"].iloc[-1])
    day_high = float(today_df["high"].max())
    day_low  = float(today_df["low"].min())
    day_open = float(today_df["open"].iloc[0])
    swing    = day_high - day_low

    conditions: list[StrategyCondition] = []

    # ── Condition 1: Meaningful swing exists ───────────────────────────
    swing_ok = swing >= _MIN_SWING_PTS
    conditions.append(StrategyCondition(
        name="Meaningful Swing",
        met=swing_ok,
        detail=(
            f"Day swing: H={day_high:.0f} L={day_low:.0f} = {swing:.0f}pts "
            f"{'✅' if swing_ok else f'❌ need ≥{_MIN_SWING_PTS}pts'}"
        ),
        weight=2,
    ))
    if not swing_ok:
        return NO(f"Swing too small ({swing:.0f}pts, need {_MIN_SWING_PTS}pts)", conditions)

    # ── Condition 2: Trend direction via ADX + day bias ─────────────────
    adx_data = ind.adx(df["high"], df["low"], df["close"])
    adx_val  = float(adx_data["adx"].iloc[-1])
    trend_ok = adx_val >= _MIN_ADX

    # Determine trend direction: price vs day open
    price_vs_open = (price - day_open) / day_open * 100
    if price > day_open * 1.001:     # at least 0.1% above open = uptrend
        trend_dir = "up"
        direction = Direction.LONG
    elif price < day_open * 0.999:   # at least 0.1% below open = downtrend
        trend_dir = "down"
        direction = Direction.SHORT
    else:
        return NO("No clear trend — price near day open (choppy)", conditions)

    conditions.append(StrategyCondition(
        name="Trend Established",
        met=trend_ok,
        detail=(
            f"ADX={adx_val:.0f} ({'✅ trending' if trend_ok else f'❌ need ≥{_MIN_ADX}'}) | "
            f"bias={trend_dir.upper()} ({price_vs_open:+.2f}% vs open)"
        ),
        weight=3,
    ))

    # ── Condition 3: Price in Fibonacci 38.2–61.8% pullback zone ────────
    if trend_dir == "up":
        # Uptrend: swing from day_low to day_high, retracing DOWN
        fibs = ind.fibonacci_retracement(day_high, day_low, direction="up")
        fib_382 = fibs["38.2"]   # shallow end of golden zone
        fib_618 = fibs["61.8"]   # deep end of golden zone
        in_zone = fib_618 <= price <= fib_382
        zone_detail = (
            f"Fib zone: {fib_618:.0f}–{fib_382:.0f} (38.2–61.8% pullback from {day_high:.0f}) "
            f"| Price={price:.0f} — {'✅ IN ZONE' if in_zone else '❌ outside zone'}"
        )
    else:
        # Downtrend: swing from day_high to day_low, retracing UP
        fibs = ind.fibonacci_retracement(day_high, day_low, direction="down")
        fib_382 = fibs["38.2"]   # shallow bounce
        fib_618 = fibs["61.8"]   # deep bounce
        in_zone = fib_382 <= price <= fib_618
        zone_detail = (
            f"Fib zone: {fib_382:.0f}–{fib_618:.0f} (38.2–61.8% bounce from {day_low:.0f}) "
            f"| Price={price:.0f} — {'✅ IN ZONE' if in_zone else '❌ outside zone'}"
        )

    conditions.append(StrategyCondition(
        name="In Fib Golden Zone",
        met=in_zone,
        detail=zone_detail,
        weight=3,
    ))

    # ── Condition 4: Reversal candle at the zone ─────────────────────────
    curr      = df.iloc[-1]
    c_open    = float(curr["open"])
    c_cls     = float(curr["close"])
    c_high    = float(curr["high"])
    c_low     = float(curr["low"])
    body      = abs(c_cls - c_open)
    rng       = c_high - c_low
    lower_wick = min(c_open, c_cls) - c_low
    upper_wick = c_high - max(c_open, c_cls)

    if direction == Direction.LONG:
        # Bullish: green candle or hammer
        candle_ok = (c_cls > c_open) or (rng > 0 and lower_wick / rng > 0.40)
        candle_detail = f"{'✅ bullish/hammer at support' if candle_ok else '❌ no reversal candle yet'}"
    else:
        # Bearish: red candle or shooting star
        candle_ok = (c_cls < c_open) or (rng > 0 and upper_wick / rng > 0.40)
        candle_detail = f"{'✅ bearish/shooting-star at resistance' if candle_ok else '❌ no reversal candle yet'}"

    conditions.append(StrategyCondition(
        name="Reversal Candle at Zone",
        met=candle_ok,
        detail=candle_detail,
        weight=2,
    ))

    # ── Condition 5: Not too late ─────────────────────────────────────────
    from datetime import time as dt_time
    curr_time = df.index[-1].time()
    time_ok   = curr_time <= dt_time(14, 0)
    conditions.append(StrategyCondition(
        name="Time Filter",
        met=time_ok,
        detail=f"{curr_time.strftime('%H:%M')} — {'✅' if time_ok else '❌ too late'}",
        weight=1,
    ))

    # ── Score ─────────────────────────────────────────────────────────────
    total_w = sum(c.weight for c in conditions)
    met_w   = sum(c.weight for c in conditions if c.met)
    conf    = round(met_w / total_w * 100, 1) if total_w > 0 else 0
    all_met = all(c.met for c in conditions)

    return StrategySignal(
        should_enter=all_met,
        direction=direction,
        confidence=conf,
        conditions=conditions,
        reason=(
            f"FIB PULLBACK {'ENTRY' if all_met else 'NO ENTRY'}: {direction.value.upper()} | "
            f"conf={conf:.0f}% | trend={trend_dir.upper()} ADX={adx_val:.0f} | "
            f"price={price:.0f} | in_zone={in_zone}"
        ),
    )


register(StrategyInfo(
    id="fib_pullback",
    name="Fibonacci Pullback",
    emoji="📐",
    description=(
        "In a trending market, waits for price to retrace to the 38.2–61.8% "
        "Fibonacci 'golden zone' from the day's swing, then enters in trend direction. "
        "Uses day's high/low as the swing reference."
    ),
    category="trend",
    difficulty="intermediate",
    market_condition="Trending days (ADX > 20). Skip on flat/choppy days.",
    evaluate=evaluate_fibonacci_pullback,
    entry_rules=[
        "Day swing must be at least 30pts (meaningful move to retrace from)",
        "Clear trend direction: price > 0.1% above/below day open",
        "ADX ≥ 20 (trending, not choppy)",
        "Price in 38.2–61.8% Fibonacci pullback zone from day swing",
        "Reversal candle (bullish for long, bearish for short) at the zone",
        "Enter before 14:00",
    ],
    exit_rules=[
        "Target: 100% extension (previous swing high/low)",
        "Stop-loss: below/above the 61.8% level (golden zone failure = exit)",
        "Trail stop on each new swing high/low",
    ],
    risk_tips=[
        "ADX < 20 = not trending enough, skip it — fib zones fail in chop",
        "Deeper than 61.8% = trend may be reversing, don't force entry",
        "Strong news events override Fibonacci levels — check calendar",
    ],
    pros=[
        "Mathematical precision — same levels that institutions watch",
        "Excellent R:R — tight stop at zone, big target at swing extension",
        "Works in both uptrends (long) and downtrends (short)",
    ],
    cons=[
        "Needs a real swing to form — won't fire in first 30 min",
        "Choppy markets give false zones — ADX gate prevents this",
    ],
    example_scenario=(
        "Day opens at 23,400. Nifty rallies to 23,550 (day high). Pulls back. "
        "Fib 38.2% = 23,493, 61.8% = 23,457. Price hits 23,470 (in zone). "
        "Bullish hammer forms. ADX = 26. → BUY at 23,475. SL=23,450. Target=23,550."
    ),
))

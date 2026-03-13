"""MACD Momentum Strategy.

Uses MACD crossover with histogram momentum for trend entries.
The histogram slope adds momentum confirmation.
"""

import pandas as pd
from strategy import StrategySignal, StrategyCondition, Direction
import indicators as ind
from strategies.registry import register, StrategyInfo


def evaluate_macd_momentum(
    df: pd.DataFrame,
) -> StrategySignal:
    """MACD Momentum with Histogram Acceleration.

    Entry conditions:
    1. MACD line just crossed above signal (long) or below (short)
    2. MACD histogram is growing (accelerating momentum)
    3. Both MACD and signal are on the correct side of zero line
    4. Price above EMA-20 for long, below for short
    5. Not within first 15 minutes
    """
    conditions = []

    if len(df) < 35:
        return StrategySignal(should_enter=False, reason="Insufficient data")

    close = df["close"]
    price = float(close.iloc[-1])
    macd_data = ind.macd(close)
    macd_line = macd_data["macd"]
    signal_line = macd_data["signal"]
    histogram = macd_data["histogram"]

    m_now = float(macd_line.iloc[-1])
    s_now = float(signal_line.iloc[-1])
    m_prev = float(macd_line.iloc[-2])
    s_prev = float(signal_line.iloc[-2])
    h_now = float(histogram.iloc[-1])
    h_prev = float(histogram.iloc[-2])
    h_prev2 = float(histogram.iloc[-3])

    # ── 1. MACD crossover (within last 3 candles) ───────
    bullish_cross = False
    bearish_cross = False
    for i in range(1, min(4, len(df))):
        ml = float(macd_line.iloc[-i])
        sl = float(signal_line.iloc[-i])
        ml_p = float(macd_line.iloc[-i - 1])
        sl_p = float(signal_line.iloc[-i - 1])
        if ml > sl and ml_p <= sl_p:
            bullish_cross = True
            break
        if ml < sl and ml_p >= sl_p:
            bearish_cross = True
            break

    cross_ok = bullish_cross or bearish_cross
    direction = Direction.LONG if bullish_cross else Direction.SHORT if bearish_cross else None

    conditions.append(StrategyCondition(
        name="MACD Crossover",
        met=cross_ok,
        detail=f"MACD {'bullish' if bullish_cross else 'bearish' if bearish_cross else 'no'} crossover (MACD={m_now:.1f}, Signal={s_now:.1f})",
    ))

    # ── 2. Histogram accelerating ───────────────────
    if direction == Direction.LONG:
        accel_ok = h_now > h_prev and h_prev > h_prev2
    elif direction == Direction.SHORT:
        accel_ok = h_now < h_prev and h_prev < h_prev2
    else:
        accel_ok = False

    conditions.append(StrategyCondition(
        name="Momentum Accel",
        met=accel_ok,
        detail=f"Histogram: {h_prev2:.1f} \u2192 {h_prev:.1f} \u2192 {h_now:.1f} ({'accelerating' if accel_ok else 'decelerating'})",
    ))

    # ── 3. MACD above/below zero line ───────────────
    if direction == Direction.LONG:
        zero_ok = m_now > 0 or (m_now > -2 and h_now > 0)  # near zero crossing up
    elif direction == Direction.SHORT:
        zero_ok = m_now < 0 or (m_now < 2 and h_now < 0)
    else:
        zero_ok = False

    conditions.append(StrategyCondition(
        name="Zero Line",
        met=zero_ok,
        detail=f"MACD {m_now:.1f} {'above' if m_now > 0 else 'below'} zero",
    ))

    # ── 4. Price vs EMA-20 ─────────────────────────
    ema_20 = float(ind.ema(close, 20).iloc[-1])
    if direction == Direction.LONG:
        ema_ok = price > ema_20
    elif direction == Direction.SHORT:
        ema_ok = price < ema_20
    else:
        ema_ok = False

    conditions.append(StrategyCondition(
        name="EMA-20 Align",
        met=ema_ok,
        detail=f"Price {'above' if price > ema_20 else 'below'} EMA-20 ({ema_20:.0f})",
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
        reason=f"MACD: {'ALL' if all_met else 'NOT all'} conditions ({confidence:.0f}%)",
    )


register(StrategyInfo(
    id="macd_momentum",
    name="MACD Momentum",
    emoji="📈",
    description=(
        "Uses MACD line crossing signal line with histogram acceleration. "
        "The histogram shows momentum — growing bars mean strengthening move."
    ),
    category="momentum",
    difficulty="intermediate",
    market_condition="Trending or early-trend markets.",
    evaluate=evaluate_macd_momentum,
    entry_rules=[
        "MACD line crosses above signal line (bullish) or below (bearish)",
        "MACD histogram is growing in the trade direction (acceleration)",
        "MACD is above zero (long) or below zero (short)",
        "Price above EMA-20 (long) or below (short)",
        "At least 15 minutes after market open",
    ],
    exit_rules=[
        "Exit when histogram starts shrinking (deceleration)",
        "Stop-loss: Below recent swing low/high",
        "Target: 2x stop-loss",
    ],
    risk_tips=[
        "MACD is a lagging indicator — best for confirmation, not prediction",
        "Histogram acceleration is the key — don't trade decelerating signals",
        "Best on 5m or 15m timeframes for intraday",
    ],
    pros=[
        "Very reliable when histogram accelerates",
        "Catches strong momentum moves",
        "Works on any timeframe",
    ],
    cons=[
        "Late entries by nature (lagging)",
        "Many false signals in choppy markets",
        "Histogram can reverse quickly",
    ],
    example_scenario=(
        "MACD line crosses above signal line. Histogram bars: 0.2 \u2192 0.5 \u2192 0.9 (growing). "
        "MACD is above zero. Price is above EMA-20. "
        "\u2192 BUY with SL below recent swing low, Target 2x."
    ),
))

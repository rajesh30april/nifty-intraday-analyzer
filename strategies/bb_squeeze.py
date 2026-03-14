"""Bollinger Band Squeeze Strategy.

When Bollinger Bands narrow INSIDE the Keltner Channel,
volatility is compressed (like a coiled spring). When the
bands EXPAND outside KC = energy releasing = trade the direction.

Classic TTM Squeeze by John Carter, adapted for Nifty 5m.
"""

import pandas as pd
import indicators as ind
from strategy import StrategySignal, StrategyCondition, Direction
from strategies.registry import register, StrategyInfo


def evaluate_bb_squeeze(df: pd.DataFrame) -> StrategySignal:
    """Bollinger Band Squeeze.

    Entry conditions:
    1. A squeeze was ON (BB inside KC) in recent candles
    2. Squeeze just turned OFF (BB expanded outside KC = release)
    3. Momentum direction confirms the breakout
       (momentum histogram positive = LONG, negative = SHORT)
    4. Price breaks beyond the squeeze range in that direction
    """
    conditions: list[StrategyCondition] = []

    if len(df) < 25:
        return StrategySignal(should_enter=False, reason="Insufficient data for squeeze")

    squeeze_data = ind.bb_squeeze(
        df["high"], df["low"], df["close"],
        bb_period=20, bb_std=2.0,
        kc_period=20, kc_mult=1.5,
    )

    if squeeze_data.empty or len(squeeze_data.dropna()) < 5:
        return StrategySignal(should_enter=False, reason="Squeeze data not ready")

    curr_squeeze = bool(squeeze_data["squeeze_on"].iloc[-1])
    prev_squeeze = bool(squeeze_data["squeeze_on"].iloc[-2])
    momentum_now = float(squeeze_data["momentum"].iloc[-1])
    momentum_prev = float(squeeze_data["momentum"].iloc[-2])

    # ── Condition 1: Squeeze WAS on (compression existed) ────────
    # Look back up to 5 candles for a recent squeeze
    recent_squeeze = any(
        bool(squeeze_data["squeeze_on"].iloc[i])
        for i in range(-6, -1)
        if i >= -len(squeeze_data)
    )
    conditions.append(StrategyCondition(
        name="Squeeze Was On",
        met=recent_squeeze,
        detail=(
            f"BB squeeze was {'✅ ON recently (compression detected)' if recent_squeeze else '❌ NOT detected — no coiling'}"
        ),
    ))

    # ── Condition 2: Squeeze just fired (turned OFF) ──────────────
    squeeze_fired = recent_squeeze and not curr_squeeze
    conditions.append(StrategyCondition(
        name="Squeeze Fired",
        met=squeeze_fired,
        detail=(
            f"Squeeze {'✅ FIRED (BB expanded outside KC)' if squeeze_fired else '❌ still ON or never fired'}"
        ),
        weight=2,
    ))

    # ── Condition 3: Momentum direction ──────────────────────────
    momentum_up   = momentum_now > 0
    momentum_accel = momentum_now > momentum_prev  # getting stronger
    momentum_ok   = not pd.isna(momentum_now)

    if momentum_ok:
        direction = Direction.LONG if momentum_up else Direction.SHORT
        mom_detail = (
            f"Momentum {momentum_now:+.2f} "
            f"({'increasing ✅' if momentum_accel else 'decreasing ⚠️'}) "
            f"→ {direction.value.upper()}"
        )
    else:
        direction = Direction.LONG
        mom_detail = "Momentum N/A"
        momentum_ok = False

    conditions.append(StrategyCondition(
        name="Momentum Direction",
        met=momentum_ok,
        detail=mom_detail,
        weight=2,
    ))

    # ── Condition 4: Price confirms — above/below squeeze range ──
    bb_upper = float(squeeze_data["bb_upper"].iloc[-1])
    bb_lower = float(squeeze_data["bb_lower"].iloc[-1])
    price    = float(df["close"].iloc[-1])

    if momentum_up:
        price_confirms = price > bb_upper
        price_detail   = (
            f"Price {price:.0f} {'> BB upper ' + f'{bb_upper:.0f}' + ' ✅' if price_confirms else '< BB upper ' + f'{bb_upper:.0f}' + ' ❌'}"
        )
    else:
        price_confirms = price < bb_lower
        price_detail   = (
            f"Price {price:.0f} {'< BB lower ' + f'{bb_lower:.0f}' + ' ✅' if price_confirms else '> BB lower ' + f'{bb_lower:.0f}' + ' ❌'}"
        )

    conditions.append(StrategyCondition(
        name="Price Breakout",
        met=price_confirms,
        detail=price_detail,
    ))

    all_met    = all(c.met for c in conditions)
    total_w    = sum(c.weight for c in conditions)
    met_w      = sum(c.weight for c in conditions if c.met)
    confidence = (met_w / total_w * 100) if total_w > 0 else 0

    return StrategySignal(
        should_enter=all_met,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=(
            f"BB SQUEEZE: {direction.value.upper()} | "
            f"momentum={momentum_now:+.2f} | "
            f"{'ALL met' if all_met else 'NOT all met'}"
        ),
    )


register(StrategyInfo(
    id="bb_squeeze",
    name="Bollinger Band Squeeze",
    emoji="🌀",
    description=(
        "Detects when volatility is compressed (BB inside Keltner Channel), "
        "then trades the explosive breakout when the squeeze releases. "
        "Momentum histogram confirms direction. High-probability breakout strategy."
    ),
    category="breakout",
    difficulty="intermediate",
    market_condition="Best after periods of low volatility. Avoid during already trending markets.",
    evaluate=evaluate_bb_squeeze,
    entry_rules=[
        "Bollinger Bands must be INSIDE Keltner Channel (squeeze on)",
        "Squeeze fires when BB expands outside KC",
        "Momentum histogram positive → LONG, negative → SHORT",
        "Price must break above BB upper (long) or below BB lower (short)",
    ],
    exit_rules=[
        "Stop: opposite BB band at entry",
        "Target: 1.5-2× risk or until momentum histogram reverses",
        "Exit if momentum turns negative during LONG trade",
    ],
    risk_tips=[
        "False squeezes happen — wait for at least 3 candles of squeeze before entry",
        "Best results on 5m and 15m timeframes for Nifty",
        "Avoid on major news days — volatility is already expanded",
    ],
    pros=[
        "Catches explosive moves early",
        "Objective entry/exit levels",
        "Works across all market conditions (finds calm periods)",
    ],
    cons=[
        "Can give false signals if squeeze is too short",
        "Needs at least 25 candles of history",
        "Momentum indicator lags slightly",
    ],
    example_scenario=(
        "Nifty ranging in 50pt band for 45 min (BB inside KC = squeeze). "
        "At 10:45, BB expands outside KC. Momentum = +0.8 (bullish). "
        "Price breaks above BB upper (23,350). "
        "→ LONG at 23,350. SL = BB lower (23,250). Target = 23,450."
    ),
))
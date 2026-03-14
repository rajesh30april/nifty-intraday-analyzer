"""Candlestick Pattern Strategy.

Detects high-conviction multi-candle patterns with trend + volume confirmation.
Patterns: Bullish/Bearish Engulfing, Morning/Evening Star, Hammer/Shooting Star,
          Three White Soldiers/Black Crows, Bullish/Bearish Harami.
"""

import pandas as pd
import numpy as np
from strategy import StrategySignal, StrategyCondition, Direction
import indicators as ind
from strategies.registry import register, StrategyInfo

# ── Tuning constants ─────────────────────────────────────────────
MIN_BODY_PCT       = 0.30   # body must be >= 30% of range for engulfing
HAMMER_WICK_PCT    = 0.55   # lower wick >= 55% of total range
STAR_BODY_PCT      = 0.15   # middle star candle body <= 15% of range (small)
VOLUME_RATIO_MIN   = 1.1    # signal candle volume > 1.1x 10-period avg
EMA_FAST, EMA_SLOW = 9, 21  # trend filter


# ── Individual pattern detectors ─────────────────────────────────

def _body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))

def _range(row: pd.Series) -> float:
    return float(row["high"]) - float(row["low"]) + 1e-9

def _is_bullish(row: pd.Series) -> bool:
    return float(row["close"]) > float(row["open"])

def _is_bearish(row: pd.Series) -> bool:
    return float(row["close"]) < float(row["open"])


def detect_engulfing(c0: pd.Series, c1: pd.Series):
    """c0 = previous candle, c1 = current (signal) candle.
    Returns ('bullish'|'bearish'|None, detail_str)"""
    b0, b1 = _body(c0), _body(c1)
    if b0 == 0 or b1 == 0:
        return None, "Body is zero"
    if _is_bearish(c0) and _is_bullish(c1):
        if (float(c1["close"]) > float(c0["open"])
                and float(c1["open"]) < float(c0["close"])
                and b1 > b0):
            return "bullish", f"Engulfs prev bearish (body {b1:.1f} > {b0:.1f})"
    if _is_bullish(c0) and _is_bearish(c1):
        if (float(c1["close"]) < float(c0["open"])
                and float(c1["open"]) > float(c0["close"])
                and b1 > b0):
            return "bearish", f"Engulfs prev bullish (body {b1:.1f} > {b0:.1f})"
    return None, "No engulfing"


def detect_hammer_star(c: pd.Series):
    """Single-candle: Hammer (bullish) or Shooting Star (bearish).
    Returns ('hammer'|'shooting_star'|None, detail_str)"""
    rng        = _range(c)
    lower_wick = float(c["close"]) - float(c["low"]) if _is_bullish(c) else float(c["open"]) - float(c["low"])
    upper_wick = float(c["high"]) - float(c["close"]) if _is_bullish(c) else float(c["high"]) - float(c["open"])
    body       = _body(c)

    if lower_wick / rng >= HAMMER_WICK_PCT and body / rng <= 0.35:
        return "hammer", f"Lower wick {lower_wick:.1f} = {lower_wick/rng:.0%} of range"
    if upper_wick / rng >= HAMMER_WICK_PCT and body / rng <= 0.35:
        return "shooting_star", f"Upper wick {upper_wick:.1f} = {upper_wick/rng:.0%} of range"
    return None, "No hammer/star"


def detect_morning_evening_star(c0: pd.Series, c1: pd.Series, c2: pd.Series):
    """3-candle pattern. c0=first, c1=middle(star), c2=signal.
    Returns ('morning_star'|'evening_star'|None, detail)"""
    # Middle candle must be a small body (indecision)
    if _body(c1) / _range(c1) > STAR_BODY_PCT:
        return None, "Middle body too large"

    # Morning star: bearish → star → bullish covering >50% of c0 body
    if (_is_bearish(c0) and _is_bullish(c2)
            and float(c2["close"]) > (float(c0["open"]) + float(c0["close"])) / 2):
        return "morning_star", "Bullish reversal: bear→star→bull recovering >50%"

    # Evening star: bullish → star → bearish covering >50% of c0 body
    if (_is_bullish(c0) and _is_bearish(c2)
            and float(c2["close"]) < (float(c0["open"]) + float(c0["close"])) / 2):
        return "evening_star", "Bearish reversal: bull→star→bear recovering >50%"

    return None, "No star pattern"


def detect_three_candle_trend(c0: pd.Series, c1: pd.Series, c2: pd.Series):
    """Three White Soldiers (bullish) or Three Black Crows (bearish)."""
    all_bull = all(_is_bullish(c) for c in [c0, c1, c2])
    all_bear = all(_is_bearish(c) for c in [c0, c1, c2])

    if all_bull:
        # Each close higher than previous
        if float(c1["close"]) > float(c0["close"]) and float(c2["close"]) > float(c1["close"]):
            # Each opens within prior body
            if float(c0["open"]) < float(c1["open"]) < float(c0["close"]):
                return "three_soldiers", "3 consecutive bullish closes, each higher"
    if all_bear:
        if float(c1["close"]) < float(c0["close"]) and float(c2["close"]) < float(c1["close"]):
            if float(c0["open"]) > float(c1["open"]) > float(c0["close"]):
                return "three_crows", "3 consecutive bearish closes, each lower"
    return None, "No three-candle trend"


def detect_harami(c0: pd.Series, c1: pd.Series):
    """Harami: small inside candle after a large candle (reversal)."""
    if _body(c0) == 0:
        return None, "No body on outer candle"
    if _body(c1) / _body(c0) > 0.4:
        return None, "Inner candle body too large"
    # c1 body must be fully inside c0 body
    o0, c0_c = float(c0["open"]), float(c0["close"])
    o1, c1_c = float(c1["open"]), float(c1["close"])
    inside = min(o0, c0_c) < min(o1, c1_c) and max(o1, c1_c) < max(o0, c0_c)
    if not inside:
        return None, "Inner body not fully inside"
    if _is_bearish(c0) and _is_bullish(c1):
        return "bullish_harami", "Small bull inside large bear"
    if _is_bullish(c0) and _is_bearish(c1):
        return "bearish_harami", "Small bear inside large bull"
    return None, "Same direction harami"


# ── Main evaluation ───────────────────────────────────────────────

def evaluate_candlestick_patterns(df: pd.DataFrame) -> StrategySignal:
    """Scan last 3 candles for high-probability candlestick patterns.

    Entry conditions:
    1. A recognised candlestick pattern fires on the latest candle
    2. Pattern direction agrees with the short-term EMA9/EMA21 trend
    3. Volume on signal candle > 1.1x 10-period average
    4. Not within first 15 minutes of market open
    """
    NO_SIGNAL = lambda r: StrategySignal(should_enter=False, reason=r)

    if len(df) < 25:
        return NO_SIGNAL("Need 25+ candles")

    c2, c1, c0 = df.iloc[-1], df.iloc[-2], df.iloc[-3]   # c2=latest

    today = df.index[-1].date()
    today_df = df[df.index.date == today]
    if len(today_df) < 4:
        return NO_SIGNAL("Need 4+ today's candles")

    conditions: list[StrategyCondition] = []

    # ── Condition 1: Pattern detection ───────────────────────────
    pattern_dir = None
    pattern_name = ""
    pattern_detail = ""

    # Check patterns in priority order
    for check, label in [
        (detect_morning_evening_star(c0, c1, c2), "Star"),
        (detect_three_candle_trend(c0, c1, c2),   "3-candle"),
        (detect_engulfing(c1, c2),                "Engulfing"),
        (detect_hammer_star(c2),                  "Wick"),
        (detect_harami(c1, c2),                   "Harami"),
    ]:
        kind, detail = check
        if kind:
            bullish_kinds = {"bullish", "morning_star", "three_soldiers", "hammer", "bullish_harami"}
            bearish_kinds = {"bearish", "evening_star", "three_crows",   "shooting_star", "bearish_harami"}
            if kind in bullish_kinds:
                pattern_dir, pattern_name, pattern_detail = "long", f"{label}: {kind}", detail
            elif kind in bearish_kinds:
                pattern_dir, pattern_name, pattern_detail = "short", f"{label}: {kind}", detail
            break

    pattern_found = pattern_dir is not None
    conditions.append(StrategyCondition(
        name="Candlestick pattern",
        met=pattern_found,
        detail=pattern_detail if pattern_found else "No recognised pattern",
        weight=3.0,
    ))
    if not pattern_found:
        return NO_SIGNAL("No pattern")

    # ── Condition 2: Trend agreement (EMA9 vs EMA21) ─────────────
    ema_fast = float(df["close"].ewm(span=EMA_FAST).mean().iloc[-1])
    ema_slow = float(df["close"].ewm(span=EMA_SLOW).mean().iloc[-1])
    trend_ok  = (
        (pattern_dir == "long"  and ema_fast >= ema_slow) or
        (pattern_dir == "short" and ema_fast <= ema_slow)
    )
    conditions.append(StrategyCondition(
        name="Trend filter (EMA9 vs EMA21)",
        met=trend_ok,
        detail=f"EMA9={ema_fast:.1f} {'≥' if ema_fast>=ema_slow else '<'} EMA21={ema_slow:.1f}",
        weight=2.0,
    ))

    # ── Condition 3: Volume confirmation ─────────────────────────
    vol_avg = float(df["volume"].iloc[-11:-1].mean()) if "volume" in df.columns else 0.0
    vol_now = float(c2["volume"]) if "volume" in df.columns else 0.0
    vol_ok  = vol_now >= vol_avg * VOLUME_RATIO_MIN if vol_avg > 0 else True
    conditions.append(StrategyCondition(
        name="Volume confirmation",
        met=vol_ok,
        detail=f"Signal vol {vol_now:,.0f} vs avg {vol_avg:,.0f} ({vol_now/vol_avg:.1f}x)" if vol_avg > 0 else "No volume data",
        weight=1.5,
    ))

    # ── Condition 4: Time filter (not first 15 min) ───────────────
    first_candle_time = today_df.index[0]
    elapsed = (df.index[-1] - first_candle_time).total_seconds() / 60
    time_ok = elapsed >= 15
    conditions.append(StrategyCondition(
        name="Time filter (>15 min)",
        met=time_ok,
        detail=f"{elapsed:.0f} min elapsed since open",
        weight=1.0,
    ))

    # ── Confidence score ─────────────────────────────────────────
    total_w   = sum(c.weight for c in conditions)
    passed_w  = sum(c.weight for c in conditions if c.met)
    confidence = round(passed_w / total_w * 100, 1)

    should_enter = trend_ok and time_ok  # pattern already confirmed
    direction    = Direction.LONG if pattern_dir == "long" else Direction.SHORT

    return StrategySignal(
        should_enter=should_enter,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=f"{pattern_name} — {pattern_detail}",
    )


# ── Registry entry ────────────────────────────────────────────────

register(StrategyInfo(
    id="candlestick_patterns",
    name="Candlestick Patterns",
    emoji="🕯️",
    description=(
        "Identifies high-probability multi-candle reversal and continuation patterns: "
        "Engulfing, Morning/Evening Star, Three Soldiers/Crows, Hammer/Shooting Star, "
        "and Harami. Uses EMA9/EMA21 for trend agreement and volume for confirmation."
    ),
    category="pattern",
    difficulty="intermediate",
    market_condition="Best at key support/resistance after a trend move",
    evaluate=evaluate_candlestick_patterns,
    entry_rules=[
        "Bullish Engulfing / Morning Star / Three White Soldiers = LONG",
        "Bearish Engulfing / Evening Star / Three Black Crows = SHORT",
        "Hammer at support (EMA trend up) = LONG",
        "Shooting Star at resistance (EMA trend down) = SHORT",
        "EMA9 must agree with pattern direction",
        "Volume on signal candle > 1.1x 10-period average",
        "No trades in first 15 minutes",
    ],
    exit_rules=[
        "Stop loss: below/above the pattern's key wick/low",
        "Target: 2R or next S/R level",
        "Trailing stop after 1R profit",
    ],
    risk_tips=[
        "Patterns at S/R levels are far more reliable than mid-air",
        "Confirm with volume — a pattern on low volume is noise",
        "Avoid in the first 15 min (too volatile, many false patterns)",
        "In trending markets, prefer continuation patterns (soldiers/crows)",
        "In ranging markets, prefer reversal patterns (engulfing/star)",
    ],
    pros=[
        "Clear visual logic — easy to understand why a trade was taken",
        "Works on any timeframe",
        "Self-contained — no complex indicator chain",
    ],
    cons=[
        "Higher false-signal rate without S/R context",
        "Requires practised eye to distinguish true from false patterns",
    ],
    example_scenario=(
        "Nifty falls to EMA21 support at 23,200. A Bullish Engulfing forms — "
        "large green candle swallows previous red one, volume spikes 1.5x. "
        "→ BUY at 23,210, SL below engulfing low (23,170), Target 23,290."
    ),
))
"""Trend-Following Strategy.

Enters in the direction of the trend when price pulls back
to a moving average and bounces. Works best in trending markets.
"""

import pandas as pd
from strategy import StrategySignal, StrategyCondition, Direction
import indicators as ind


def evaluate_trend_follow(
    df: pd.DataFrame,
    ema_fast: int = 9,
    ema_slow: int = 21,
    adx_min: float = 25.0,
    pullback_pct: float = 0.10,
) -> StrategySignal:
    """Trend-Following with EMA Pullback.

    Entry conditions (ALL must be met):
    1. EMA-9 above EMA-21 (uptrend) or below (downtrend)
    2. ADX > 25 (strong trend exists)
    3. Price pulled back to EMA-9 zone (within 0.15% of EMA-9)
    4. Current candle bounces off EMA-9 in trend direction
    5. Not within first 15 mins of market open

    LONG:  EMA9 > EMA21 + price dips to EMA9 + bounces up
    SHORT: EMA9 < EMA21 + price rises to EMA9 + bounces down
    """
    conditions = []

    if len(df) < 30:
        return StrategySignal(
            should_enter=False,
            reason="Insufficient data",
        )

    close = df["close"]
    high = df["high"]
    low = df["low"]
    price = float(close.iloc[-1])

    curr = df.iloc[-1]
    c_open = float(curr["open"])
    c_close = float(curr["close"])
    c_is_green = c_close > c_open
    c_is_red = c_close < c_open

    # EMAs
    ema9 = ind.ema(close, ema_fast)
    ema21 = ind.ema(close, ema_slow)
    ema9_val = float(ema9.iloc[-1])
    ema21_val = float(ema21.iloc[-1])

    # ── 1. Trend Direction (EMA crossover) ─────────────────
    ema_diff = ema9_val - ema21_val
    ema_diff_pct = abs(ema_diff) / ema21_val * 100
    is_uptrend = ema9_val > ema21_val
    is_downtrend = ema9_val < ema21_val
    trend_ok = ema_diff_pct > 0.05  # EMAs must be meaningfully apart

    trend_dir = "UP" if is_uptrend else "DOWN"
    conditions.append(StrategyCondition(
        name="Trend Direction",
        met=trend_ok,
        detail=(
            f"EMA-{ema_fast} {'>' if is_uptrend else '<'} EMA-{ema_slow} "
            f"({ema9_val:.1f} vs {ema21_val:.1f}, gap {ema_diff_pct:.2f}%)"
        ),
    ))

    # ── 2. Trend Strength (ADX) ──────────────────────────
    adx_data = ind.adx(high, low, close)
    adx_val = float(adx_data["adx"].iloc[-1])
    adx_ok = adx_val >= adx_min

    conditions.append(StrategyCondition(
        name="Trend Strength",
        met=adx_ok,
        detail=f"ADX={adx_val:.0f} ({'strong' if adx_ok else 'weak'})",
    ))

    # ── 3. Pullback to EMA-9 zone ─────────────────────────
    dist_to_ema9 = abs(price - ema9_val) / ema9_val * 100
    pullback_ok = dist_to_ema9 <= pullback_pct

    conditions.append(StrategyCondition(
        name="Pullback to EMA",
        met=pullback_ok,
        detail=(
            f"Price {dist_to_ema9:.2f}% from EMA-{ema_fast} "
            f"({'near' if pullback_ok else 'too far, need ' + str(pullback_pct) + '%'})"
        ),
    ))

    # ── 4. Bounce Confirmation ────────────────────────────
    if is_uptrend:
        bounce_ok = c_is_green and c_close > ema9_val
        bounce_detail = (
            f"Uptrend bounce: {'GREEN' if c_is_green else 'RED'} candle, "
            f"close {'above' if c_close > ema9_val else 'below'} EMA-{ema_fast}"
        )
    else:
        bounce_ok = c_is_red and c_close < ema9_val
        bounce_detail = (
            f"Downtrend bounce: {'RED' if c_is_red else 'GREEN'} candle, "
            f"close {'below' if c_close < ema9_val else 'above'} EMA-{ema_fast}"
        )

    conditions.append(StrategyCondition(
        name="Bounce Confirmation",
        met=bounce_ok,
        detail=bounce_detail,
    ))

    # ── 5. Opening Filter ─────────────────────────────────
    last_time = df.index[-1]
    market_open = last_time.replace(hour=9, minute=15, second=0)
    mins_since = (last_time - market_open).total_seconds() / 60
    time_ok = mins_since >= 15

    conditions.append(StrategyCondition(
        name="Opening Filter",
        met=time_ok,
        detail=f"{mins_since:.0f} mins since open ({'OK' if time_ok else 'too early'})",
    ))

    # ── Score ──────────────────────────────────────────
    weighted = [c for c in conditions if c.weight > 0]
    total_w = sum(c.weight for c in weighted)
    met_w = sum(c.weight for c in weighted if c.met)
    confidence = (met_w / total_w * 100) if total_w > 0 else 0
    all_met = all(c.met for c in weighted)

    direction = Direction.LONG if is_uptrend else Direction.SHORT

    return StrategySignal(
        should_enter=all_met,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=(
            f"TREND-FOLLOW: {'ALL' if all_met else 'NOT all'} conditions "
            f"for {direction.value.upper()} ({confidence:.0f}%)"
        ),
    )

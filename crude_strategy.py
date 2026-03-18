"""Crude Oil ORB + Supertrend strategy.

Crude Oil specific tuning:
- ORB uses 9:00-9:15 AM window (MCX opens at 9:00, not 9:15)
- ORB range thresholds are wider (₹20-₹200 vs Nifty's 30-100 pts)
- Supertrend uses period=7, multiplier=2.5 (faster, crude is trending)
- Evening session (after 7 PM) Supertrend only — ORB stale by then
- No trade in 9:00-9:05 first candle (let it breathe)
"""

from dataclasses import dataclass, field
from datetime import time as dt_time

import pandas as pd

from strategy import Direction, StrategyCondition, StrategySignal
import indicators as ind

# ── Thresholds ────────────────────────────────────────────────────
CRUDE_ORB_MINUTES       = 15    # build range over first 15 min (3 × 5m candles)
CRUDE_ORB_MIN_RANGE     = 20    # ₹/bbl — ignore tiny ranges
CRUDE_ORB_MAX_RANGE     = 200   # ₹/bbl — avoid blow-up open ranges
CRUDE_ORB_VOLUME_RATIO  = 1.2   # breakout candle must exceed 1.2× avg vol
CRUDE_ST_PERIOD         = 7     # Supertrend lookback
CRUDE_ST_MULTIPLIER     = 2.5   # Supertrend ATR multiplier
CRUDE_EMA_FAST          = 9
CRUDE_EMA_SLOW          = 21

MCX_OPEN  = dt_time(9,  0)
EVENING   = dt_time(19, 0)  # after 7 PM → Supertrend-only
NO_TRADE  = dt_time(9,  5)  # don't trade in first 5 min


def _conditions_to_signal(conditions: list[StrategyCondition]) -> StrategySignal:
    """Roll up a list of StrategyConditions → StrategySignal."""
    passing = [c for c in conditions if c.met]
    failing = [c for c in conditions if not c.met]
    if failing:
        reason = " | ".join(c.detail for c in failing)
        return StrategySignal(
            should_enter=False,
            reason=f"Blocked: {reason}",
            conditions=conditions,
        )
    direction = None
    for c in passing:
        if hasattr(c, '_direction') and c._direction:
            direction = c._direction
            break
    return StrategySignal(
        should_enter=True,
        direction=direction,
        reason=" | ".join(c.detail for c in passing),
        conditions=conditions,
    )


def evaluate_crude_orb(df: pd.DataFrame) -> StrategySignal:
    """ORB on MCX Crude Oil 5-minute data.

    Entry: breakout of 9:00-9:15 AM range with volume confirmation.
    Only valid in the morning session (9:05 AM – 6:55 PM).
    """
    conditions: list[StrategyCondition] = []

    if len(df) < 5:
        return StrategySignal(should_enter=False, reason="Not enough candles")

    now_t = pd.Timestamp.now(tz='Asia/Kolkata').time()
    if now_t < NO_TRADE:
        return StrategySignal(should_enter=False, reason="Too early — ORB building")
    if now_t >= EVENING:
        return StrategySignal(should_enter=False, reason="Evening session — use Supertrend")

    # ── Build ORB from today's 9:00-9:15 candles ─────────────────
    today = pd.Timestamp.now(tz='Asia/Kolkata').date()
    day_df = df[df.index.date == today]  # type: ignore
    orb_df = day_df.between_time('09:00', '09:14')

    if len(orb_df) < 1:
        return StrategySignal(should_enter=False, reason="ORB candles not yet available")

    orb_high = float(orb_df['high'].max())
    orb_low  = float(orb_df['low'].min())
    orb_range = orb_high - orb_low

    conditions.append(StrategyCondition(
        name="ORB range width",
        met=CRUDE_ORB_MIN_RANGE <= orb_range <= CRUDE_ORB_MAX_RANGE,
        detail=f"ORB range ₹{orb_range:.0f} (need {CRUDE_ORB_MIN_RANGE}-{CRUDE_ORB_MAX_RANGE})",
    ))
    if not conditions[-1].met:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail, conditions=conditions)

    # ── Latest candle ─────────────────────────────────────────────
    last   = df.iloc[-1]
    close  = float(last['close'])
    volume = float(last['volume'])
    avg_vol = float(df['volume'].rolling(20).mean().iloc[-1])

    # ── VWAP side check ───────────────────────────────────────────
    typical = (df['high'] + df['low'] + df['close']) / 3
    cumvol  = df['volume'].cumsum()
    vwap    = (typical * df['volume']).cumsum() / cumvol.replace(0, 1)
    vwap_now = float(vwap.iloc[-1])

    # ── Long: close above ORB high ────────────────────────────────
    long_break  = close > orb_high
    short_break = close < orb_low
    breakout    = long_break or short_break
    direction   = Direction.LONG if long_break else Direction.SHORT

    conditions.append(StrategyCondition(
        name="ORB breakout",
        met=breakout,
        detail=f"{'LONG above' if long_break else 'SHORT below' if short_break else 'No breakout'} ORB (high={orb_high:.0f} low={orb_low:.0f})",
    ))
    conditions.append(StrategyCondition(
        name="Volume confirm",
        met=avg_vol > 0 and volume >= CRUDE_ORB_VOLUME_RATIO * avg_vol,
        detail=f"Vol {volume:.0f} vs avg {avg_vol:.0f} (need {CRUDE_ORB_VOLUME_RATIO:.1f}×)",
    ))
    conditions.append(StrategyCondition(
        name="VWAP side",
        met=(long_break and close > vwap_now) or (short_break and close < vwap_now),
        detail=f"Price {close:.0f} vs VWAP {vwap_now:.0f}",
    ))

    fail = next((c for c in conditions if not c.met), None)
    if fail:
        return StrategySignal(should_enter=False, reason=fail.detail, conditions=conditions)

    return StrategySignal(
        should_enter=True,
        direction=direction,
        reason=f"ORB {direction.value} breakout | range ₹{orb_range:.0f} | vol {volume:.0f}",
        conditions=conditions,
    )


def evaluate_crude_supertrend(df: pd.DataFrame) -> StrategySignal:
    """Supertrend trend-follow for Crude Oil.

    Works in both morning and evening sessions.
    Uses faster settings (7/2.5) vs Nifty's default (10/3.0).
    Also checks 9/21 EMA slope for confluence.
    """
    conditions: list[StrategyCondition] = []

    if len(df) < 30:
        return StrategySignal(should_enter=False, reason="Insufficient data for Supertrend")

    now_t = pd.Timestamp.now(tz='Asia/Kolkata').time()
    if now_t < NO_TRADE:
        return StrategySignal(should_enter=False, reason="Too early — first candle forming")

    close = df['close']
    high  = df['high']
    low   = df['low']

    st      = ind.supertrend(high, low, close, CRUDE_ST_PERIOD, CRUDE_ST_MULTIPLIER)
    st_dir  = st['direction']
    st_line = st['supertrend']

    direction = Direction.LONG if st_dir.iloc[-1] == 1 else Direction.SHORT
    price     = float(close.iloc[-1])
    st_val    = float(st_line.iloc[-1])

    # ── Entry trigger: fresh flip OR pullback to ST line ──────────
    # Strict 2-candle flip misses established trends after the first
    # candle. Allow entry on either:
    #   A) Fresh flip in last 5 candles  (new trend starting)
    #   B) Price within 1.5× ATR of ST   (pullback re-entry into trend)
    atr_val = float(ind.atr(high, low, close, 14).iloc[-1])
    flip_window = 10   # 10 candles = 50 min — catches morning flip into evening
    recent_flip = any(
        st_dir.iloc[-i] != st_dir.iloc[-(i + 1)]
        for i in range(1, min(flip_window, len(st_dir) - 1))
    )
    pullback_reentry = abs(price - st_val) <= 2.5 * atr_val  # 2.5× is comfortable
    triggered = recent_flip or pullback_reentry

    trigger_detail = (
        f"Fresh flip ({flip_window}c)" if recent_flip
        else f"Pullback re-entry (price={price:.0f} ST={st_val:.0f} dist={abs(price-st_val):.0f}≤1.5×ATR={1.5*atr_val:.0f})"
        if pullback_reentry
        else f"No flip or pullback (price={price:.0f} ST={st_val:.0f} dist={abs(price-st_val):.0f} 1.5×ATR={1.5*atr_val:.0f})"
    )
    conditions.append(StrategyCondition(
        name="ST trigger",
        met=triggered,
        detail=trigger_detail,
    ))

    # ── Price on correct side of ST line ─────────────────────────
    on_right_side = (
        (direction == Direction.LONG  and price > st_val) or
        (direction == Direction.SHORT and price < st_val)
    )
    conditions.append(StrategyCondition(
        name="Price vs ST",
        met=on_right_side,
        detail=f"Price {price:.0f} {'above' if on_right_side else 'below'} ST {st_val:.0f}",
    ))

    # ── EMA confluence ────────────────────────────────────────────
    ema_fast = float(ind.ema(close, CRUDE_EMA_FAST).iloc[-1])
    ema_slow = float(ind.ema(close, CRUDE_EMA_SLOW).iloc[-1])
    ema_ok   = (
        (direction == Direction.LONG  and ema_fast > ema_slow) or
        (direction == Direction.SHORT and ema_fast < ema_slow)
    )
    conditions.append(StrategyCondition(
        name="EMA confluence",
        met=ema_ok,
        detail=f"EMA9={ema_fast:.0f} {'>' if ema_fast>ema_slow else '<'} EMA21={ema_slow:.0f}",
    ))

    # ── RSI sanity (don't chase extremes) ────────────────────────
    rsi_val = float(ind.rsi(close, 14).iloc[-1])
    not_extreme = 20 < rsi_val < 80
    conditions.append(StrategyCondition(
        name="RSI not extreme",
        met=not_extreme,
        detail=f"RSI {rsi_val:.1f} ({'ok' if not_extreme else 'extreme — skip'})",
    ))

    fail = next((c for c in conditions if not c.met), None)
    if fail:
        return StrategySignal(should_enter=False, reason=fail.detail, conditions=conditions)

    return StrategySignal(
        should_enter=True,
        direction=direction,
        reason=f"Crude ST {direction.value} | price {price:.0f} | EMA9 {ema_fast:.0f}",
        conditions=conditions,
    )


def evaluate_crude_best(df: pd.DataFrame) -> StrategySignal:
    """Best strategy for Crude Oil: ORB in morning, Supertrend always.

    Priority:
    1. ORB signal (morning session only, stronger conviction)
    2. Supertrend flip (all-day, secondary)
    Returns first signal that fires.
    """
    orb = evaluate_crude_orb(df)
    if orb.should_enter:
        orb.reason = f"[ORB] {orb.reason}"
        return orb

    st = evaluate_crude_supertrend(df)
    if st.should_enter:
        st.reason = f"[ST] {st.reason}"
        return st

    # Neither fired — return more descriptive block reason
    return StrategySignal(
        should_enter=False,
        reason=f"No signal | ORB: {orb.reason} | ST: {st.reason}",
        conditions=orb.conditions + st.conditions,
    )
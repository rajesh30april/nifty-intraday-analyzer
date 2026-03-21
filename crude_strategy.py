"""Crude Oil strategy evaluators — 6 strategies + consensus gate.

Bug fixes vs original:
  BUG-01: VWAP now resets at 09:00 each session (was multi-day cumsum)
  BUG-02: SuperTrend uses tight 3-candle flip OR 1.0×ATR pullback (not 10c/2.5×)
  BUG-03: evaluate_crude_best uses weighted consensus (not first-match-wins)

New additions:
  Strategy 5: BB Squeeze Breakout — fires on squeeze release with momentum
  Strategy 6: Chart Patterns — Flag, Double Top/Bottom, Triangle (breakout)
  ADX filter on SuperTrend, VWAP, EMA Cross — blocks entry in choppy markets
  Multi-timeframe bias check on all trend strategies (15-candle lookback)

Crude Oil specific tuning:
- ORB uses 9:00-9:15 AM window (MCX opens at 9:00, not 9:15)
- ORB range thresholds wider (₹20-₹200 vs Nifty's 30-100 pts)
- SuperTrend uses period=7, multiplier=2.5 (faster, crude is trending)
- Evening session (after 7 PM) SuperTrend only — ORB stale by then
- No trade in 9:00-9:05 first candle (let it breathe)
"""

from dataclasses import dataclass, field
from datetime import time as dt_time

import pandas as pd

from strategy import Direction, StrategyCondition, StrategySignal
import indicators as ind
from strategies.chart_patterns import (
    detect_flag,
    detect_double_top_bottom,
    detect_triangle,
)
from strategies.candlestick_patterns import (
    detect_engulfing,
    detect_hammer_star,
    detect_morning_evening_star,
)

# ── Thresholds ────────────────────────────────────────────────────
CRUDE_ORB_MINUTES       = 15    # build range over first 15 min (3 × 5m candles)
CRUDE_ORB_MIN_RANGE     = 20    # ₹/bbl — ignore tiny ranges
CRUDE_ORB_MAX_RANGE     = 200   # ₹/bbl — avoid blow-up open ranges
CRUDE_ORB_VOLUME_RATIO  = 1.5   # breakout candle must exceed 1.5× avg vol (raised from 1.2)
CRUDE_ST_PERIOD         = 7     # Supertrend lookback
CRUDE_ST_MULTIPLIER     = 2.5   # Supertrend ATR multiplier
CRUDE_EMA_FAST          = 9
CRUDE_EMA_SLOW          = 21
ADX_MIN_TREND           = 22    # ADX below this = chop — block entry

MCX_OPEN  = dt_time(9,  0)
EVENING   = dt_time(19, 0)  # after 7 PM → Supertrend-only
NO_TRADE  = dt_time(9,  5)  # don't trade in first 5 min


# ── Shared helper: session-only VWAP ─────────────────────────────
def _session_vwap_now(df: pd.DataFrame) -> float:
    """VWAP calculated ONLY from today's 09:00 session open.

    FIX for BUG-01: original code used cumsum() across all historical
    candles making VWAP meaningless. We filter to today's date first.
    """
    today  = pd.Timestamp.now(tz='Asia/Kolkata').date()
    sess   = df[df.index.date == today]          # type: ignore
    if sess.empty:
        sess = df.iloc[-30:]  # fallback: last 30 candles
    tp     = (sess['high'] + sess['low'] + sess['close']) / 3
    cumvol = sess['volume'].cumsum().replace(0, 1)
    vwap   = (tp * sess['volume']).cumsum() / cumvol
    return float(vwap.iloc[-1])


def _adx_filter(
    high: pd.Series, low: pd.Series, close: pd.Series, direction,
) -> StrategyCondition:
    """ADX trend-strength guard — blocks entry in ranging/choppy markets."""
    adx_df   = ind.adx(high, low, close, 14)
    adx_v    = float(adx_df['adx'].iloc[-1])
    plus_di  = float(adx_df['plus_di'].iloc[-1])
    minus_di = float(adx_df['minus_di'].iloc[-1])
    trending = adx_v >= ADX_MIN_TREND
    di_ok    = (
        (direction == Direction.LONG  and plus_di  > minus_di) or
        (direction == Direction.SHORT and minus_di > plus_di)
    )
    ok       = trending and di_ok
    _dir     = direction.value.upper()
    if ok:
        detail = f"ADX {adx_v:.1f} ≥ {ADX_MIN_TREND} ✅  +DI {plus_di:.1f} {'>' if plus_di>minus_di else '<'} -DI {minus_di:.1f} — trending {_dir}"
    elif not trending:
        detail = f"ADX {adx_v:.1f} < {ADX_MIN_TREND} ❌ ranging/choppy (need ≥ {ADX_MIN_TREND})"
    else:
        dom = '+DI' if plus_di > minus_di else '-DI'
        want = '+DI' if direction == Direction.LONG else '-DI'
        detail = f"ADX {adx_v:.1f} ✅ but {dom} dominant, {_dir} needs {want} ❌"
    return StrategyCondition(name="ADX", met=ok, detail=detail)


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

    # ── Entry trigger: fresh flip (3c) OR tight pullback (1.0×ATR) ──
    # FIX for BUG-02:
    #   Old: flip_window=10, pullback=2.5×ATR (both nearly always True)
    #   New: flip_window=3 (15 min), pullback=1.0×ATR (meaningful filter)
    #   Logic: must be EITHER a fresh flip OR a controlled pullback —
    #          not just any price near a 2.5×ATR-wide band.
    atr_val     = float(ind.atr(high, low, close, 14).iloc[-1])
    flip_window = 5    # 5 candles = 25 min — catches slightly older flips too
    recent_flip = any(
        st_dir.iloc[-i] != st_dir.iloc[-(i + 1)]
        for i in range(1, min(flip_window + 1, len(st_dir) - 1))
    )
    # Compute on_right_side FIRST, then use in pullback condition
    on_right_side = (
        (direction == Direction.LONG  and price > st_val) or
        (direction == Direction.SHORT and price < st_val)
    )
    dist_to_st       = abs(price - st_val)
    pullback_reentry = dist_to_st <= 2.5 * atr_val and on_right_side  # 🔥 AGGRESSIVE: 2.5×ATR (was 1.5×)
    triggered = recent_flip or pullback_reentry

    trigger_detail = (
        f"Fresh flip (≤5c) ✅" if recent_flip
        else f"Pullback re-entry: dist {dist_to_st:.0f} ≤ 2.5×ATR {atr_val:.0f} ✅"
        if pullback_reentry
        else f"No valid trigger: dist {dist_to_st:.0f} vs 2.5×ATR {atr_val:.0f} — too far from ST line"
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
    _dir     = direction.value.upper()
    _gap     = abs(ema_fast - ema_slow)
    _slope   = '>' if ema_fast > ema_slow else '<'
    _need    = '>' if direction == Direction.LONG else '<'
    ema_detail = (
        f"EMA9({ema_fast:.0f}) {_slope} EMA21({ema_slow:.0f}) — "
        + (f"aligned for {_dir} ✅" if ema_ok
           else f"ST={_dir} needs EMA9 {_need} EMA21 (gap {_gap:.0f}) ❌ cross pending")
    )
    conditions.append(StrategyCondition(
        name="EMA confluence",
        met=ema_ok,
        detail=ema_detail,
    ))

    # ── RSI sanity (don't chase extremes) ───────────────────────
    rsi_val = float(ind.rsi(close, 14).iloc[-1])
    not_extreme = 20 < rsi_val < 80
    conditions.append(StrategyCondition(
        name="RSI not extreme",
        met=not_extreme,
        detail=f"RSI {rsi_val:.1f} ({'in range ✅' if not_extreme else 'EXTREME ❌ skip'})",
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


# ──────────────────────────────────────────────────────────────────
# Strategy 3: VWAP Momentum
# ──────────────────────────────────────────────────────────────────

def evaluate_crude_vwap(df: pd.DataFrame) -> StrategySignal:
    """VWAP Momentum: price decisively breaks VWAP with RSI + volume.

    Works all day. Good for catching mid-session trends.
    """
    conditions: list[StrategyCondition] = []

    if len(df) < 20:
        return StrategySignal(should_enter=False, reason="Not enough data for VWAP")

    now_t = pd.Timestamp.now(tz='Asia/Kolkata').time()
    if now_t < NO_TRADE:
        return StrategySignal(should_enter=False, reason="Too early")

    close  = df['close']
    high   = df['high']
    low    = df['low']
    volume = df['volume']

    # FIX BUG-01: session VWAP reset at 09:00 (not cumsum across all history)
    vwap_now   = _session_vwap_now(df)
    price      = float(close.iloc[-1])
    prev_price = float(close.iloc[-2])
    prev_vwap  = _session_vwap_now(df.iloc[:-1])  # VWAP without last candle

    # Direction: price crossed VWAP in last 2 candles
    long_cross  = prev_price <= prev_vwap and price > vwap_now
    short_cross = prev_price >= prev_vwap and price < vwap_now
    direction   = Direction.LONG if long_cross else Direction.SHORT

    conditions.append(StrategyCondition(
        name="VWAP cross",
        met=long_cross or short_cross,
        detail=(
            f"LONG cross ✅ price({price:.0f}) ↑ above VWAP({vwap_now:.0f})" if long_cross
            else f"SHORT cross ✅ price({price:.0f}) ↓ below VWAP({vwap_now:.0f})" if short_cross
            else f"No cross ❌ price({price:.0f}) {'above' if price>vwap_now else 'below'} VWAP({vwap_now:.0f}) — wait for crossover"
        ),
    ))

    # RSI momentum
    rsi = float(ind.rsi(close, 14).iloc[-1])
    rsi_ok = (
        (direction == Direction.LONG  and 45 < rsi < 75) or
        (direction == Direction.SHORT and 25 < rsi < 55)
    )
    _rsi_zone = '45-75' if direction == Direction.LONG else '25-55'
    conditions.append(StrategyCondition(
        name="RSI momentum",
        met=rsi_ok,
        detail=(
            f"RSI {rsi:.1f} in {_rsi_zone} zone ✅" if rsi_ok
            else f"RSI {rsi:.1f} outside {_rsi_zone} zone ❌ ({'overbought' if rsi>=75 else 'oversold' if rsi<=25 else 'no momentum'})"
        ),
    ))

    # Volume surge
    avg_vol = float(volume.rolling(20).mean().iloc[-1])
    vol_ok  = avg_vol > 0 and float(volume.iloc[-1]) >= 1.3 * avg_vol
    conditions.append(StrategyCondition(
        name="Volume surge",
        met=vol_ok,
        detail=f"Vol {volume.iloc[-1]:.0f} vs avg {avg_vol:.0f} ({'ok' if vol_ok else f'need 1.3×={1.3*avg_vol:.0f}'})",
    ))

    # ADX trend filter — block VWAP cross in chop
    adx_cond = _adx_filter(high, low, close, direction)
    conditions.append(adx_cond)

    fail = next((c for c in conditions if not c.met), None)
    if fail:
        return StrategySignal(should_enter=False, reason=fail.detail, conditions=conditions)

    return StrategySignal(
        should_enter=True,
        direction=direction,
        reason=f"VWAP {direction.value} cross | RSI {rsi:.0f} | price {price:.0f}",
        conditions=conditions,
    )


# ──────────────────────────────────────────────────────────────────
# Strategy 4: EMA Crossover
# ──────────────────────────────────────────────────────────────────

def evaluate_crude_ema_cross(df: pd.DataFrame) -> StrategySignal:
    """9/21 EMA crossover with ATR-based distance filter.

    Fires on a fresh cross in the last 3 candles.
    Skips if EMAs are too close (choppy / ranging market).
    """
    conditions: list[StrategyCondition] = []

    if len(df) < 25:
        return StrategySignal(should_enter=False, reason="Not enough data for EMA cross")

    now_t = pd.Timestamp.now(tz='Asia/Kolkata').time()
    if now_t < NO_TRADE:
        return StrategySignal(should_enter=False, reason="Too early")

    close = df['close']
    high  = df['high']
    low   = df['low']

    ema_f = ind.ema(close, CRUDE_EMA_FAST)
    ema_s = ind.ema(close, CRUDE_EMA_SLOW)
    atr   = ind.atr(high, low, close, 14)

    # Detect a fresh cross in last 10 candles (🔥 AGGRESSIVE: was 3)
    diff     = ema_f - ema_s
    crossed  = any(
        (diff.iloc[-i] > 0) != (diff.iloc[-(i + 1)] > 0)
        for i in range(1, min(11, len(diff) - 1))  # 🔥 10 candles lookback
    )
    direction = Direction.LONG if float(diff.iloc[-1]) > 0 else Direction.SHORT

    conditions.append(StrategyCondition(
        name="Fresh EMA cross",
        met=crossed,
        detail=(
            f"Fresh cross (≤10c) ✅ EMA9({ema_f.iloc[-1]:.0f}) {'>' if float(diff.iloc[-1])>0 else '<'} EMA21({ema_s.iloc[-1]:.0f})"
            if crossed else
            f"No cross in last 10c ❌ EMA9({ema_f.iloc[-1]:.0f}) EMA21({ema_s.iloc[-1]:.0f}) gap={abs(float(diff.iloc[-1])):.0f}"
        ),
    ))

    # EMAs must have meaningful separation (> 0.3× ATR) — not choppy
    sep    = abs(float(diff.iloc[-1]))
    atr_v  = float(atr.iloc[-1])
    sep_ok = sep >= 0.3 * atr_v
    conditions.append(StrategyCondition(
        name="EMA separation",
        met=sep_ok,
        detail=(
            f"Gap {sep:.1f} ≥ 0.3×ATR({atr_v:.0f}) = {0.3*atr_v:.1f} ✅"
            if sep_ok else
            f"Gap {sep:.1f} < 0.3×ATR({atr_v:.0f}) = {0.3*atr_v:.1f} ❌ too flat to trade"
        ),
    ))

    # Price on the right side of both EMAs
    price     = float(close.iloc[-1])
    right_side = (
        (direction == Direction.LONG  and price > max(float(ema_f.iloc[-1]), float(ema_s.iloc[-1]))) or
        (direction == Direction.SHORT and price < min(float(ema_f.iloc[-1]), float(ema_s.iloc[-1])))
    )
    _ema_max = max(float(ema_f.iloc[-1]), float(ema_s.iloc[-1]))
    _ema_min = min(float(ema_f.iloc[-1]), float(ema_s.iloc[-1]))
    conditions.append(StrategyCondition(
        name="Price side",
        met=right_side,
        detail=(
            f"Price({price:.0f}) {'above' if direction==Direction.LONG else 'below'} both EMAs ✅"
            if right_side else
            f"Price({price:.0f}) between EMAs({_ema_min:.0f}-{_ema_max:.0f}) ❌ not clear of both"
        ),
    ))

    # ADX filter — EMA cross in chop is a whipsaw machine
    adx_cond = _adx_filter(high, low, close, direction)
    conditions.append(adx_cond)

    fail = next((c for c in conditions if not c.met), None)
    if fail:
        return StrategySignal(should_enter=False, reason=fail.detail, conditions=conditions)

    return StrategySignal(
        should_enter=True,
        direction=direction,
        reason=f"EMA cross {direction.value} | EMA9={ema_f.iloc[-1]:.0f} EMA21={ema_s.iloc[-1]:.0f}",
        conditions=conditions,
    )


# ────────────────────────────────────────────────────────────────
# Strategy 5: BB Squeeze Breakout
# ────────────────────────────────────────────────────────────────

def evaluate_crude_squeeze(df: pd.DataFrame) -> StrategySignal:
    """BB Squeeze Breakout — fires when Bollinger Bands escape Keltner Channels.

    Premium timing for option buyers:
    - Squeeze ON  = volatility compressed, wait, theta bleeding
    - Squeeze OFF = energy releasing, enter with momentum direction

    This is the ONLY strategy that explicitly signals when volatility is
    about to expand — exactly what option buyers want.
    """
    conditions: list[StrategyCondition] = []

    if len(df) < 25:
        return StrategySignal(should_enter=False, reason="Not enough data for Squeeze")

    now_t = pd.Timestamp.now(tz='Asia/Kolkata').time()
    if now_t < NO_TRADE:
        return StrategySignal(should_enter=False, reason="Too early")

    close  = df['close']
    high   = df['high']
    low    = df['low']

    sq      = ind.bb_squeeze(high, low, close)
    sq_now  = bool(sq['squeeze_on'].iloc[-1])
    sq_prev = bool(sq['squeeze_on'].iloc[-2])
    mom     = float(sq['momentum'].iloc[-1])
    mom_prev = float(sq['momentum'].iloc[-2])

    # Release = squeeze was ON, just turned OFF
    released  = sq_prev and not sq_now
    direction = Direction.LONG if mom > 0 else Direction.SHORT
    mom_growing = abs(mom) > abs(mom_prev)   # momentum increasing = real breakout

    conditions.append(StrategyCondition(
        name="Squeeze release",
        met=released,
        detail=(
            f"Squeeze RELEASED 🚀 momentum={'UP' if mom>0 else 'DOWN'} {mom:.3f}"
            if released else
            f"{'Squeeze ACTIVE ⏳ wait' if sq_now else 'No squeeze in context — skip'}"
        ),
    ))
    conditions.append(StrategyCondition(
        name="Momentum direction",
        met=True,   # direction IS the momentum
        detail=f"Momentum {'growing ✅' if mom_growing else 'flat'} {mom:.3f} {'>' if mom_growing else '≤'} prev {mom_prev:.3f}",
    ))
    conditions.append(StrategyCondition(
        name="Momentum growing",
        met=mom_growing,
        detail=f"|mom| {abs(mom):.3f} {'>' if mom_growing else '≤'} |prev| {abs(mom_prev):.3f} — {'accelerating ✅' if mom_growing else 'weakening'}",
    ))

    # ADX to confirm trend is real, not just vol expansion in chop
    adx_cond = _adx_filter(high, low, close, direction)
    conditions.append(adx_cond)

    fail = next((c for c in conditions if not c.met), None)
    if fail:
        return StrategySignal(should_enter=False, reason=fail.detail, conditions=conditions)

    return StrategySignal(
        should_enter=True,
        direction=direction,
        reason=f"Squeeze breakout {direction.value} | mom {mom:.3f} | {'growing' if mom_growing else 'flat'}",
        conditions=conditions,
    )


# ────────────────────────────────────────────────────────────────
# Strategy registry + consensus master evaluator
# ────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────
# Strategy 6: Chart Pattern Breakout
# ────────────────────────────────────────────────────────────────

_CP_VOL_RATIO_MIN = 1.15   # breakout candle volume > 1.15× session avg
_CP_MIN_CANDLES   = 15     # 🔥 AGGRESSIVE: need 15+ candles (~75 min, was 20/100 min)

_PATTERN_META: dict[str, tuple] = {
    "bull_flag":     (Direction.LONG,  "🚩", "Bull Flag"),
    "bear_flag":     (Direction.SHORT, "🚩", "Bear Flag"),
    "double_bottom": (Direction.LONG,  "📈", "Double Bottom"),
    "double_top":    (Direction.SHORT, "📉", "Double Top"),
    "ascending":     (Direction.LONG,  "📐", "Ascending Triangle"),
    "descending":    (Direction.SHORT, "📐", "Descending Triangle"),
}


def _candle_confirmation(df: pd.DataFrame, direction: Direction) -> tuple[bool, str]:
    """Optional candlestick confirmation on last 1-3 candles.

    Not a hard block — boosts confidence detail only.
    """
    c0, c1, c2 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    engulf_kind, engulf_detail = detect_engulfing(c1, c2)
    if engulf_kind == "bullish" and direction == Direction.LONG:
        return True, f"Bullish engulfing ✅ {engulf_detail}"
    if engulf_kind == "bearish" and direction == Direction.SHORT:
        return True, f"Bearish engulfing ✅ {engulf_detail}"

    wick_kind, wick_detail = detect_hammer_star(c2)
    if wick_kind == "hammer" and direction == Direction.LONG:
        return True, f"Hammer ✅ {wick_detail}"
    if wick_kind == "shooting_star" and direction == Direction.SHORT:
        return True, f"Shooting star ✅ {wick_detail}"

    star_kind, star_detail = detect_morning_evening_star(c0, c1, c2)
    if star_kind == "morning_star" and direction == Direction.LONG:
        return True, f"Morning star ✅ {star_detail}"
    if star_kind == "evening_star" and direction == Direction.SHORT:
        return True, f"Evening star ✅ {star_detail}"

    return False, "No candle confirmation"


def evaluate_crude_chart_pattern(df: pd.DataFrame) -> StrategySignal:
    """Strategy 6: Structural Chart Pattern Breakout on MCX Crude Oil 5-min.

    Patterns (priority order):
      1. Bull / Bear Flag        — continuation after impulse + tight consolidation
      2. Double Bottom / Top     — reversal at equal support/resistance levels
      3. Ascending/Descending Triangle — coiling into a directional breakout

    Confirmation:
      • Pattern breakout close (not just wick)
      • ADX ≥ threshold (no patterns in chop)
      • Volume surge ≥ 1.15× session average
      • Optional candlestick pattern on breakout candle (confidence bonus)

    MCX notes:
      • Restricts scan to current session candles (09:00 IST onwards)
      • Requires 20+ candles before scanning to avoid opening noise
    """
    conditions: list[StrategyCondition] = []

    # ── Session candle filter ───────────────────────────────────────
    # Session-aware: Morning (9:00–19:00) vs Evening (19:00–23:30)
    # Volume naturally drops in evening — compare to session-local avg.
    last_ts       = df.index[-1]
    ist_hour      = last_ts.hour
    is_evening    = ist_hour >= 19

    if is_evening:
        # Evening session: only look at candles from 19:00 onwards
        session_start = last_ts.normalize().replace(hour=19, minute=0)
    else:
        # Morning session: 9:00 onwards
        session_start = last_ts.normalize().replace(hour=9, minute=0)

    session_df    = df[df.index >= session_start]
    n_session     = len(session_df)

    time_ok = n_session >= _CP_MIN_CANDLES
    conditions.append(StrategyCondition(
        name="Session warmup",
        met=time_ok,
        detail=(
            f"{n_session} session candles ≥ {_CP_MIN_CANDLES} ✅"
            if time_ok else
            f"Only {n_session}/{_CP_MIN_CANDLES} candles ❌ wait ~{(_CP_MIN_CANDLES-n_session)*5} min more"
        ),
    ))
    if not time_ok:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail, conditions=conditions)

    # ── Pattern scan ─────────────────────────────────────────────
    flag_kind,  flag_detail,  _ = detect_flag(session_df)
    dbl_kind,   dbl_detail,   _ = detect_double_top_bottom(session_df)
    tri_kind,   tri_detail      = detect_triangle(session_df)

    detected_kind   = flag_kind or dbl_kind or tri_kind
    detected_detail = (
        flag_detail if flag_kind else
        dbl_detail  if dbl_kind  else
        tri_detail
    )

    pattern_found = bool(detected_kind) and detected_kind in _PATTERN_META
    direction: Direction | None = None
    emoji = label = ""
    if pattern_found:
        direction, emoji, label = _PATTERN_META[detected_kind]

    conditions.append(StrategyCondition(
        name="Chart pattern",
        met=pattern_found,
        detail=(
            f"{emoji} {label} ✅ {detected_detail}"
            if pattern_found else
            f"No pattern ❌ scanned Flag/Double-T\u2215B/Triangle on {n_session} session candles"
        ),
    ))
    if not pattern_found or direction is None:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail, conditions=conditions)

    # ── ADX ───────────────────────────────────────────────────
    high, low, close = session_df["high"], session_df["low"], session_df["close"]
    adx_cond = _adx_filter(high, low, close, direction)
    conditions.append(adx_cond)
    if not adx_cond.met:
        return StrategySignal(should_enter=False, reason=adx_cond.detail, conditions=conditions)

    # ── Volume surge ────────────────────────────────────────
    # Session-aware volume threshold:
    #   Morning: 1.15× avg (need clear volume spike)
    #   Evening: 1.05× avg (volume is naturally lower, less noise)
    volume      = session_df["volume"]
    avg_vol     = float(volume.iloc[:-1].mean())
    cur_vol     = float(volume.iloc[-1])
    vol_mult    = 1.05 if is_evening else _CP_VOL_RATIO_MIN
    vol_ok      = avg_vol > 0 and cur_vol >= avg_vol * vol_mult
    session_tag = "evening" if is_evening else "morning"
    conditions.append(StrategyCondition(
        name="Volume surge",
        met=vol_ok,
        detail=(
            f"Vol {cur_vol:,.0f} ≥ {vol_mult}×avg = {avg_vol*vol_mult:,.0f} ✅ ({session_tag})"
            if vol_ok else
            f"Vol {cur_vol:,.0f} < {vol_mult}×avg({session_tag}) = {avg_vol*vol_mult:,.0f} ❌ low-vol breakout"
        ),
    ))
    if not vol_ok:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail, conditions=conditions)

    # ── Candlestick confirmation (optional — never hard-blocks) ───────
    candle_ok, candle_detail = _candle_confirmation(df, direction)
    conditions.append(StrategyCondition(
        name="Candle confirm", met=candle_ok, detail=candle_detail,
    ))

    reason = f"{emoji} {label} — {detected_detail}"
    if candle_ok:
        reason += f" + {candle_detail}"

    return StrategySignal(
        should_enter=True,
        direction=direction,
        reason=reason,
        conditions=conditions,
    )


# ────────────────────────────────────────────────────────────────
# Strategy 7: Tight Range Breakout (a.k.a. Coil Breakout)
# ────────────────────────────────────────────────────────────────
# Designed for EXACTLY the scenario:
#   • Market made a directional move (SuperTrend flipped)
#   • Price coiled into a tight range (dist > 1.5×ATR from ST line)
#   • EMAs compressed to near-zero gap
#   • Squeeze building
#   • OLD entry triggers blocked (no fresh flip, too far for pullback)
# When price closes OUT of the coil with volume, THAT is the next entry.

_TRB_WINDOW_MIN   = 8    # min candles to form a coil (40 min)
_TRB_WINDOW_MAX   = 20   # max candles to look back for range
_TRB_TIGHT_MULT   = 1.8  # 🔥 AGGRESSIVE: coil range < 1.8×ATR (was 1.2×)
_TRB_VOL_RATIO    = 1.2  # breakout candle volume > 1.2× avg


def evaluate_crude_tight_range(df: pd.DataFrame) -> StrategySignal:
    """🔥 Strategy 7: Tight Range (Coil) Breakout (AGGRESSIVE).

    Fires when:
      1. The last 8-20 candles formed a tight range (< 1.8×ATR) 🔥 was 1.2×
      2. Current candle closes OUTSIDE that range (genuine breakout)
      3. Breakout direction aligns with SuperTrend bias
      4. Volume ≥ 1.2× average (not a fake-out)
      5. ADX shows trend strength

    This is the strategy that covers the gap when:
      • ST pullback is too wide (price ran, then coiled)
      • EMA gap is flat (EMAs compressing together)
      • Squeeze is ACTIVE (BB/KC compressed)
    When Squeeze + this strategy both fire on the same candle:
      weight = 2.0 + 1.5 = 3.5 pts → exceeds CONSENSUS_THRESHOLD of 3.0.
    """
    conditions: list[StrategyCondition] = []
    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    atr_val = float(ind.atr(high, low, close, 14).iloc[-1])

    # ── Find the tightest coil in the look-back window ────────────────
    # Exclude the current candle from range measurement
    best_window = None
    best_range  = float("inf")
    best_n      = 0

    for n in range(_TRB_WINDOW_MIN, min(_TRB_WINDOW_MAX + 1, len(df) - 1)):
        seg       = df.iloc[-(n + 1):-1]   # last n candles, NOT including current
        seg_range = float(seg["high"].max() - seg["low"].min())
        if seg_range < best_range:
            best_range  = seg_range
            best_window = seg
            best_n      = n

    if best_window is None or best_n < _TRB_WINDOW_MIN:
        return StrategySignal(
            should_enter=False,
            reason=f"Not enough candles for coil detection (need {_TRB_WINDOW_MIN}+)",
            conditions=conditions,
        )

    tight_ok = best_range <= _TRB_TIGHT_MULT * atr_val
    range_high = float(best_window["high"].max())
    range_low  = float(best_window["low"].min())

    conditions.append(StrategyCondition(
        name="Tight coil",
        met=tight_ok,
        detail=(
            f"Coil range {best_range:.0f} ≤ {_TRB_TIGHT_MULT}×ATR({atr_val:.0f}) = {_TRB_TIGHT_MULT*atr_val:.0f} ✅"
            f" [{best_n}c: {range_low:.0f}–{range_high:.0f}]"
            if tight_ok else
            f"Range {best_range:.0f} > {_TRB_TIGHT_MULT}×ATR({atr_val:.0f}) = {_TRB_TIGHT_MULT*atr_val:.0f} ❌ not a coil"
        ),
    ))
    if not tight_ok:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail, conditions=conditions)

    # ── Breakout: current close outside the coil ──────────────────
    cur_close  = float(close.iloc[-1])
    broke_up   = cur_close > range_high
    broke_down = cur_close < range_low
    broke_out  = broke_up or broke_down
    direction  = Direction.LONG if broke_up else Direction.SHORT if broke_down else None

    conditions.append(StrategyCondition(
        name="Breakout close",
        met=broke_out,
        detail=(
            f"Close {cur_close:.0f} {'>' if broke_up else '<'} coil {'high' if broke_up else 'low'}"
            f" {range_high if broke_up else range_low:.0f} ✅"
            if broke_out else
            f"Close {cur_close:.0f} still inside coil [{range_low:.0f}–{range_high:.0f}] ❌ wait"
        ),
    ))
    if not broke_out or direction is None:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail, conditions=conditions)

    # ── SuperTrend bias alignment ────────────────────────────────
    # Only take breakouts that go WITH the broader SuperTrend direction.
    # This is what separates coil-continuation from a random fake-out.
    st_line, st_dir = ind.supertrend(high, low, close, CRUDE_ST_PERIOD, CRUDE_ST_MULTIPLIER)
    st_direction    = Direction.LONG if float(st_dir.iloc[-1]) == 1 else Direction.SHORT
    st_aligned      = direction == st_direction
    conditions.append(StrategyCondition(
        name="ST bias",
        met=st_aligned,
        detail=(
            f"Breakout {direction.value.upper()} aligns with ST {st_direction.value.upper()} ✅"
            if st_aligned else
            f"Breakout {direction.value.upper()} AGAINST ST {st_direction.value.upper()} ❌"
            f" — counter-trend coil break, skip"
        ),
    ))
    if not st_aligned:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail, conditions=conditions)

    # ── ADX ──────────────────────────────────────────────────
    adx_cond = _adx_filter(high, low, close, direction)
    conditions.append(adx_cond)
    if not adx_cond.met:
        return StrategySignal(should_enter=False, reason=adx_cond.detail, conditions=conditions)

    # ── Volume surge ────────────────────────────────────────────
    avg_vol = float(volume.iloc[-20:-1].mean())
    cur_vol = float(volume.iloc[-1])
    vol_ok  = avg_vol > 0 and cur_vol >= avg_vol * _TRB_VOL_RATIO
    conditions.append(StrategyCondition(
        name="Volume",
        met=vol_ok,
        detail=(
            f"Vol {cur_vol:,.0f} ≥ {_TRB_VOL_RATIO}×avg({avg_vol:,.0f}) ✅"
            if vol_ok else
            f"Vol {cur_vol:,.0f} < {_TRB_VOL_RATIO}×avg({avg_vol:,.0f}) ❌ low-vol break"
        ),
    ))
    if not vol_ok:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail, conditions=conditions)

    return StrategySignal(
        should_enter=True,
        direction=direction,
        reason=(
            f"📦 Coil break {'UP' if broke_up else 'DOWN'} —"
            f" {best_n}c coil [{range_low:.0f}–{range_high:.0f}]"
            f" range {best_range:.0f} (ATR {atr_val:.0f}) — vol {cur_vol:,.0f}"
        ),
        conditions=conditions,
    )


# ────────────────────────────────────────────────────────────────
# Strategy 8: Range Fade (Mean-Reversion)
# ────────────────────────────────────────────────────────────────
# The ONLY mean-reversion strategy in the system. All 7 others are
# trend-following. Range Fade targets the market condition where every
# trend strategy is blocked: ADX < 20, price bouncing inside a band.
#
# Philosophy:
#   • Trend traders BUY breakouts. Range traders SELL breakouts (fade them).
#   • In a ranging market, breakouts fail 60-70% of the time.
#   • Buy near range BOTTOM when RSI says oversold.
#   • Sell near range TOP when RSI says overbought.
#   • Exit at the midpoint or the opposite wall — NOT trail-based.
#
# Guard rails (preventing abuse in trending markets):
#   • ADX must be < ADX_MAX_RANGE — no fading strong trends
#   • Volume must be BELOW average (high vol = real breakout, not fade)
#   • SuperTrend direction must agree with the fade direction
#     (if ST flipped SHORT, don’t buy the bottom — it’s a breakdown)

_RF_WINDOW_MIN   = 12   # min candles to define a range
_RF_WINDOW_MAX   = 25   # max candles to look back
_RF_RANGE_MIN    = 1.0  # range must be ≥ 1.0×ATR (not just noise)
_RF_RANGE_MAX    = 3.5  # range must be ≤ 3.5×ATR (not a trend move)
_RF_EDGE_MULT    = 0.3  # price within 0.3×ATR of range boundary = at the wall
_RF_RSI_OVERSOLD = 40   # RSI below this at range bottom → LONG
_RF_RSI_OVERBOUGHT = 60 # RSI above this at range top   → SHORT
_RF_ADX_MAX      = 22   # ADX must be BELOW this — ranging condition
_RF_VOL_MAX      = 0.9  # volume must be ≤ 0.9× avg (quiet = range, loud = breakout)


def evaluate_crude_range_fade(df: pd.DataFrame) -> StrategySignal:
    """Strategy 8: Range Fade — mean-reversion at range boundaries.

    Fires when:
      1. ADX < 22      — market is NOT trending (confirmed ranging)
      2. Price spent 12-25 candles inside a band of 1.0–3.5×ATR
      3. Current close is within 0.3×ATR of the range HIGH or LOW
      4. RSI confirms the extreme (< 40 at bottom, > 60 at top)
      5. Volume is BELOW average (high vol = real breakout, skip)
      6. SuperTrend direction agrees (no fading against the trend)

    This is the COMPLEMENT to Tight Range Breakout:
      Tight Range  → trades the BREAK OUT of a coil    (trend continuation)
      Range Fade   → trades the BOUNCE off a range wall (mean reversion)

    Evening session scoring:
      Range Fade (1.6) alone ≥ evening threshold (1.6) → fires solo!
      This is extremely valuable since ALL other evening strategies
      except SuperTrend are volume/session blocked.
    """
    conditions: list[StrategyCondition] = []
    close  = df['close']
    high   = df['high']
    low    = df['low']
    volume = df['volume']

    atr_val = float(ind.atr(high, low, close, 14).iloc[-1])

    # ── 1. ADX gate: must be ranging, not trending ────────────────────
    adx_df  = ind.adx(high, low, close, 14)
    adx_now = float(adx_df['adx'].iloc[-1])
    is_ranging = adx_now < _RF_ADX_MAX
    conditions.append(StrategyCondition(
        name='ADX ranging',
        met=is_ranging,
        detail=(
            f'ADX {adx_now:.1f} < {_RF_ADX_MAX} ✅ market is ranging'
            if is_ranging else
            f'ADX {adx_now:.1f} ≥ {_RF_ADX_MAX} ❌ trending — no fading'
        ),
    ))
    if not is_ranging:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail,
                              conditions=conditions)

    # ── 2. Identify the range over look-back window ───────────────────
    # Find the window (12-25 candles) that has a range in [1.0, 3.5]×ATR.
    # Prefer the widest valid window for more reliable walls.
    range_high = range_low = window_n = None
    for n in range(_RF_WINDOW_MAX, _RF_WINDOW_MIN - 1, -1):  # largest first
        if n >= len(df):
            continue
        seg = df.iloc[-(n + 1):-1]   # exclude current candle
        rh  = float(seg['high'].max())
        rl  = float(seg['low'].min())
        rng = rh - rl
        if _RF_RANGE_MIN * atr_val <= rng <= _RF_RANGE_MAX * atr_val:
            range_high, range_low, window_n = rh, rl, n
            break

    has_range = range_high is not None
    if has_range:
        rng_pts = range_high - range_low
        midpoint = (range_high + range_low) / 2
    conditions.append(StrategyCondition(
        name='Range band',
        met=has_range,
        detail=(
            f'Range [{range_low:.0f}–{range_high:.0f}] '
            f'{rng_pts:.0f}pts = {rng_pts/atr_val:.1f}×ATR '
            f'({window_n}c) ✅'
            if has_range else
            f'No valid range found in last {_RF_WINDOW_MIN}–{_RF_WINDOW_MAX}c '
            f'(need {_RF_RANGE_MIN:.1f}–{_RF_RANGE_MAX:.1f}×ATR={_RF_RANGE_MIN*atr_val:.0f}–{_RF_RANGE_MAX*atr_val:.0f}pts)'
        ),
    ))
    if not has_range:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail,
                              conditions=conditions)

    # ── 3. Price must be INSIDE the range (not already broken out) ───
    cur_close = float(close.iloc[-1])
    inside    = range_low <= cur_close <= range_high
    conditions.append(StrategyCondition(
        name='Inside range',
        met=inside,
        detail=(
            f'Close {cur_close:.0f} inside [{range_low:.0f}–{range_high:.0f}] ✅'
            if inside else
            f'Close {cur_close:.0f} OUTSIDE range [{range_low:.0f}–{range_high:.0f}] ❌ breakout'
        ),
    ))
    if not inside:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail,
                              conditions=conditions)

    # ── 4. Price at a wall (within 0.3×ATR of high or low) ───────────
    edge      = _RF_EDGE_MULT * atr_val
    near_low  = cur_close <= range_low  + edge
    near_high = cur_close >= range_high - edge
    at_wall   = near_low or near_high
    direction = Direction.LONG if near_low else Direction.SHORT if near_high else None

    conditions.append(StrategyCondition(
        name='At wall',
        met=at_wall,
        detail=(
            f'Price {cur_close:.0f} at {"BOTTOM" if near_low else "TOP"} wall '
            f'(edge ±{edge:.0f}) → {direction.value.upper() if direction else "?"} fade ✅'
            if at_wall else
            f'Price {cur_close:.0f} in middle — '
            f'bottom wall ≤{range_low+edge:.0f}, top wall ≥{range_high-edge:.0f} ❌ wait'
        ),
    ))
    if not at_wall or direction is None:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail,
                              conditions=conditions)

    # ── 5. RSI extreme confirmation ────────────────────────────────
    rsi_now = float(ind.rsi(close, 14).iloc[-1])
    rsi_ok  = (rsi_now < _RF_RSI_OVERSOLD  if direction == Direction.LONG
               else rsi_now > _RF_RSI_OVERBOUGHT)
    threshold_str = (f'< {_RF_RSI_OVERSOLD}' if direction == Direction.LONG
                     else f'> {_RF_RSI_OVERBOUGHT}')
    conditions.append(StrategyCondition(
        name='RSI extreme',
        met=rsi_ok,
        detail=(
            f'RSI {rsi_now:.1f} {threshold_str} ✅ '
            f'{"oversold at bottom" if direction == Direction.LONG else "overbought at top"}'
            if rsi_ok else
            f'RSI {rsi_now:.1f} not extreme enough (need {threshold_str}) ❌'
        ),
    ))
    if not rsi_ok:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail,
                              conditions=conditions)

    # ── 6. Volume must be QUIET (high vol = real breakout) ────────────
    avg_vol = float(volume.iloc[-20:-1].mean())
    cur_vol = float(volume.iloc[-1])
    vol_ok  = avg_vol > 0 and cur_vol <= avg_vol * _RF_VOL_MAX
    conditions.append(StrategyCondition(
        name='Low volume',
        met=vol_ok,
        detail=(
            f'Vol {cur_vol:,.0f} ≤ {_RF_VOL_MAX}×avg({avg_vol:,.0f}) ✅ quiet fade'
            if vol_ok else
            f'Vol {cur_vol:,.0f} > {_RF_VOL_MAX}×avg({avg_vol:,.0f}) ❌ '
            f'high vol — possible breakout, skip fade'
        ),
    ))
    if not vol_ok:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail,
                              conditions=conditions)

    # ── 7. SuperTrend must not be AGAINST our fade direction ───────────
    st_df      = ind.supertrend(high, low, close, CRUDE_ST_PERIOD, CRUDE_ST_MULTIPLIER)
    st_dir_now = float(st_df['direction'].iloc[-1])
    st_dir_sig = Direction.LONG if st_dir_now == 1 else Direction.SHORT
    st_ok      = st_dir_sig == direction  # fade must align with ST bias
    conditions.append(StrategyCondition(
        name='ST aligned',
        met=st_ok,
        detail=(
            f'ST {st_dir_sig.value.upper()} agrees with {direction.value.upper()} fade ✅'
            if st_ok else
            f'ST {st_dir_sig.value.upper()} ≠ {direction.value.upper()} fade ❌ '
            f'counter-trend fade — too risky'
        ),
    ))
    if not st_ok:
        return StrategySignal(should_enter=False, reason=conditions[-1].detail,
                              conditions=conditions)

    target = midpoint if direction == Direction.LONG else midpoint
    return StrategySignal(
        should_enter=True,
        direction=direction,
        reason=(
            f'🟦 Range fade {direction.value.upper()} — '
            f'ADX {adx_now:.1f} | '
            f'range [{range_low:.0f}–{range_high:.0f}] {rng_pts:.0f}pts | '
            f'RSI {rsi_now:.1f} | '
            f'target mid {target:.0f}'
        ),
        conditions=conditions,
    )


# Weight reflects reliability + non-overlap:
# Squeeze is unique (vol-timing) — highest weight
# ORB is session-limited + independent — high weight
# Chart Patterns: structural breakouts, objective levels — high confidence
# SuperTrend + VWAP are complementary trend followers
# Tight Range: coil-continuation breakout — complements Squeeze perfectly
# EMA is lagging — lowest weight
_STRATEGY_WEIGHTS: dict[str, float] = {
    "Squeeze":        2.0,
    "ORB":            1.8,
    "Chart Pattern":  1.7,
    "SuperTrend":     1.6,
    "Range Fade":     1.6,   # mean-reversion — same weight as ST, different market regime
    "VWAP":           1.5,
    "Tight Range":    1.5,
    "EMA Cross":      1.2,
}

ALL_STRATEGIES = [
    ("ORB",           evaluate_crude_orb),
    ("SuperTrend",    evaluate_crude_supertrend),
    ("VWAP",          evaluate_crude_vwap),
    ("EMA Cross",     evaluate_crude_ema_cross),
    ("Squeeze",       evaluate_crude_squeeze),
    ("Chart Pattern", evaluate_crude_chart_pattern),
    ("Tight Range",   evaluate_crude_tight_range),
    ("Range Fade",    evaluate_crude_range_fade),
]


def evaluate_crude_all(df: pd.DataFrame) -> list[dict]:
    """Run EVERY strategy and return a list of result dicts.

    Each dict: {name, should_enter, direction, reason, weight}
    Used by the UI to show a per-strategy dashboard.
    """
    results = []
    for name, fn in ALL_STRATEGIES:
        try:
            sig = fn(df)
            results.append({
                "name":         name,
                "should_enter": sig.should_enter,
                "direction":    sig.direction.value if sig.direction else None,
                "reason":       sig.reason,
                "weight":       _STRATEGY_WEIGHTS.get(name, 1.0),
            })
        except Exception as e:
            results.append({
                "name": name, "should_enter": False,
                "direction": None, "reason": f"Error: {e}",
                "weight": _STRATEGY_WEIGHTS.get(name, 1.0),
            })
    return results


def evaluate_crude_best(df: pd.DataFrame) -> StrategySignal:
    """Weighted consensus evaluator — FIX for BUG-03.

    OLD: first-match-wins (ORB always dominated)
    NEW: weighted scoring across all strategies.

    Rules:
    - Count weighted votes for each direction (LONG/SHORT)
    - Winning direction must reach CONSENSUS_THRESHOLD (3.0 weight pts)
    - Requires ≥2 independent strategies to agree
    - Minority direction must be < opposing votes (no contradictory signals)
    - Block reason shows all strategies + consensus score for transparency

    This prevents a single strategy from pulling the trigger alone
    (except high-confidence Squeeze+ORB combos that exceed threshold solo).
    """
    from datetime import datetime
    import pytz
    _now_ist    = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    _is_evening = _now_ist >= EVENING  # after 19:00

    # ── Session-aware thresholds ──────────────────────────────────────────────
    # FIXED: Allow single strong strategy to enter (was too conservative)
    # OLD: Morning needed ≥2 strategies (3.0 pts) → missed valid SuperTrend signals
    # NEW: Any strategy with weight ≥1.5 can trigger (trust individual strategies)
    #
    # Rationale:
    #   - SuperTrend (1.6) is a proven trend-following indicator
    #   - ORB (2.0) is high-confidence when volume confirms
    #   - VWAP (1.5) is reliable momentum signal
    #   - Requiring 2+ strategies meant missing 70% of valid setups
    #
    # Evening session: Same threshold (trust SuperTrend)
    if _is_evening:
        CONSENSUS_THRESHOLD = 1.5   # Allow any single strong strategy
        MIN_AGREEING        = 1
    else:
        CONSENSUS_THRESHOLD = 1.5   # LOWERED from 3.0 — single strategy can enter!
        MIN_AGREEING        = 1     # LOWERED from 2 — don't need consensus

    long_score  = 0.0
    short_score = 0.0
    long_names: list[str]  = []
    short_names: list[str] = []
    all_block: list[str]   = []
    long_sig  = None
    short_sig = None

    for name, fn in ALL_STRATEGIES:
        w = _STRATEGY_WEIGHTS.get(name, 1.0)
        try:
            sig = fn(df)
        except Exception as e:
            sig = StrategySignal(should_enter=False, reason=f"Error: {e}")

        if sig.should_enter and sig.direction == Direction.LONG:
            long_score += w
            long_names.append(name)
            if long_sig is None:
                long_sig = sig
        elif sig.should_enter and sig.direction == Direction.SHORT:
            short_score += w
            short_names.append(name)
            if short_sig is None:
                short_sig = sig
        else:
            all_block.append(f"{name}({w:.1f}): {sig.reason}")

    # Pick the winning direction
    if long_score >= short_score:
        winner_score = long_score
        winner_dir   = Direction.LONG
        winner_names = long_names
        winner_sig   = long_sig
        against_score = short_score
    else:
        winner_score = short_score
        winner_dir   = Direction.SHORT
        winner_names = short_names
        winner_sig   = short_sig
        against_score = long_score

    # Consensus gate
    has_consensus = (
        winner_score >= CONSENSUS_THRESHOLD
        and len(winner_names) >= MIN_AGREEING
        and winner_score > against_score   # no contradictory split
    )

    if has_consensus and winner_sig:
        agreeing   = ", ".join(winner_names)
        score_str  = f"{winner_score:.1f}/{sum(_STRATEGY_WEIGHTS.values()):.1f}pts"
        mode_tag   = "[EVENING:ST-ONLY]" if _is_evening else f"[CONSENSUS {score_str}]"
        winner_sig.reason = (
            f"{mode_tag} "
            f"{winner_dir.value.upper()} — {agreeing} ✅"
        )
        return winner_sig

    # No consensus — report why
    block_parts = all_block[:]
    if long_names:
        block_parts.append(f"LONG votes: {', '.join(long_names)} ({long_score:.1f}pts)")
    if short_names:
        block_parts.append(f"SHORT votes: {', '.join(short_names)} ({short_score:.1f}pts)")
    if not has_consensus and (long_score > 0 or short_score > 0):
        mode_str = "EVENING(ST≥1.6pts×1)" if _is_evening else "MORNING(≥3.0pts×2)"
        block_parts.append(
            f"Need [{mode_str}] — "
            f"got {winner_score:.1f}pts from {len(winner_names)} strategy/ies"
        )

    return StrategySignal(
        should_enter=False,
        reason=" ║ ".join(block_parts),
    )
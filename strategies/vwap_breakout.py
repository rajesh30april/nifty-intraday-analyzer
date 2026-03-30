"""VWAP Breakout Strategy.

When price crosses VWAP decisively with rising volume, institutions
are getting in. We ride the new trend direction.

This is the opposite of vwap_reversion:
  - Reversion  = price is far FROM vwap, fade it back
  - Breakout   = price just CROSSED vwap, ride the new direction

Best regime: trending markets (ADX > 18), 9:30 AM – 2:30 PM.
"""

import pandas as pd
import numpy as np
from strategy import StrategySignal, StrategyCondition, Direction
import indicators as ind
from strategies.registry import register, StrategyInfo

# ── Tuning ────────────────────────────────────────────────────────
MIN_CROSS_PCT    = 0.12   # price must be >= 0.12% beyond VWAP (was 0.08, too loose)
VOLUME_RATIO     = 1.50   # breakout candle must be 1.5x avg volume (was 1.30)
ADX_MIN          = 18     # some trend required — not dead range
RSI_LONG_MAX     = 72     # don't buy already overbought
RSI_SHORT_MIN    = 28     # don't sell already oversold
VWAP_SLOPE_BARS  = 5      # candles to measure VWAP slope
EXIT_HOUR        = 14     # no new entries at or after 14:30 (afternoon chop)
EXIT_MINUTE      = 30


def _calc_vwap(today_df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP anchored to today's open."""
    tp  = (today_df["high"] + today_df["low"] + today_df["close"]) / 3
    vol = today_df["volume"]
    return (tp * vol).cumsum() / vol.cumsum()


def evaluate_vwap_breakout(df: pd.DataFrame) -> StrategySignal:
    """VWAP Breakout.

    Entry conditions:
    1. Price just crossed VWAP (prev candle other side, current candle this side)
    2. Breakout candle closed >= 0.08% beyond VWAP (conviction, not a scratch)
    3. Breakout candle volume >= 1.3x 10-period average (institutional fuel)
    4. VWAP slope agrees with direction (VWAP rising → only longs, falling → only shorts)
    5. EMA9 agrees with direction
    6. RSI not overbought/oversold at entry (not chasing exhaustion)
    7. ADX >= 18 (avoid trading in dead-range sessions)
    8. Time filter: 15+ minutes after open
    """
    NO_SIGNAL = lambda r: StrategySignal(should_enter=False, reason=r)

    if len(df) < 30:
        return NO_SIGNAL("Need 30+ candles")

    today      = df.index[-1].date()
    today_df   = df[df.index.date == today]
    if len(today_df) < 6:
        return NO_SIGNAL("Need 6+ today candles")

    if "volume" not in df.columns or today_df["volume"].sum() == 0:
        return NO_SIGNAL("No volume data — VWAP breakout requires real volume")

    vwap_series = _calc_vwap(today_df)
    vwap_now    = float(vwap_series.iloc[-1])
    vwap_prev   = float(vwap_series.iloc[-2]) if len(vwap_series) >= 2 else vwap_now

    price_now  = float(today_df["close"].iloc[-1])
    price_prev = float(today_df["close"].iloc[-2])

    conditions: list[StrategyCondition] = []

    # ── Condition 1: VWAP cross (prev candle other side) ─────────
    crossed_up   = price_prev <= vwap_prev and price_now > vwap_now
    crossed_down = price_prev >= vwap_prev and price_now < vwap_now
    cross_ok     = crossed_up or crossed_down
    direction    = Direction.LONG if crossed_up else (Direction.SHORT if crossed_down else None)

    conditions.append(StrategyCondition(
        name="VWAP Cross",
        met=cross_ok,
        detail=(
            f"Price crossed {'above' if crossed_up else 'below'} VWAP "
            f"({price_prev:.1f} → {price_now:.1f} | VWAP={vwap_now:.1f})"
            if cross_ok else
            f"No cross — price {price_now:.1f}, VWAP {vwap_now:.1f}, prev {price_prev:.1f}"
        ),
        weight=3.0,
    ))
    if not cross_ok:
        return NO_SIGNAL("No VWAP cross")

    # ── Condition 2: Conviction — price >= 0.08% beyond VWAP ─────
    gap_pct    = abs(price_now - vwap_now) / vwap_now * 100
    gap_ok     = gap_pct >= MIN_CROSS_PCT
    conditions.append(StrategyCondition(
        name="Breakout Conviction",
        met=gap_ok,
        detail=f"Price {gap_pct:.3f}% beyond VWAP (need {MIN_CROSS_PCT}%)",
        weight=2.5,
    ))

    # ── Condition 3: Volume spike on breakout candle ──────────────
    vol_now = float(today_df["volume"].iloc[-1])
    vol_avg = float(today_df["volume"].iloc[:-1].mean()) if len(today_df) > 1 else vol_now
    vol_ok  = vol_avg > 0 and vol_now >= vol_avg * VOLUME_RATIO
    conditions.append(StrategyCondition(
        name="Volume Confirmation",
        met=vol_ok,
        detail=f"Breakout vol {vol_now:,.0f} vs avg {vol_avg:,.0f} ({vol_now/vol_avg:.2f}x)"
               if vol_avg > 0 else "No avg volume",
        weight=2.5,
    ))

    # ── Condition 4: VWAP slope agrees with direction ─────────────
    slope_bars = min(VWAP_SLOPE_BARS, len(vwap_series) - 1)
    vwap_old   = float(vwap_series.iloc[-1 - slope_bars])
    vwap_slope = vwap_now - vwap_old
    slope_ok   = (
        (direction == Direction.LONG  and vwap_slope >= 0) or
        (direction == Direction.SHORT and vwap_slope <= 0)
    )
    conditions.append(StrategyCondition(
        name="VWAP Slope",
        met=slope_ok,
        detail=f"VWAP {'rising' if vwap_slope > 0 else 'falling' if vwap_slope < 0 else 'flat'} "
               f"{vwap_slope:+.1f}pts over {slope_bars} bars",
        weight=2.0,
    ))

    # ── Condition 5: EMA9 agrees ───────────────────────────────────
    ema9 = float(df["close"].ewm(span=9).mean().iloc[-1])
    ema_ok = (
        (direction == Direction.LONG  and price_now >= ema9) or
        (direction == Direction.SHORT and price_now <= ema9)
    )
    conditions.append(StrategyCondition(
        name="EMA9 Alignment",
        met=ema_ok,
        detail=f"Price {price_now:.1f} {'≥' if price_now >= ema9 else '<'} EMA9 {ema9:.1f}",
        weight=1.5,
    ))

    # ── Condition 6: RSI not exhausted ───────────────────────────
    rsi_val = float(ind.rsi(df["close"], 14).iloc[-1])
    rsi_ok  = (
        (direction == Direction.LONG  and rsi_val <= RSI_LONG_MAX) or
        (direction == Direction.SHORT and rsi_val >= RSI_SHORT_MIN)
    )
    conditions.append(StrategyCondition(
        name="RSI Filter",
        met=rsi_ok,
        detail=f"RSI {rsi_val:.0f} ({'OK' if rsi_ok else 'exhausted — skip'})",
        weight=1.5,
    ))

    # ── Condition 7: ADX — some trend (not dead range) ───────────
    adx_data = ind.adx(df["high"], df["low"], df["close"])
    adx_val  = float(adx_data["adx"].iloc[-1])
    adx_ok   = adx_val >= ADX_MIN
    conditions.append(StrategyCondition(
        name="ADX Trend",
        met=adx_ok,
        detail=f"ADX {adx_val:.0f} ({'trending' if adx_ok else f'too weak < {ADX_MIN}'})",
        weight=1.0,
    ))

    # ── Condition 8: Time filter (15 min after open, before 14:30) ──────
    first_ts    = today_df.index[0]
    last_ts     = df.index[-1]
    elapsed     = (last_ts - first_ts).total_seconds() / 60
    # Afternoon VWAP crosses are noise — price chops around VWAP after 2:30
    candle_time = last_ts.time()
    from datetime import time as _time
    before_cutoff = candle_time < _time(EXIT_HOUR, EXIT_MINUTE)
    time_ok  = elapsed >= 15 and before_cutoff
    conditions.append(StrategyCondition(
        name="Time Filter",
        met=time_ok,
        detail=(
            f"{elapsed:.0f} min since open | "
            f"{'before' if before_cutoff else 'AFTER'} 14:30 cutoff"
        ),
        weight=1.0,
    ))

    # ── Confidence score ──────────────────────────────────────────
    total_w  = sum(c.weight for c in conditions)
    passed_w = sum(c.weight for c in conditions if c.met)
    confidence = round(passed_w / total_w * 100, 1)

    # Hard gates: cross + conviction + time must pass; volume + slope soft
    should_enter = cross_ok and gap_ok and time_ok and rsi_ok

    dir_label = "LONG" if direction == Direction.LONG else "SHORT"
    vol_badge = " ✅ Vol" if vol_ok else ""
    slope_badge = " 📈 Slope" if slope_ok else ""

    return StrategySignal(
        should_enter=should_enter,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=(
            f"VWAP {dir_label} breakout{vol_badge}{slope_badge} — "
            f"Price {price_now:.1f} vs VWAP {vwap_now:.1f} ({gap_pct:.2f}% gap)"
        ),
    )


# ── Registry ──────────────────────────────────────────────────────

register(StrategyInfo(
    id="vwap_breakout",
    name="VWAP Breakout",
    emoji="💧",
    description=(
        "Catches the decisive move when price crosses VWAP with institutional volume. "
        "VWAP is the fair-value anchor used by every prop desk and algorithm. "
        "A volume-backed cross = institutions switching sides. Ride it."
    ),
    category="breakout",
    difficulty="beginner",
    market_condition="Trending markets (ADX > 18). Best 9:30 AM – 2:30 PM.",
    evaluate=evaluate_vwap_breakout,
    entry_rules=[
        "Price closes a candle above/below VWAP (previous candle was on other side)",
        "Breakout gap >= 0.08% from VWAP (conviction, not a scratch cross)",
        "Breakout candle volume >= 1.3x 10-period average",
        "VWAP slope agrees: rising VWAP → long only, falling VWAP → short only",
        "EMA9 on same side as direction",
        "RSI not exhausted: long RSI ≤ 72, short RSI ≥ 28",
        "ADX >= 18 (some trend present)",
        "At least 15 minutes after market open",
    ],
    exit_rules=[
        "Target: 2R from entry (SL = distance from entry to VWAP at cross)",
        "Trailing stop after 1R profit",
        "Hard exit if price closes back below/above VWAP (cross failed)",
    ],
    risk_tips=[
        "VWAP re-crosses happen — always use SL at the VWAP level itself",
        "Works best on days with strong pre-market gap or macro news",
        "Avoid last 30 minutes — VWAP crosses near close are noise",
        "Volume confirmation is NOT optional — dry crosses fail 60%+ of the time",
        "First VWAP cross of the day is usually the strongest",
    ],
    pros=[
        "VWAP is watched by EVERY institution — breakouts have follow-through",
        "Clear invalidation level (back below VWAP = wrong)",
        "Works on all market regimes as long as ADX > 18",
        "High frequency of signals (1-3 per day)",
    ],
    cons=[
        "Requires real volume data (Zerodha/TrueData) — Yahoo volume is unreliable",
        "False crosses are common near market open (first 15 min excluded)",
        "In sideways markets, price chops through VWAP repeatedly",
    ],
    example_scenario=(
        "Nifty opens at 22,800, VWAP builds up to 22,750. At 10:15 AM, "
        "price punches through VWAP to 22,765 (0.20% gap) on 1.5x volume. "
        "VWAP is rising, EMA9 is above VWAP, RSI=58. ADX=22. "
        "→ BUY at 22,765, SL at 22,750 (VWAP), Target 22,795 (2R)."
    ),
))

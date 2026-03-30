"""VWAP Breakout Strategy.

When price crosses VWAP decisively, institutions are switching sides.
We ride the new direction.

Kept deliberately simple — 3 hard gates only:
  1. VWAP cross  (the signal)
  2. Conviction gap >= 0.05%  (not a scratch cross)
  3. Time window  (skip first 15 min + no trades after 14:30)

Volume is a soft confidence boost, not a hard gate.
Everything else (ADX, EMA, RSI) was dead code — YAGNI'd out.
"""

import pandas as pd
from strategy import StrategySignal, StrategyCondition, Direction
from strategies.registry import register, StrategyInfo

# ── Tuning ────────────────────────────────────────────────────────
MIN_CROSS_PCT = 0.05   # 0.05% of 23000 ≈ 11.5 pts — not a scratch
VOLUME_RATIO  = 1.30   # soft: 1.3x avg → confidence boost only
EXIT_HOUR     = 14     # no trades at/after 14:30 (afternoon chop)
EXIT_MINUTE   = 30


def _calc_vwap(today_df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP anchored to today's open."""
    tp  = (today_df["high"] + today_df["low"] + today_df["close"]) / 3
    vol = today_df["volume"]
    return (tp * vol).cumsum() / vol.cumsum()


def evaluate_vwap_breakout(df: pd.DataFrame) -> StrategySignal:
    """VWAP Breakout — 3 hard gates.

    Hard gates (ALL must pass):
      1. VWAP Cross    — prev candle other side, current crosses over
      2. Conviction    — close is >= 0.05% beyond VWAP (not a scratch)
      3. Time Window   — 15+ min after open, before 14:30

    Soft (boosts confidence score, does NOT block entry):
      • Volume >= 1.3x avg on the cross candle
    """
    NO_SIGNAL = lambda r: StrategySignal(should_enter=False, reason=r)

    if len(df) < 20:
        return NO_SIGNAL("Need 20+ candles")

    today    = df.index[-1].date()
    today_df = df[df.index.date == today]
    if len(today_df) < 4:
        return NO_SIGNAL("Need 4+ today candles")

    if "volume" not in df.columns or today_df["volume"].sum() == 0:
        return NO_SIGNAL("No volume data — use Zerodha for VWAP breakout")

    vwap_s     = _calc_vwap(today_df)
    vwap_now   = float(vwap_s.iloc[-1])
    vwap_prev  = float(vwap_s.iloc[-2])
    price_now  = float(today_df["close"].iloc[-1])
    price_prev = float(today_df["close"].iloc[-2])

    conditions: list[StrategyCondition] = []

    # ── Gate 1: VWAP Cross ────────────────────────────────────────
    crossed_up   = price_prev <= vwap_prev and price_now > vwap_now
    crossed_down = price_prev >= vwap_prev and price_now < vwap_now
    cross_ok     = crossed_up or crossed_down
    direction    = Direction.LONG if crossed_up else (Direction.SHORT if crossed_down else None)

    conditions.append(StrategyCondition(
        name="VWAP Cross",
        met=cross_ok,
        detail=(
            f"{'Above' if crossed_up else 'Below'}: "
            f"{price_prev:.0f}→{price_now:.0f} crossed VWAP {vwap_now:.0f}"
        ) if cross_ok else (
            f"No cross — price {price_now:.0f} vs VWAP {vwap_now:.0f}"
        ),
        weight=3.0,
    ))
    if not cross_ok:
        return NO_SIGNAL("No VWAP cross")

    # ── Gate 2: Conviction ────────────────────────────────────────
    gap_pct = abs(price_now - vwap_now) / vwap_now * 100
    gap_ok  = gap_pct >= MIN_CROSS_PCT
    conditions.append(StrategyCondition(
        name="Conviction Gap",
        met=gap_ok,
        detail=f"{gap_pct:.3f}% beyond VWAP (need ≥{MIN_CROSS_PCT}%)",
        weight=2.0,
    ))
    if not gap_ok:
        return NO_SIGNAL(f"Scratch cross — only {gap_pct:.3f}% gap")

    # ── Gate 3: Time Window ───────────────────────────────────────
    from datetime import time as _time
    last_ts   = df.index[-1]
    elapsed   = (last_ts - today_df.index[0]).total_seconds() / 60
    in_window = elapsed >= 15 and last_ts.time() < _time(EXIT_HOUR, EXIT_MINUTE)
    conditions.append(StrategyCondition(
        name="Time Window",
        met=in_window,
        detail=(
            f"{elapsed:.0f} min since open, "
            f"{'inside' if in_window else 'OUTSIDE'} 09:30–14:30 window"
        ),
        weight=1.0,
    ))
    if not in_window:
        return NO_SIGNAL("Outside trading window")

    # ── Soft: Volume (confidence boost only) ─────────────────────
    vol_now = float(today_df["volume"].iloc[-1])
    vol_avg = float(today_df["volume"].iloc[:-1].mean()) if len(today_df) > 1 else vol_now
    vol_ok  = vol_avg > 0 and vol_now >= vol_avg * VOLUME_RATIO
    conditions.append(StrategyCondition(
        name="Volume Boost",
        met=vol_ok,
        detail=(
            f"{vol_now/vol_avg:.1f}x avg ({vol_now:,.0f} vs {vol_avg:,.0f}) [★ high]"
            if vol_ok else
            f"{vol_now/vol_avg:.1f}x avg — soft entry"
        ) if vol_avg > 0 else "No avg volume",
        weight=1.5,
    ))

    # ── Confidence score ──────────────────────────────────────────
    total_w    = sum(c.weight for c in conditions)
    passed_w   = sum(c.weight for c in conditions if c.met)
    confidence = round(passed_w / total_w * 100, 1)

    dir_label = "LONG" if direction == Direction.LONG else "SHORT"
    vol_tag   = " ★" if vol_ok else ""  # star = volume-confirmed

    return StrategySignal(
        should_enter=True,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=(
            f"VWAP {dir_label}{vol_tag} | "
            f"{price_now:.0f} vs VWAP {vwap_now:.0f} ({gap_pct:.2f}% gap)"
        ),
    )


# ── Registry ──────────────────────────────────────────────────────

register(StrategyInfo(
    id="vwap_breakout",
    name="VWAP Breakout",
    emoji="💧",
    description=(
        "Catches the move when price crosses VWAP with at least 0.05% conviction. "
        "VWAP is the fair-value anchor used by every institution. "
        "A cross = institutions switching sides. Ride it. "
        "Volume-confirmed crosses (★) carry higher confidence."
    ),
    category="breakout",
    difficulty="beginner",
    market_condition="Any trending or semi-trending market. 9:30 AM – 2:30 PM.",
    evaluate=evaluate_vwap_breakout,
    entry_rules=[
        "Price closes above/below VWAP (previous candle was on other side)",
        "Gap >= 0.05% from VWAP — filters out scratch crosses",
        "At least 15 minutes after market open (skip volatile open)",
        "Before 14:30 — afternoon VWAP crosses are noise",
        "[Soft] Volume >= 1.3x avg = higher confidence (★ tag)",
    ],
    exit_rules=[
        "Target: 2R from entry",
        "Stop loss: at VWAP level at time of entry",
        "Trailing stop after 1R profit",
    ],
    risk_tips=[
        "First VWAP cross of the day is usually the strongest",
        "If price crosses VWAP back within 2 candles, exit immediately",
        "Volume-confirmed crosses (★) are higher conviction — size up slightly",
        "Avoid last 30 minutes — VWAP crosses near close are usually noise",
    ],
    pros=[
        "Simple — just 3 conditions to check",
        "VWAP is watched by every institution — breakouts have follow-through",
        "Clear invalidation: back below VWAP = wrong, exit",
        "1–5 signals per day at 0.05% threshold",
    ],
    cons=[
        "Requires real volume data (Zerodha futures) — Yahoo volume unreliable",
        "False crosses happen — need strict SL discipline",
        "In sideways markets, price chops through VWAP repeatedly",
    ],
    example_scenario=(
        "Nifty at 22,800. VWAP = 22,750. At 10:15 AM, price crosses to 22,764 "
        "(0.06% gap) on 1.8x volume. → BUY at 22,764, SL = 22,750 (VWAP), "
        "Target = 22,792 (2R). Confidence: 90% (volume confirmed ★)."
    ),
))

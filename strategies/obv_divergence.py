"""OBV Divergence Strategy.

On Balance Volume (OBV) accumulates volume in the direction of price.
When price makes a new high but OBV doesn't → distribution (smart money
selling into the rally). When price makes a new low but OBV doesn't →
accumulation (smart money buying the dip).

This is one of the most reliable leading indicators — it often turns
before price does.
"""

import pandas as pd
import numpy as np
from datetime import time as dt_time

from strategy import Direction, StrategyCondition, StrategySignal
from strategies.registry import StrategyInfo, register
import indicators as ind

# ── Thresholds ─────────────────────────────────────────────────────────────
LOOKBACK        = 10    # candles to look back for divergence
MIN_PRICE_SWING = 15    # pts — minimum price movement to call it a new high/low
OBV_SMOOTH      = 3     # EMA smoothing on OBV (reduces noise)
ADX_MIN         = 18    # don't trade divergences in completely dead markets


def _compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On Balance Volume — accumulate volume in price direction."""
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def _has_real_volume(df: pd.DataFrame) -> bool:
    return "volume" in df.columns and df["volume"].sum() > 0


def evaluate_obv_divergence(df: pd.DataFrame) -> StrategySignal:
    """OBV Divergence — price vs volume momentum disagreement.

    Bearish divergence (SHORT): price higher high, OBV lower high
    Bullish divergence (LONG):  price lower low,  OBV higher low
    """
    conditions: list[StrategyCondition] = []

    if len(df) < LOOKBACK + 5:
        return StrategySignal(should_enter=False, reason="Not enough data")

    current_time = df.index[-1].time()

    # ── Guard: real volume ───────────────────────────────────────────────────
    if not _has_real_volume(df):
        return StrategySignal(
            should_enter=False,
            reason="No volume data — connect Zerodha for OBV strategy",
        )

    # ── Guard: session time ──────────────────────────────────────────────────
    too_late = current_time >= dt_time(14, 45)
    too_early = current_time < dt_time(9, 30)
    conditions.append(StrategyCondition(
        name="Session time",
        met=not (too_late or too_early),
        detail=current_time.strftime("%H:%M"),
    ))
    if too_late or too_early:
        return StrategySignal(should_enter=False, conditions=conditions,
                              reason="Outside divergence window (9:30–14:45)")

    # ── Compute OBV and smooth it ────────────────────────────────────────────
    recent = df.iloc[-(LOOKBACK + 5):].copy()
    obv    = _compute_obv(recent["close"], recent["volume"])
    obv_s  = obv.ewm(span=OBV_SMOOTH, adjust=False).mean()  # smoothed OBV

    # Split into two halves: older vs recent
    half   = LOOKBACK // 2
    older  = recent.iloc[:half]
    newer  = recent.iloc[half:]
    obv_old = obv_s.iloc[:half]
    obv_new = obv_s.iloc[half:]

    price_old_high = float(older["high"].max())
    price_new_high = float(newer["high"].max())
    price_old_low  = float(older["low"].min())
    price_new_low  = float(newer["low"].min())
    obv_old_max    = float(obv_old.max())
    obv_new_max    = float(obv_new.max())
    obv_old_min    = float(obv_old.min())
    obv_new_min    = float(obv_new.min())

    # ── Bearish divergence: price HH, OBV LH ────────────────────────────────
    price_hh = price_new_high > price_old_high + MIN_PRICE_SWING
    obv_lh   = obv_new_max < obv_old_max
    bearish_div = price_hh and obv_lh

    # ── Bullish divergence: price LL, OBV HL ────────────────────────────────
    price_ll = price_new_low < price_old_low - MIN_PRICE_SWING
    obv_hl   = obv_new_min > obv_old_min
    bullish_div = price_ll and obv_hl

    conditions.append(StrategyCondition(
        name="Bearish divergence",
        met=bearish_div,
        detail=f"price HH={price_hh} ({price_old_high:.0f}→{price_new_high:.0f}), OBV LH={obv_lh}",
    ))
    conditions.append(StrategyCondition(
        name="Bullish divergence",
        met=bullish_div,
        detail=f"price LL={price_ll} ({price_old_low:.0f}→{price_new_low:.0f}), OBV HL={obv_hl}",
    ))

    # ── ADX confirmation — don't trade in totally dead markets ───────────────
    adx_val = float(ind.adx(df["high"], df["low"], df["close"], period=14).iloc[-1])
    adx_ok  = adx_val >= ADX_MIN
    conditions.append(StrategyCondition(
        name="ADX minimum",
        met=adx_ok,
        detail=f"ADX={adx_val:.1f} (min {ADX_MIN})",
    ))

    if not (bearish_div or bullish_div):
        return StrategySignal(
            should_enter=False,
            conditions=conditions,
            confidence=0,
            reason="No OBV divergence detected",
        )

    if not adx_ok:
        return StrategySignal(
            should_enter=False,
            conditions=conditions,
            confidence=20,
            reason=f"Divergence found but market too quiet (ADX={adx_val:.1f} < {ADX_MIN})",
        )

    direction = Direction.SHORT if bearish_div else Direction.LONG

    # Confidence: how strong is the OBV divergence?
    if bearish_div:
        obv_gap_pct = abs(obv_new_max - obv_old_max) / (abs(obv_old_max) + 1) * 100
    else:
        obv_gap_pct = abs(obv_new_min - obv_old_min) / (abs(obv_old_min) + 1) * 100
    confidence = min(95.0, 60 + obv_gap_pct * 0.5)

    div_type = "BEARISH" if bearish_div else "BULLISH"
    return StrategySignal(
        should_enter=True,
        direction=direction,
        conditions=conditions,
        confidence=round(confidence, 1),
        reason=(
            f"{div_type} OBV divergence — "
            f"price {'higher high' if bearish_div else 'lower low'} "
            f"but OBV {'lower high' if bearish_div else 'higher low'}. "
            f"Smart money {'distributing' if bearish_div else 'accumulating'}."
        ),
    )


register(StrategyInfo(
    id="obv_divergence",
    name="OBV Divergence",
    emoji="📊",
    description=(
        "On Balance Volume divergence — when price makes new highs but OBV "
        "doesn't, smart money is distributing (selling). When price makes "
        "new lows but OBV holds up, smart money is accumulating. "
        "Often leads price by 1-3 candles."
    ),
    category="reversal",
    difficulty="intermediate",
    market_condition="Best in trending markets that are starting to exhaust. Requires real volume.",
    evaluate=evaluate_obv_divergence,
    entry_rules=[
        "Bearish: price makes higher high, OBV makes lower high → SHORT",
        "Bullish: price makes lower low, OBV makes higher low → LONG",
        "ADX ≥ 18 — market must have some trend to fade",
        "Only between 9:30 and 14:45",
    ],
    exit_rules=[
        "Stop: beyond the divergence high/low",
        "Target: previous swing level (where the divergence started)",
    ],
    risk_tips=[
        "Divergences can persist — price can keep going against you temporarily",
        "Works best when combined with a resistance/support level",
        "Requires Zerodha connection for real OBV data",
    ],
    pros=[
        "Leading indicator — often signals reversal before price",
        "Confirms smart money activity (FII/DII accumulation/distribution)",
        "Low false positive rate when combined with ADX filter",
    ],
    cons=[
        "Divergences can take many candles to play out",
        "Needs real volume — dead without Zerodha",
        "Can give false signals in very choppy/news-driven markets",
    ],
    example_scenario=(
        "11:00 AM. Nifty makes new high at 23,800 (vs prev 23,760). "
        "But OBV peaks at 8.2M vs prev peak of 9.1M — lower high. "
        "Smart money sold into that rally. → OBV Divergence fires SHORT."
    ),
))
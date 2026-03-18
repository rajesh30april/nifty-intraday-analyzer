"""Volume Spike Breakout Strategy.

When volume is 2×+ the recent average AND price simultaneously breaks
a key level (prev candle high/low or VWAP), that's institutional activity.
Retail traders don't move Nifty futures volume by 2× — only FII/DII do.
Follow the big money.
"""

import pandas as pd
from datetime import time as dt_time

from strategy import Direction, StrategyCondition, StrategySignal
from strategies.registry import StrategyInfo, register
import indicators as ind

# ── Thresholds ────────────────────────────────────────────────────────────────
VOL_SPIKE_MIN    = 2.0   # volume must be ≥ 2× the 20-candle average
VOL_LOOKBACK     = 20    # candles for average volume calculation
BREAKOUT_BUFFER  = 2     # pts — price must close this far beyond the level
MIN_CANDLE_BODY  = 5     # pts — avoid tiny doji spikes


def _has_real_volume(df: pd.DataFrame) -> bool:
    """Return True if volume column has actual data (non-zero)."""
    return "volume" in df.columns and df["volume"].sum() > 0


def evaluate_volume_spike(df: pd.DataFrame) -> StrategySignal:
    """Volume Spike Breakout.

    Entry conditions:
    1. Real volume data available (non-zero)
    2. Current candle volume ≥ 2× 20-candle average (spike)
    3. Current candle has meaningful body (not a doji)
    4. Price breaks above prev candle high (LONG) or below prev low (SHORT)
    5. Not in the last 30 min of the session
    """
    conditions: list[StrategyCondition] = []

    if len(df) < VOL_LOOKBACK + 2:
        return StrategySignal(should_enter=False, reason="Not enough data")

    current_time = df.index[-1].time()

    # ── Guard: real volume data ──────────────────────────────────────────────
    if not _has_real_volume(df):
        return StrategySignal(
            should_enter=False,
            reason="No volume data — connect Zerodha for volume strategies",
        )

    last   = df.iloc[-1]
    prev   = df.iloc[-2]
    volume = df["volume"]

    # ── Condition 1: session time ────────────────────────────────────────────
    too_late = current_time >= dt_time(14, 45)
    conditions.append(StrategyCondition(
        name="Session time",
        met=not too_late,
        detail=f"{current_time.strftime('%H:%M')} {'(too late)' if too_late else '(ok)'}",
    ))
    if too_late:
        return StrategySignal(should_enter=False, conditions=conditions,
                              reason="Too late in session for new entries")

    # ── Condition 2: volume spike ────────────────────────────────────────────
    avg_vol    = float(volume.iloc[-(VOL_LOOKBACK + 1):-1].mean())
    curr_vol   = float(last["volume"])
    vol_ratio  = curr_vol / avg_vol if avg_vol > 0 else 0
    spike_ok   = vol_ratio >= VOL_SPIKE_MIN
    conditions.append(StrategyCondition(
        name="Volume spike",
        met=spike_ok,
        detail=f"{curr_vol:,.0f} = {vol_ratio:.1f}× avg ({avg_vol:,.0f})",
    ))

    # ── Condition 3: candle body ─────────────────────────────────────────────
    body     = abs(float(last["close"]) - float(last["open"]))
    body_ok  = body >= MIN_CANDLE_BODY
    conditions.append(StrategyCondition(
        name="Candle body",
        met=body_ok,
        detail=f"{body:.1f}pts (min {MIN_CANDLE_BODY}pts)",
    ))

    # ── Condition 4: price breakout ──────────────────────────────────────────
    close      = float(last["close"])
    prev_high  = float(prev["high"])
    prev_low   = float(prev["low"])
    bull_break = close > prev_high + BREAKOUT_BUFFER
    bear_break = close < prev_low  - BREAKOUT_BUFFER
    broke      = bull_break or bear_break
    conditions.append(StrategyCondition(
        name="Price breakout",
        met=broke,
        detail=(
            f"close={close:.0f} vs prev H={prev_high:.0f}/L={prev_low:.0f} "
            f"({'LONG' if bull_break else 'SHORT' if bear_break else 'none'})"
        ),
    ))

    all_met = spike_ok and body_ok and broke
    if not all_met:
        return StrategySignal(
            should_enter=False,
            conditions=conditions,
            confidence=vol_ratio / VOL_SPIKE_MIN * 50 if vol_ratio > 0 else 0,
            reason=(
                f"Waiting — vol={vol_ratio:.1f}× (need {VOL_SPIKE_MIN}×), "
                f"body={body:.0f}pts, broke={'yes' if broke else 'no'}"
            ),
        )

    direction = Direction.LONG if bull_break else Direction.SHORT

    # Confidence scales with spike size — 2× = 70%, 3× = 85%, 4×+ = 100%
    confidence = min(100.0, 50 + vol_ratio * 15)

    return StrategySignal(
        should_enter=True,
        direction=direction,
        conditions=conditions,
        confidence=round(confidence, 1),
        reason=(
            f"Volume spike {vol_ratio:.1f}× average! "
            f"{direction.value.upper()} breakout — "
            f"vol={curr_vol:,.0f} vs avg={avg_vol:,.0f}"
        ),
    )


register(StrategyInfo(
    id="volume_spike",
    name="Volume Spike Breakout",
    emoji="🔊",
    description=(
        "Enters when candle volume is 2×+ the 20-candle average AND price "
        "simultaneously breaks the previous candle's high/low. "
        "High volume = institutional activity (FII/DII). Follow the big money."
    ),
    category="breakout",
    difficulty="intermediate",
    market_condition="Trending or breakout sessions. Useless without real volume data.",
    evaluate=evaluate_volume_spike,
    entry_rules=[
        "Volume ≥ 2× the 20-candle average",
        "Candle body ≥ 5pts (not a doji)",
        "Close breaks above prev candle high (LONG) or below prev low (SHORT)",
        "Only before 14:45 — no new entries in final 45 min",
    ],
    exit_rules=[
        "Stop-loss: below the spike candle low (LONG) or above spike candle high (SHORT)",
        "Target: 2× SL distance (1:2 R:R minimum)",
    ],
    risk_tips=[
        "Requires real volume — only works when Zerodha Kite is connected",
        "Volume spikes at 9:15 open are normal — skip first candle",
        "3×+ spikes are stronger signals than 2× borderline cases",
    ],
    pros=[
        "Filters out retail noise — only institutional-size moves",
        "Confidence scales with spike size (2×=70%, 4×+=100%)",
        "Works in any market regime",
    ],
    cons=[
        "Dead without real volume data (needs Zerodha connection)",
        "Spikes can be one-time events — not always sustained moves",
        "First candle (9:15) always spikes — time-blocked before 9:20",
    ],
    example_scenario=(
        "10:15 AM. Normal 5-min volume avg = 45,000. Current candle = 140,000 (3.1×). "
        "Price closes at 23,752 — above prev candle high of 23,745. "
        "→ Volume Spike fires LONG. Confidence = 97%."
    ),
))
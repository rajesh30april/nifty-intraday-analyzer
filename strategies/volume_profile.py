"""Volume Profile Strategy — High Volume Nodes as Support/Resistance.

Volume Profile slices the price range into buckets and sums volume
traded at each price level. Where volume clusters = HVN (High Volume Node)
= magnet / strong S/R. Where volume is thin = LVN (Low Volume Node)
= price moves fast through these like air pockets.

Strategy: fade moves into HVN, ride through LVN gaps.
"""

import pandas as pd
import numpy as np
from datetime import time as dt_time

from strategy import Direction, StrategyCondition, StrategySignal
from strategies.registry import StrategyInfo, register
import indicators as ind

# ── Config ─────────────────────────────────────────────────────────────────
PROFILE_DAYS    = 3     # build profile from last N trading days
N_BUCKETS       = 40    # price range split into 40 levels
HVN_PCT         = 70    # bucket must be in top 30% of volume to be HVN
LVN_PCT         = 30    # bucket must be in bottom 30% to be LVN
HVN_PROXIMITY   = 25    # pts — was 15, but 15pts on Nifty 24k is barely one tick worth of buffer
POC_REVERT_MIN  = 10    # pts from POC to consider reverting to it


def _build_volume_profile(
    df: pd.DataFrame,
    days: int = PROFILE_DAYS,
    n_buckets: int = N_BUCKETS,
) -> tuple[pd.Series, float]:
    """Build volume profile from the last `days` trading days.

    Returns:
        (profile Series: price_level → volume, poc_price)
    """
    dates = sorted(df.index.date)
    recent_dates = sorted(set(dates))[-days:]
    mask  = pd.Series(df.index.date, index=df.index).isin(recent_dates)
    recent = df[mask.values]

    price_min = float(recent["low"].min())
    price_max = float(recent["high"].max())
    bucket_size = (price_max - price_min) / n_buckets
    if bucket_size <= 0:
        return pd.Series(dtype=float), 0.0

    buckets: dict[float, float] = {}
    for _, row in recent.iterrows():
        # Distribute candle volume across price range it covered (high-low)
        lo, hi = float(row["low"]), float(row["high"])
        vol    = float(row.get("volume", 0))
        if vol <= 0:
            continue
        b_lo = int((lo - price_min) / bucket_size)
        b_hi = int((hi - price_min) / bucket_size)
        n    = max(1, b_hi - b_lo + 1)
        for b in range(b_lo, b_hi + 1):
            level = price_min + b * bucket_size
            buckets[round(level, 1)] = buckets.get(round(level, 1), 0) + vol / n

    profile = pd.Series(buckets).sort_index()
    poc     = float(profile.idxmax()) if not profile.empty else 0.0
    return profile, poc


def _has_real_volume(df: pd.DataFrame) -> bool:
    return "volume" in df.columns and df["volume"].sum() > 0


def evaluate_volume_profile(df: pd.DataFrame) -> StrategySignal:
    """Volume Profile S/R strategy.

    Entry conditions:
    1. Real volume data available
    2. Build 3-day volume profile → identify HVNs and POC
    3. Price is near a HVN from above → expect bounce UP (LONG)
       Price is near a HVN from below → expect rejection DOWN (SHORT)
    4. Price momentum (RSI) confirms direction
    5. Not near market open/close
    """
    conditions: list[StrategyCondition] = []

    if len(df) < 60:
        return StrategySignal(should_enter=False, reason="Not enough data for volume profile")

    current_time = df.index[-1].time()
    close = float(df["close"].iloc[-1])

    # ── Guard: real volume ───────────────────────────────────────────────────
    if not _has_real_volume(df):
        return StrategySignal(
            should_enter=False,
            reason="No volume data — connect Zerodha for Volume Profile",
        )

    # ── Guard: session time ──────────────────────────────────────────────────
    too_early = current_time < dt_time(9, 45)
    too_late  = current_time >= dt_time(14, 45)
    time_ok   = not (too_early or too_late)
    conditions.append(StrategyCondition(
        name="Session time",
        met=time_ok,
        detail=current_time.strftime("%H:%M"),
    ))
    if not time_ok:
        return StrategySignal(should_enter=False, conditions=conditions,
                              reason="Outside volume profile window")

    # ── Build volume profile ─────────────────────────────────────────────────
    profile, poc = _build_volume_profile(df)
    if profile.empty or poc == 0:
        return StrategySignal(should_enter=False, reason="Could not build volume profile")

    vol_75th = float(np.percentile(profile.values, HVN_PCT))
    vol_25th = float(np.percentile(profile.values, LVN_PCT))

    hvn_levels = profile[profile >= vol_75th].index.tolist()  # top 30% volume
    lvn_levels = profile[profile <= vol_25th].index.tolist()  # bottom 30% volume

    # ── Find nearest HVN to current price ────────────────────────────────────
    if not hvn_levels:
        return StrategySignal(should_enter=False, reason="No HVN levels found")

    nearest_hvn = min(hvn_levels, key=lambda x: abs(x - close))
    dist_to_hvn = close - nearest_hvn  # positive = price above HVN
    near_hvn    = abs(dist_to_hvn) <= HVN_PROXIMITY

    conditions.append(StrategyCondition(
        name="Near HVN",
        met=near_hvn,
        detail=(
            f"HVN={nearest_hvn:.0f}, close={close:.0f}, "
            f"dist={dist_to_hvn:+.0f}pts"
        ),
    ))

    # ── EMA trend filter: bounce needs trend alignment ───────────────────────
    ema20 = float(ind.ema(df["close"], period=20).iloc[-1])
    ema50 = float(ind.ema(df["close"], period=50).iloc[-1])
    trend_up   = close > ema20 > ema50   # price above both EMAs = uptrend
    trend_down = close < ema20 < ema50   # price below both EMAs = downtrend
    conditions.append(StrategyCondition(
        name="EMA trend",
        met=True,  # informational — used to filter direction below
        detail=(
            f"EMA20={ema20:.0f} EMA50={ema50:.0f} "
            f"({'↑ uptrend' if trend_up else '↓ downtrend' if trend_down else '↔ sideways'})"
        ),
    ))

    # ── RSI for momentum direction ────────────────────────────────────────────
    rsi_val = float(ind.rsi(df["close"], period=9).iloc[-1])
    # Bounce LONG: price below HVN (support) + RSI oversold + NOT in confirmed downtrend
    # Reject SHORT: price above HVN (resistance) + RSI overbought + NOT in confirmed uptrend
    bounce_setup  = dist_to_hvn < 0 and rsi_val < 45 and not trend_down
    reject_setup  = dist_to_hvn > 0 and rsi_val > 55 and not trend_up
    rsi_confirms  = bounce_setup or reject_setup
    conditions.append(StrategyCondition(
        name="RSI confirms",
        met=rsi_confirms,
        detail=f"RSI={rsi_val:.1f} ({'bounce ✅' if bounce_setup else 'reject ✅' if reject_setup else '⚠️ contra-trend blocked'})",
    ))

    # ── POC gravity: is price far from POC? ──────────────────────────────────
    dist_to_poc = abs(close - poc)
    conditions.append(StrategyCondition(
        name="POC reference",
        met=True,  # informational only
        detail=f"POC={poc:.0f}, dist={dist_to_poc:.0f}pts",
    ))

    all_ok = near_hvn and rsi_confirms
    if not all_ok:
        confidence = 30 if near_hvn else 10
        return StrategySignal(
            should_enter=False,
            conditions=conditions,
            confidence=confidence,
            reason=(
                f"HVN at {nearest_hvn:.0f} ({dist_to_hvn:+.0f}pts), "
                f"RSI={rsi_val:.1f} — "
                f"{'need RSI confirmation' if near_hvn else 'not near any HVN'}"
            ),
        )

    direction  = Direction.LONG if bounce_setup else Direction.SHORT
    confidence = min(90.0, 65 + (HVN_PROXIMITY - abs(dist_to_hvn)) * 1.5)

    return StrategySignal(
        should_enter=True,
        direction=direction,
        conditions=conditions,
        confidence=round(confidence, 1),
        reason=(
            f"Volume Profile: {'Bounce' if bounce_setup else 'Rejection'} at HVN {nearest_hvn:.0f} "
            f"| POC={poc:.0f} | RSI={rsi_val:.1f} "
            f"| {'Support' if bounce_setup else 'Resistance'} confirmed by 3-day profile"
        ),
    )


register(StrategyInfo(
    id="volume_profile",
    name="Volume Profile HVN",
    emoji="🏔️",
    description=(
        "Builds a 3-day volume profile and trades bounces/rejections at "
        "High Volume Nodes (HVN) — price levels where the most contracts "
        "were traded. HVNs act as strong support/resistance because many "
        "traders have positions there and will defend those levels."
    ),
    category="reversal",
    difficulty="advanced",
    market_condition="Any regime. Most reliable in range-bound or early-trend markets.",
    evaluate=evaluate_volume_profile,
    entry_rules=[
        "Build 3-day volume profile → find top-30% volume price levels (HVNs)",
        "Price approaches HVN from below → expect bounce UP (LONG)",
        "Price approaches HVN from above → expect rejection DOWN (SHORT)",
        "RSI < 45 confirms oversold bounce, RSI > 55 confirms overbought rejection",
        "Only between 9:45 and 14:45",
    ],
    exit_rules=[
        "Target: next HVN in the direction of trade",
        "Stop: beyond the HVN level (if it breaks, the level failed)",
    ],
    risk_tips=[
        "Requires Zerodha connection for real volume profile",
        "HVNs shift day by day — profile is rebuilt each candle",
        "POC (Point of Control) = highest volume price = strongest magnet",
        "LVNs (thin volume) = price moves through fast — don't set targets there",
    ],
    pros=[
        "Based on actual traded volume — not just price patterns",
        "Automatically adapts to the last 3 days of market structure",
        "POC acts as day's fair value — strong mean-reversion target",
    ],
    cons=[
        "Needs real volume — completely dead without Zerodha",
        "Slow to react to sudden regime changes",
        "Bucket granularity affects quality (40 buckets tuned for Nifty)",
    ],
    example_scenario=(
        "11:30 AM. 3-day profile shows HVN at 23,700 (massive volume cluster). "
        "Price dips to 23,695 — just below HVN. RSI = 38 (oversold). "
        "→ Volume Profile fires LONG. Target: next HVN at 23,780."
    ),
))
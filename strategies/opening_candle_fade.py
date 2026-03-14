"""Opening Candle Fade (OCF) Strategy.

Rajesh's manual strategy:
  1. 9:15 candle is BIG (large body) — strong opening move
  2. 9:20 candle is SMALL + volume DROPS vs 9:15
     → Market is exhausted, fade (reverse) the opening move
  3. Confluence: is the extreme near:
     a) Previous day's High or Low?
     b) A multi-day cluster (same level touched 2+ times in last 5 days)?

More confluence = higher confidence = take the trade.
"""

import pandas as pd
import numpy as np
from strategy import StrategySignal, StrategyCondition, Direction
from strategies.registry import register, StrategyInfo


# ── Tunable parameters ───────────────────────────────────────────
MIN_BODY_POINTS   = 15    # first candle body must be >= this to count as "big"
LEVEL_TOLERANCE   = 25    # pts within which a price is "at" a level
CLUSTER_TOUCHES   = 2     # min touches in 5-day history to call it a cluster
CLUSTER_ZONE      = 30    # pts — how close two touches must be to be in same zone


# ── Helpers ──────────────────────────────────────────────────────

def _candle_body(candle) -> float:
    return abs(float(candle["close"]) - float(candle["open"]))


def _candle_direction(candle) -> str:
    """'bull' if close > open, else 'bear'."""
    return "bull" if float(candle["close"]) >= float(candle["open"]) else "bear"


def _find_5day_clusters(df: pd.DataFrame, today) -> list[float]:
    """Find price levels touched 2+ times in the last 5 trading days.

    Strategy: collect all candle highs and lows from prev days,
    bin them into 30-pt zones, return zones with 2+ touches.
    """
    past = df[df.index.date < today].copy()
    if past.empty:
        return []

    # Collect every high and low across all candles in last 5 days
    levels: list[float] = []
    for _, row in past.iterrows():
        levels.append(float(row["high"]))
        levels.append(float(row["low"]))

    if not levels:
        return []

    # Cluster nearby levels into zones
    levels_sorted = sorted(levels)
    clusters: list[float] = []
    zone_start = levels_sorted[0]
    zone_prices = [zone_start]

    for price in levels_sorted[1:]:
        if price - zone_start <= CLUSTER_ZONE:
            zone_prices.append(price)
        else:
            if len(zone_prices) >= CLUSTER_TOUCHES:
                clusters.append(round(sum(zone_prices) / len(zone_prices), 2))
            zone_start = price
            zone_prices = [price]

    if len(zone_prices) >= CLUSTER_TOUCHES:
        clusters.append(round(sum(zone_prices) / len(zone_prices), 2))

    return clusters


def _near_level(price: float, levels: list[float], tol: float = LEVEL_TOLERANCE) -> tuple[bool, float | None]:
    """Returns (True, nearest_level) if price is within tol of any level."""
    for lvl in levels:
        if abs(price - lvl) <= tol:
            return True, lvl
    return False, None


# ── Main evaluate function ────────────────────────────────────────

def evaluate_ocf(df: pd.DataFrame) -> StrategySignal:
    """Opening Candle Fade evaluation."""
    conditions: list[StrategyCondition] = []

    if len(df) < 10:
        return StrategySignal(should_enter=False, reason="Not enough data")

    today = df.index[-1].date()
    today_df = df[df.index.date == today].copy()

    if len(today_df) < 2:
        return StrategySignal(should_enter=False, reason="Need at least 2 candles today")

    candle1 = today_df.iloc[0]   # 9:15 candle
    candle2 = today_df.iloc[1]   # 9:20 candle

    body1   = _candle_body(candle1)
    body2   = _candle_body(candle2)
    dir1    = _candle_direction(candle1)
    vol1    = float(candle1["volume"])
    vol2    = float(candle2["volume"])

    # Only evaluate at/after the 2nd candle (9:20)
    current_time = df.index[-1].time()
    from datetime import time as dt_time
    if current_time < dt_time(9, 20):
        return StrategySignal(should_enter=False, reason="Wait for 9:20 candle")

    # ── Condition 1: Big opening candle ──────────────────────────
    big_candle = body1 >= MIN_BODY_POINTS
    conditions.append(StrategyCondition(
        name="Big Opening Candle",
        met=big_candle,
        detail=(
            f"9:15 body={body1:.1f} pts "
            f"({'✅ big' if big_candle else f'❌ need >={MIN_BODY_POINTS}pts'}), "
            f"direction={'⬆ BULL' if dir1 == 'bull' else '⬇ BEAR'}"
        ),
    ))

    # ── Condition 2: 2nd candle body is SMALLER ───────────────────
    small_body2 = body2 < body1
    conditions.append(StrategyCondition(
        name="2nd Candle Smaller",
        met=small_body2,
        detail=(
            f"9:20 body={body2:.1f} pts vs 9:15 body={body1:.1f} pts "
            f"({'✅ smaller' if small_body2 else '❌ bigger — momentum continuing'}"
        ),
    ))

    # ── Condition 3: 2nd candle VOLUME drops ─────────────────────
    vol_data_ok = vol1 > 0 and vol2 > 0
    if vol_data_ok:
        vol_dropped = vol2 < vol1
        vol_ratio   = vol2 / vol1
        vol_detail  = f"9:20 vol={vol2:,.0f} vs 9:15 vol={vol1:,.0f} ({vol_ratio:.0%}) — {'✅ dropped' if vol_dropped else '❌ still high'}"
    else:
        vol_dropped = True  # index data has no volume — skip
        vol_detail  = "Volume N/A (index data) — skipped"

    conditions.append(StrategyCondition(
        name="Volume Drops",
        met=vol_dropped,
        detail=vol_detail,
    ))

    # ── Direction: fade the opening candle ───────────────────────
    # Bull 9:15 → SHORT (fade the rally)
    # Bear 9:15 → LONG  (fade the sell-off)
    direction = Direction.SHORT if dir1 == "bull" else Direction.LONG
    extreme   = float(candle1["high"]) if dir1 == "bull" else float(candle1["low"])

    # ── Condition 4: Near Previous Day High/Low ───────────────────
    prev_days = df[df.index.date < today]
    prev_day  = prev_days[prev_days.index.date == max(prev_days.index.date)] if not prev_days.empty else pd.DataFrame()

    if not prev_day.empty:
        pdh = float(prev_day["high"].max())
        pdl = float(prev_day["low"].min())
        pdc = float(prev_day["close"].iloc[-1])
        key_levels = [pdh, pdl, pdc]
        near_pd, which_pd = _near_level(extreme, key_levels)
        pd_detail = (
            f"Extreme ₹{extreme:.0f} near PDH={pdh:.0f} / PDL={pdl:.0f} / PDC={pdc:.0f} — "
            f"{'✅ YES (' + str(which_pd) + ')' if near_pd else '❌ no key level nearby'}"
        )
    else:
        near_pd = False
        pd_detail = "No previous day data"

    conditions.append(StrategyCondition(
        name="Near Prev Day Level",
        met=near_pd,
        detail=pd_detail,
        weight=2,   # extra weight — strong confluence
    ))

    # ── Condition 5: Multi-day cluster at this level ─────────────
    clusters = _find_5day_clusters(df, today)
    near_cluster, which_cluster = _near_level(extreme, clusters)
    conditions.append(StrategyCondition(
        name="5-Day Cluster Level",
        met=near_cluster,
        detail=(
            f"Extreme ₹{extreme:.0f} — "
            f"{'✅ hits cluster at ' + str(which_cluster) if near_cluster else '❌ no cluster here'}. "
            f"({len(clusters)} clusters found in last 5 days)"
        ),
        weight=2,   # extra weight
    ))

    # ── Score + decision ─────────────────────────────────────────
    # Required: conditions 1+2+3 ALL must be met
    # Bonus: condition 4 or 5 adds confluence (at least one)
    core_met    = big_candle and small_body2 and vol_dropped
    confluence  = near_pd or near_cluster
    all_met     = core_met and confluence

    weighted    = [c for c in conditions if c.weight > 0]
    total_w     = sum(c.weight for c in weighted)
    met_w       = sum(c.weight for c in weighted if c.met)
    confidence  = (met_w / total_w * 100) if total_w > 0 else 0

    reason_parts = []
    if not big_candle:   reason_parts.append(f"Opening candle too small ({body1:.0f}pts)")
    if not small_body2:  reason_parts.append("2nd candle bigger — momentum continuing")
    if not vol_dropped and vol_data_ok:  reason_parts.append("Volume didn't drop")
    if not confluence:   reason_parts.append("No level confluence (no PDH/PDL/cluster nearby)")

    reason = (
        f"OCF {'ENTRY' if all_met else 'NO ENTRY'}: {direction.value.upper()} | "
        f"confidence={confidence:.0f}% | "
        + ("ALL conditions met" if all_met else " + ".join(reason_parts))
    )

    return StrategySignal(
        should_enter=all_met,
        direction=direction if all_met else direction,
        confidence=confidence,
        conditions=conditions,
        reason=reason,
    )


# ── Register ─────────────────────────────────────────────────────
register(StrategyInfo(
    id="ocf",
    name="Opening Candle Fade",
    emoji="🕯️",
    description=(
        "Fade the big opening candle when the 2nd candle shows exhaustion "
        "(smaller body + dropping volume), especially when the extreme hits "
        "a key level (Previous Day High/Low or 5-day cluster)."
    ),
    category="reversal",
    difficulty="intermediate",
    market_condition="Best on gap-and-trap days. Avoid on strong news-driven trend days.",
    evaluate=evaluate_ocf,
    entry_rules=[
        "9:15 candle body >= 15 pts (big opening move)",
        "9:20 candle body is SMALLER than 9:15 candle (momentum fading)",
        "9:20 candle volume is LESS than 9:15 candle (sellers/buyers exhausted)",
        "Opening candle's extreme (high for bull, low for bear) is near:",
        "  → Previous day's High or Low or Close (within 25 pts), OR",
        "  → A 5-day cluster level (same zone touched 2+ times)",
        "Direction: SHORT if 9:15 was bullish, LONG if 9:15 was bearish",
    ],
    exit_rules=[
        "Stop-loss: Above the HIGH of the 9:15 candle (for SHORT)",
        "Stop-loss: Below the LOW of the 9:15 candle (for LONG)",
        "Target: 1.5x or 2x the opening candle size",
        "Force exit at 3:15 PM",
    ],
    risk_tips=[
        "Works BEST when the opening candle hits a previous day high/low — classic trap",
        "If both PDL/PDH AND a 5-day cluster line up — very high probability",
        "Avoid if 9:20 candle continues in the same direction as 9:15 (momentum is real)",
        "News days (budget, Fed, results) = gaps don't fade, skip those days",
    ],
    pros=[
        "Only need to watch 2 candles — simple decision",
        "Entry at 9:20 AM — full day to run",
        "Stop-loss is clearly defined (high/low of opening candle)",
        "Level confluence filters out bad trades",
    ],
    cons=[
        "Only one entry per day (morning only)",
        "Strong trend days — fade will get stopped out",
        "Requires previous day data for level checks",
    ],
    example_scenario=(
        "Market opens at 23,400. 9:15 candle: HIGH=23,480, body=60pts (bullish). "
        "Previous day HIGH was 23,475 — extreme is at a key level! "
        "9:20 candle: body=15pts, volume dropped 40%. "
        "→ SHORT at 23,430 (9:20 close), SL=23,490 (9:15 high), Target=23,340."
    ),
))
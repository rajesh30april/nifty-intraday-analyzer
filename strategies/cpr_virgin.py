"""CPR Virgin Play — Central Pivot Range Price Magnet.

If today's price has NOT yet touched the CPR zone (between BC and TC),
CPR acts as a strong price magnet. Trade TOWARD it.

Widely used by Indian professional traders. Virgin CPR is one of the
highest-probability intraday setups on Nifty Futures.

Logic:
  Price above TC (untouched CPR below) → SHORT toward TC
  Price below BC (untouched CPR above) → LONG toward BC
"""

from __future__ import annotations

import pandas as pd
from strategy import StrategySignal, StrategyCondition, Direction
import indicators as ind
from strategies.registry import register, StrategyInfo

_MIN_DIST_PCT  = 0.10   # must be > 0.10% away from CPR (not already on it)
_MAX_DIST_PCT  = 0.80   # must be < 0.80% away (target reachable same day)


def evaluate_cpr_virgin(df: pd.DataFrame) -> StrategySignal:
    """CPR Virgin Play."""
    NO = lambda r, c=[]: StrategySignal(should_enter=False, reason=r, conditions=c)

    if len(df) < 10:
        return NO("Need 10+ candles")

    today    = df.index[-1].date()
    today_df = df[df.index.date == today]
    prev_df  = df[df.index.date < today]

    if prev_df.empty or len(today_df) < 2:
        return NO("Need previous day data + 2 today candles")

    prev_h = float(prev_df["high"].max())
    prev_l = float(prev_df["low"].min())
    prev_c = float(prev_df["close"].iloc[-1])

    cpr   = ind.central_pivot_range(prev_h, prev_l, prev_c)
    tc    = cpr["tc"]
    bc    = cpr["bc"]
    price = float(df["close"].iloc[-1])

    conditions: list[StrategyCondition] = []

    # ── Condition 1: Virgin CPR — no candle has entered CPR zone today ──
    today_lows  = today_df["low"].values
    today_highs = today_df["high"].values

    if price > tc:
        # Price above CPR — check no candle dipped below TC into CPR
        is_virgin = all(low >= tc for low in today_lows)
        direction = Direction.SHORT
        nearest_edge = tc
        virgin_detail = (
            f"Price {price:.0f} above CPR (TC={tc:.0f}, BC={bc:.0f}) "
            f"— {'✅ untouched (VIRGIN)' if is_virgin else '❌ CPR already touched today'}"
        )
    elif price < bc:
        # Price below CPR — check no candle rose above BC into CPR
        is_virgin = all(high <= bc for high in today_highs)
        direction = Direction.LONG
        nearest_edge = bc
        virgin_detail = (
            f"Price {price:.0f} below CPR (TC={tc:.0f}, BC={bc:.0f}) "
            f"— {'✅ untouched (VIRGIN)' if is_virgin else '❌ CPR already touched today'}"
        )
    else:
        # Price INSIDE the CPR zone — no trade
        return NO(
            f"Price {price:.0f} inside CPR zone ({bc:.0f}–{tc:.0f}) — wait for breakout",
            [],
        )

    conditions.append(StrategyCondition(
        name="Virgin CPR",
        met=is_virgin,
        detail=virgin_detail,
        weight=3,
    ))

    # ── Condition 2: Distance check — not too close, not too far ────────
    dist_pts = abs(price - nearest_edge)
    dist_pct = dist_pts / price * 100

    dist_ok = _MIN_DIST_PCT <= dist_pct <= _MAX_DIST_PCT
    conditions.append(StrategyCondition(
        name="Distance to CPR",
        met=dist_ok,
        detail=(
            f"Price {dist_pts:.0f}pts ({dist_pct:.2f}%) from CPR edge — "
            f"need {_MIN_DIST_PCT}%–{_MAX_DIST_PCT}%: "
            f"{'✅' if dist_ok else '❌ too close or too far'}"
        ),
        weight=2,
    ))

    # ── Condition 3: Candle moving TOWARD CPR ─────────────────────────
    curr   = df.iloc[-1]
    c_open = float(curr["open"])
    c_cls  = float(curr["close"])

    toward = (direction == Direction.SHORT and c_cls < c_open) or \
             (direction == Direction.LONG  and c_cls > c_open)

    conditions.append(StrategyCondition(
        name="Moving Toward CPR",
        met=toward,
        detail=(
            f"Candle {'⬇ bearish' if c_cls < c_open else '⬆ bullish'} — "
            f"{'✅ moving toward CPR' if toward else '❌ moving away from CPR'}"
        ),
        weight=2,
    ))

    # ── Condition 4: Time filter — before 14:30 ────────────────────────
    from datetime import time as dt_time
    curr_time = df.index[-1].time()
    time_ok   = curr_time <= dt_time(14, 30)
    conditions.append(StrategyCondition(
        name="Time Filter",
        met=time_ok,
        detail=f"{curr_time.strftime('%H:%M')} — {'✅ enough time to reach CPR' if time_ok else '❌ too late'}",
        weight=1,
    ))

    # ── Score ────────────────────────────────────────────────────────────
    total_w = sum(c.weight for c in conditions)
    met_w   = sum(c.weight for c in conditions if c.met)
    conf    = round(met_w / total_w * 100, 1) if total_w > 0 else 0
    all_met = all(c.met for c in conditions)

    return StrategySignal(
        should_enter=all_met,
        direction=direction,
        confidence=conf,
        conditions=conditions,
        reason=(
            f"CPR VIRGIN {'ENTRY' if all_met else 'NO ENTRY'}: {direction.value.upper()} | "
            f"conf={conf:.0f}% | price={price:.0f} → CPR ({bc:.0f}–{tc:.0f}) "
            f"| dist={dist_pts:.0f}pts ({dist_pct:.2f}%)"
        ),
    )


register(StrategyInfo(
    id="cpr_virgin",
    name="CPR Virgin Play",
    emoji="🧲",
    description=(
        "If today's price has never touched the Central Pivot Range (CPR), "
        "the CPR acts like a price magnet. Trade TOWARD CPR until it gets touched. "
        "One of the most reliable setups among Indian professional traders."
    ),
    category="reversal",
    difficulty="intermediate",
    market_condition="Works any day — strongest when CPR is narrow (< 0.2% of price).",
    evaluate=evaluate_cpr_virgin,
    entry_rules=[
        "Calculate CPR (TC/BC) from previous day H/L/C",
        "Price must be outside CPR zone (above TC or below BC)",
        "CPR must be VIRGIN — no candle has entered the zone yet today",
        "Price must be 0.10%–0.80% away from nearest CPR edge",
        "Current candle must be moving TOWARD CPR",
        "Enter before 14:30 (needs time to reach target)",
    ],
    exit_rules=[
        "Target: the CPR zone itself (TC if coming from above, BC if from below)",
        "Stop-loss: 1.5× the current distance beyond entry",
        "Exit immediately if price enters CPR zone and bounces back through",
    ],
    risk_tips=[
        "Once CPR is touched, this setup is DONE for the day — don't re-enter",
        "Narrow CPR (< 0.2%) = stronger magnet. Wide CPR = weaker pull.",
        "News events can override CPR magnet — check for scheduled events",
    ],
    pros=[
        "Very high probability when CPR is truly virgin",
        "Clear, mathematical target (the CPR zone itself)",
        "Popular among NSE professional traders — self-fulfilling signal",
    ],
    cons=[
        "Only fires once per day per direction",
        "Won't work if market opens inside CPR",
        "Wide CPR days = weaker signal",
    ],
    example_scenario=(
        "Prev day: H=23,800 L=23,500 C=23,650. CPR: TC=23,683 BC=23,650. "
        "Today price opens at 23,750 (above TC). 10:15 AM — price still above TC "
        "and no candle has dipped into CPR zone (VIRGIN). Bearish candle forms. "
        "→ SHORT at 23,750. Target=23,683 (TC). SL=23,800."
    ),
))

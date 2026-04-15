"""Opening Gap Fill Strategy.

Small gaps (< 0.30%) on Nifty tend to fill ~75% of the time.
This is the OPPOSITE of Gap-and-Go: fade the small gap back toward prev close.

Data basis: Nifty historical analysis
  - Gaps > 0.5%  → continue in gap direction 89% of the time (Gap-and-Go)
  - Gaps < 0.30% → fill back to prev close ~75% of the time (this strategy)

The two strategies are complementary: Gap-and-Go handles big gaps,
Gap Fill handles small ones. They never conflict.
"""

from __future__ import annotations

import pandas as pd
from strategy import StrategySignal, StrategyCondition, Direction
from strategies.registry import register, StrategyInfo

_MIN_GAP_PCT = 0.04   # ignore micro-gaps (noise, < 0.04%)
_MAX_GAP_PCT = 0.30   # above this → Gap-and-Go territory (won't fill)
_MIN_GAP_PTS = 8      # absolute minimum gap size in points


def evaluate_gap_fill(df: pd.DataFrame) -> StrategySignal:
    """Opening Gap Fill — trade AGAINST a small gap back toward prev close."""
    NO = lambda r, c=[]: StrategySignal(should_enter=False, reason=r, conditions=c)

    if len(df) < 5:
        return NO("Need 5+ candles")

    today    = df.index[-1].date()
    today_df = df[df.index.date == today]
    prev_df  = df[df.index.date < today]

    if today_df.empty or prev_df.empty:
        return NO("Need previous day data")

    prev_close = float(prev_df["close"].iloc[-1])
    today_open = float(today_df["open"].iloc[0])
    price      = float(df["close"].iloc[-1])

    gap_pts = today_open - prev_close
    gap_pct = abs(gap_pts) / prev_close * 100
    gap_up  = gap_pts > 0

    conditions: list[StrategyCondition] = []

    # ── Condition 1: Gap is small (fill territory) ────────────────────
    gap_ok = _MIN_GAP_PCT <= gap_pct <= _MAX_GAP_PCT and abs(gap_pts) >= _MIN_GAP_PTS
    conditions.append(StrategyCondition(
        name="Small Gap (Fill Territory)",
        met=gap_ok,
        detail=(
            f"Gap {gap_pts:+.0f}pts ({gap_pct:.2f}%) vs prev close {prev_close:.0f} "
            f"— {'✅ small gap = fill likely' if gap_ok else '❌ gap too large (use Gap-and-Go) or too tiny (noise)'}"
        ),
        weight=3,
    ))
    if not gap_ok:
        return NO(
            f"Gap {gap_pct:.2f}% outside fill zone ({_MIN_GAP_PCT}%–{_MAX_GAP_PCT}%)",
            conditions,
        )

    # ── Condition 2: Already started filling (candle moving toward prev close)
    # Gap fill direction: gap-up → price needs to come DOWN → SHORT
    #                    gap-down → price needs to come UP   → LONG
    fill_direction = Direction.SHORT if gap_up else Direction.LONG

    curr   = df.iloc[-1]
    c_open = float(curr["open"])
    c_cls  = float(curr["close"])

    filling = (fill_direction == Direction.SHORT and c_cls < c_open) or \
              (fill_direction == Direction.LONG  and c_cls > c_open)

    conditions.append(StrategyCondition(
        name="Filling Already Started",
        met=filling,
        detail=(
            f"Current candle {'⬇ bearish' if c_cls < c_open else '⬆ bullish'} "
            f"— {'✅ moving toward prev close fill' if filling else '❌ still gapping away'}"
        ),
        weight=2,
    ))

    # ── Condition 3: Gap not yet filled (target still exists) ─────────
    if gap_up:
        # gap-up: fill = reach prev_close (below current price)
        already_filled = price <= prev_close
    else:
        # gap-down: fill = reach prev_close (above current price)
        already_filled = price >= prev_close

    target_exists = not already_filled
    remaining_pts = abs(price - prev_close)
    conditions.append(StrategyCondition(
        name="Gap Not Yet Filled",
        met=target_exists,
        detail=(
            f"Prev close={prev_close:.0f} | current={price:.0f} | "
            f"remaining={remaining_pts:.0f}pts "
            f"{'✅ target exists' if target_exists else '❌ gap already filled!'}"
        ),
        weight=2,
    ))

    # ── Condition 4: Early session — gap fills happen early or not at all
    from datetime import time as dt_time
    curr_time = df.index[-1].time()
    time_ok   = curr_time <= dt_time(10, 30)
    conditions.append(StrategyCondition(
        name="Early Session",
        met=time_ok,
        detail=(
            f"{curr_time.strftime('%H:%M')} — "
            f"{'✅ early (fills happen by 10:30 or not at all)' if time_ok else '❌ too late for gap fill'}"
        ),
        weight=1,
    ))

    # ── Score ─────────────────────────────────────────────────────────
    total_w = sum(c.weight for c in conditions)
    met_w   = sum(c.weight for c in conditions if c.met)
    conf    = round(met_w / total_w * 100, 1) if total_w > 0 else 0
    all_met = all(c.met for c in conditions)

    return StrategySignal(
        should_enter=all_met,
        direction=fill_direction,
        confidence=conf,
        conditions=conditions,
        reason=(
            f"GAP FILL {'ENTRY' if all_met else 'NO ENTRY'}: {fill_direction.value.upper()} | "
            f"conf={conf:.0f}% | gap={gap_pts:+.0f}pts ({gap_pct:.2f}%) → target prev_close={prev_close:.0f}"
        ),
    )


register(StrategyInfo(
    id="gap_fill",
    name="Opening Gap Fill",
    emoji="🔄",
    description=(
        "Small gaps (0.04%–0.30%) on Nifty tend to fill back to previous close ~75% of the time. "
        "Fades the gap direction — the OPPOSITE of Gap-and-Go. "
        "Both strategies coexist: Gap-and-Go handles large gaps, this handles small ones."
    ),
    category="reversal",
    difficulty="beginner",
    market_condition="Works on low-to-medium volatility days with small pre-market moves.",
    evaluate=evaluate_gap_fill,
    entry_rules=[
        "Gap must be between 0.04% and 0.30% of price (and ≥ 8pts absolute)",
        "Larger gaps (> 0.30%) → use Gap-and-Go, not this strategy",
        "Current candle must be moving TOWARD previous close (fill started)",
        "Gap must not already be filled (target still exists)",
        "Enter before 10:30 AM (fills happen early or not at all)",
    ],
    exit_rules=[
        "Target: previous session's close price",
        "Stop-loss: beyond today's opening price (gap direction extreme)",
        "Exit flat if not filled by 10:30 AM — abandon",
    ],
    risk_tips=[
        "News events turn small gaps into big ones — check calendar before entry",
        "If gap starts accelerating AWAY from prev close → exit immediately",
        "Only trade in the FILL direction — never trade with the gap",
    ],
    pros=[
        "Clear, mathematical target (previous close)",
        "Complementary to Gap-and-Go — no conflicts",
        "High historical fill rate for small Nifty gaps",
    ],
    cons=[
        "Small gaps = small targets (8–50pts typically)",
        "News can convert a fill day into a gap-and-go day instantly",
    ],
    example_scenario=(
        "Prev close = 23,500. Nifty opens at 23,528 (+28pts, +0.12%). "
        "9:20 candle is bearish (filling). "
        "→ SHORT at 23,520. Target=23,500 (prev close). SL=23,540 (above open). "
        "20pt target, 20pt risk — 1:1 R:R but 75% win rate = positive EV."
    ),
))

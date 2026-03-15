"""Previous Day High / Low (PDH/PDL) Breakout Strategy.

The most-watched levels on any intraday chart are yesterday's High and Low.
Every institution, algo, and retail trader has them marked.

Strategy logic:
  1. Price closes ABOVE PDH → LONG  (institutional buying confirmed)
  2. Price closes BELOW PDL → SHORT (distribution / selling confirmed)
  3. Volume surge on the breakout candle (not a fake-out poke)
  4. Breakout must be clean — not a wick, but a candle BODY crossing the level
  5. Trade is valid at any time of day, not just morning

Why PDH/PDL > ORB:
  ORB range is random — depends on what the first 15 min happen to do.
  PDH/PDL are pre-calculated, significant, widely-watched — when they break,
  the move tends to be real and sustainable.
"""

import pandas as pd
from datetime import datetime, time as dt_time
from strategy import StrategySignal, StrategyCondition, Direction
from strategies.registry import register, StrategyInfo


# ── Tunable parameters ───────────────────────────────────────────────────────
MIN_BREAKOUT_PTS   = 3     # body must close at least this many pts beyond level
VOLUME_RATIO_MIN   = 1.15  # breakout candle volume >= 1.15× recent avg
MAX_ENTRY_HOUR     = dt_time(14, 30)  # too late after 2:30 PM (auto-exit at 3:15)


def evaluate_pdhl_breakout(df: pd.DataFrame) -> StrategySignal:
    """Previous Day High/Low Breakout.

    Entry conditions:
    1. Previous day's High and Low clearly identifiable
    2. Today's price has broken OUT of the previous day's range
    3. Breakout candle body (not wick) closes beyond PDH or PDL
    4. Volume on breakout candle >= 1.15× recent average
    5. Not too late in the day (before 2:30 PM)
    """
    conditions: list[StrategyCondition] = []

    if len(df) < 10:
        return StrategySignal(should_enter=False, reason="Insufficient data")

    # ── Wall clock check ────────────────────────────────────────────────────
    now = datetime.now()
    if now.time() > MAX_ENTRY_HOUR:
        return StrategySignal(
            should_enter=False,
            reason=f"Too late for PDH/PDL trade (after {MAX_ENTRY_HOUR.strftime('%H:%M')})",
        )

    # ── Identify previous day's range ───────────────────────────────────────
    today       = now.date()
    today_df    = df[df.index.date == today]
    prev_df     = df[df.index.date < today]

    if prev_df.empty or today_df.empty:
        return StrategySignal(should_enter=False, reason="Need both previous and today data")

    # Use only the most recent previous trading day (not all history)
    prev_day_date = max(prev_df.index.date)
    prev_day_df   = prev_df[prev_df.index.date == prev_day_date]

    pdh = float(prev_day_df["high"].max())
    pdl = float(prev_day_df["low"].min())
    pdr = pdh - pdl  # previous day range

    conditions.append(StrategyCondition(
        name="PDH/PDL Identified",
        met=True,
        detail=f"PDH={pdh:.0f} | PDL={pdl:.0f} | PDR={pdr:.0f} pts",
    ))

    # ── Current candle ──────────────────────────────────────────────────────
    curr  = df.iloc[-1]
    close = float(curr["close"])
    open_ = float(curr["open"])
    high  = float(curr["high"])
    low   = float(curr["low"])

    # Body = min(open, close) to max(open, close)
    body_low  = min(open_, close)
    body_high = max(open_, close)

    # ── Condition 2: Breakout (body, not wick) ──────────────────────────────
    broke_above = body_low > pdh and close > pdh + MIN_BREAKOUT_PTS
    broke_below = body_high < pdl and close < pdl - MIN_BREAKOUT_PTS
    breakout_ok = broke_above or broke_below
    direction   = Direction.LONG if broke_above else Direction.SHORT

    conditions.append(StrategyCondition(
        name="Clean Breakout",
        met=breakout_ok,
        detail=(
            f"Close={close:.0f} | PDH={pdh:.0f} | PDL={pdl:.0f} | "
            f"{'✅ Body above PDH' if broke_above else '✅ Body below PDL' if broke_below else '❌ Inside previous day range'}"
        ),
    ))

    # ── Condition 3: Volume surge ────────────────────────────────────────────
    vol_col_exists = "volume" in df.columns
    if vol_col_exists:
        curr_vol = float(curr["volume"])
        avg_vol  = float(df["volume"].rolling(14).mean().iloc[-1])
        vol_ok   = avg_vol > 0 and curr_vol >= avg_vol * VOLUME_RATIO_MIN
        vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 0
        vol_detail = (
            f"Vol={curr_vol:,.0f} | Avg={avg_vol:,.0f} | "
            f"Ratio={vol_ratio:.2f}× — {'✅ surge' if vol_ok else '❌ weak volume'}"
        )
    else:
        vol_ok     = True   # index data — skip
        vol_detail = "Volume N/A (index data) — skipped"

    conditions.append(StrategyCondition(
        name="Volume Surge",
        met=vol_ok,
        detail=vol_detail,
    ))

    # ── Condition 4: VWAP alignment ─────────────────────────────────────────
    import indicators as _ind
    _vwap_series = _ind.vwap(
        today_df["high"], today_df["low"], today_df["close"], today_df["volume"]
    )
    vwap_val = float(_vwap_series.iloc[-1]) if not _vwap_series.empty else None
    if vwap_val is not None and not pd.isna(vwap_val):
        vwap_ok = (broke_above and close > vwap_val) or (broke_below and close < vwap_val)
        vwap_detail = (
            f"VWAP={vwap_val:.0f} | Close={close:.0f} — "
            f"{'✅ above VWAP (long confirmed)' if broke_above and close > vwap_val else '✅ below VWAP (short confirmed)' if broke_below and close < vwap_val else '❌ wrong side of VWAP'}"
        )
    else:
        vwap_ok     = True
        vwap_detail = "VWAP N/A — skipped"

    conditions.append(StrategyCondition(
        name="VWAP Alignment",
        met=vwap_ok,
        detail=vwap_detail,
    ))

    # ── Score ────────────────────────────────────────────────────────────────
    all_met    = breakout_ok and vol_ok and vwap_ok
    total_cond = len(conditions)
    met_cond   = sum(1 for c in conditions if c.met)
    confidence = met_cond / total_cond * 100

    blocks = []
    if not breakout_ok: blocks.append("Price inside previous day range")
    if not vol_ok:      blocks.append(f"Weak volume ({vol_ratio:.2f}× avg, need {VOLUME_RATIO_MIN}×)")
    if not vwap_ok:     blocks.append("Wrong side of VWAP")

    reason = (
        f"PDH/PDL {'ENTRY' if all_met else 'NO ENTRY'}: {direction.value.upper() if breakout_ok else '?'} | "
        f"confidence={confidence:.0f}% | PDH={pdh:.0f} PDL={pdl:.0f} | "
        + ("ALL conditions met" if all_met else " + ".join(blocks))
    )

    return StrategySignal(
        should_enter=all_met,
        direction=direction if breakout_ok else Direction.LONG,
        confidence=confidence,
        conditions=conditions,
        reason=reason,
    )


# ── Register ─────────────────────────────────────────────────────────────────
register(StrategyInfo(
    id="pdhl_breakout",
    name="PDH/PDL Breakout",
    emoji="📏",
    description=(
        "Trades breakouts of the Previous Day's High (PDH) or Low (PDL). "
        "These are the most-watched levels on any chart — when they break with "
        "volume, the move is real. Works at any time of day."
    ),
    category="breakout",
    difficulty="beginner",
    market_condition="Best on trending or news-driven days. Avoid on flat/consolidating days.",
    evaluate=evaluate_pdhl_breakout,
    entry_rules=[
        "Identify previous day's High and Low (pre-calculated, no candles needed)",
        "Wait for a candle BODY (not just wick) to close above PDH or below PDL",
        "Body must close at least 3 pts beyond the level (filters fake pokes)",
        "Volume on breakout candle >= 1.15× 14-period average volume",
        "Price must be on the correct side of VWAP (above for long, below for short)",
    ],
    exit_rules=[
        "Stop-loss: Back inside the previous day's range (below PDH for long)",
        "Target: PDH + PDR (for long) = PDH + yesterday's full range",
        "Auto-exit at 3:15 PM",
    ],
    risk_tips=[
        "PDH/PDL breakouts fail ~30% of the time — always use SL",
        "If price breaks PDH but closes back below → exit immediately (false breakout)",
        "Best when PDH/PDL coincides with a round number or ORB level",
        "Don't trade this on budget day / RBI policy day — levels get blown past",
    ],
    pros=[
        "Pre-calculated levels — know the trade setup the night before",
        "Works at any time of day, not just morning",
        "Very clean SL definition",
        "Widely watched by institutions = self-fulfilling momentum",
    ],
    cons=[
        "False breakouts happen — need volume confirmation",
        "On range-bound days PDH/PDL may not break at all",
    ],
    example_scenario=(
        "Yesterday: High=23,500, Low=23,350. Today at 11:30 AM: "
        "candle body closes at 23,515 (above PDH=23,500) with volume 1.3× avg. "
        "VWAP=23,430 — price is above it. → LONG at 23,515. "
        "SL=23,490 (back inside range). Target=23,650 (PDH + PDR=150 pts)."
    ),
))
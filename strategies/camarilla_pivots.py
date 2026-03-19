"""Camarilla Pivot Points Strategy.

Camarilla pivots are based on previous day H/L/C and use the
1.1 multiplier formula. Very popular for intraday S/R levels.

Classic rules:
  Price at H3 → short targeting L3 (range-bound day)
  Price breaks above H4 → strong long (trending breakout)
  Price at L3 → long targeting H3 (range-bound day)
  Price breaks below L4 → strong short (trending breakdown)
"""

import pandas as pd
import indicators as ind
from strategy import StrategySignal, StrategyCondition, Direction
from strategies.registry import register, StrategyInfo


TOLERANCE     = 20    # pts — how close price must be to a level
MIN_CANDLES   = 5     # need at least this many today's candles


def evaluate_camarilla(df: pd.DataFrame) -> StrategySignal:
    """Camarilla Pivot Points strategy.

    Two modes:
    A) REVERSAL at H3/L3 (range-bound day) — fade the level
    B) BREAKOUT above H4 / below L4 (trending day) — ride the break
    """
    conditions: list[StrategyCondition] = []

    if len(df) < 10:
        return StrategySignal(should_enter=False, reason="Insufficient data")

    today    = df.index[-1].date()
    today_df = df[df.index.date == today]
    prev_df  = df[df.index.date < today]

    if prev_df.empty or today_df.empty:
        return StrategySignal(should_enter=False, reason="Need previous day data")

    # Previous day values
    prev_h = float(prev_df["high"].max())
    prev_l = float(prev_df["low"].min())
    prev_c = float(prev_df["close"].iloc[-1])

    # Camarilla levels
    lvls  = ind.camarilla_pivots(prev_h, prev_l, prev_c)
    price = float(df["close"].iloc[-1])
    rng   = prev_h - prev_l

    # ── Detect mode ──────────────────────────────────────────────
    # Breakout mode: price has pushed through H4 or L4
    broke_h4 = price > lvls["H4"]
    broke_l4 = price < lvls["L4"]

    # Reversal mode: price is near H3 or L3
    near_h3 = abs(price - lvls["H3"]) <= TOLERANCE
    near_l3 = abs(price - lvls["L3"]) <= TOLERANCE

    mode = (
        "breakout_long"  if broke_h4 else
        "breakout_short" if broke_l4 else
        "reversal_short" if near_h3 else
        "reversal_long"  if near_l3 else
        "none"
    )

    # ── Condition 1: At a key Camarilla level ─────────────────────
    at_level = mode != "none"
    level_detail = (
        f"H4={lvls['H4']:.0f} H3={lvls['H3']:.0f} "
        f"L3={lvls['L3']:.0f} L4={lvls['L4']:.0f} | "
        f"Price={price:.0f} | Mode={mode.upper()}"
    )
    conditions.append(StrategyCondition(
        name="At Camarilla Level",
        met=at_level,
        detail=level_detail,
        weight=2,
    ))

    # ── Condition 2: RSI confirms ─────────────────────────────────
    rsi_vals = ind.rsi(df["close"], 14)
    rsi_now  = float(rsi_vals.iloc[-1]) if not pd.isna(rsi_vals.iloc[-1]) else 50

    if mode == "reversal_short":
        rsi_ok = rsi_now > 55    # overbought at H3 resistance → short
        rsi_detail = f"RSI={rsi_now:.0f} ({'✅ overbought — good for reversal short' if rsi_ok else '❌ not overbought yet'})"
    elif mode == "reversal_long":
        rsi_ok = rsi_now < 45    # oversold at L3 support → long
        rsi_detail = f"RSI={rsi_now:.0f} ({'✅ oversold — good for reversal long' if rsi_ok else '❌ not oversold yet'})"
    elif mode == "breakout_long":
        rsi_ok = rsi_now > 50    # momentum above 50 confirms breakout thrust
        rsi_detail = f"RSI={rsi_now:.0f} ({'✅ momentum confirms breakout long' if rsi_ok else '❌ weak momentum for breakout'})"
    elif mode == "breakout_short":
        rsi_ok = rsi_now < 50    # momentum below 50 confirms breakdown
        rsi_detail = f"RSI={rsi_now:.0f} ({'✅ momentum confirms breakout short' if rsi_ok else '❌ weak momentum for breakout'})"
    else:
        rsi_ok = False
        rsi_detail = "RSI check N/A"

    conditions.append(StrategyCondition(
        name="RSI Confirmation",
        met=rsi_ok,
        detail=rsi_detail,
    ))

    # ── Condition 3: Candle type confirms ────────────────────────
    # REVERSAL modes (H3/L3): need a direction-flip candle — you're fading
    #   the level so you need the candle to show the turn.
    # BREAKOUT modes (H4/L4): price already broke and is well beyond the
    #   level. A single bullish candle inside a downtrend (or bearish inside
    #   an uptrend) is just noise. Requiring a bearish candle here blocked
    #   valid entries 382pts into a breakdown (price=23255, L4=23637).
    #   For breakouts, just confirm the close stays on the right side of
    #   the H4/L4 level — the breakout itself is the confirmation.
    curr      = df.iloc[-1]
    c_bull    = float(curr["close"]) > float(curr["open"])
    c_close   = float(curr["close"])

    if mode == "reversal_short":
        candle_ok    = not c_bull
        candle_detail = f"{'✅ bearish candle confirms reversal short' if candle_ok else '❌ bullish candle — wait for rejection'}"
    elif mode == "reversal_long":
        candle_ok    = c_bull
        candle_detail = f"{'✅ bullish candle confirms reversal long' if candle_ok else '❌ bearish candle — wait for bounce'}"
    elif mode == "breakout_short":
        # Close must stay below L4 — the breakdown is the signal, not the candle colour
        candle_ok    = c_close < lvls["L4"]
        candle_detail = (
            f"✅ close {c_close:.0f} below L4 {lvls['L4']:.0f} — breakdown confirmed"
            if candle_ok else
            f"❌ close {c_close:.0f} reclaimed L4 {lvls['L4']:.0f} — breakdown failed"
        )
    elif mode == "breakout_long":
        # Close must stay above H4 — the breakout is the signal
        candle_ok    = c_close > lvls["H4"]
        candle_detail = (
            f"✅ close {c_close:.0f} above H4 {lvls['H4']:.0f} — breakout confirmed"
            if candle_ok else
            f"❌ close {c_close:.0f} fell back below H4 {lvls['H4']:.0f} — breakout failed"
        )
    else:
        candle_ok    = False
        candle_detail = "N/A"

    conditions.append(StrategyCondition(
        name="Candle Confirmation",
        met=candle_ok,
        detail=candle_detail,
    ))

    # ── Direction + score ─────────────────────────────────────────
    direction = (
        Direction.LONG  if "long"  in mode else
        Direction.SHORT if "short" in mode else
        Direction.SHORT  # fallback
    )

    all_met    = at_level and all(c.met for c in conditions)
    total_w    = sum(c.weight for c in conditions)
    met_w      = sum(c.weight for c in conditions if c.met)
    confidence = (met_w / total_w * 100) if total_w > 0 else 0

    return StrategySignal(
        should_enter=all_met,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=(
            f"CAMARILLA: {mode.upper()} | {direction.value.upper()} | "
            f"price={price:.0f} vs H3={lvls['H3']:.0f}/H4={lvls['H4']:.0f} "
            f"L3={lvls['L3']:.0f}/L4={lvls['L4']:.0f} | "
            f"{'ALL met' if all_met else 'NOT all met'}"
        ),
    )


register(StrategyInfo(
    id="camarilla",
    name="Camarilla Pivots",
    emoji="🎯",
    description=(
        "Uses previous day H/L/C to calculate H1-H4 and L1-L4 Camarilla levels. "
        "Trades reversals at H3/L3 (range days) and breakouts above H4/below L4 "
        "(trending days). Very precise intraday S/R for Nifty futures."
    ),
    category="reversal",
    difficulty="intermediate",
    market_condition="H3/L3 reversal on sideways days. H4/L4 breakout on trending days.",
    evaluate=evaluate_camarilla,
    entry_rules=[
        "Calculate H4, H3, L3, L4 from prev day High/Low/Close",
        "REVERSAL: Price at H3 (within 20pts) + RSI > 55 + bearish candle → SHORT",
        "REVERSAL: Price at L3 (within 20pts) + RSI < 45 + bullish candle → LONG",
        "BREAKOUT: Price breaks above H4 + bullish candle → LONG",
        "BREAKOUT: Price breaks below L4 + bearish candle → SHORT",
    ],
    exit_rules=[
        "Reversal SHORT at H3: target L3, SL above H4",
        "Reversal LONG at L3: target H3, SL below L4",
        "Breakout LONG above H4: trailing SL, no fixed target",
        "Breakout SHORT below L4: trailing SL, no fixed target",
    ],
    risk_tips=[
        "H3/L3 reversals fail on strong trend days — check ADX first",
        "H4/L4 breakouts are rare but very powerful when they happen",
        "Camarilla levels are most reliable on Nifty Futures (high volume)",
        "Combine with CPR width: narrow CPR = H3/L3 works, wide CPR = breakout mode",
    ],
    pros=[
        "Very precise price levels calculated mathematically",
        "Works for both trending and sideways days",
        "Clear risk/reward — H3 to H4 is always your risk",
        "Extremely popular among Indian professional traders",
    ],
    cons=[
        "Needs the previous day's full data",
        "Level tolerance can be tricky on very volatile days",
        "Works best on Nifty Futures/Options, not spot index",
    ],
    example_scenario=(
        "Prev day: H=23,833 L=23,558 C=23,639. "
        "Camarilla: H3=23,714 H4=23,789 L3=23,564 L4=23,489. "
        "At 11:30, price hits 23,720 (near H3). RSI=62. Bearish candle. "
        "→ SHORT at 23,720. SL=23,800 (above H4). Target L3=23,564."
    ),
))
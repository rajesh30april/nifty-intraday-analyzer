"""First Candle Range (FCR) Strategy.

The simplest breakout strategy that exists:
  The 9:15 AM candle (first 5 minutes) sets the HIGH and LOW.
  These two levels act as support/resistance for the ENTIRE day.

  Break above FCR high → LONG
  Break below FCR low  → SHORT

Why it works:
  - The first candle captures the initial price discovery / gap
  - Every intraday trader marks the first candle H/L on their chart
  - Institutional algos fade moves INSIDE the first candle range,
    and join moves that BREAK OUT of it
  - Earlier entry than ORB (fires at 9:20 vs 9:30) while still
    waiting for the opening candle to fully close

Vs ORB:
  ORB  = 15 min range (3 candles), fires at 9:30
  FCR  = 5 min range  (1 candle),  fires at 9:20
  FCR is faster but slightly noisier — compensate with stricter
  volume and VWAP filters.
"""

import pandas as pd
from datetime import time as dt_time
from strategy import StrategySignal, StrategyCondition, Direction
from strategies.registry import register, StrategyInfo


# ── Tunable parameters ────────────────────────────────────────────
MIN_FCR_RANGE      = 20    # pts — skip if opening candle was a doji
MAX_FCR_RANGE      = 120   # pts — skip if range is too huge (SL too large)
MIN_BREAKOUT_PTS   = 3     # candle body must close this far beyond FCR level
VOLUME_RATIO_MIN   = 1.15  # breakout candle volume >= 1.15× recent avg
MAX_ENTRY_HOUR     = dt_time(14, 30)  # no entries after 2:30 PM


def evaluate_fcr(df: pd.DataFrame) -> StrategySignal:
    """First Candle Range Breakout.

    Entry conditions:
    1. First candle (9:15) has closed — we have FCR high and low
    2. FCR range is meaningful: 20–120 pts
    3. Current candle BODY closes above FCR high or below FCR low
    4. Volume on breakout candle >= 1.15× average
    5. VWAP is on the correct side (confirms direction)
    """
    conditions: list[StrategyCondition] = []

    if len(df) < 5:
        return StrategySignal(should_enter=False, reason="Insufficient data")

    # ── Wall clock checks ────────────────────────────────────────────
    # Use candle timestamp — NOT datetime.now() — so backtest replays work
    # correctly on historical data (wall clock would be wrong in simulation).
    now_time = df.index[-1].time()

    if now_time < dt_time(9, 20):
        return StrategySignal(
            should_enter=False,
            reason="Wait for 9:15 candle to close (9:20 AM)",
        )
    if now_time > MAX_ENTRY_HOUR:
        return StrategySignal(
            should_enter=False,
            reason=f"Too late for FCR trade (after {MAX_ENTRY_HOUR.strftime('%H:%M')})",
        )

    # ── Build today's dataset ────────────────────────────────────────
    # "today" = the date of the most recent candle in df (works in both
    # live and backtest since df.index[-1] IS the current evaluation candle).
    today    = df.index[-1].date()
    today_df = df[df.index.date == today]

    if today_df.empty:
        return StrategySignal(should_enter=False, reason="No today data yet")

    # First candle = the 9:15–9:20 candle (index 0)
    first_candle = today_df.iloc[0]
    fcr_high     = float(first_candle["high"])
    fcr_low      = float(first_candle["low"])
    fcr_range    = fcr_high - fcr_low

    # ── Condition 1: FCR range quality ───────────────────────────────
    range_ok = MIN_FCR_RANGE <= fcr_range <= MAX_FCR_RANGE
    conditions.append(StrategyCondition(
        name="FCR Range Quality",
        met=range_ok,
        detail=(
            f"FCR: {fcr_low:.0f}–{fcr_high:.0f} ({fcr_range:.0f} pts) | "
            + (
                "✅ good range"
                if range_ok
                else f"❌ too {'narrow' if fcr_range < MIN_FCR_RANGE else 'wide'} "
                     f"(need {MIN_FCR_RANGE}–{MAX_FCR_RANGE} pts)"
            )
        ),
    ))

    if not range_ok:
        return StrategySignal(
            should_enter=False,
            reason=(
                f"FCR range {fcr_range:.0f} pts is "
                f"{'too narrow' if fcr_range < MIN_FCR_RANGE else 'too wide'} — skip"
            ),
            conditions=conditions,
        )

    # ── Current candle ───────────────────────────────────────────────
    curr  = df.iloc[-1]
    close = float(curr["close"])
    open_ = float(curr["open"])

    body_low  = min(open_, close)
    body_high = max(open_, close)

    # Don't evaluate the first candle against itself
    if df.index[-1] == today_df.index[0]:
        return StrategySignal(
            should_enter=False,
            reason="Waiting for candle after first one",
            conditions=conditions,
        )

    # ── Condition 2: Body breakout ────────────────────────────────────
    broke_above = body_low > fcr_high and close > fcr_high + MIN_BREAKOUT_PTS
    broke_below = body_high < fcr_low  and close < fcr_low  - MIN_BREAKOUT_PTS
    breakout_ok = broke_above or broke_below
    direction   = Direction.LONG if broke_above else Direction.SHORT

    conditions.append(StrategyCondition(
        name="Body Breakout",
        met=breakout_ok,
        detail=(
            f"Close={close:.0f} | FCR {fcr_low:.0f}–{fcr_high:.0f} | "
            + (
                f"✅ {'above FCR high' if broke_above else 'below FCR low'}"
                if breakout_ok
                else "❌ still inside FCR range"
            )
        ),
    ))

    # ── Condition 3: Volume surge ─────────────────────────────────────
    if "volume" in df.columns:
        curr_vol  = float(curr["volume"])
        avg_vol   = float(df["volume"].rolling(14).mean().iloc[-1])
        if avg_vol == 0:
            # Index data (e.g. Zerodha Nifty index) has no volume — skip check
            vol_ok, vol_detail = True, "Vol N/A (index data — condition skipped)"
        else:
            vol_ratio  = curr_vol / avg_vol
            vol_ok     = vol_ratio >= VOLUME_RATIO_MIN
            vol_detail = (
                f"Vol={curr_vol:,.0f} | Avg={avg_vol:,.0f} | "
                f"{vol_ratio:.2f}× — {'✅ surge' if vol_ok else '❌ weak'}"
            )
    else:
        vol_ok     = True
        vol_detail = "Volume N/A (index) — skipped"

    conditions.append(StrategyCondition(
        name="Volume Surge",
        met=vol_ok,
        detail=vol_detail,
    ))

    # ── Condition 4: VWAP alignment ───────────────────────────────────
    import indicators as _ind
    _vwap_series = _ind.vwap(
        today_df["high"], today_df["low"], today_df["close"], today_df["volume"]
    )
    vwap_val = float(_vwap_series.iloc[-1]) if not _vwap_series.empty else None
    if vwap_val is not None and not pd.isna(vwap_val):
        vwap_ok = (
            (broke_above and close > vwap_val)
            or (broke_below and close < vwap_val)
        )
        vwap_detail = (
            f"VWAP={vwap_val:.0f} | Close={close:.0f} — "
            + (
                "✅ aligned" if vwap_ok
                else "❌ wrong side of VWAP"
            )
        )
    else:
        vwap_ok     = True
        vwap_detail = "VWAP N/A — skipped"

    conditions.append(StrategyCondition(
        name="VWAP Alignment",
        met=vwap_ok,
        detail=vwap_detail,
    ))

    # ── Score + decision ──────────────────────────────────────────────
    all_met    = breakout_ok and vol_ok and vwap_ok
    total_cond = len(conditions)
    met_cond   = sum(1 for c in conditions if c.met)
    confidence = met_cond / total_cond * 100

    blocks = []
    if not range_ok:    blocks.append(f"FCR range {fcr_range:.0f} pts out of bounds")
    if not breakout_ok: blocks.append("No body breakout yet")
    if not vol_ok:      blocks.append("Weak volume")
    if not vwap_ok:     blocks.append("Wrong side of VWAP")

    reason = (
        f"FCR {'ENTRY' if all_met else 'NO ENTRY'}: "
        f"{direction.value.upper() if breakout_ok else '?'} | "
        f"confidence={confidence:.0f}% | FCR {fcr_low:.0f}–{fcr_high:.0f} | "
        + ("ALL conditions met" if all_met else " + ".join(blocks))
    )

    return StrategySignal(
        should_enter=all_met,
        direction=direction if breakout_ok else Direction.LONG,
        confidence=confidence,
        conditions=conditions,
        reason=reason,
    )


# ── Register ──────────────────────────────────────────────────────
register(StrategyInfo(
    id="fcr",
    name="First Candle Range",
    emoji="🕯",
    description=(
        "Trades breakouts of the first 5-minute candle's High/Low. "
        "Fires at 9:20 AM — 10 minutes earlier than ORB — capturing "
        "momentum before it runs away."
    ),
    category="breakout",
    difficulty="beginner",
    market_condition="Best on trending/gap days. Skip on flat/chop days (narrow FCR range).",
    evaluate=evaluate_fcr,
    entry_rules=[
        "Wait for 9:15 candle to close (9:20 AM minimum)",
        "FCR range must be 20–120 pts (not a doji, not a blow-off candle)",
        "Breakout: candle BODY closes above FCR high or below FCR low",
        "Body must clear the level by at least 3 pts (filters fake pokes)",
        "Volume >= 1.15× 14-period average (confirms real move)",
        "Price on correct side of VWAP",
    ],
    exit_rules=[
        "Stop-loss: Opposite end of FCR (below FCR low for long, above FCR high for short)",
        "Target: FCR range × 1.5 projected from entry",
        "Auto-exit at 3:15 PM",
    ],
    risk_tips=[
        "FCR is noisier than ORB — the 3-pt body filter is important",
        "If price breaks out then re-enters FCR range → exit immediately",
        "Works best when there is a gap up/down (reinforces direction)",
        "On days with no gap and narrow FCR range — skip, range trading day likely",
    ],
    pros=[
        "Fires 10 min earlier than ORB — catches more of the move",
        "Simple, well-defined levels (first candle H/L)",
        "Combines with Gap & Go naturally on gap days",
    ],
    cons=[
        "Single candle range is noisier than 15-min ORB range",
        "More false breakouts than ORB — volume filter is mandatory",
    ],
    example_scenario=(
        "9:15 candle: High=23,480, Low=23,420 (range=60 pts). "
        "At 9:35 AM, candle body closes at 23,495 (above FCR high) with vol 1.4× avg. "
        "VWAP=23,445 — price above it. → LONG at 23,495. "
        "SL=23,420 (FCR low). Target=23,585 (FCR range × 1.5 above entry)."
    ),
))
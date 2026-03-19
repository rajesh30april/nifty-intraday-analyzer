"""Opening Range Breakout (ORB) Strategy.

Classic intraday strategy: wait for the first 15 minutes to form
a range, then trade the breakout direction with volume confirmation.
"""

import pandas as pd
from datetime import datetime, time as dt_time, date as dt_date
from strategy import StrategySignal, StrategyCondition, Direction
import indicators as ind
from strategies.registry import register, StrategyInfo

MARKET_OPEN_TIME  = dt_time(9, 15)
MARKET_CLOSE_TIME = dt_time(15, 30)


# ── ORB quality thresholds ───────────────────────────────────────────────────
# Range too narrow → noise, slippage kills R:R
# Range too wide   → SL too far, option premium too expensive
ORB_MIN_RANGE = 30   # pts — below this it's a doji session, skip
ORB_MAX_RANGE = 150  # pts — was 100, but volatile days (VIX>18) ORB can easily hit 120+


def evaluate_orb(
    df: pd.DataFrame,
    orb_minutes: int = 15,
    volume_ratio_min: float = 1.2,
) -> StrategySignal:
    """Opening Range Breakout.

    Entry conditions:
    1. At least 15 minutes have passed since market open
    2. ORB range quality: 30–100 pts (not too narrow, not too wide)
    3. Price breaks above ORB high (long) or below ORB low (short)
    4. Breakout candle closes beyond the ORB level (not just a wick)
    5. Volume on breakout candle > 1.2x average volume
    6. Price is on the right side of VWAP (above for long, below for short)
    """
    conditions = []

    if len(df) < 20:
        return StrategySignal(should_enter=False, reason="Insufficient data")

    # ── Time / staleness guard ──────────────────────────────────────────────
    # Use the CANDLE's timestamp (df.index[-1]) for all time checks so that
    # both live trading and backtest simulation work correctly.
    #
    # Live trading concern ("stale data at 1 AM"): the auto-trader only feeds
    # fresh candles via websocket, so stale data never reaches evaluate_orb
    # in practice. The backtester always passes historical candles whose
    # date is the day being simulated — not today's real date.
    last_candle_ts   = df.index[-1]
    last_candle_date = last_candle_ts.date()
    now_time         = last_candle_ts.time()

    if not (MARKET_OPEN_TIME <= now_time <= MARKET_CLOSE_TIME):
        return StrategySignal(
            should_enter=False,
            reason=f"⏸ Outside market hours ({now_time.strftime('%H:%M')}) — ORB paused",
        )

    # Get today's data only (all candles matching the last candle's date)
    today_df = df[df.index.date == last_candle_date]
    if len(today_df) < 4:
        return StrategySignal(should_enter=False, reason="Need more today's data")

    # ── ORB range: first N minutes of TODAY's session ────────────────────────
    # Use the last CANDLE's timestamp (last_candle_ts) so backtest simulation
    # uses the correct historical time, not the real wall clock.
    market_open_dt  = last_candle_ts.replace(hour=9, minute=15, second=0, microsecond=0)
    mins_since_open = max(0, (last_candle_ts - market_open_dt).total_seconds() / 60)
    time_ok = mins_since_open >= orb_minutes

    orb_end     = today_df.index[0] + pd.Timedelta(minutes=orb_minutes)
    orb_candles = today_df[today_df.index <= orb_end]

    if len(orb_candles) < 2:
        return StrategySignal(should_enter=False, reason="ORB range not formed yet")

    orb_high  = float(orb_candles["high"].max())
    orb_low   = float(orb_candles["low"].min())
    orb_range = orb_high - orb_low

    # ── Range quality guard ────────────────────────────────────────────────────
    # Bail early before building conditions — a bad range poisons all signals.
    range_ok = ORB_MIN_RANGE <= orb_range <= ORB_MAX_RANGE
    if not range_ok:
        reason = (
            f"ORB range {orb_range:.0f} pts is "
            f"{'too narrow (< {ORB_MIN_RANGE} pts — noise/slippage)' if orb_range < ORB_MIN_RANGE else 'too wide (> {ORB_MAX_RANGE} pts — SL too large)'}"
        )
        return StrategySignal(should_enter=False, reason=reason)

    curr    = df.iloc[-1]
    price   = float(curr["close"])
    c_high  = float(curr["high"])
    c_low   = float(curr["low"])

    conditions.append(StrategyCondition(
        name="ORB Formed",
        met=time_ok,
        detail=(
            f"ORB range: {orb_low:.0f} - {orb_high:.0f} ({orb_range:.0f} pts)"
            f" | {mins_since_open:.0f}min since open"
            if time_ok else
            f"Need {orb_minutes - mins_since_open:.0f} more min to complete ORB range"
        ),
    ))

    # ── 2. Breakout detection ──────────────────────
    breakout_long = price > orb_high
    breakout_short = price < orb_low
    breakout_ok = breakout_long or breakout_short
    direction = Direction.LONG if breakout_long else Direction.SHORT if breakout_short else None

    conditions.append(StrategyCondition(
        name="Price Breakout",
        met=breakout_ok,
        detail=(
            f"Close {price:.0f} {'> ORB high ' + str(orb_high) if breakout_long else '< ORB low ' + str(orb_low) if breakout_short else 'inside ORB range'}"
        ),
    ))

    # ── 3. Candle close beyond level (not just a wick) ─────
    if breakout_long:
        close_beyond = price > orb_high + 2  # at least 2 pts beyond
    elif breakout_short:
        close_beyond = price < orb_low - 2
    else:
        close_beyond = False

    conditions.append(StrategyCondition(
        name="Close Confirmation",
        met=close_beyond,
        detail=f"Price closed {'cleanly beyond' if close_beyond else 'too close to'} ORB level",
    ))

    # ── 4. Volume confirmation ─────────────────────
    vol = df["volume"]
    avg_vol = float(vol.rolling(20).mean().iloc[-1]) if len(vol) >= 20 else float(vol.mean())
    curr_vol = float(vol.iloc[-1])
    vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 0

    # Skip volume check if index data (zero volume)
    if vol.sum() == 0:
        vol_ok = True
        vol_detail = "Volume N/A (index data) — skipped"
    else:
        vol_ok = vol_ratio >= volume_ratio_min
        vol_detail = f"Vol ratio {vol_ratio:.1f}x ({'OK' if vol_ok else 'weak'})"

    conditions.append(StrategyCondition(
        name="Volume Surge",
        met=vol_ok,
        detail=vol_detail,
    ))

    # ── 5. VWAP alignment ─────────────────────────
    vwap_val = float(ind.vwap(
        today_df["high"], today_df["low"],
        today_df["close"], today_df["volume"]
    ).iloc[-1])

    if breakout_long:
        vwap_ok = price > vwap_val
    elif breakout_short:
        vwap_ok = price < vwap_val
    else:
        vwap_ok = False

    conditions.append(StrategyCondition(
        name="VWAP Alignment",
        met=vwap_ok,
        detail=f"Price {'above' if price > vwap_val else 'below'} VWAP ({vwap_val:.0f})",
    ))

    # Score
    weighted = [c for c in conditions if c.weight > 0]
    total_w = sum(c.weight for c in weighted)
    met_w = sum(c.weight for c in weighted if c.met)
    confidence = (met_w / total_w * 100) if total_w > 0 else 0
    all_met = all(c.met for c in weighted)

    return StrategySignal(
        should_enter=all_met and direction is not None,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=f"ORB: {'ALL' if all_met else 'NOT all'} conditions met ({confidence:.0f}%)",
    )


# ── Register ─────────────────────────────────────────────────
register(StrategyInfo(
    id="orb",
    name="Opening Range Breakout",
    emoji="🌅",
    description=(
        "The classic first-15-minutes breakout. Wait for the opening range "
        "to form, then trade the breakout with volume confirmation."
    ),
    category="breakout",
    difficulty="beginner",
    market_condition="Works best on trending/volatile days. Avoid on gap days.",
    evaluate=evaluate_orb,
    entry_rules=[
        "Wait for 15 minutes after market open for ORB range to form",
        "Price breaks and closes above ORB high (long) or below ORB low (short)",
        "Candle closes cleanly beyond the ORB level (not just a wick poke)",
        "Volume on breakout candle is 1.2x above average",
        "Price is on the correct side of VWAP",
    ],
    exit_rules=[
        "Stop-loss: Opposite end of the ORB range",
        "Target: 1:2 R:R from entry",
        "Trailing SL if price moves 1R in your favor",
        "Force exit at 3:15 PM",
    ],
    risk_tips=[
        "ORB works best when range is 30-80 pts. Too narrow = whipsaw, too wide = big SL",
        "Avoid ORB on days with major news events at market open",
        "First breakout attempt has highest probability",
    ],
    pros=[
        "Very simple to understand and execute",
        "Works on most trading days",
        "Clear entry/exit levels defined by the range",
    ],
    cons=[
        "False breakouts are common (30-40% of the time)",
        "Wide ORB range = large stop-loss",
        "Only works once per day (first breakout)",
    ],
    example_scenario=(
        "Market opens at 22,500. In first 15 min, high = 22,540, low = 22,470 "
        "(ORB range = 70 pts). At 9:35, a strong green candle closes at 22,555 "
        "(above ORB high) with 1.5x volume. VWAP is at 22,510. "
        "\u2192 BUY at 22,555, SL at 22,470 (ORB low), Target 22,725."
    ),
))

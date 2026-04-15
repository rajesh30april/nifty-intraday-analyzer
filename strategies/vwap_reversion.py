"""VWAP Mean Reversion Strategy.

Price tends to revert to VWAP after deviating too far.
Enter when price snaps back toward VWAP on a ranging day.

Kept to 4 hard gates + 1 soft:
  Hard: deviation, reversal candle, ADX < 25, time window
  Soft: RSI extreme (confidence boost only)

NOTE: Inherently works poorly in strong trends (like the Jan-Mar 2026
Nifty crash). ADX < 25 filter exists specifically to prevent trading
this strategy on trending days.
"""

import pandas as pd
from strategy import StrategySignal, StrategyCondition, Direction
import indicators as ind
from strategies.registry import register, StrategyInfo


def evaluate_vwap_reversion(
    df: pd.DataFrame,
    deviation_pct: float = 0.08,
    rsi_oversold: float = 42.0,
    rsi_overbought: float = 58.0,
) -> StrategySignal:
    """VWAP Mean Reversion.

    Hard gates (ALL must pass):
      1. Price deviated > 0.08% from VWAP  (23k ≈ 18pts stretch)
      2. Reversal candle: green below VWAP, red above (momentum turning)
      3. ADX < 25  (range day — reversion gets killed in trends)
      4. Time: 15+ min after open

    Soft (confidence boost only):
      • RSI < 42 for long / RSI > 58 for short
    """
    NO_SIGNAL = lambda r, c=[]: StrategySignal(should_enter=False, reason=r, conditions=c)

    if len(df) < 30:
        return NO_SIGNAL("Need 30+ candles")

    today    = df.index[-1].date()
    today_df = df[df.index.date == today]
    if len(today_df) < 5:
        return NO_SIGNAL("Need 5+ today candles")

    curr   = df.iloc[-1]
    price  = float(curr["close"])
    c_open = float(curr["open"])

    # VWAP — real volume if available, else close average fallback
    has_vol  = today_df["volume"].sum() > 0 if "volume" in today_df.columns else False
    if has_vol:
        vwap_val = float(ind.vwap(
            today_df["high"], today_df["low"],
            today_df["close"], today_df["volume"]
        ).iloc[-1])
    else:
        vwap_val = float(today_df["close"].mean())

    dev_pct   = (price - vwap_val) / vwap_val * 100
    is_above  = price > vwap_val
    direction = Direction.SHORT if is_above else Direction.LONG

    conditions: list[StrategyCondition] = []

    # ── Gate 1: Deviation ────────────────────────────────────────
    dev_ok = abs(dev_pct) >= deviation_pct
    conditions.append(StrategyCondition(
        name="VWAP Deviation",
        met=dev_ok,
        detail=f"Price {dev_pct:+.2f}% from VWAP {vwap_val:.0f} (need ≥{deviation_pct}%)",
        weight=3.0,
    ))
    if not dev_ok:
        return NO_SIGNAL(f"Not stretched: only {dev_pct:+.2f}%", conditions)

    # ── Gate 2: Reversal Candle ─────────────────────────────────
    reversal_ok = (price > c_open) if direction == Direction.LONG else (price < c_open)
    candle_type = "Green" if price > c_open else "Red"
    conditions.append(StrategyCondition(
        name="Reversal Candle",
        met=reversal_ok,
        detail=f"{candle_type} candle — {'toward' if reversal_ok else 'AWAY from'} VWAP",
        weight=2.0,
    ))
    if not reversal_ok:
        return NO_SIGNAL("Candle still moving away from VWAP", conditions)

    # ── Gate 3: ADX < 25 (range day only) ────────────────────────
    adx_val = float(ind.adx(df["high"], df["low"], df["close"])["adx"].iloc[-1])
    adx_ok  = adx_val < 30   # loosened from 25 → catches early-trend sessions that still revert
    conditions.append(StrategyCondition(
        name="Ranging Day (ADX)",
        met=adx_ok,
        detail=f"ADX {adx_val:.0f} — {'ranging/mild-trend ✔' if adx_ok else 'STRONG TREND ✘ skip reversion'}",
        weight=2.0,
    ))
    if not adx_ok:
        return NO_SIGNAL(f"Strong trend ADX={adx_val:.0f} (need <30)", conditions)

    # ── Gate 4: Time Filter ──────────────────────────────────────
    last_ts = df.index[-1]
    elapsed = (last_ts - today_df.index[0]).total_seconds() / 60
    time_ok = elapsed >= 15
    conditions.append(StrategyCondition(
        name="Time Filter",
        met=time_ok,
        detail=f"{elapsed:.0f} min since open",
        weight=1.0,
    ))
    if not time_ok:
        return NO_SIGNAL("Too early", conditions)

    # ── Soft: RSI extreme ────────────────────────────────────────
    rsi_val = float(ind.rsi(df["close"], 14).iloc[-1])
    rsi_ok  = rsi_val <= rsi_oversold if direction == Direction.LONG else rsi_val >= rsi_overbought
    conditions.append(StrategyCondition(
        name="RSI Extreme (soft)",
        met=rsi_ok,
        detail=f"RSI {rsi_val:.0f} — {'extreme ★' if rsi_ok else 'neutral'}",
        weight=1.5,
    ))

    # ── Confidence ──────────────────────────────────────────────
    total_w    = sum(c.weight for c in conditions)
    passed_w   = sum(c.weight for c in conditions if c.met)
    confidence = round(passed_w / total_w * 100, 1)

    dir_label = "LONG" if direction == Direction.LONG else "SHORT"
    rsi_tag   = " ★" if rsi_ok else ""

    return StrategySignal(
        should_enter=True,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=(
            f"VWAP Reversion {dir_label}{rsi_tag} | "
            f"Dev {dev_pct:+.2f}% | ADX {adx_val:.0f} | VWAP {vwap_val:.0f}"
        ),
    )


register(StrategyInfo(
    id="vwap_reversion",
    name="VWAP Mean Reversion",
    emoji="🎯",
    description=(
        "Price snaps back to VWAP after over-extending on a range day. "
        "Works when ADX is low (market chopping, not trending). "
        "RSI extreme gives a confidence star ★ but doesn't block entry."
    ),
    category="reversal",
    difficulty="intermediate",
    market_condition="Range-bound days only (ADX < 25). 11AM–2PM lull is the sweet spot.",
    evaluate=evaluate_vwap_reversion,
    entry_rules=[
        "Price stretched > 0.08% from intraday VWAP",
        "Reversal candle forming: green candle when below VWAP, red when above",
        "ADX < 25 — only range days, never trending days",
        "At least 15 minutes after market open",
        "[Soft] RSI < 42 (long) or > 58 (short) = higher confidence ★",
    ],
    exit_rules=[
        "Target: VWAP itself (the mean we're reverting to)",
        "Stop-loss: 1.5x the deviation beyond entry",
        "Hard exit if ADX rises above 30 mid-trade",
    ],
    risk_tips=[
        "DO NOT use on trending days — ADX gate exists for this reason",
        "Best during 11AM–2PM when morning momentum has faded",
        "If price doesn't reach VWAP within 30 min, exit flat",
        "VWAP reversion gives smaller wins (target = VWAP, not 2R) — needs high win rate",
    ],
    pros=[
        "VWAP is the mean that price always reverts to eventually",
        "Clear entry (deviation) and target (VWAP)",
        "Tight stops possible when price is stretched",
    ],
    cons=[
        "Gets destroyed in trending markets — ADX filter is non-negotiable",
        "Doesn't work during macro news events or budget days",
        "Small profit per trade (target = VWAP, not a big R multiple)",
    ],
    example_scenario=(
        "VWAP = 22,500. Nifty drops to 22,477 (−0.10% below VWAP). "
        "ADX = 18 (ranging). A green candle forms reversing up. RSI = 38 ★. "
        "→ BUY at 22,480, Target = 22,500 (VWAP), SL = 22,462."
    ),
))

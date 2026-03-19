"""Chart Pattern Strategy.

Detects structural intraday chart patterns on 5-min candles.
Patterns: Bull/Bear Flag, Double Top/Bottom, Ascending/Descending Triangle.

All patterns use the last 10-30 candles of the current day.
"""

import pandas as pd
import numpy as np
from strategy import StrategySignal, StrategyCondition, Direction
from strategies.registry import register, StrategyInfo

# ── Tuning ────────────────────────────────────────────────────────
FLAG_IMPULSE_PCT   = 0.25   # impulse leg must move >= 0.25% in 3-5 candles
FLAG_CONSOL_MAX    = 8      # consolidation phase: 4-8 candles
FLAG_RETRACE_MAX   = 0.55   # consolidation retraces <= 55% of impulse
DBL_TOLERANCE      = 0.0015 # two peaks/troughs within 0.15% of each other
DBL_MIN_SEP        = 5      # at least 5 candles between the two peaks/troughs
TRI_MIN_CANDLES    = 8      # need at least 8 candles to form a triangle
VOLUME_RATIO_MIN   = 1.15   # breakout candle volume > 1.15x average


# ── Pattern detectors ─────────────────────────────────────────────

def detect_flag(df: pd.DataFrame):
    """Bull Flag (long) or Bear Flag (short).

    1. Strong impulse move in N candles (N=3..6)
    2. Followed by a consolidation of 4-8 candles (lower highs/higher lows)
    3. Breakout of the consolidation channel in the impulse direction

    Returns: ('bull_flag'|'bear_flag'|None, detail, impulse_pct)
    """
    if len(df) < 14:
        return None, "Not enough candles", 0.0

    # Try different impulse window sizes
    # Layout: [ ... | impulse (N candles) | consolidation | current candle ]
    for impulse_n in range(3, 7):
        # consol_end is negative index where consolidation ends (exclusive of current candle)
        consol_end   = -1                             # up to but NOT including current candle
        consol_start = -(impulse_n + FLAG_CONSOL_MAX) # consolidation start index
        imp_end      = consol_start                   # impulse ends where consolidation starts
        imp_start    = imp_end - impulse_n

        imp_seg    = df.iloc[imp_start: imp_end] if imp_end != 0 else df.iloc[imp_start:]
        consol_seg = df.iloc[consol_start: -1]        # exclude current candle from consolidation

        if len(imp_seg) < impulse_n or len(consol_seg) < 3:
            continue

        imp_low  = float(imp_seg["low"].min())
        imp_high = float(imp_seg["high"].max())
        imp_pct  = (imp_high - imp_low) / imp_low * 100
        if imp_pct < FLAG_IMPULSE_PCT:
            continue

        # Determine impulse direction from close of first vs last candle
        imp_dir = "up" if float(imp_seg["close"].iloc[-1]) > float(imp_seg["close"].iloc[0]) else "down"

        # Use the clean consolidation segment (no current candle bleeding in)
        consol = consol_seg
        if len(consol) < 3:
            continue

        consol_high = float(consol["high"].max())
        consol_low  = float(consol["low"].min())
        retrace     = (consol_high - consol_low) / (imp_high - imp_low + 1e-9)

        # Retracement must be <= 55% of the impulse
        if retrace > FLAG_RETRACE_MAX:
            continue

        # Check breakout on current candle
        current_close = float(df.iloc[-1]["close"])
        current_high  = float(df.iloc[-1]["high"])
        current_low   = float(df.iloc[-1]["low"])

        if imp_dir == "up" and current_close > consol_high:
            return ("bull_flag",
                    f"Impulse +{imp_pct:.2f}%, consol {len(consol)}c, breakout above {consol_high:,.1f}",
                    imp_pct)
        if imp_dir == "down" and current_close < consol_low:
            return ("bear_flag",
                    f"Impulse -{imp_pct:.2f}%, consol {len(consol)}c, breakdown below {consol_low:,.1f}",
                    imp_pct)

    return None, "No flag pattern", 0.0


def detect_double_top_bottom(df: pd.DataFrame):
    """Double Top (short) or Double Bottom (long).

    1. Find two swing highs (or lows) at approximately the same price
    2. Separated by a valley (or peak) of at least DBL_MIN_SEP candles
    3. Current close breaks the neckline (the valley/peak between them)

    Returns: ('double_top'|'double_bottom'|None, detail, neckline)
    """
    if len(df) < 20:
        return None, "Not enough candles", 0.0

    window = df.iloc[-30:]
    highs  = window["high"].values
    lows   = window["low"].values
    closes = window["close"].values
    n      = len(window)

    # ── Double Top ───────────────────────────────────────────────
    # Find top-2 highest highs with minimum separation
    peak1_idx = int(np.argmax(highs))
    # Mask a window around peak1 and find peak2
    mask = np.ones(n, dtype=bool)
    mask[max(0, peak1_idx - DBL_MIN_SEP): min(n, peak1_idx + DBL_MIN_SEP + 1)] = False
    if mask.any():
        peak2_idx = int(np.argmax(np.where(mask, highs, -np.inf)))
        p1, p2 = highs[peak1_idx], highs[peak2_idx]
        if abs(p1 - p2) / max(p1, p2) <= DBL_TOLERANCE:
            # Neckline = lowest low between the two peaks
            lo, hi = sorted([peak1_idx, peak2_idx])
            neckline = float(lows[lo:hi + 1].min())
            if closes[-1] < neckline:  # breakdown below neckline
                return (
                    "double_top",
                    f"Peaks {p1:,.1f} & {p2:,.1f} (Δ{abs(p1-p2):.1f}), neckline break {neckline:,.1f}",
                    neckline,
                )

    # ── Double Bottom ────────────────────────────────────────────
    trough1_idx = int(np.argmin(lows))
    mask2 = np.ones(n, dtype=bool)
    mask2[max(0, trough1_idx - DBL_MIN_SEP): min(n, trough1_idx + DBL_MIN_SEP + 1)] = False
    if mask2.any():
        trough2_idx = int(np.argmin(np.where(mask2, lows, np.inf)))
        t1, t2 = lows[trough1_idx], lows[trough2_idx]
        if abs(t1 - t2) / max(t1, t2) <= DBL_TOLERANCE:
            lo, hi = sorted([trough1_idx, trough2_idx])
            neckline = float(highs[lo:hi + 1].max())
            if closes[-1] > neckline:  # breakout above neckline
                return (
                    "double_bottom",
                    f"Troughs {t1:,.1f} & {t2:,.1f} (Δ{abs(t1-t2):.1f}), neckline break {neckline:,.1f}",
                    neckline,
                )

    return None, "No double top/bottom", 0.0


def detect_triangle(df: pd.DataFrame):
    """Ascending Triangle (long) or Descending Triangle (short).

    Ascending:  Flat resistance (similar highs) + rising support (higher lows)
    Descending: Flat support (similar lows)  + falling resistance (lower highs)
    Breakout confirmed when current close exceeds the flat level.

    Returns: ('ascending'|'descending'|None, detail)
    """
    if len(df) < TRI_MIN_CANDLES + 2:
        return None, "Not enough candles"

    window = df.iloc[-(TRI_MIN_CANDLES + 2): -1]
    highs  = window["high"].values
    lows   = window["low"].values
    n      = len(window)
    x      = np.arange(n, dtype=float)

    # Linear regression on highs and lows
    h_slope = np.polyfit(x, highs, 1)[0]
    l_slope = np.polyfit(x, lows,  1)[0]
    h_std   = float(np.std(highs))
    l_std   = float(np.std(lows))
    flat_tol = float(np.mean(highs)) * 0.003   # 0.3% tolerance for "flat"

    current_close = float(df.iloc[-1]["close"])

    # Ascending triangle: highs are flat, lows are rising
    if h_std < flat_tol and l_slope > 0:
        resistance = float(np.mean(highs))
        if current_close > resistance:
            return (
                "ascending",
                f"Flat resistance {resistance:,.1f}, rising lows (slope {l_slope:+.2f}), breakout ↑",
            )

    # Descending triangle: lows are flat, highs are falling
    if l_std < flat_tol and h_slope < 0:
        support = float(np.mean(lows))
        if current_close < support:
            return (
                "descending",
                f"Flat support {support:,.1f}, falling highs (slope {h_slope:+.2f}), breakdown ↓",
            )

    return None, "No triangle"


# ── Main evaluation ───────────────────────────────────────────────

def evaluate_chart_patterns(df: pd.DataFrame) -> StrategySignal:
    """Detect structural chart patterns on the current day.

    Checks (in priority order):
    1. Flag / Pennant
    2. Double Top / Double Bottom
    3. Ascending / Descending Triangle
    Plus volume and time gate.
    """
    NO_SIGNAL = lambda r: StrategySignal(should_enter=False, reason=r)

    if len(df) < 30:
        return NO_SIGNAL("Need 30+ candles")

    today = df.index[-1].date()
    today_df = df[df.index.date == today]
    if len(today_df) < 8:
        return NO_SIGNAL("Need 8+ today's candles")

    conditions: list[StrategyCondition] = []

    # ── Condition 1: Pattern scan ─────────────────────────────────
    pattern_dir  = None
    pattern_name = ""
    pattern_detail = ""
    strength     = 1.0  # multiplier for confidence

    flag_kind, flag_detail, flag_pct = detect_flag(today_df)
    dbl_kind,  dbl_detail,  dbl_nl  = detect_double_top_bottom(today_df)
    tri_kind,  tri_detail           = detect_triangle(today_df)

    if flag_kind:
        pattern_dir    = "long" if flag_kind == "bull_flag" else "short"
        pattern_name   = "🚩 " + flag_kind.replace("_", " ").title()
        pattern_detail = flag_detail
        strength       = min(flag_pct / FLAG_IMPULSE_PCT, 3.0)  # stronger impulse = more confidence
    elif dbl_kind:
        pattern_dir    = "long" if dbl_kind == "double_bottom" else "short"
        pattern_name   = "📊 " + dbl_kind.replace("_", " ").title()
        pattern_detail = dbl_detail
        strength       = 2.0
    elif tri_kind:
        pattern_dir    = "long" if tri_kind == "ascending" else "short"
        pattern_name   = "📐 " + tri_kind.title() + " Triangle"
        pattern_detail = tri_detail
        strength       = 1.5

    pattern_found = pattern_dir is not None
    conditions.append(StrategyCondition(
        name="Chart pattern",
        met=pattern_found,
        detail=pattern_detail if pattern_found else "No pattern detected",
        weight=3.0,
    ))
    if not pattern_found:
        return NO_SIGNAL("No chart pattern")

    # ── Condition 2: Volume on breakout candle ────────────────────
    if "volume" in df.columns:
        vol_avg = float(today_df["volume"].iloc[:-1].mean())
        vol_now = float(today_df["volume"].iloc[-1])
        vol_ok  = vol_now >= vol_avg * VOLUME_RATIO_MIN if vol_avg > 0 else True
        vol_detail = f"Breakout vol {vol_now:,.0f} vs avg {vol_avg:,.0f} ({vol_now/vol_avg:.1f}x)" if vol_avg > 0 else "No vol data"
    else:
        vol_ok, vol_detail = True, "No volume data"

    conditions.append(StrategyCondition(
        name="Breakout volume",
        met=vol_ok,
        detail=vol_detail,
        weight=2.0,
    ))

    # ── Condition 3: Time filter ──────────────────────────────────
    first_ts = today_df.index[0]
    elapsed  = (df.index[-1] - first_ts).total_seconds() / 60
    time_ok  = elapsed >= 15
    conditions.append(StrategyCondition(
        name="Time filter (>15 min)",
        met=time_ok,
        detail=f"{elapsed:.0f} min elapsed",
        weight=1.0,
    ))

    # ── Confidence score ─────────────────────────────────────────
    total_w   = sum(c.weight for c in conditions)
    passed_w  = sum(c.weight for c in conditions if c.met)
    base_conf = passed_w / total_w * 100
    confidence = round(min(base_conf * strength / 1.5, 100.0), 1)

    should_enter = time_ok and vol_ok  # volume is required — low-volume breakouts fail 60%+
    direction    = Direction.LONG if pattern_dir == "long" else Direction.SHORT

    return StrategySignal(
        should_enter=should_enter,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        reason=f"{pattern_name} — {pattern_detail}",
    )


# ── Registry entry ────────────────────────────────────────────────

register(StrategyInfo(
    id="chart_patterns",
    name="Chart Patterns",
    emoji="📐",
    description=(
        "Detects structural intraday chart patterns on 5-min candles: "
        "Bull/Bear Flag (continuation), Double Top/Bottom (reversal), "
        "and Ascending/Descending Triangle (breakout). "
        "All patterns require a breakout close and elevated volume for confirmation."
    ),
    category="pattern",
    difficulty="intermediate",
    market_condition="Best after a strong directional move (flags) or after prolonged consolidation (triangles, double tops)",
    evaluate=evaluate_chart_patterns,
    entry_rules=[
        "Bull Flag: strong up-move → tight consolidation → close above consolidation high",
        "Bear Flag: strong down-move → tight bounce → close below consolidation low",
        "Double Bottom: two equal lows → close above the neckline (middle peak)",
        "Double Top: two equal highs → close below the neckline (middle trough)",
        "Ascending Triangle: flat highs + rising lows → close above flat resistance",
        "Descending Triangle: flat lows + falling highs → close below flat support",
        "Breakout candle volume must exceed 1.15x the day's average",
    ],
    exit_rules=[
        "Flag: SL below the flag's lowest low; Target = measured move (flag pole height)",
        "Double Top/Bottom: SL above/below the pattern; Target = pattern height projected from neckline",
        "Triangle: SL back inside the triangle; Target = widest part of triangle projected from breakout",
    ],
    risk_tips=[
        "Flags only work in trending markets — avoid in sideways/choppy conditions",
        "Volume confirmation is mandatory — breakouts on low volume fail 60%+ of the time",
        "Double Top/Bottom need two touches — a single peak is not a pattern",
        "Give triangles room — false breakouts are common; wait for a candle close outside",
    ],
    pros=[
        "Clear measured-move targets",
        "High risk-to-reward potential (2R-3R setups)",
        "Objective entry triggers (breakout close)",
    ],
    cons=[
        "Requires more candles to form — fewer signals per day",
        "False breakouts are common without volume confirmation",
        "5-min chart patterns can be noisy — benefit from higher TF alignment",
    ],
    example_scenario=(
        "Nifty drops sharply from 23,500 to 23,350 in 4 candles (Bear Flag pole). "
        "Price then bounces sideways in a 20-point range for 5 candles. "
        "Next candle closes at 23,345, breaking below the consolidation low. Volume 1.3x avg. "
        "→ SHORT at 23,345, SL at consolidation high (23,370), Target 23,200 (pole height = 150pts)."
    ),
))
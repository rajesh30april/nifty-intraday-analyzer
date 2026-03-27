"""Advanced Chart Patterns for Nifty 50.

Patterns added here (NOT in pattern_detector.py to keep files small):
  - Triple Top / Triple Bottom
  - Head & Shoulders / Inverse Head & Shoulders
  - Rising Wedge / Falling Wedge
  - Rising Channel / Falling Channel / Horizontal Channel
  - Symmetrical Triangle

All patterns return PatternMatch or None.
Author: Code Puppy 🐶
"""

import numpy as np
import pandas as pd
from typing import Optional

from pattern_detector import (
    PatternMatch,
    _calculate_atr,
    _check_volume_confirmation,
    _find_peaks_troughs,
)


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def _linreg_slope(values: list[float]) -> float:
    """Slope of a simple linear regression through values."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(values) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
    den = sum((x - x_mean) ** 2 for x in xs)
    return num / den if den != 0 else 0.0


def _peaks_within(highs: list, tolerance_pts: float) -> bool:
    """Check all highs are within tolerance_pts of each other."""
    return (max(highs) - min(highs)) <= tolerance_pts


# ══════════════════════════════════════════════════════════════════
# TRIPLE TOP
# ══════════════════════════════════════════════════════════════════

def detect_triple_top(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
    tolerance_pct: float = 0.35,
    min_separation: int = 5,
) -> Optional[PatternMatch]:
    """Triple Top: 3 peaks at same level → bearish reversal.

    Three roughly equal highs with two pullbacks between them.
    Neckline = average of the two troughs between peaks.
    Confirmation: close below neckline.
    """
    if len(high) < 30:
        return None

    atr_val = float(_calculate_atr(high, low, close).iloc[-1] or 0)
    tol = max(high.iloc[-1] * tolerance_pct / 100, atr_val * 0.5)

    peaks, _ = _find_peaks_troughs(high, low, order=4, min_distance=min_separation)
    if len(peaks) < 3:
        return None

    # Try every combination of 3 consecutive peaks
    for i in range(len(peaks) - 2):
        p1_idx, p1 = peaks[i]
        p2_idx, p2 = peaks[i + 1]
        p3_idx, p3 = peaks[i + 2]

        if not _peaks_within([p1, p2, p3], tol):
            continue

        # Middle peak (head) must NOT be significantly higher (else it's H&S)
        if p2 > max(p1, p3) * 1.01:        # >1% higher → H&S territory
            continue

        # Find neckline = lowest close between peak pairs
        seg1 = low.iloc[p1_idx: p2_idx + 1]
        seg2 = low.iloc[p2_idx: p3_idx + 1]
        t1 = float(seg1.min())
        t2 = float(seg2.min())
        neckline = (t1 + t2) / 2
        pattern_height = max(p1, p2, p3) - neckline

        if pattern_height < atr_val * 1.5:
            continue  # Too small — noise

        confirmed = float(close.iloc[-1]) < neckline
        target = neckline - pattern_height

        vol_confirmed = False
        if volume is not None:
            vol_confirmed, _ = _check_volume_confirmation(volume, len(close) - 1)

        base_conf = 0.80 if confirmed else 0.55
        if vol_confirmed:
            base_conf = min(base_conf + 0.08, 0.90)

        status = "✅ Neckline broken" if confirmed else "⏳ Watch neckline ₹{:.0f}".format(neckline)
        return PatternMatch(
            name="Triple Top",
            pattern_type="reversal",
            bias="bearish",
            confidence=base_conf,
            description=(
                f"3 peaks ₹{p1:.0f} / ₹{p2:.0f} / ₹{p3:.0f} (within ₹{tol:.0f}). "
                f"Neckline ₹{neckline:.0f}. Height ₹{pattern_height:.0f}. "
                f"{status}. Target ₹{target:.0f}."
            ),
            start_idx=p1_idx,
            end_idx=len(close) - 1,
            key_levels={
                "resistance": round((p1 + p2 + p3) / 3, 2),
                "neckline":   round(neckline, 2),
                "entry":      round(neckline, 2),
                "support":    round(neckline, 2),
            },
            volume_confirmed=vol_confirmed,
            measured_target=round(target, 2),
            stop_loss=round(max(p1, p2, p3) + atr_val * 0.5, 2),
            pivot_times=[p1_idx, p2_idx, p3_idx],
        )
    return None


# ══════════════════════════════════════════════════════════════════
# TRIPLE BOTTOM
# ══════════════════════════════════════════════════════════════════

def detect_triple_bottom(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
    tolerance_pct: float = 0.35,
    min_separation: int = 5,
) -> Optional[PatternMatch]:
    """Triple Bottom: 3 troughs at same level → bullish reversal."""
    if len(low) < 30:
        return None

    atr_val = float(_calculate_atr(high, low, close).iloc[-1] or 0)
    tol = max(low.iloc[-1] * tolerance_pct / 100, atr_val * 0.5)

    _, troughs = _find_peaks_troughs(high, low, order=4, min_distance=min_separation)
    if len(troughs) < 3:
        return None

    for i in range(len(troughs) - 2):
        t1_idx, t1 = troughs[i]
        t2_idx, t2 = troughs[i + 1]
        t3_idx, t3 = troughs[i + 2]

        if not _peaks_within([t1, t2, t3], tol):
            continue

        # Middle trough must NOT be significantly lower (else it's inv H&S)
        if t2 < min(t1, t3) * 0.99:
            continue

        seg1 = high.iloc[t1_idx: t2_idx + 1]
        seg2 = high.iloc[t2_idx: t3_idx + 1]
        n1 = float(seg1.max())
        n2 = float(seg2.max())
        neckline = (n1 + n2) / 2
        pattern_height = neckline - min(t1, t2, t3)

        if pattern_height < atr_val * 1.5:
            continue

        confirmed = float(close.iloc[-1]) > neckline
        target = neckline + pattern_height

        vol_confirmed = False
        if volume is not None:
            vol_confirmed, _ = _check_volume_confirmation(volume, len(close) - 1)

        base_conf = 0.80 if confirmed else 0.55
        if vol_confirmed:
            base_conf = min(base_conf + 0.08, 0.90)

        status = "✅ Neckline broken" if confirmed else "⏳ Watch neckline ₹{:.0f}".format(neckline)
        return PatternMatch(
            name="Triple Bottom",
            pattern_type="reversal",
            bias="bullish",
            confidence=base_conf,
            description=(
                f"3 troughs ₹{t1:.0f} / ₹{t2:.0f} / ₹{t3:.0f} (within ₹{tol:.0f}). "
                f"Neckline ₹{neckline:.0f}. Height ₹{pattern_height:.0f}. "
                f"{status}. Target ₹{target:.0f}."
            ),
            start_idx=t1_idx,
            end_idx=len(close) - 1,
            key_levels={
                "support":    round((t1 + t2 + t3) / 3, 2),
                "neckline":   round(neckline, 2),
                "entry":      round(neckline, 2),
                "resistance": round(neckline, 2),
            },
            volume_confirmed=vol_confirmed,
            measured_target=round(target, 2),
            stop_loss=round(min(t1, t2, t3) - atr_val * 0.5, 2),
            pivot_times=[t1_idx, t2_idx, t3_idx],
        )
    return None


# ══════════════════════════════════════════════════════════════════
# HEAD AND SHOULDERS
# ══════════════════════════════════════════════════════════════════

def detect_head_and_shoulders(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
    shoulder_tolerance_pct: float = 0.40,
    min_separation: int = 5,
) -> Optional[PatternMatch]:
    """Head & Shoulders: Left Shoulder → Head (higher) → Right Shoulder → bearish.

    Requirements:
    - 3 peaks: head is meaningfully higher than both shoulders
    - Shoulders within tolerance of each other
    - Neckline = line connecting the two troughs
    - Confirmation: close below neckline
    """
    if len(high) < 30:
        return None

    atr_val = float(_calculate_atr(high, low, close).iloc[-1] or 0)
    peaks, _ = _find_peaks_troughs(high, low, order=4, min_distance=min_separation)
    if len(peaks) < 3:
        return None

    tol = max(high.iloc[-1] * shoulder_tolerance_pct / 100, atr_val * 0.8)

    for i in range(len(peaks) - 2):
        ls_idx, ls = peaks[i]       # Left shoulder
        h_idx,  hd = peaks[i + 1]  # Head
        rs_idx, rs = peaks[i + 2]  # Right shoulder

        # Head must be higher than both shoulders
        if hd <= max(ls, rs):
            continue
        # Shoulders must be roughly equal
        if abs(ls - rs) > tol:
            continue
        # Head must be at least 1 ATR above shoulders
        if hd - max(ls, rs) < atr_val:
            continue

        # Neckline = troughs between L-H and H-R
        seg1 = low.iloc[ls_idx: h_idx + 1]
        seg2 = low.iloc[h_idx: rs_idx + 1]
        nl1 = float(seg1.min())
        nl2 = float(seg2.min())
        neckline = (nl1 + nl2) / 2
        pattern_height = hd - neckline

        if pattern_height < atr_val * 2:
            continue

        confirmed = float(close.iloc[-1]) < neckline
        target = neckline - pattern_height

        vol_confirmed = False
        if volume is not None:
            vol_confirmed, _ = _check_volume_confirmation(volume, len(close) - 1)

        base_conf = 0.82 if confirmed else 0.58
        if vol_confirmed:
            base_conf = min(base_conf + 0.08, 0.92)

        status = "✅ Neckline broken — SELL" if confirmed else "⏳ Watch neckline ₹{:.0f}".format(neckline)
        return PatternMatch(
            name="Head & Shoulders",
            pattern_type="reversal",
            bias="bearish",
            confidence=base_conf,
            description=(
                f"LS ₹{ls:.0f} → Head ₹{hd:.0f} → RS ₹{rs:.0f}. "
                f"Neckline ₹{neckline:.0f}. Height ₹{pattern_height:.0f}. "
                f"{status}. Target ₹{target:.0f}."
            ),
            start_idx=ls_idx,
            end_idx=len(close) - 1,
            key_levels={
                "resistance": round(hd, 2),
                "neckline":   round(neckline, 2),
                "entry":      round(neckline, 2),
                "left_shoulder": round(ls, 2),
                "right_shoulder": round(rs, 2),
            },
            volume_confirmed=vol_confirmed,
            measured_target=round(target, 2),
            stop_loss=round(rs + atr_val * 0.5, 2),
            pivot_times=[ls_idx, h_idx, rs_idx],
        )
    return None


# ══════════════════════════════════════════════════════════════════
# INVERSE HEAD AND SHOULDERS
# ══════════════════════════════════════════════════════════════════

def detect_inverse_head_and_shoulders(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
    shoulder_tolerance_pct: float = 0.40,
    min_separation: int = 5,
) -> Optional[PatternMatch]:
    """Inverse H&S: Left Shoulder → Head (lower) → Right Shoulder → bullish."""
    if len(low) < 30:
        return None

    atr_val = float(_calculate_atr(high, low, close).iloc[-1] or 0)
    _, troughs = _find_peaks_troughs(high, low, order=4, min_distance=min_separation)
    if len(troughs) < 3:
        return None

    tol = max(low.iloc[-1] * shoulder_tolerance_pct / 100, atr_val * 0.8)

    for i in range(len(troughs) - 2):
        ls_idx, ls = troughs[i]
        h_idx,  hd = troughs[i + 1]
        rs_idx, rs = troughs[i + 2]

        if hd >= min(ls, rs):
            continue
        if abs(ls - rs) > tol:
            continue
        if min(ls, rs) - hd < atr_val:
            continue

        seg1 = high.iloc[ls_idx: h_idx + 1]
        seg2 = high.iloc[h_idx: rs_idx + 1]
        nl1 = float(seg1.max())
        nl2 = float(seg2.max())
        neckline = (nl1 + nl2) / 2
        pattern_height = neckline - hd

        if pattern_height < atr_val * 2:
            continue

        confirmed = float(close.iloc[-1]) > neckline
        target = neckline + pattern_height

        vol_confirmed = False
        if volume is not None:
            vol_confirmed, _ = _check_volume_confirmation(volume, len(close) - 1)

        base_conf = 0.82 if confirmed else 0.58
        if vol_confirmed:
            base_conf = min(base_conf + 0.08, 0.92)

        status = "✅ Neckline broken — BUY" if confirmed else "⏳ Watch neckline ₹{:.0f}".format(neckline)
        return PatternMatch(
            name="Inverse H&S",
            pattern_type="reversal",
            bias="bullish",
            confidence=base_conf,
            description=(
                f"LS ₹{ls:.0f} → Head ₹{hd:.0f} → RS ₹{rs:.0f}. "
                f"Neckline ₹{neckline:.0f}. Height ₹{pattern_height:.0f}. "
                f"{status}. Target ₹{target:.0f}."
            ),
            start_idx=ls_idx,
            end_idx=len(close) - 1,
            key_levels={
                "support":  round(hd, 2),
                "neckline": round(neckline, 2),
                "entry":    round(neckline, 2),
                "left_shoulder": round(ls, 2),
                "right_shoulder": round(rs, 2),
            },
            volume_confirmed=vol_confirmed,
            measured_target=round(target, 2),
            stop_loss=round(rs - atr_val * 0.5, 2),
            pivot_times=[ls_idx, h_idx, rs_idx],
        )
    return None


# ══════════════════════════════════════════════════════════════════
# WEDGES
# ══════════════════════════════════════════════════════════════════

def _detect_wedge(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series],
    rising: bool,
    min_candles: int = 10,
) -> Optional[PatternMatch]:
    """Shared wedge logic for rising and falling wedges.

    Rising wedge:  highs and lows both rise, but lows rise FASTER → bearish
    Falling wedge: highs and lows both fall, but highs fall FASTER → bullish
    Both converge to a point (tightening range).
    """
    if len(high) < min_candles:
        return None

    atr_val = float(_calculate_atr(high, low, close).iloc[-1] or 0)
    seg_h = list(high.iloc[-min_candles:])
    seg_l = list(low.iloc[-min_candles:])

    slope_h = _linreg_slope(seg_h)
    slope_l = _linreg_slope(seg_l)

    if rising:
        # Both slopes UP, low slope > high slope (converging from below)
        if not (slope_h > 0 and slope_l > 0):
            return None
        if not (slope_l > slope_h):     # Lows rising faster
            return None
        bias    = "bearish"
        name    = "Rising Wedge"
        emoji   = "📐↗"
        idea    = "Buying exhausting — expect breakdown"
        target  = float(low.iloc[-min_candles])   # Back to wedge start
        sl      = float(high.iloc[-1]) + atr_val
    else:
        # Both slopes DOWN, high slope > low slope (converging from above)
        if not (slope_h < 0 and slope_l < 0):
            return None
        if not (slope_h < slope_l):     # Highs falling faster
            return None
        bias    = "bullish"
        name    = "Falling Wedge"
        emoji   = "📐↘"
        idea    = "Selling exhausting — expect breakout"
        target  = float(high.iloc[-min_candles])  # Back to wedge start
        sl      = float(low.iloc[-1]) - atr_val

    # Range must be converging (std dev of range shrinking)
    ranges = [h - l for h, l in zip(seg_h, seg_l)]
    first_half_range = sum(ranges[:len(ranges)//2]) / (len(ranges)//2)
    second_half_range = sum(ranges[len(ranges)//2:]) / (len(ranges) - len(ranges)//2)
    if second_half_range >= first_half_range * 0.85:   # Less than 15% compression
        return None

    vol_confirmed = False
    if volume is not None:
        vol_confirmed, _ = _check_volume_confirmation(volume, len(close) - 1)

    confidence = 0.68
    if vol_confirmed:
        confidence += 0.07
    compression = 1 - (second_half_range / first_half_range)
    confidence = min(confidence + compression * 0.15, 0.85)

    current = float(close.iloc[-1])
    return PatternMatch(
        name=name,
        pattern_type="reversal",
        bias=bias,
        confidence=round(confidence, 2),
        description=(
            f"{emoji} {idea}. "
            f"Range compressing {compression*100:.0f}%. "
            f"Slope H:{slope_h:.2f} L:{slope_l:.2f}. "
            f"Target ₹{target:.0f}. SL ₹{sl:.0f}."
        ),
        start_idx=len(close) - min_candles,
        end_idx=len(close) - 1,
        key_levels={
            "entry":      round(current, 2),
            "support":    round(float(min(seg_l)), 2),
            "resistance": round(float(max(seg_h)), 2),
        },
        volume_confirmed=vol_confirmed,
        measured_target=round(target, 2),
        stop_loss=round(sl, 2),
    )


def detect_rising_wedge(
    high: pd.Series, low: pd.Series, close: pd.Series,
    volume: Optional[pd.Series] = None,
) -> Optional[PatternMatch]:
    """Rising Wedge → bearish (buying exhaustion)."""
    return _detect_wedge(high, low, close, volume, rising=True)


def detect_falling_wedge(
    high: pd.Series, low: pd.Series, close: pd.Series,
    volume: Optional[pd.Series] = None,
) -> Optional[PatternMatch]:
    """Falling Wedge → bullish (selling exhaustion)."""
    return _detect_wedge(high, low, close, volume, rising=False)


# ══════════════════════════════════════════════════════════════════
# CHANNELS
# ══════════════════════════════════════════════════════════════════

def detect_channel(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
    min_candles: int = 12,
    parallelism_tolerance: float = 0.30,  # slope diff as % of ATR
) -> Optional[PatternMatch]:
    """Detect Rising / Falling / Horizontal Channel.

    A channel = price bouncing between two PARALLEL trendlines.
    Rising channel   → bullish continuation (buy dips at lower trendline)
    Falling channel  → bearish continuation (sell rallies at upper trendline)
    Horizontal channel → range bound (buy support, sell resistance)
    """
    if len(high) < min_candles:
        return None

    atr_val = float(_calculate_atr(high, low, close).iloc[-1] or 0)
    seg_h = list(high.iloc[-min_candles:])
    seg_l = list(low.iloc[-min_candles:])

    slope_h = _linreg_slope(seg_h)
    slope_l = _linreg_slope(seg_l)

    # Slopes must be parallel (within tolerance)
    slope_diff = abs(slope_h - slope_l)
    if slope_diff > atr_val * parallelism_tolerance:
        return None

    # Channel width must be meaningful (not too narrow)
    channel_width = float(np.mean([h - l for h, l in zip(seg_h, seg_l)]))
    if channel_width < atr_val * 1.2:
        return None

    # Channel must be sustained — at least 2 touches on each trendline
    upper = float(max(seg_h))
    lower = float(min(seg_l))
    touch_tol = atr_val * 0.5
    upper_touches = sum(1 for h in seg_h if abs(h - upper) <= touch_tol)
    lower_touches = sum(1 for l in seg_l if abs(l - lower) <= touch_tol)
    if upper_touches < 2 or lower_touches < 2:
        return None

    avg_slope = (slope_h + slope_l) / 2
    flat_threshold = atr_val * 0.05

    if avg_slope > flat_threshold:
        name = "Rising Channel"
        bias = "bullish"
        idea = "Buy dips near lower trendline ₹{:.0f}, SL below channel".format(lower)
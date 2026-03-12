"""Chart pattern detection for Nifty 50 intraday analysis.

Detects common chart patterns from OHLC data using peak/trough
analysis and geometric pattern matching.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass


@dataclass
class PatternMatch:
    """A detected chart pattern."""
    name: str
    pattern_type: str  # 'reversal', 'continuation', 'structure'
    bias: str  # 'bullish', 'bearish', 'neutral'
    confidence: float  # 0.0 to 1.0
    description: str
    start_idx: int  # index in the dataframe where pattern starts
    end_idx: int  # index where pattern ends/current
    key_levels: dict  # support, resistance, neckline, etc.
    timeframe: str = ""  # e.g. '5m', '15m'
    start_time: str = ""  # ISO timestamp of pattern start
    end_time: str = ""  # ISO timestamp of pattern end/current
    pivot_times: list = None  # timestamps of key pivot points (peaks/troughs)


def _find_peaks_troughs(
    high: pd.Series, low: pd.Series, order: int = 5,
) -> tuple[list[int], list[int]]:
    """Find local peaks (highs) and troughs (lows) in price data.

    Uses HIGH prices for peaks and LOW prices for troughs, because:
    - A peak is the highest point sellers couldn't push past
    - A trough is the lowest point buyers defended

    Args:
        high: High price series (for peak detection).
        low: Low price series (for trough detection).
        order: Number of candles on each side to confirm a peak/trough.

    Returns:
        Tuple of (peak_indices, trough_indices).
    """
    peaks = []
    troughs = []
    high_vals = high.values
    low_vals = low.values

    for i in range(order, len(high_vals) - order):
        # Peak: this candle's HIGH is higher than all neighbors' HIGHs
        if all(high_vals[i] >= high_vals[i - j] for j in range(1, order + 1)) and \
           all(high_vals[i] >= high_vals[i + j] for j in range(1, order + 1)):
            peaks.append(i)

        # Trough: this candle's LOW is lower than all neighbors' LOWs
        if all(low_vals[i] <= low_vals[i - j] for j in range(1, order + 1)) and \
           all(low_vals[i] <= low_vals[i + j] for j in range(1, order + 1)):
            troughs.append(i)

    return peaks, troughs


def detect_support_resistance(
    high: pd.Series, low: pd.Series, close: pd.Series,
    num_levels: int = 4,
) -> dict:
    """Detect key support and resistance levels using price clustering."""
    peaks, troughs = _find_peaks_troughs(high, low, order=3)

    peak_prices = [high.iloc[i] for i in peaks] if peaks else []
    trough_prices = [low.iloc[i] for i in troughs] if troughs else []

    # Cluster nearby levels (within 0.2% of each other)
    def cluster_levels(prices: list[float], threshold_pct: float = 0.2) -> list[float]:
        if not prices:
            return []
        sorted_prices = sorted(prices)
        clusters = []
        current_cluster = [sorted_prices[0]]

        for price in sorted_prices[1:]:
            if (price - current_cluster[-1]) / current_cluster[-1] * 100 < threshold_pct:
                current_cluster.append(price)
            else:
                clusters.append(round(np.mean(current_cluster), 2))
                current_cluster = [price]
        clusters.append(round(np.mean(current_cluster), 2))
        return clusters

    resistance_levels = cluster_levels(peak_prices)[-num_levels:]
    support_levels = cluster_levels(trough_prices)[:num_levels]

    current_price = close.iloc[-1]

    # Find nearest support and resistance
    nearest_support = max([s for s in support_levels if s < current_price], default=None)
    nearest_resistance = min([r for r in resistance_levels if r > current_price], default=None)

    return {
        "support_levels": support_levels,
        "resistance_levels": resistance_levels,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
    }


def detect_double_top(high: pd.Series, low: pd.Series, close: pd.Series, tolerance_pct: float = 0.3) -> PatternMatch | None:
    """Detect Double Top pattern (bearish reversal)."""
    peaks, troughs = _find_peaks_troughs(high, low, order=5)

    if len(peaks) < 2 or len(troughs) < 1:
        return None

    # Check last two peaks — use HIGH prices for peaks
    p1_idx, p2_idx = peaks[-2], peaks[-1]
    p1_val, p2_val = high.iloc[p1_idx], high.iloc[p2_idx]

    # Peaks should be roughly equal (within tolerance)
    diff_pct = abs(p1_val - p2_val) / p1_val * 100
    if diff_pct > tolerance_pct:
        return None

    # Must have a trough between the peaks — use LOW for neckline
    middle_troughs = [t for t in troughs if p1_idx < t < p2_idx]
    if not middle_troughs:
        return None

    neckline = low.iloc[middle_troughs[0]]
    current = close.iloc[-1]

    # Pattern is confirmed if price breaks below neckline
    confirmed = current < neckline
    confidence = 0.85 if confirmed else 0.55

    return PatternMatch(
        name="Double Top",
        pattern_type="reversal",
        bias="bearish",
        confidence=confidence,
        description=f"Two peaks near {round(p1_val, 1)}, neckline at {round(neckline, 1)}. "
                    f"{'CONFIRMED — broke neckline!' if confirmed else 'Forming — watch neckline break.'}",
        start_idx=p1_idx,
        end_idx=len(close) - 1,
        key_levels={"peak": round(p1_val, 2), "peak2": round(p2_val, 2), "neckline": round(neckline, 2)},
        pivot_times=[p1_idx, middle_troughs[0], p2_idx],  # will be converted to timestamps
    )


def detect_double_bottom(high: pd.Series, low: pd.Series, close: pd.Series, tolerance_pct: float = 0.3) -> PatternMatch | None:
    """Detect Double Bottom pattern (bullish reversal)."""
    peaks, troughs = _find_peaks_troughs(high, low, order=5)

    if len(troughs) < 2 or len(peaks) < 1:
        return None

    # Use LOW prices for troughs (actual lowest point of the candle)
    t1_idx, t2_idx = troughs[-2], troughs[-1]
    t1_val, t2_val = low.iloc[t1_idx], low.iloc[t2_idx]

    diff_pct = abs(t1_val - t2_val) / t1_val * 100
    if diff_pct > tolerance_pct:
        return None

    # Use HIGH price for neckline (peak between troughs)
    middle_peaks = [p for p in peaks if t1_idx < p < t2_idx]
    if not middle_peaks:
        return None

    neckline = high.iloc[middle_peaks[0]]
    current = close.iloc[-1]

    confirmed = current > neckline
    confidence = 0.85 if confirmed else 0.55

    return PatternMatch(
        name="Double Bottom",
        pattern_type="reversal",
        bias="bullish",
        confidence=confidence,
        description=f"Two troughs near {round(t1_val, 1)}, neckline at {round(neckline, 1)}. "
                    f"{'CONFIRMED — broke neckline!' if confirmed else 'Forming — watch neckline break.'}",
        start_idx=t1_idx,
        end_idx=len(close) - 1,
        key_levels={"trough1": round(t1_val, 2), "trough2": round(t2_val, 2), "neckline": round(neckline, 2)},
        pivot_times=[t1_idx, middle_peaks[0], t2_idx],  # will be converted to timestamps
    )


def detect_head_and_shoulders(high: pd.Series, low: pd.Series, close: pd.Series, tolerance_pct: float = 0.5) -> PatternMatch | None:
    """Detect Head & Shoulders (bearish) or Inverse H&S (bullish)."""
    peaks, troughs = _find_peaks_troughs(high, low, order=4)

    # Regular H&S: 3 peaks, middle is highest — use HIGH prices
    if len(peaks) >= 3:
        p1, p2, p3 = peaks[-3], peaks[-2], peaks[-1]
        v1, v2, v3 = high.iloc[p1], high.iloc[p2], high.iloc[p3]

        # Head (p2) should be higher than both shoulders
        if v2 > v1 and v2 > v3:
            # Shoulders should be roughly equal
            shoulder_diff = abs(v1 - v3) / v1 * 100
            if shoulder_diff < tolerance_pct * 2:
                # Find neckline from troughs between peaks — use LOW prices
                neck_troughs = [t for t in troughs if p1 < t < p3]
                if neck_troughs:
                    neckline = min(low.iloc[t] for t in neck_troughs)
                    current = close.iloc[-1]
                    confirmed = current < neckline

                    return PatternMatch(
                        name="Head & Shoulders",
                        pattern_type="reversal",
                        bias="bearish",
                        confidence=0.9 if confirmed else 0.6,
                        description=f"Head at {round(v2, 1)}, shoulders ~{round((v1+v3)/2, 1)}, "
                                    f"neckline {round(neckline, 1)}. "
                                    f"{'CONFIRMED!' if confirmed else 'Watch neckline.'}",
                        start_idx=p1,
                        end_idx=len(close) - 1,
                        key_levels={"head": round(v2, 2), "neckline": round(neckline, 2),
                                    "left_shoulder": round(v1, 2), "right_shoulder": round(v3, 2)},
                    )

    # Inverse H&S: 3 troughs, middle is lowest — use LOW prices
    if len(troughs) >= 3:
        t1, t2, t3 = troughs[-3], troughs[-2], troughs[-1]
        v1, v2, v3 = low.iloc[t1], low.iloc[t2], low.iloc[t3]

        if v2 < v1 and v2 < v3:
            shoulder_diff = abs(v1 - v3) / v1 * 100
            if shoulder_diff < tolerance_pct * 2:
                # Neckline from peaks — use HIGH prices
                neck_peaks = [p for p in peaks if t1 < p < t3]
                if neck_peaks:
                    neckline = max(high.iloc[p] for p in neck_peaks)
                    current = close.iloc[-1]
                    confirmed = current > neckline

                    return PatternMatch(
                        name="Inverse Head & Shoulders",
                        pattern_type="reversal",
                        bias="bullish",
                        confidence=0.9 if confirmed else 0.6,
                        description=f"Head at {round(v2, 1)}, shoulders ~{round((v1+v3)/2, 1)}, "
                                    f"neckline {round(neckline, 1)}. "
                                    f"{'CONFIRMED!' if confirmed else 'Watch neckline.'}",
                        start_idx=t1,
                        end_idx=len(close) - 1,
                        key_levels={"head": round(v2, 2), "neckline": round(neckline, 2),
                                    "left_shoulder": round(v1, 2), "right_shoulder": round(v3, 2)},
                    )

    return None


def detect_trend_structure(high: pd.Series, low: pd.Series, close: pd.Series) -> PatternMatch | None:
    """Detect Higher Highs/Higher Lows or Lower Highs/Lower Lows."""
    peaks, troughs = _find_peaks_troughs(high, low, order=4)

    if len(peaks) < 2 or len(troughs) < 2:
        return None

    recent_peaks = peaks[-3:] if len(peaks) >= 3 else peaks[-2:]
    recent_troughs = troughs[-3:] if len(troughs) >= 3 else troughs[-2:]

    # Use HIGH for peaks, LOW for troughs
    peak_vals = [high.iloc[i] for i in recent_peaks]
    trough_vals = [low.iloc[i] for i in recent_troughs]

    # Check for HH/HL (uptrend)
    hh = all(peak_vals[i] > peak_vals[i - 1] for i in range(1, len(peak_vals)))
    hl = all(trough_vals[i] > trough_vals[i - 1] for i in range(1, len(trough_vals)))

    # Check for LH/LL (downtrend)
    lh = all(peak_vals[i] < peak_vals[i - 1] for i in range(1, len(peak_vals)))
    ll = all(trough_vals[i] < trough_vals[i - 1] for i in range(1, len(trough_vals)))

    if hh and hl:
        return PatternMatch(
            name="Uptrend Structure (HH/HL)",
            pattern_type="structure",
            bias="bullish",
            confidence=0.75,
            description=f"Making Higher Highs ({', '.join(str(round(v, 1)) for v in peak_vals)}) "
                        f"and Higher Lows ({', '.join(str(round(v, 1)) for v in trough_vals)}). "
                        f"Trend is intact — buy dips near the latest higher low.",
            start_idx=min(recent_peaks[0], recent_troughs[0]),
            end_idx=len(close) - 1,
            key_levels={"latest_hl": round(trough_vals[-1], 2), "latest_hh": round(peak_vals[-1], 2)},
        )
    elif lh and ll:
        return PatternMatch(
            name="Downtrend Structure (LH/LL)",
            pattern_type="structure",
            bias="bearish",
            confidence=0.75,
            description=f"Making Lower Highs ({', '.join(str(round(v, 1)) for v in peak_vals)}) "
                        f"and Lower Lows ({', '.join(str(round(v, 1)) for v in trough_vals)}). "
                        f"Trend is down — sell rallies near the latest lower high.",
            start_idx=min(recent_peaks[0], recent_troughs[0]),
            end_idx=len(close) - 1,
            key_levels={"latest_lh": round(peak_vals[-1], 2), "latest_ll": round(trough_vals[-1], 2)},
        )
    elif hh and ll:
        return PatternMatch(
            name="Expanding Range",
            pattern_type="structure",
            bias="neutral",
            confidence=0.5,
            description="Higher highs but lower lows — volatility expanding. Be cautious with position sizing.",
            start_idx=min(recent_peaks[0], recent_troughs[0]),
            end_idx=len(close) - 1,
            key_levels={},
        )
    elif lh and hl:
        return PatternMatch(
            name="Contracting Range (Triangle)",
            pattern_type="continuation",
            bias="neutral",
            confidence=0.6,
            description="Lower highs and higher lows — price coiling. Breakout imminent! Trade the direction of the break.",
            start_idx=min(recent_peaks[0], recent_troughs[0]),
            end_idx=len(close) - 1,
            key_levels={"upper": round(peak_vals[-1], 2), "lower": round(trough_vals[-1], 2)},
        )

    return None


def detect_flag(close: pd.Series, volume: pd.Series) -> PatternMatch | None:
    """Detect Bull/Bear Flag (continuation pattern).

    A flag is a sharp move followed by a small counter-trend consolidation.
    """
    if len(close) < 30:
        return None

    # Look at last 30 candles
    recent = close.iloc[-30:]

    # Find the sharpest move in the first half
    first_half = recent.iloc[:15]
    move = first_half.iloc[-1] - first_half.iloc[0]
    move_pct = (move / first_half.iloc[0]) * 100

    # Second half should be a gentle counter-trend (the flag)
    second_half = recent.iloc[15:]
    counter_move = second_half.iloc[-1] - second_half.iloc[0]
    counter_pct = (counter_move / second_half.iloc[0]) * 100

    # Bull flag: big up move, small down consolidation
    if move_pct > 0.3 and counter_pct < 0 and abs(counter_pct) < move_pct * 0.5:
        return PatternMatch(
            name="Bull Flag",
            pattern_type="continuation",
            bias="bullish",
            confidence=0.7,
            description=f"Sharp +{round(move_pct, 1)}% rally followed by gentle {round(counter_pct, 1)}% pullback. "
                        f"Expect continuation upward on breakout above flag high.",
            start_idx=len(close) - 30,
            end_idx=len(close) - 1,
            key_levels={"flag_high": round(second_half.max(), 2), "flag_low": round(second_half.min(), 2)},
        )

    # Bear flag: big down move, small up consolidation
    if move_pct < -0.3 and counter_pct > 0 and abs(counter_pct) < abs(move_pct) * 0.5:
        return PatternMatch(
            name="Bear Flag",
            pattern_type="continuation",
            bias="bearish",
            confidence=0.7,
            description=f"Sharp {round(move_pct, 1)}% drop followed by gentle +{round(counter_pct, 1)}% bounce. "
                        f"Expect continuation downward on break below flag low.",
            start_idx=len(close) - 30,
            end_idx=len(close) - 1,
            key_levels={"flag_high": round(second_half.max(), 2), "flag_low": round(second_half.min(), 2)},
        )

    return None


def _to_native(val):
    """Convert numpy types to native Python types for JSON serialization."""
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return float(val)
    if isinstance(val, np.ndarray):
        return val.tolist()
    if isinstance(val, dict):
        return {k: _to_native(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_to_native(v) for v in val]
    return val


def detect_all_patterns(df: pd.DataFrame, timeframe: str = "5m") -> dict:
    """Run all pattern detectors and return results.

    Args:
        df: OHLCV DataFrame with DatetimeIndex.
        timeframe: Label for the timeframe (e.g. '5m', '15m').

    Returns:
        Dict with 'patterns' list and 'support_resistance' data.
    """
    if df.empty or len(df) < 20:
        return {"patterns": [], "support_resistance": {}}

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df.get("volume", pd.Series(dtype=float))

    patterns: list[PatternMatch] = []

    # Helper to extract timestamp string from DataFrame index
    def _ts(idx: int) -> str:
        try:
            return str(df.index[idx])
        except (IndexError, KeyError):
            return ""

    # Run all detectors
    detectors = [
        lambda: detect_trend_structure(high, low, close),
        lambda: detect_double_top(high, low, close),
        lambda: detect_double_bottom(high, low, close),
        lambda: detect_head_and_shoulders(high, low, close),
        lambda: detect_flag(close, volume),
    ]

    for detector in detectors:
        try:
            result = detector()
            if result:
                # Convert numpy types in key_levels
                result.key_levels = _to_native(result.key_levels)
                # Populate timestamps and timeframe
                result.timeframe = timeframe
                result.start_time = _ts(result.start_idx)
                result.end_time = _ts(result.end_idx)
                # Build pivot_times from key pattern indices
                result.pivot_times = [_ts(i) for i in (result.pivot_times or []) if isinstance(i, int)]
                patterns.append(result)
        except Exception:
            continue

    # Support/Resistance
    sr = _to_native(detect_support_resistance(high, low, close))

    # Sort by confidence
    patterns.sort(key=lambda p: p.confidence, reverse=True)

    return {
        "patterns": patterns,
        "support_resistance": sr,
    }

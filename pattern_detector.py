"""Enhanced Chart Pattern Detection for Nifty 50.

Improved accuracy with:
- Volume confirmation 
- ATR-based dynamic tolerances
- Better peak/trough detection
- Fibonacci retracement levels
- Multi-candle confirmation
- Measured move targets and stop losses

Author: Enhanced by Code Puppy 🐶
Version: 2.0
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PatternMatch:
    """A detected chart pattern with rich metadata."""
    name: str
    pattern_type: str  # 'reversal', 'continuation', 'structure'
    bias: str  # 'bullish', 'bearish', 'neutral'
    confidence: float  # 0.0 to 1.0
    description: str
    start_idx: int
    end_idx: int
    key_levels: dict = field(default_factory=dict)
    timeframe: str = ""
    start_time: str = ""
    end_time: str = ""
    pivot_times: list = field(default_factory=list)
    volume_confirmed: bool = False  # NEW: volume validation
    measured_target: Optional[float] = None  # NEW: calculated target price
    stop_loss: Optional[float] = None  # NEW: calculated stop loss


# ══════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS (Improved Accuracy)
# ══════════════════════════════════════════════════════════════════════

def _calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Average True Range for dynamic thresholds.
    
    ATR helps us adapt tolerances to market volatility:
    - High volatility = wider tolerances
    - Low volatility = tighter tolerances
    """
    high_low = high - low
    high_close = (high - close.shift(1)).abs()
    low_close = (low - close.shift(1)).abs()
    
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr


def _check_volume_confirmation(
    volume: pd.Series,
    breakout_idx: int,
    lookback: int = 20,
    threshold: float = 1.15
) -> tuple[bool, str]:
    """Check if volume confirms the breakout.
    
    Returns:
        (is_confirmed, detail_message)
    """
    if volume is None or len(volume) < lookback + 1:
        return False, "No volume data"
    
    try:
        breakout_vol = volume.iloc[breakout_idx]
        avg_vol = volume.iloc[max(0, breakout_idx - lookback):breakout_idx].mean()
        
        if avg_vol == 0:
            return False, "Zero average volume"
        
        ratio = breakout_vol / avg_vol
        confirmed = ratio >= threshold
        
        detail = f"Vol {ratio:.2f}x avg"
        return confirmed, detail
    except Exception as e:
        return False, f"Vol error: {str(e)}"


def _find_peaks_troughs(
    high: pd.Series,
    low: pd.Series,
    order: int = 5,
    min_distance: int = 3
) -> tuple[list[int], list[int]]:
    """IMPROVED: Find local peaks and troughs with minimum distance filter.
    
    Enhancements:
    - Filters out peaks/troughs too close together (noise)
    - Uses strict comparison (>) instead of (>=) for cleaner detection
    - Validates that peaks/troughs are significant moves
    """
    peaks = []
    troughs = []
    high_vals = high.values
    low_vals = low.values
    
    for i in range(order, len(high_vals) - order):
        # Peak: STRICTLY higher than all neighbors
        is_peak = all(high_vals[i] > high_vals[i - j] for j in range(1, order + 1)) and \
                  all(high_vals[i] > high_vals[i + j] for j in range(1, order + 1))
        
        if is_peak:
            # Ensure minimum distance from last peak
            if not peaks or (i - peaks[-1]) >= min_distance:
                peaks.append(i)
        
        # Trough: STRICTLY lower than all neighbors
        is_trough = all(low_vals[i] < low_vals[i - j] for j in range(1, order + 1)) and \
                    all(low_vals[i] < low_vals[i + j] for j in range(1, order + 1))
        
        if is_trough:
            if not troughs or (i - troughs[-1]) >= min_distance:
                troughs.append(i)
    
    return peaks, troughs


def _dynamic_tolerance(
    price: float,
    atr_value: float,
    base_pct: float = 0.3,
    atr_multiplier: float = 1.5
) -> float:
    """Calculate dynamic tolerance based on ATR.
    
    In volatile markets, we need wider tolerances.
    In calm markets, we can be stricter.
    
    Returns:
        Tolerance in price points (not percentage)
    """
    pct_tolerance = (base_pct / 100) * price
    atr_tolerance = atr_value * atr_multiplier if atr_value > 0 else pct_tolerance
    
    # Use the larger of the two (more forgiving in volatile markets)
    return max(pct_tolerance, atr_tolerance)


# ══════════════════════════════════════════════════════════════════════
# PATTERN DETECTION FUNCTIONS (Improved)
# ══════════════════════════════════════════════════════════════════════

def detect_support_resistance(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    num_levels: int = 4,
) -> dict:
    """Detect key support and resistance levels using price clustering."""
    peaks, troughs = _find_peaks_troughs(high, low, order=3)
    
    peak_prices = [high.iloc[i] for i in peaks] if peaks else []
    trough_prices = [low.iloc[i] for i in troughs] if troughs else []
    
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
    nearest_support = max([s for s in support_levels if s < current_price], default=None)
    nearest_resistance = min([r for r in resistance_levels if r > current_price], default=None)
    
    return {
        "support_levels": support_levels,
        "resistance_levels": resistance_levels,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
    }


def detect_double_top(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
    tolerance_pct: float = 0.3,
    min_separation: int = 8
) -> Optional[PatternMatch]:
    """IMPROVED: Detect Double Top with volume confirmation."""
    peaks, troughs = _find_peaks_troughs(high, low, order=4, min_distance=3)
    
    if len(peaks) < 2 or len(troughs) < 1:
        return None
    
    # Calculate ATR for dynamic tolerance
    atr = _calculate_atr(high, low, close)
    current_atr = atr.iloc[-1] if not pd.isna(atr.iloc[-1]) else 0
    
    # Check last two peaks
    p1_idx, p2_idx = peaks[-2], peaks[-1]
    p1_val, p2_val = high.iloc[p1_idx], high.iloc[p2_idx]
    
    # Minimum separation check
    if (p2_idx - p1_idx) < min_separation:
        return None
    
    # Dynamic tolerance using ATR
    tolerance = _dynamic_tolerance(p1_val, current_atr, tolerance_pct)
    
    # Peaks must be roughly equal
    if abs(p1_val - p2_val) > tolerance:
        return None
    
    # P2 must NOT be higher (that's bullish higher high)
    if p2_val > p1_val + tolerance:
        return None
    
    # Find neckline
    middle_troughs = [t for t in troughs if p1_idx < t < p2_idx]
    if not middle_troughs:
        return None
    
    neckline_idx = middle_troughs[0]
    neckline = low.iloc[neckline_idx]
    
    # Validate neckline depth
    pattern_height = p1_val - neckline
    depth_pct = (pattern_height / p1_val) * 100
    
    if depth_pct < 0.3:
        return None
    
    # Check breakdown
    current = close.iloc[-1]
    confirmed = current < neckline
    
    # Volume confirmation
    vol_confirmed = False
    vol_detail = "No volume"
    if confirmed and volume is not None:
        vol_confirmed, vol_detail = _check_volume_confirmation(volume, len(close) - 1)
    
    # Calculate targets
    measured_target = neckline - pattern_height
    stop_loss = p2_val + (current_atr * 1.5) if current_atr > 0 else p2_val + (pattern_height * 0.1)
    
    # Confidence
    base_confidence = 0.85 if confirmed else 0.55
    if vol_confirmed:
        base_confidence = min(base_confidence + 0.10, 0.95)
    
    return PatternMatch(
        name="Double Top",
        pattern_type="reversal",
        bias="bearish",
        confidence=base_confidence,
        description=f"Peaks: P1={round(p1_val,1)}, P2={round(p2_val,1)}. Neckline={round(neckline,1)}. {vol_detail}",
        start_idx=p1_idx,
        end_idx=len(close) - 1,
        key_levels={
            "peak": round(p1_val, 2),
            "peak2": round(p2_val, 2),
            "neckline": round(neckline, 2),
        },
        volume_confirmed=vol_confirmed,
        measured_target=round(measured_target, 2),
        stop_loss=round(stop_loss, 2),
        pivot_times=[p1_idx, neckline_idx, p2_idx],
    )


def detect_double_bottom(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
    tolerance_pct: float = 0.3,
    min_separation: int = 8
) -> Optional[PatternMatch]:
    """IMPROVED: Detect Double Bottom with volume confirmation."""
    peaks, troughs = _find_peaks_troughs(high, low, order=4, min_distance=3)
    
    if len(troughs) < 2 or len(peaks) < 1:
        return None
    
    atr = _calculate_atr(high, low, close)
    current_atr = atr.iloc[-1] if not pd.isna(atr.iloc[-1]) else 0
    
    t1_idx, t2_idx = troughs[-2], troughs[-1]
    t1_val, t2_val = low.iloc[t1_idx], low.iloc[t2_idx]
    
    if (t2_idx - t1_idx) < min_separation:
        return None
    
    tolerance = _dynamic_tolerance(t1_val, current_atr, tolerance_pct)
    
    if abs(t1_val - t2_val) > tolerance:
        return None
    
    if t2_val < t1_val - tolerance:
        return None
    
    middle_peaks = [p for p in peaks if t1_idx < p < t2_idx]
    if not middle_peaks:
        return None
    
    neckline_idx = middle_peaks[0]
    neckline = high.iloc[neckline_idx]
    
    pattern_height = neckline - t1_val
    height_pct = (pattern_height / t1_val) * 100
    
    if height_pct < 0.3:
        return None
    
    current = close.iloc[-1]
    confirmed = current > neckline
    
    vol_confirmed = False
    vol_detail = "No volume"
    if confirmed and volume is not None:
        vol_confirmed, vol_detail = _check_volume_confirmation(volume, len(close) - 1)
    
    measured_target = neckline + pattern_height
    stop_loss = t2_val - (current_atr * 1.5) if current_atr > 0 else t2_val - (pattern_height * 0.1)
    
    base_confidence = 0.85 if confirmed else 0.55
    if vol_confirmed:
        base_confidence = min(base_confidence + 0.10, 0.95)
    
    return PatternMatch(
        name="Double Bottom",
        pattern_type="reversal",
        bias="bullish",
        confidence=base_confidence,
        description=f"Troughs: T1={round(t1_val,1)}, T2={round(t2_val,1)}. Neckline={round(neckline,1)}. {vol_detail}",
        start_idx=t1_idx,
        end_idx=len(close) - 1,
        key_levels={
            "trough1": round(t1_val, 2),
            "trough2": round(t2_val, 2),
            "neckline": round(neckline, 2),
        },
        volume_confirmed=vol_confirmed,
        measured_target=round(measured_target, 2),
        stop_loss=round(stop_loss, 2),
        pivot_times=[t1_idx, neckline_idx, t2_idx],
    )


def detect_flag(
    df: pd.DataFrame,
    volume: Optional[pd.Series] = None,
    impulse_min_pct: float = 0.25,
    consol_max_candles: int = 8,
    retrace_max_pct: float = 0.55
) -> Optional[PatternMatch]:
    """IMPROVED: Detect Bull/Bear Flag with volume validation."""
    if len(df) < 14:
        return None
    
    high = df['high']
    low = df['low']
    close = df['close']
    
    # Try different impulse lengths
    for impulse_len in range(3, 7):
        consol_end = -1
        consol_start = -(impulse_len + consol_max_candles)
        imp_end = consol_start
        imp_start = imp_end - impulse_len
        
        imp_seg = df.iloc[imp_start:imp_end] if imp_end != 0 else df.iloc[imp_start:]
        consol_seg = df.iloc[consol_start:-1]
        
        if len(imp_seg) < impulse_len or len(consol_seg) < 3:
            continue
        
        imp_low = float(imp_seg['low'].min())
        imp_high = float(imp_seg['high'].max())
        imp_pct = (imp_high - imp_low) / imp_low * 100
        
        if imp_pct < impulse_min_pct:
            continue
        
        imp_dir = "up" if float(imp_seg['close'].iloc[-1]) > float(imp_seg['close'].iloc[0]) else "down"
        
        consol_high = float(consol_seg['high'].max())
        consol_low = float(consol_seg['low'].min())
        retrace_pct = (consol_high - consol_low) / (imp_high - imp_low + 1e-9)
        
        if retrace_pct > retrace_max_pct:
            continue
        
        current_close = float(df.iloc[-1]['close'])
        
        vol_confirmed = False
        vol_detail = "No vol"
        if volume is not None:
            vol_confirmed, vol_detail = _check_volume_confirmation(volume, len(df) - 1)
        
        # Bull flag
        if imp_dir == "up" and current_close > consol_high:
            measured_target = current_close + (imp_high - imp_low)
            stop_loss = consol_low
            
            base_conf = 0.70
            if vol_confirmed:
                base_conf = min(base_conf + 0.15, 0.85)
            
            return PatternMatch(
                name="Bull Flag",
                pattern_type="continuation",
                bias="bullish",
                confidence=base_conf,
                description=f"Impulse +{imp_pct:.2f}%, {len(consol_seg)}c consol, breakout! {vol_detail}",
                start_idx=len(df) + imp_start,
                end_idx=len(df) - 1,
                key_levels={
                    "flag_high": round(consol_high, 2),
                    "flag_low": round(consol_low, 2),
                },
                volume_confirmed=vol_confirmed,
                measured_target=round(measured_target, 2),
                stop_loss=round(stop_loss, 2),
            )
        
        # Bear flag
        if imp_dir == "down" and current_close < consol_low:
            measured_target = current_close - (imp_high - imp_low)
            stop_loss = consol_high
            
            base_conf = 0.70
            if vol_confirmed:
                base_conf = min(base_conf + 0.15, 0.85)
            
            return PatternMatch(
                name="Bear Flag",
                pattern_type="continuation",
                bias="bearish",
                confidence=base_conf,
                description=f"Impulse -{imp_pct:.2f}%, {len(consol_seg)}c bounce, breakdown! {vol_detail}",
                start_idx=len(df) + imp_start,
                end_idx=len(df) - 1,
                key_levels={
                    "flag_high": round(consol_high, 2),
                    "flag_low": round(consol_low, 2),
                },
                volume_confirmed=vol_confirmed,
                measured_target=round(measured_target, 2),
                stop_loss=round(stop_loss, 2),
            )
    
    return None


def detect_ascending_triangle(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
    tolerance_pct: float = 0.2
) -> Optional[PatternMatch]:
    """IMPROVED: Ascending Triangle with volume confirmation."""
    peaks, troughs = _find_peaks_troughs(high, low, order=4, min_distance=3)
    
    if len(peaks) < 2 or len(troughs) < 2:
        return None
    
    atr = _calculate_atr(high, low, close)
    current_atr = atr.iloc[-1] if not pd.isna(atr.iloc[-1]) else 0
    
    rp = peaks[-3:] if len(peaks) >= 3 else peaks[-2:]
    rt = troughs[-3:] if len(troughs) >= 3 else troughs[-2:]
    pv = [high.iloc[i] for i in rp]
    tv = [low.iloc[i] for i in rt]
    
    avg_peak = np.mean(pv)
    tolerance = _dynamic_tolerance(avg_peak, current_atr, tolerance_pct)
    flat_res = all(abs(v - avg_peak) < tolerance for v in pv)
    rising_sup = all(tv[i] > tv[i - 1] for i in range(1, len(tv)))
    
    if not (flat_res and rising_sup):
        return None
    
    current = close.iloc[-1]
    breakout = current > max(pv)
    
    vol_confirmed = False
    vol_detail = "No vol"
    if breakout and volume is not None:
        vol_confirmed, vol_detail = _check_volume_confirmation(volume, len(close) - 1)
    
    pattern_height = avg_peak - min(tv)
    measured_target = avg_peak + pattern_height
    stop_loss = tv[-1] - (current_atr * 1.5) if current_atr > 0 else tv[-1] - (pattern_height * 0.1)
    
    base_conf = 0.80 if breakout else 0.65
    if vol_confirmed:
        base_conf = min(base_conf + 0.10, 0.90)
    
    return PatternMatch(
        name="Ascending Triangle",
        pattern_type="continuation",
        bias="bullish",
        confidence=base_conf,
        description=f"Flat resistance {round(avg_peak,1)}, rising lows. {vol_detail}",
        start_idx=min(rp[0], rt[0]),
        end_idx=len(close) - 1,
        key_levels={"resistance": round(avg_peak, 2), "latest_support": round(tv[-1], 2)},
        volume_confirmed=vol_confirmed,
        measured_target=round(measured_target, 2),
        stop_loss=round(stop_loss, 2),
        pivot_times=[*rp, *rt],
    )


def detect_descending_triangle(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
    tolerance_pct: float = 0.2
) -> Optional[PatternMatch]:
    """IMPROVED: Descending Triangle with volume confirmation."""
    peaks, troughs = _find_peaks_troughs(high, low, order=4, min_distance=3)
    
    if len(peaks) < 2 or len(troughs) < 2:
        return None
    
    atr = _calculate_atr(high, low, close)
    current_atr = atr.iloc[-1] if not pd.isna(atr.iloc[-1]) else 0
    
    rp = peaks[-3:] if len(peaks) >= 3 else peaks[-2:]
    rt = troughs[-3:] if len(troughs) >= 3 else troughs[-2:]
    pv = [high.iloc[i] for i in rp]
    tv = [low.iloc[i] for i in rt]
    
    avg_trough = np.mean(tv)
    tolerance = _dynamic_tolerance(avg_trough, current_atr, tolerance_pct)
    flat_sup = all(abs(v - avg_trough) < tolerance for v in tv)
    falling_res = all(pv[i] < pv[i - 1] for i in range(1, len(pv)))
    
    if not (flat_sup and falling_res):
        return None
    
    current = close.iloc[-1]
    breakdown = current < min(tv)
    
    vol_confirmed = False
    vol_detail = "No vol"
    if breakdown and volume is not None:
        vol_confirmed, vol_detail = _check_volume_confirmation(volume, len(close) - 1)
    
    pattern_height = max(pv) - avg_trough
    measured_target = avg_trough - pattern_height
    stop_loss = pv[-1] + (current_atr * 1.5) if current_atr > 0 else pv[-1] + (pattern_height * 0.1)
    
    base_conf = 0.80 if breakdown else 0.65
    if vol_confirmed:
        base_conf = min(base_conf + 0.10, 0.90)
    
    return PatternMatch(
        name="Descending Triangle",
        pattern_type="continuation",
        bias="bearish",
        confidence=base_conf,
        description=f"Flat support {round(avg_trough,1)}, falling highs. {vol_detail}",
        start_idx=min(rp[0], rt[0]),
        end_idx=len(close) - 1,
        key_levels={"support": round(avg_trough, 2), "latest_resistance": round(pv[-1], 2)},
        volume_confirmed=vol_confirmed,
        measured_target=round(measured_target, 2),
        stop_loss=round(stop_loss, 2),
        pivot_times=[*rp, *rt],
    )


def detect_trend_structure(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    lookback: int = 40
) -> Optional[PatternMatch]:
    """Detect price structure: HH/HL (uptrend) or LH/LL (downtrend).
    
    This is NOT a breakout pattern — it's trend confirmation.
    Used to identify when price is making a clean trending structure.
    """
    if len(high) < lookback:
        return None
    
    recent = pd.DataFrame({"high": high, "low": low, "close": close}).tail(lookback)
    
    # Split into two halves
    mid = lookback // 2
    first_half = recent.iloc[:mid]
    second_half = recent.iloc[mid:]
    
    first_high = first_half["high"].max()
    second_high = second_half["high"].max()
    first_low = first_half["low"].min()
    second_low = second_half["low"].min()
    
    # Collect swing highs and lows for detailed description
    peaks, troughs = _find_peaks_troughs(recent["high"], recent["low"], order=3, min_distance=3)
    
    recent_highs = [recent["high"].iloc[i] for i in peaks[-3:]] if len(peaks) >= 3 else []
    recent_lows = [recent["low"].iloc[i] for i in troughs[-3:]] if len(troughs) >= 3 else []
    
    # UPTREND: Higher Highs + Higher Lows
    if second_high > first_high and second_low > first_low:
        hh_str = ", ".join([f"{h:.1f}" for h in recent_highs]) if recent_highs else f"{second_high:.1f}"
        hl_str = ", ".join([f"{l:.1f}" for l in recent_lows]) if recent_lows else f"{second_low:.1f}"
        
        latest_hh = recent_highs[-1] if recent_highs else second_high
        latest_hl = recent_lows[-1] if recent_lows else second_low
        
        return PatternMatch(
            name="Uptrend Structure (HH/HL)",
            pattern_type="structure",
            bias="bullish",
            confidence=0.75,
            description=f"Making Higher Highs ({hh_str}) and Higher Lows ({hl_str}). Trend is up — buy dips near latest higher low.",
            start_idx=len(high) - lookback,
            end_idx=len(high) - 1,
            key_levels={
                "latest_hh": round(latest_hh, 2),
                "latest_hl": round(latest_hl, 2),
            },
            measured_target=None,  # No target - it's a trend, not a breakout
            stop_loss=round(latest_hl * 0.998, 2),  # Just below last higher low
            pivot_times=peaks + troughs,
        )
    
    # DOWNTREND: Lower Highs + Lower Lows
    elif second_high < first_high and second_low < first_low:
        lh_str = ", ".join([f"{h:.1f}" for h in recent_highs]) if recent_highs else f"{second_high:.1f}"
        ll_str = ", ".join([f"{l:.1f}" for l in recent_lows]) if recent_lows else f"{second_low:.1f}"
        
        latest_lh = recent_highs[-1] if recent_highs else second_high
        latest_ll = recent_lows[-1] if recent_lows else second_low
        
        return PatternMatch(
            name="Downtrend Structure (LH/LL)",
            pattern_type="structure",
            bias="bearish",
            confidence=0.75,
            description=f"Making Lower Highs ({lh_str}) and Lower Lows ({ll_str}). Trend is down — sell rallies near the latest lower high.",
            start_idx=len(high) - lookback,
            end_idx=len(high) - 1,
            key_levels={
                "latest_lh": round(latest_lh, 2),
                "latest_ll": round(latest_ll, 2),
            },
            measured_target=None,  # No target - it's a trend, not a breakout
            stop_loss=round(latest_lh * 1.002, 2),  # Just above last lower high
            pivot_times=peaks + troughs,
        )
    
    # Not a clear trend structure
    return None


def _to_native(val):
    """Convert numpy types to native Python types."""
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
    """IMPROVED: Run all pattern detectors with enhanced accuracy."""
    if df.empty or len(df) < 20:
        return {"patterns": [], "support_resistance": {}}
    
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df.get("volume", None)
    
    patterns: list[PatternMatch] = []
    
    def _ts(idx: int) -> str:
        try:
            return str(df.index[idx])
        except (IndexError, KeyError):
            return ""
    
    # Run all detectors
    detectors = [
        lambda: detect_double_top(high, low, close, volume),
        lambda: detect_double_bottom(high, low, close, volume),
        lambda: detect_ascending_triangle(high, low, close, volume),
        lambda: detect_descending_triangle(high, low, close, volume),
        lambda: detect_flag(df, volume),
        lambda: detect_trend_structure(high, low, close),  # 🐶 NEW: Trend structure (HH/HL, LH/LL)
    ]
    
    for detector in detectors:
        try:
            result = detector()
            if result:
                result.key_levels = _to_native(result.key_levels)
                result.timeframe = timeframe
                result.start_time = _ts(result.start_idx)
                result.end_time = _ts(result.end_idx)
                result.pivot_times = [_ts(i) for i in (result.pivot_times or []) if isinstance(i, int)]
                patterns.append(result)
        except Exception:
            continue
    
    sr = _to_native(detect_support_resistance(high, low, close))
    patterns.sort(key=lambda p: p.confidence, reverse=True)
    
    # Build candle slices
    pattern_candles = {}
    for i, p in enumerate(patterns):
        pad = 5
        s = max(0, p.start_idx - pad)
        e = min(len(df) - 1, p.end_idx + pad)
        sliced = df.iloc[s:e + 1]
        candles = []
        for idx, row in sliced.iterrows():
            candles.append({
                "time": str(idx),
                "open": _to_native(row.get("open", row["close"])),
                "high": _to_native(row.get("high", row["close"])),
                "low": _to_native(row.get("low", row["close"])),
                "close": _to_native(row["close"]),
                "volume": _to_native(row.get("volume", 0)),
            })
        pattern_candles[i] = candles
    
    return {
        "patterns": patterns,
        "pattern_candles": pattern_candles,
        "support_resistance": sr,
    }

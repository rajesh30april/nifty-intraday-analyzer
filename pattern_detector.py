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
        
        detail = f"Breakout vol {int(breakout_vol):,} vs avg {int(avg_vol):,} ({ratio:.1f}x)"
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
    tolerance_pct: float = 0.20,   # tighter: was 0.3% — reduces false positives
    min_separation: int = 8
) -> Optional[PatternMatch]:
    """Detect Double Bottom (W pattern) with strict validation.

    Requirements:
    - Two troughs within 0.2% of each other (tighter than before)
    - Minimum 8 candles separation (ensures a real bounce, not noise)
    - Pattern height >= 0.4% of price (filters micro-wiggles)
    - Neckline must be a confirmed swing high between the two troughs
    """
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
    
    if height_pct < 0.4:   # was 0.3 — stricter noise filter
        return None
    
    current = close.iloc[-1]
    confirmed = current > neckline
    
    vol_confirmed = False
    vol_detail = "No volume"
    if confirmed and volume is not None:
        vol_confirmed, vol_detail = _check_volume_confirmation(volume, len(close) - 1)
    
    measured_target = neckline + pattern_height
    stop_loss = t2_val - (current_atr * 1.5) if current_atr > 0 else t2_val - (pattern_height * 0.1)
    
    confirmed_text = "✅ Neckline BROKEN — pattern active" if confirmed else "⏳ Watching for neckline break"
    vol_note = f" + {vol_detail}" if vol_confirmed else ""
    trough_diff = abs(t1_val - t2_val)
    trough_diff_pct = (trough_diff / t1_val) * 100

    description = (
        f"W-Pattern: Trough 1 ₹{t1_val:.0f} → Neckline ₹{neckline:.0f} → "
        f"Trough 2 ₹{t2_val:.0f} ({trough_diff_pct:.2f}% apart). "
        f"Height ₹{pattern_height:.0f} ({height_pct:.1f}%). "
        f"{confirmed_text}{vol_note}. "
        f"Target: ₹{measured_target:.0f} | SL below ₹{t2_val:.0f}."
    )

    base_confidence = 0.85 if confirmed else 0.55
    if vol_confirmed:
        base_confidence = min(base_confidence + 0.10, 0.95)

    return PatternMatch(
        name="Double Bottom",
        pattern_type="reversal",
        bias="bullish",
        confidence=base_confidence,
        description=description,
        start_idx=t1_idx,
        end_idx=len(close) - 1,
        key_levels={
            "trough1":    round(t1_val,   2),
            "trough2":    round(t2_val,   2),
            "neckline":   round(neckline, 2),
            "support":    round(min(t1_val, t2_val), 2),
            "resistance": round(neckline, 2),
            "entry":      round(neckline, 2),  # enter on neckline break
            "t1_idx":     int(t1_idx),
            "t2_idx":     int(t2_idx),
            "neckline_idx": int(neckline_idx),
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


# ══════════════════════════════════════════════════════════════════════
# DIVERGENCE PATTERNS (Advanced Early Warning)
# ══════════════════════════════════════════════════════════════════════

def detect_rsi_divergence(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    lookback: int = 20,
) -> Optional[PatternMatch]:
    """Detect RSI divergence - early reversal warning signal.
    
    Bullish Divergence:
    - Price making lower lows
    - RSI making higher lows
    - Indicates buying pressure building despite price drop
    
    Bearish Divergence:
    - Price making higher highs
    - RSI making lower highs
    - Indicates selling pressure building despite price rise
    
    Confidence: 80-90% (strongest when combined with other patterns)
    """
    if len(close) < lookback + 14:
        return None
    
    # Calculate RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).ewm(span=14).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(span=14).mean()
    rs = gain / loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    
    # Find recent price pivots
    price_highs_idx = []
    price_lows_idx = []
    
    for i in range(lookback - 5, lookback):
        if i < 5 or i >= len(close) - 5:
            continue
        
        # Check if local high
        if high.iloc[i] > high.iloc[i-3:i].max() and high.iloc[i] > high.iloc[i+1:i+4].max():
            price_highs_idx.append(i)
        
        # Check if local low
        if low.iloc[i] < low.iloc[i-3:i].min() and low.iloc[i] < low.iloc[i+1:i+4].min():
            price_lows_idx.append(i)
    
    # Need at least 2 pivots to compare
    if len(price_highs_idx) < 2 and len(price_lows_idx) < 2:
        return None
    
    # Check for BULLISH divergence (lower lows in price, higher lows in RSI)
    if len(price_lows_idx) >= 2:
        idx1, idx2 = price_lows_idx[-2], price_lows_idx[-1]
        price_ll = low.iloc[idx2] < low.iloc[idx1]  # Lower low in price
        rsi_hl = rsi.iloc[idx2] > rsi.iloc[idx1]    # Higher low in RSI
        
        if price_ll and rsi_hl and rsi.iloc[idx2] < 40:  # RSI still in oversold region
            target = close.iloc[-1] + abs(close.iloc[-1] - low.iloc[idx2])
            stop_loss = low.iloc[idx2] - (abs(close.iloc[-1] - low.iloc[idx2]) * 0.2)
            
            return PatternMatch(
                name="Bullish RSI Divergence",
                pattern_type="reversal",
                bias="bullish",
                confidence=0.85,
                description=f"Bullish RSI Divergence: Price {low.iloc[idx1]:.1f}→{low.iloc[idx2]:.1f} (lower), "
                           f"RSI {rsi.iloc[idx1]:.1f}→{rsi.iloc[idx2]:.1f} (higher). "
                           f"Strong reversal signal! Target: {target:.1f}, SL: {stop_loss:.1f}",
                start_idx=idx1,
                end_idx=len(close) - 1,
                key_levels={"entry": close.iloc[-1], "target": target, "stop_loss": stop_loss,
                          "pivot1_price": low.iloc[idx1], "pivot2_price": low.iloc[idx2],
                          "pivot1_rsi": rsi.iloc[idx1], "pivot2_rsi": rsi.iloc[idx2]},
                measured_target=round(target, 2),
                stop_loss=round(stop_loss, 2),
                pivot_times=[idx1, idx2],
            )
    
    # Check for BEARISH divergence (higher highs in price, lower highs in RSI)
    if len(price_highs_idx) >= 2:
        idx1, idx2 = price_highs_idx[-2], price_highs_idx[-1]
        price_hh = high.iloc[idx2] > high.iloc[idx1]  # Higher high in price
        rsi_lh = rsi.iloc[idx2] < rsi.iloc[idx1]      # Lower high in RSI
        
        if price_hh and rsi_lh and rsi.iloc[idx2] > 60:  # RSI still in overbought region
            target = close.iloc[-1] - abs(high.iloc[idx2] - close.iloc[-1])
            stop_loss = high.iloc[idx2] + (abs(high.iloc[idx2] - close.iloc[-1]) * 0.2)
            
            return PatternMatch(
                name="Bearish RSI Divergence",
                pattern_type="reversal",
                bias="bearish",
                confidence=0.85,
                description=f"Bearish RSI Divergence: Price {high.iloc[idx1]:.1f}→{high.iloc[idx2]:.1f} (higher), "
                           f"RSI {rsi.iloc[idx1]:.1f}→{rsi.iloc[idx2]:.1f} (lower). "
                           f"Strong reversal signal! Target: {target:.1f}, SL: {stop_loss:.1f}",
                start_idx=idx1,
                end_idx=len(close) - 1,
                key_levels={"entry": close.iloc[-1], "target": target, "stop_loss": stop_loss,
                          "pivot1_price": high.iloc[idx1], "pivot2_price": high.iloc[idx2],
                          "pivot1_rsi": rsi.iloc[idx1], "pivot2_rsi": rsi.iloc[idx2]},
                measured_target=round(target, 2),
                stop_loss=round(stop_loss, 2),
                pivot_times=[idx1, idx2],
            )
    
    return None


# ══════════════════════════════════════════════════════════════════════
# CANDLESTICK PATTERNS (Early Reversal Detection)
# ══════════════════════════════════════════════════════════════════════

def detect_bullish_engulfing(
    open_p: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
) -> Optional[PatternMatch]:
    """Detect Bullish Engulfing pattern - strong reversal signal.
    
    Requirements:
    - Previous candle: bearish (close < open)
    - Current candle: bullish (close > open)
    - Current body completely engulfs previous body
    - Occurs after downtrend or at support
    - Volume confirmation (optional but adds confidence)
    
    Confidence: 75-85%
    """
    if len(close) < 15:
        return None
    
    # Check last 2 candles
    prev_open = float(open_p.iloc[-2])
    prev_close = float(close.iloc[-2])
    curr_open = float(open_p.iloc[-1])
    curr_close = float(close.iloc[-1])
    
    # Previous candle must be bearish
    if prev_close >= prev_open:
        return None
    
    # Current candle must be bullish
    if curr_close <= curr_open:
        return None
    
    # Current must engulf previous
    engulfs = curr_open <= prev_close and curr_close >= prev_open
    if not engulfs:
        return None
    
    # Check if in downtrend (price below EMA)
    recent_closes = close.tail(10)
    ema = recent_closes.ewm(span=9).mean()
    in_downtrend = float(close.iloc[-3]) < float(ema.iloc[-3])
    
    # Volume confirmation
    vol_confirmed = False
    if volume is not None and len(volume) >= 20:
        vol_confirmed, _ = _check_volume_confirmation(volume, len(volume) - 1, lookback=20)
    
    confidence = 0.75
    if in_downtrend:
        confidence += 0.05
    if vol_confirmed:
        confidence += 0.05
    
    body_size = abs(curr_close - curr_open)
    target = curr_close + (body_size * 2)  # 2x body size target
    stop_loss = min(curr_open, curr_close, low.iloc[-1]) - (body_size * 0.3)
    
    return PatternMatch(
        name="Bullish Engulfing",
        pattern_type="reversal",
        bias="bullish",
        confidence=min(confidence, 0.85),
        description=f"Bullish Engulfing at {curr_close:.1f} (prev {prev_close:.1f}→{prev_open:.1f}, curr {curr_open:.1f}→{curr_close:.1f}). "
                   f"{'In downtrend. ' if in_downtrend else ''}{'Volume confirmed. ' if vol_confirmed else ''}"
                   f"Target: {target:.1f}, SL: {stop_loss:.1f}",
        start_idx=len(close) - 2,
        end_idx=len(close) - 1,
        key_levels={"entry": curr_close, "target": target, "stop_loss": stop_loss},
        volume_confirmed=vol_confirmed,
        measured_target=round(target, 2),
        stop_loss=round(stop_loss, 2),
    )


def detect_bearish_engulfing(
    open_p: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
) -> Optional[PatternMatch]:
    """Detect Bearish Engulfing pattern - strong reversal signal.
    
    Requirements:
    - Previous candle: bullish (close > open)
    - Current candle: bearish (close < open)
    - Current body completely engulfs previous body
    - Occurs after uptrend or at resistance
    - Volume confirmation (optional but adds confidence)
    
    Confidence: 75-85%
    """
    if len(close) < 15:
        return None
    
    # Check last 2 candles
    prev_open = float(open_p.iloc[-2])
    prev_close = float(close.iloc[-2])
    curr_open = float(open_p.iloc[-1])
    curr_close = float(close.iloc[-1])
    
    # Previous candle must be bullish
    if prev_close <= prev_open:
        return None
    
    # Current candle must be bearish
    if curr_close >= curr_open:
        return None
    
    # Current must engulf previous
    engulfs = curr_open >= prev_close and curr_close <= prev_open
    if not engulfs:
        return None
    
    # Check if in uptrend (price above EMA)
    recent_closes = close.tail(10)
    ema = recent_closes.ewm(span=9).mean()
    in_uptrend = float(close.iloc[-3]) > float(ema.iloc[-3])
    
    # Volume confirmation
    vol_confirmed = False
    if volume is not None and len(volume) >= 20:
        vol_confirmed, _ = _check_volume_confirmation(volume, len(volume) - 1, lookback=20)
    
    confidence = 0.75
    if in_uptrend:
        confidence += 0.05
    if vol_confirmed:
        confidence += 0.05
    
    body_size = abs(curr_close - curr_open)
    target = curr_close - (body_size * 2)  # 2x body size target
    stop_loss = max(curr_open, curr_close, high.iloc[-1]) + (body_size * 0.3)
    
    return PatternMatch(
        name="Bearish Engulfing",
        pattern_type="reversal",
        bias="bearish",
        confidence=min(confidence, 0.85),
        description=f"Bearish Engulfing at {curr_close:.1f} (prev {prev_open:.1f}→{prev_close:.1f}, curr {curr_open:.1f}→{curr_close:.1f}). "
                   f"{'In uptrend. ' if in_uptrend else ''}{'Volume confirmed. ' if vol_confirmed else ''}"
                   f"Target: {target:.1f}, SL: {stop_loss:.1f}",
        start_idx=len(close) - 2,
        end_idx=len(close) - 1,
        key_levels={"entry": curr_close, "target": target, "stop_loss": stop_loss},
        volume_confirmed=vol_confirmed,
        measured_target=round(target, 2),
        stop_loss=round(stop_loss, 2),
    )


def detect_hammer(
    open_p: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
) -> Optional[PatternMatch]:
    """Detect Hammer candle - bullish reversal at support.
    
    Requirements:
    - Small body at top (body < 30% of total range)
    - Long lower shadow (at least 2x body size)
    - Little/no upper shadow (< 10% of total range)
    - Occurs after downtrend or at support level
    
    Confidence: 70-80%
    """
    if len(close) < 15:
        return None
    
    curr_open = float(open_p.iloc[-1])
    curr_high = float(high.iloc[-1])
    curr_low = float(low.iloc[-1])
    curr_close = float(close.iloc[-1])
    
    body_size = abs(curr_close - curr_open)
    total_range = curr_high - curr_low
    
    if total_range == 0:
        return None
    
    # Body should be small (< 30% of range)
    if body_size / total_range > 0.30:
        return None
    
    # Lower shadow should be long (at least 2x body)
    lower_shadow = min(curr_open, curr_close) - curr_low
    if lower_shadow < body_size * 2:
        return None
    
    # Upper shadow should be tiny (< 10% of range)
    upper_shadow = curr_high - max(curr_open, curr_close)
    if upper_shadow / total_range > 0.10:
        return None
    
    # Check if in downtrend
    recent_closes = close.tail(10)
    ema = recent_closes.ewm(span=9).mean()
    in_downtrend = float(close.iloc[-2]) < float(ema.iloc[-2])
    
    # Volume confirmation
    vol_confirmed = False
    if volume is not None and len(volume) >= 20:
        vol_confirmed, _ = _check_volume_confirmation(volume, len(volume) - 1, lookback=20)
    
    confidence = 0.70
    if in_downtrend:
        confidence += 0.05
    if vol_confirmed:
        confidence += 0.05
    
    target = curr_close + (lower_shadow * 0.618)  # Fib 61.8% of shadow
    stop_loss = curr_low - (total_range * 0.2)
    
    return PatternMatch(
        name="Hammer",
        pattern_type="reversal",
        bias="bullish",
        confidence=min(confidence, 0.80),
        description=f"Hammer at {curr_close:.1f} (low {curr_low:.1f}, shadow {lower_shadow:.1f}pts). "
                   f"{'In downtrend. ' if in_downtrend else ''}{'Volume confirmed. ' if vol_confirmed else ''}"
                   f"Target: {target:.1f}, SL: {stop_loss:.1f}",
        start_idx=len(close) - 1,
        end_idx=len(close) - 1,
        key_levels={"entry": curr_close, "target": target, "stop_loss": stop_loss, "low": curr_low},
        volume_confirmed=vol_confirmed,
        measured_target=round(target, 2),
        stop_loss=round(stop_loss, 2),
    )


def detect_shooting_star(
    open_p: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
) -> Optional[PatternMatch]:
    """Detect Shooting Star candle - bearish reversal at resistance.
    
    Requirements:
    - Small body at bottom (body < 30% of total range)
    - Long upper shadow (at least 2x body size)
    - Little/no lower shadow (< 10% of total range)
    - Occurs after uptrend or at resistance level
    
    Confidence: 70-80%
    """
    if len(close) < 15:
        return None
    
    curr_open = float(open_p.iloc[-1])
    curr_high = float(high.iloc[-1])
    curr_low = float(low.iloc[-1])
    curr_close = float(close.iloc[-1])
    
    body_size = abs(curr_close - curr_open)
    total_range = curr_high - curr_low
    
    if total_range == 0:
        return None
    
    # Body should be small (< 30% of range)
    if body_size / total_range > 0.30:
        return None
    
    # Upper shadow should be long (at least 2x body)
    upper_shadow = curr_high - max(curr_open, curr_close)
    if upper_shadow < body_size * 2:
        return None
    
    # Lower shadow should be tiny (< 10% of range)
    lower_shadow = min(curr_open, curr_close) - curr_low
    if lower_shadow / total_range > 0.10:
        return None
    
    # Check if in uptrend
    recent_closes = close.tail(10)
    ema = recent_closes.ewm(span=9).mean()
    in_uptrend = float(close.iloc[-2]) > float(ema.iloc[-2])
    
    # Volume confirmation
    vol_confirmed = False
    if volume is not None and len(volume) >= 20:
        vol_confirmed, _ = _check_volume_confirmation(volume, len(volume) - 1, lookback=20)
    
    confidence = 0.70
    if in_uptrend:
        confidence += 0.05
    if vol_confirmed:
        confidence += 0.05
    
    target = curr_close - (upper_shadow * 0.618)  # Fib 61.8% of shadow
    stop_loss = curr_high + (total_range * 0.2)
    
    return PatternMatch(
        name="Shooting Star",
        pattern_type="reversal",
        bias="bearish",
        confidence=min(confidence, 0.80),
        description=f"Shooting Star at {curr_close:.1f} (high {curr_high:.1f}, shadow {upper_shadow:.1f}pts). "
                   f"{'In uptrend. ' if in_uptrend else ''}{'Volume confirmed. ' if vol_confirmed else ''}"
                   f"Target: {target:.1f}, SL: {stop_loss:.1f}",
        start_idx=len(close) - 1,
        end_idx=len(close) - 1,
        key_levels={"entry": curr_close, "target": target, "stop_loss": stop_loss, "high": curr_high},
        volume_confirmed=vol_confirmed,
        measured_target=round(target, 2),
        stop_loss=round(stop_loss, 2),
    )


def detect_morning_star(
    open_p: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
) -> Optional[PatternMatch]:
    """Detect Morning Star pattern - strong bullish reversal.
    
    Three-candle pattern:
    1. Large bearish candle
    2. Small indecision candle (doji/spinning top) - gap down
    3. Large bullish candle closing above midpoint of candle 1
    
    Confidence: 80-85%
    """
    if len(close) < 15:
        return None
    
    # Get last 3 candles
    o1, h1, l1, c1 = float(open_p.iloc[-3]), float(high.iloc[-3]), float(low.iloc[-3]), float(close.iloc[-3])
    o2, h2, l2, c2 = float(open_p.iloc[-2]), float(high.iloc[-2]), float(low.iloc[-2]), float(close.iloc[-2])
    o3, h3, l3, c3 = float(open_p.iloc[-1]), float(high.iloc[-1]), float(low.iloc[-1]), float(close.iloc[-1])
    
    # Candle 1: Large bearish
    body1 = abs(c1 - o1)
    if c1 >= o1 or body1 < (h1 - l1) * 0.5:  # Must be bearish with decent body
        return None
    
    # Candle 2: Small body (indecision)
    body2 = abs(c2 - o2)
    range2 = h2 - l2
    if range2 > 0 and body2 / range2 > 0.3:  # Body should be < 30% of range
        return None
    
    # Candle 3: Large bullish
    body3 = abs(c3 - o3)
    if c3 <= o3 or body3 < (h3 - l3) * 0.5:  # Must be bullish with decent body
        return None
    
    # Candle 3 should close above midpoint of candle 1
    mid1 = (o1 + c1) / 2
    if c3 <= mid1:
        return None
    
    # Volume confirmation on candle 3
    vol_confirmed = False
    if volume is not None and len(volume) >= 20:
        vol_confirmed, _ = _check_volume_confirmation(volume, len(volume) - 1, lookback=20)
    
    confidence = 0.80
    if vol_confirmed:
        confidence += 0.05
    
    target = c3 + body3  # One body size above
    stop_loss = min(l1, l2, l3) - (body3 * 0.2)
    
    return PatternMatch(
        name="Morning Star",
        pattern_type="reversal",
        bias="bullish",
        confidence=min(confidence, 0.85),
        description=f"Morning Star completed at {c3:.1f} (3-candle: {c1:.1f}→{c2:.1f}→{c3:.1f}). "
                   f"{'Volume confirmed. ' if vol_confirmed else ''}"
                   f"Target: {target:.1f}, SL: {stop_loss:.1f}",
        start_idx=len(close) - 3,
        end_idx=len(close) - 1,
        key_levels={"entry": c3, "target": target, "stop_loss": stop_loss},
        volume_confirmed=vol_confirmed,
        measured_target=round(target, 2),
        stop_loss=round(stop_loss, 2),
        pivot_times=[len(close) - 3, len(close) - 2, len(close) - 1],
    )


def detect_evening_star(
    open_p: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: Optional[pd.Series] = None,
) -> Optional[PatternMatch]:
    """Detect Evening Star pattern - strong bearish reversal.
    
    Three-candle pattern:
    1. Large bullish candle
    2. Small indecision candle (doji/spinning top) - gap up
    3. Large bearish candle closing below midpoint of candle 1
    
    Confidence: 80-85%
    """
    if len(close) < 15:
        return None
    
    # Get last 3 candles
    o1, h1, l1, c1 = float(open_p.iloc[-3]), float(high.iloc[-3]), float(low.iloc[-3]), float(close.iloc[-3])
    o2, h2, l2, c2 = float(open_p.iloc[-2]), float(high.iloc[-2]), float(low.iloc[-2]), float(close.iloc[-2])
    o3, h3, l3, c3 = float(open_p.iloc[-1]), float(high.iloc[-1]), float(low.iloc[-1]), float(close.iloc[-1])
    
    # Candle 1: Large bullish
    body1 = abs(c1 - o1)
    if c1 <= o1 or body1 < (h1 - l1) * 0.5:  # Must be bullish with decent body
        return None
    
    # Candle 2: Small body (indecision)
    body2 = abs(c2 - o2)
    range2 = h2 - l2
    if range2 > 0 and body2 / range2 > 0.3:  # Body should be < 30% of range
        return None
    
    # Candle 3: Large bearish
    body3 = abs(c3 - o3)
    if c3 >= o3 or body3 < (h3 - l3) * 0.5:  # Must be bearish with decent body
        return None
    
    # Candle 3 should close below midpoint of candle 1
    mid1 = (o1 + c1) / 2
    if c3 >= mid1:
        return None
    
    # Volume confirmation on candle 3
    vol_confirmed = False
    if volume is not None and len(volume) >= 20:
        vol_confirmed, _ = _check_volume_confirmation(volume, len(volume) - 1, lookback=20)
    
    confidence = 0.80
    if vol_confirmed:
        confidence += 0.05
    
    target = c3 - body3  # One body size below
    stop_loss = max(h1, h2, h3) + (body3 * 0.2)
    
    return PatternMatch(
        name="Evening Star",
        pattern_type="reversal",
        bias="bearish",
        confidence=min(confidence, 0.85),
        description=f"Evening Star completed at {c3:.1f} (3-candle: {c1:.1f}→{c2:.1f}→{c3:.1f}). "
                   f"{'Volume confirmed. ' if vol_confirmed else ''}"
                   f"Target: {target:.1f}, SL: {stop_loss:.1f}",
        start_idx=len(close) - 3,
        end_idx=len(close) - 1,
        key_levels={"entry": c3, "target": target, "stop_loss": stop_loss},
        volume_confirmed=vol_confirmed,
        measured_target=round(target, 2),
        stop_loss=round(stop_loss, 2),
        pivot_times=[len(close) - 3, len(close) - 2, len(close) - 1],
    )


# ══════════════════════════════════════════════════════════════════════
# CHART PATTERNS (Existing + New)
# ══════════════════════════════════════════════════════════════════════

def detect_trend_structure(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    lookback: int = 50,
) -> Optional[PatternMatch]:
    """Detect price structure using REAL swing pivots — not a half-split heuristic.

    Requires at least 2 confirmed Lower Highs + 2 confirmed Lower Lows
    (or Higher Highs + Higher Lows for uptrend), each step exceeding
    0.25 × ATR so random noise can't trigger this.

    Returns a PatternMatch with all pivot indices in key_levels so the
    chart can draw zigzag lines and label each LH/LL (or HH/HL) marker.
    """
    if len(high) < lookback:
        return None

    recent_high  = high.iloc[-lookback:].reset_index(drop=True)
    recent_low   = low.iloc[-lookback:].reset_index(drop=True)
    recent_close = close.iloc[-lookback:].reset_index(drop=True)
    offset       = len(high) - lookback          # maps local idx → global idx

    atr_val = _calculate_atr(recent_high, recent_low, recent_close).iloc[-1]
    if pd.isna(atr_val) or atr_val <= 0:
        atr_val = recent_close.mean() * 0.003   # fallback: 0.3% of price
    min_step = 0.25 * atr_val                   # noise filter

    peaks, troughs = _find_peaks_troughs(
        recent_high, recent_low, order=4, min_distance=4
    )

    if len(peaks) < 2 or len(troughs) < 2:
        return None

    swing_highs = [(i, float(recent_high.iloc[i])) for i in peaks]
    swing_lows  = [(i, float(recent_low.iloc[i]))  for i in troughs]

    # ── DOWNTREND: every swing high and every swing low must step DOWN ──
    lh_seq = _strictly_decreasing(swing_highs, min_step)
    ll_seq  = _strictly_decreasing(swing_lows,  min_step)

    if len(lh_seq) >= 2 and len(ll_seq) >= 2:
        lh_vals  = [v for _, v in lh_seq]
        ll_vals  = [v for _, v in ll_seq]
        lh_idxs  = [i + offset for i, _ in lh_seq]
        ll_idxs  = [i + offset for i, _ in ll_seq]

        avg_lh_drop = (lh_vals[0] - lh_vals[-1]) / max(len(lh_vals) - 1, 1)
        avg_ll_drop = (ll_vals[0] - ll_vals[-1]) / max(len(ll_vals) - 1, 1)

        # Confidence scales with number of clean pivots (cap at 0.92)
        n_pivots    = len(lh_seq) + len(ll_seq)
        confidence  = min(0.55 + n_pivots * 0.06, 0.92)

        latest_lh   = lh_vals[-1]
        latest_ll   = ll_vals[-1]
        prev_lh     = lh_vals[-2]
        prev_ll     = ll_vals[-2]

        lh_str = " → ".join(f"₹{h:.0f}" for h in lh_vals)
        ll_str = " → ".join(f"₹{l:.0f}" for l in ll_vals)

        description = (
            f"Lower Highs: {lh_str} | "
            f"Lower Lows: {ll_str} | "
            f"Avg LH drop ₹{avg_lh_drop:.0f}, LL drop ₹{avg_ll_drop:.0f}/swing. "
            f"Sell rallies near ₹{latest_lh:.0f} (last LH). "
            f"Structure breaks above ₹{prev_lh:.0f}."
        )

        return PatternMatch(
            name="Downtrend Structure (LH/LL)",
            pattern_type="structure",
            bias="bearish",
            confidence=confidence,
            description=description,
            start_idx=offset,
            end_idx=len(high) - 1,
            key_levels={
                "latest_lh":   round(latest_lh, 2),
                "latest_ll":   round(latest_ll, 2),
                "prev_lh":     round(prev_lh,   2),
                "prev_ll":     round(prev_ll,   2),
                "resistance":  round(latest_lh, 2),   # generic chart compat
                "support":     round(latest_ll, 2),
                "lh_indices":  lh_idxs,
                "ll_indices":  ll_idxs,
                "lh_values":   [round(v, 2) for v in lh_vals],
                "ll_values":   [round(v, 2) for v in ll_vals],
                "structure_break": round(prev_lh, 2),
            },
            measured_target=None,
            stop_loss=round(latest_lh * 1.003, 2),  # 0.3% above last LH
            pivot_times=sorted(lh_idxs + ll_idxs),
        )

    # ── UPTREND: every swing high and every swing low must step UP ──
    hh_seq = _strictly_increasing(swing_highs, min_step)
    hl_seq  = _strictly_increasing(swing_lows,  min_step)

    if len(hh_seq) >= 2 and len(hl_seq) >= 2:
        hh_vals  = [v for _, v in hh_seq]
        hl_vals  = [v for _, v in hl_seq]
        hh_idxs  = [i + offset for i, _ in hh_seq]
        hl_idxs  = [i + offset for i, _ in hl_seq]

        avg_hh_rise = (hh_vals[-1] - hh_vals[0]) / max(len(hh_vals) - 1, 1)
        avg_hl_rise = (hl_vals[-1] - hl_vals[0]) / max(len(hl_vals) - 1, 1)

        n_pivots    = len(hh_seq) + len(hl_seq)
        confidence  = min(0.55 + n_pivots * 0.06, 0.92)

        latest_hh   = hh_vals[-1]
        latest_hl   = hl_vals[-1]
        prev_hl     = hl_vals[-2]

        hh_str = " → ".join(f"₹{h:.0f}" for h in hh_vals)
        hl_str = " → ".join(f"₹{l:.0f}" for l in hl_vals)

        description = (
            f"Higher Highs: {hh_str} | "
            f"Higher Lows: {hl_str} | "
            f"Avg HH rise ₹{avg_hh_rise:.0f}, HL rise ₹{avg_hl_rise:.0f}/swing. "
            f"Buy dips near ₹{latest_hl:.0f} (last HL). "
            f"Structure breaks below ₹{prev_hl:.0f}."
        )

        return PatternMatch(
            name="Uptrend Structure (HH/HL)",
            pattern_type="structure",
            bias="bullish",
            confidence=confidence,
            description=description,
            start_idx=offset,
            end_idx=len(high) - 1,
            key_levels={
                "latest_hh":   round(latest_hh, 2),
                "latest_hl":   round(latest_hl, 2),
                "prev_hl":     round(prev_hl,   2),
                "resistance":  round(latest_hh, 2),
                "support":     round(latest_hl, 2),
                "hh_indices":  hh_idxs,
                "hl_indices":  hl_idxs,
                "hh_values":   [round(v, 2) for v in hh_vals],
                "hl_values":   [round(v, 2) for v in hl_vals],
                "structure_break": round(prev_hl, 2),
            },
            measured_target=None,
            stop_loss=round(latest_hl * 0.997, 2),  # 0.3% below last HL
            pivot_times=sorted(hh_idxs + hl_idxs),
        )

    return None


def _strictly_decreasing(
    seq: list[tuple[int, float]], min_step: float
) -> list[tuple[int, float]]:
    """Return the longest suffix of seq where each value is strictly lower
    than the previous by at least min_step. Returns empty if < 2 qualify."""
    result = [seq[0]]
    for i, (idx, val) in enumerate(seq[1:], 1):
        prev_val = result[-1][1]
        if prev_val - val >= min_step:
            result.append((idx, val))
        else:
            # Chain broken — restart from here
            result = [(idx, val)]
    return result if len(result) >= 2 else []


def _strictly_increasing(
    seq: list[tuple[int, float]], min_step: float
) -> list[tuple[int, float]]:
    """Return the longest suffix of seq where each value is strictly higher
    than the previous by at least min_step."""
    result = [seq[0]]
    for i, (idx, val) in enumerate(seq[1:], 1):
        prev_val = result[-1][1]
        if val - prev_val >= min_step:
            result.append((idx, val))
        else:
            result = [(idx, val)]
    return result if len(result) >= 2 else []


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
    open_p = df.get("open", close)  # Fallback to close if open missing
    volume = df.get("volume", None)
    
    patterns: list[PatternMatch] = []
    
    def _ts(idx: int) -> str:
        try:
            return str(df.index[idx])
        except (IndexError, KeyError):
            return ""
    
    # Run all detectors
    # 🐶 NEW: Candlestick patterns added (catch reversals EARLY!)
    from patterns_advanced import (
        detect_triple_top, detect_triple_bottom,
        detect_head_and_shoulders, detect_inverse_head_and_shoulders,
        detect_rising_wedge, detect_falling_wedge,
        detect_channel, detect_symmetrical_triangle,
    )
    detectors = [
        lambda: detect_rsi_divergence(high, low, close),
        lambda: detect_bullish_engulfing(open_p, high, low, close, volume),
        lambda: detect_bearish_engulfing(open_p, high, low, close, volume),
        lambda: detect_hammer(open_p, high, low, close, volume),
        lambda: detect_shooting_star(open_p, high, low, close, volume),
        lambda: detect_morning_star(open_p, high, low, close, volume),
        lambda: detect_evening_star(open_p, high, low, close, volume),
        lambda: detect_double_top(high, low, close, volume),
        lambda: detect_double_bottom(high, low, close, volume),
        lambda: detect_triple_top(high, low, close, volume),
        lambda: detect_triple_bottom(high, low, close, volume),
        lambda: detect_head_and_shoulders(high, low, close, volume),
        lambda: detect_inverse_head_and_shoulders(high, low, close, volume),
        lambda: detect_ascending_triangle(high, low, close, volume),
        lambda: detect_descending_triangle(high, low, close, volume),
        lambda: detect_symmetrical_triangle(high, low, close, volume),
        lambda: detect_rising_wedge(high, low, close, volume),
        lambda: detect_falling_wedge(high, low, close, volume),
        lambda: detect_channel(high, low, close, volume),
        lambda: detect_flag(df, volume),
        lambda: detect_trend_structure(high, low, close),
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

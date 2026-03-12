"""Trade Signal Engine — Entry, Exit & Reversal Probability.

Combines multiple technical indicators into a composite reversal
probability score. When score > 80%, signals EXIT.

Indicators used for reversal detection:
1. RSI divergence (price vs RSI direction mismatch)
2. Volume divergence (price moving but volume fading)
3. EMA crossover (9 EMA vs 21 EMA)
4. MACD histogram momentum shift
5. ADX trend strength (declining = trend exhaustion)
6. Price at key S/R level (hitting ceiling/floor)
7. Candlestick reversal patterns (engulfing, pin bar)
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field

import indicators as ind


@dataclass
class ReversalSignal:
    """A single reversal indicator's contribution."""
    name: str
    score: float  # 0.0 (no reversal) to 1.0 (strong reversal)
    weight: float  # importance weight
    detail: str  # human-readable explanation


@dataclass
class TradeSignal:
    """Complete trade signal with entry, exit, and reversal info."""
    action: str  # 'BUY', 'SELL', 'EXIT_LONG', 'EXIT_SHORT', 'HOLD'
    entry_price: float | None = None
    stop_loss: float | None = None
    target: float | None = None
    risk_reward: float | None = None
    current_trend: str = "neutral"  # 'uptrend', 'downtrend', 'sideways'
    trend_strength: str = "weak"  # 'strong', 'moderate', 'weak'
    reversal_probability: float = 0.0  # 0-100%
    reversal_signals: list[ReversalSignal] = field(default_factory=list)
    reasoning: str = ""
    exit_warning: bool = False  # True when reversal_probability > 80%


# ── Reversal Detection Indicators ──────────────────────────────────

def _detect_rsi_divergence(close: pd.Series, rsi_vals: pd.Series,
                           lookback: int = 10) -> ReversalSignal:
    """Detect RSI divergence — price and RSI moving in opposite directions.

    Bearish divergence: price making higher highs, RSI making lower highs.
    Bullish divergence: price making lower lows, RSI making higher lows.
    """
    if len(close) < lookback + 5:
        return ReversalSignal("RSI Divergence", 0.0, 0.20, "Insufficient data")

    recent_close = close.iloc[-lookback:]
    recent_rsi = rsi_vals.iloc[-lookback:]
    prior_close = close.iloc[-lookback * 2:-lookback]
    prior_rsi = rsi_vals.iloc[-lookback * 2:-lookback]

    if prior_close.empty or prior_rsi.empty:
        return ReversalSignal("RSI Divergence", 0.0, 0.20, "Insufficient data")

    price_higher = recent_close.max() > prior_close.max()
    rsi_lower = recent_rsi.max() < prior_rsi.max()
    price_lower = recent_close.min() < prior_close.min()
    rsi_higher = recent_rsi.min() > prior_rsi.min()

    # Bearish divergence (price up, RSI down)
    if price_higher and rsi_lower:
        current_rsi = float(rsi_vals.iloc[-1])
        # Stronger signal if RSI is overbought
        severity = min(1.0, 0.6 + (current_rsi - 60) / 100) if current_rsi > 60 else 0.5
        return ReversalSignal(
            "RSI Divergence", severity, 0.20,
            f"Bearish divergence — price making new highs but RSI declining (RSI={current_rsi:.0f})"
        )

    # Bullish divergence (price down, RSI up)
    if price_lower and rsi_higher:
        current_rsi = float(rsi_vals.iloc[-1])
        severity = min(1.0, 0.6 + (40 - current_rsi) / 100) if current_rsi < 40 else 0.5
        return ReversalSignal(
            "RSI Divergence", severity, 0.20,
            f"Bullish divergence — price making new lows but RSI rising (RSI={current_rsi:.0f})"
        )

    return ReversalSignal("RSI Divergence", 0.0, 0.20, "No divergence detected")


def _detect_volume_divergence(close: pd.Series, volume: pd.Series,
                              lookback: int = 10) -> ReversalSignal:
    """Volume divergence — price trending but volume fading."""
    if volume.sum() == 0 or len(close) < lookback:
        return ReversalSignal("Volume Divergence", 0.0, 0.15,
                              "Volume data not available (index data)")

    recent_close = close.iloc[-lookback:]
    recent_vol = volume.iloc[-lookback:]

    # Price direction
    price_slope = np.polyfit(range(lookback), recent_close.values, 1)[0]
    vol_slope = np.polyfit(range(lookback), recent_vol.values, 1)[0]

    # Divergence: price up + volume down, or price down + volume down
    if price_slope > 0 and vol_slope < 0:
        return ReversalSignal(
            "Volume Divergence", 0.7, 0.15,
            "Price rising but volume declining — buying exhaustion"
        )
    if price_slope < 0 and vol_slope < 0:
        return ReversalSignal(
            "Volume Divergence", 0.5, 0.15,
            "Price falling with declining volume — selling may be exhausting"
        )

    return ReversalSignal("Volume Divergence", 0.0, 0.15, "Volume confirms trend")


def _detect_ema_crossover(close: pd.Series) -> ReversalSignal:
    """EMA crossover — 9 EMA vs 21 EMA proximity and cross detection."""
    if len(close) < 25:
        return ReversalSignal("EMA Crossover", 0.0, 0.15, "Insufficient data")

    ema9 = ind.ema(close, 9)
    ema21 = ind.ema(close, 21)

    current_diff = float(ema9.iloc[-1] - ema21.iloc[-1])
    prev_diff = float(ema9.iloc[-2] - ema21.iloc[-2])
    price = float(close.iloc[-1])
    diff_pct = abs(current_diff) / price * 100

    # Just crossed (sign change)
    if current_diff * prev_diff < 0:
        if current_diff < 0:  # 9 EMA crossed below 21 EMA = bearish
            return ReversalSignal(
                "EMA Crossover", 0.9, 0.15,
                "9 EMA just crossed BELOW 21 EMA — bearish crossover!"
            )
        else:  # 9 EMA crossed above 21 EMA = bullish reversal from downtrend
            return ReversalSignal(
                "EMA Crossover", 0.9, 0.15,
                "9 EMA just crossed ABOVE 21 EMA — bullish crossover!"
            )

    # Converging (gap narrowing) — early warning
    prev5_diff = float(ema9.iloc[-6] - ema21.iloc[-6]) if len(close) > 30 else current_diff
    if abs(current_diff) < abs(prev5_diff) and diff_pct < 0.1:
        return ReversalSignal(
            "EMA Crossover", 0.5, 0.15,
            f"EMAs converging (gap={current_diff:.1f}) — crossover approaching"
        )

    return ReversalSignal("EMA Crossover", 0.0, 0.15,
                          f"EMAs separated ({current_diff:.1f}pts) — trend intact")


def _detect_macd_momentum(close: pd.Series) -> ReversalSignal:
    """MACD histogram momentum shift — histogram shrinking = momentum fading."""
    if len(close) < 30:
        return ReversalSignal("MACD Momentum", 0.0, 0.15, "Insufficient data")

    macd_data = ind.macd(close)
    hist = macd_data["histogram"]

    if len(hist) < 5:
        return ReversalSignal("MACD Momentum", 0.0, 0.15, "Insufficient data")

    current_hist = float(hist.iloc[-1])
    prev_hist = float(hist.iloc[-2])
    hist_3ago = float(hist.iloc[-4])

    # Histogram shrinking towards zero = momentum fading
    if abs(current_hist) < abs(hist_3ago) * 0.5:
        direction = "bullish" if current_hist > 0 else "bearish"
        return ReversalSignal(
            "MACD Momentum", 0.7, 0.15,
            f"MACD histogram shrinking — {direction} momentum fading"
        )

    # Histogram crossed zero
    if current_hist * prev_hist < 0:
        if current_hist < 0:
            return ReversalSignal(
                "MACD Momentum", 0.85, 0.15,
                "MACD histogram turned negative — momentum shift to bearish"
            )
        else:
            return ReversalSignal(
                "MACD Momentum", 0.85, 0.15,
                "MACD histogram turned positive — momentum shift to bullish"
            )

    return ReversalSignal("MACD Momentum", 0.0, 0.15, "MACD momentum steady")


def _detect_adx_exhaustion(high: pd.Series, low: pd.Series,
                           close: pd.Series) -> ReversalSignal:
    """ADX declining from high level = trend exhaustion."""
    if len(close) < 20:
        return ReversalSignal("ADX Trend", 0.0, 0.10, "Insufficient data")

    adx_data = ind.adx(high, low, close, period=14)
    adx_val = float(adx_data["adx"].iloc[-1])
    adx_prev = float(adx_data["adx"].iloc[-5]) if len(adx_data) > 5 else adx_val

    # ADX was strong (>25) but is now declining
    if adx_prev > 25 and adx_val < adx_prev:
        decline = adx_prev - adx_val
        severity = min(1.0, decline / 15)  # Full score at 15pt decline
        return ReversalSignal(
            "ADX Trend", severity, 0.10,
            f"ADX declining ({adx_prev:.0f}→{adx_val:.0f}) — trend losing strength"
        )

    if adx_val < 20:
        return ReversalSignal(
            "ADX Trend", 0.3, 0.10,
            f"ADX weak ({adx_val:.0f}) — no clear trend, choppy market"
        )

    return ReversalSignal("ADX Trend", 0.0, 0.10,
                          f"ADX strong ({adx_val:.0f}) — trend healthy")


def _detect_sr_rejection(close: pd.Series, high: pd.Series, low: pd.Series,
                         support_levels: list, resistance_levels: list,
                         atr_val: float) -> ReversalSignal:
    """Price rejecting a key S/R level (wick rejection)."""
    if not support_levels and not resistance_levels:
        return ReversalSignal("S/R Rejection", 0.0, 0.15, "No S/R levels available")

    price = float(close.iloc[-1])
    candle_high = float(high.iloc[-1])
    candle_low = float(low.iloc[-1])
    threshold = atr_val * 0.3  # within 30% of ATR

    # Check resistance rejection (price touched resistance but closed below)
    for r in resistance_levels:
        if candle_high >= r - threshold and price < r:
            wick = candle_high - price
            body = abs(price - float(close.iloc[-2])) if len(close) > 1 else 1
            wick_ratio = wick / body if body > 0 else 0
            severity = min(1.0, 0.5 + wick_ratio * 0.2)
            return ReversalSignal(
                "S/R Rejection", severity, 0.15,
                f"Price rejected resistance at {r:.0f} (wick touched but closed below)"
            )

    # Check support rejection (price touched support but closed above)
    for s in support_levels:
        if candle_low <= s + threshold and price > s:
            wick = price - candle_low
            body = abs(price - float(close.iloc[-2])) if len(close) > 1 else 1
            wick_ratio = wick / body if body > 0 else 0
            severity = min(1.0, 0.5 + wick_ratio * 0.2)
            return ReversalSignal(
                "S/R Rejection", severity, 0.15,
                f"Price rejected support at {s:.0f} (wick touched but closed above)"
            )

    return ReversalSignal("S/R Rejection", 0.0, 0.15, "Not near key S/R levels")


def _detect_candle_reversal(open_: pd.Series, high: pd.Series,
                            low: pd.Series, close: pd.Series) -> ReversalSignal:
    """Detect candlestick reversal patterns (engulfing, pin bar, doji)."""
    if len(close) < 3:
        return ReversalSignal("Candle Pattern", 0.0, 0.10, "Insufficient data")

    # Current and previous candle
    c_open, c_high, c_low, c_close = (
        float(open_.iloc[-1]), float(high.iloc[-1]),
        float(low.iloc[-1]), float(close.iloc[-1])
    )
    p_open, p_close = float(open_.iloc[-2]), float(close.iloc[-2])

    body = abs(c_close - c_open)
    upper_wick = c_high - max(c_open, c_close)
    lower_wick = min(c_open, c_close) - c_low
    total_range = c_high - c_low if c_high != c_low else 0.01

    # Bearish engulfing
    if (p_close > p_open and c_close < c_open and
            c_open >= p_close and c_close <= p_open):
        return ReversalSignal(
            "Candle Pattern", 0.8, 0.10,
            "Bearish Engulfing — sellers overpowered buyers completely"
        )

    # Bullish engulfing
    if (p_close < p_open and c_close > c_open and
            c_open <= p_close and c_close >= p_open):
        return ReversalSignal(
            "Candle Pattern", 0.8, 0.10,
            "Bullish Engulfing — buyers overpowered sellers completely"
        )

    # Pin bar / hammer (long lower wick, small body at top)
    if lower_wick > body * 2 and upper_wick < body * 0.5:
        return ReversalSignal(
            "Candle Pattern", 0.6, 0.10,
            "Hammer/Pin Bar — buyers rejected lower prices aggressively"
        )

    # Shooting star (long upper wick, small body at bottom)
    if upper_wick > body * 2 and lower_wick < body * 0.5:
        return ReversalSignal(
            "Candle Pattern", 0.6, 0.10,
            "Shooting Star — sellers rejected higher prices aggressively"
        )

    # Doji (tiny body relative to range)
    if body / total_range < 0.1:
        return ReversalSignal(
            "Candle Pattern", 0.4, 0.10,
            "Doji — indecision, potential reversal point"
        )

    return ReversalSignal("Candle Pattern", 0.0, 0.10, "No reversal candle pattern")


# ── Trend & Entry Detection ────────────────────────────────────────

def _identify_trend(close: pd.Series, high: pd.Series,
                    low: pd.Series) -> tuple[str, str]:
    """Identify current trend direction and strength.

    Returns: (direction, strength) where
        direction: 'uptrend', 'downtrend', 'sideways'
        strength: 'strong', 'moderate', 'weak'
    """
    if len(close) < 25:
        return "sideways", "weak"

    ema9 = ind.ema(close, 9)
    ema21 = ind.ema(close, 21)
    adx_data = ind.adx(high, low, close)
    adx_val = float(adx_data["adx"].iloc[-1])

    price = float(close.iloc[-1])
    e9 = float(ema9.iloc[-1])
    e21 = float(ema21.iloc[-1])

    # Direction
    if price > e9 > e21:
        direction = "uptrend"
    elif price < e9 < e21:
        direction = "downtrend"
    else:
        direction = "sideways"

    # Strength from ADX
    if adx_val > 30:
        strength = "strong"
    elif adx_val > 20:
        strength = "moderate"
    else:
        strength = "weak"

    return direction, strength


def _find_entry_point(df: pd.DataFrame, trend: str,
                      support_levels: list,
                      resistance_levels: list) -> dict:
    """Find best entry point based on trend, pullbacks, and S/R.

    Returns dict with entry_price, stop_loss, target, risk_reward.
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    price = float(close.iloc[-1])
    atr_val = float(ind.atr(high, low, close).iloc[-1])

    ema9 = float(ind.ema(close, 9).iloc[-1])
    ema21 = float(ind.ema(close, 21).iloc[-1])

    entry = None
    stop = None
    target = None
    reason = ""

    if trend == "uptrend":
        # Best entry: pullback to 9 EMA or 21 EMA
        if price <= ema9 * 1.001:  # within 0.1% of 9 EMA
            entry = round(ema9, 2)
            stop = round(ema21 - atr_val * 0.5, 2)
            reason = "Pullback to 9 EMA in uptrend"
        elif price <= ema21 * 1.002:  # within 0.2% of 21 EMA
            entry = round(ema21, 2)
            stop = round(ema21 - atr_val, 2)
            reason = "Deep pullback to 21 EMA in uptrend"
        else:
            # Near support level
            near_support = [s for s in support_levels if s < price and (price - s) / price < 0.005]
            if near_support:
                entry = round(max(near_support), 2)
                stop = round(entry - atr_val, 2)
                reason = f"Near support at {entry}"
            else:
                entry = round(price, 2)
                stop = round(ema21 - atr_val * 0.5, 2)
                reason = "Current price in uptrend (no ideal pullback)"

        # Target: nearest resistance or 2x risk
        above_resistances = [r for r in resistance_levels if r > price]
        if above_resistances:
            target = round(min(above_resistances), 2)
        else:
            target = round(entry + (entry - stop) * 2, 2) if entry and stop else None

    elif trend == "downtrend":
        # Best entry for short: rally to 9 EMA or 21 EMA
        if price >= ema9 * 0.999:
            entry = round(ema9, 2)
            stop = round(ema21 + atr_val * 0.5, 2)
            reason = "Rally to 9 EMA in downtrend (short entry)"
        elif price >= ema21 * 0.998:
            entry = round(ema21, 2)
            stop = round(ema21 + atr_val, 2)
            reason = "Rally to 21 EMA in downtrend (short entry)"
        else:
            entry = round(price, 2)
            stop = round(ema21 + atr_val * 0.5, 2)
            reason = "Current price in downtrend"

        below_supports = [s for s in support_levels if s < price]
        if below_supports:
            target = round(max(below_supports), 2)
        else:
            target = round(entry - (stop - entry) * 2, 2) if entry and stop else None

    else:  # sideways
        # Range trading: buy near support, sell near resistance
        if support_levels and resistance_levels:
            nearest_support = max([s for s in support_levels if s < price], default=None)
            nearest_resistance = min([r for r in resistance_levels if r > price], default=None)

            if nearest_support and (price - nearest_support) / price < 0.003:
                entry = round(nearest_support, 2)
                stop = round(nearest_support - atr_val, 2)
                target = nearest_resistance if nearest_resistance else round(entry + atr_val * 2, 2)
                reason = f"Near support {entry} in range (buy)"
            elif nearest_resistance and (nearest_resistance - price) / price < 0.003:
                entry = round(nearest_resistance, 2)
                stop = round(nearest_resistance + atr_val, 2)
                target = nearest_support if nearest_support else round(entry - atr_val * 2, 2)
                reason = f"Near resistance {entry} in range (sell)"
            else:
                entry = round(price, 2)
                stop = round(price - atr_val, 2)
                target = round(price + atr_val, 2)
                reason = "Mid-range — no clear entry, wait for S/R approach"
        else:
            entry = round(price, 2)
            stop = round(price - atr_val, 2)
            target = round(price + atr_val, 2)
            reason = "Sideways, no S/R data"

    risk = abs(entry - stop) if entry and stop else 1
    reward = abs(target - entry) if target and entry else 0
    rr = round(reward / risk, 2) if risk > 0 else 0

    return {
        "entry_price": entry,
        "stop_loss": stop,
        "target": target,
        "risk_reward": rr,
        "reason": reason,
    }


# ── Main Analysis Function ─────────────────────────────────────────

def analyze_trade(df: pd.DataFrame,
                  support_levels: list | None = None,
                  resistance_levels: list | None = None) -> TradeSignal:
    """Run complete trade analysis: trend, entry, reversal probability.

    Args:
        df: OHLCV DataFrame (full intraday data, not just today).
        support_levels: Known support levels from S/R detection.
        resistance_levels: Known resistance levels from S/R detection.

    Returns:
        TradeSignal with entry, exit, and reversal probability.
    """
    support_levels = support_levels or []
    resistance_levels = resistance_levels or []

    close = df["close"]
    high = df["high"]
    low = df["low"]
    open_ = df["open"]
    volume = df["volume"]

    # Compute indicators
    rsi_vals = ind.rsi(close)
    atr_val = float(ind.atr(high, low, close).iloc[-1])

    # 1. Identify trend
    trend, strength = _identify_trend(close, high, low)

    # 2. Find entry point
    entry_data = _find_entry_point(df, trend, support_levels, resistance_levels)

    # 3. Calculate reversal probability from 7 indicators
    reversal_signals = [
        _detect_rsi_divergence(close, rsi_vals),
        _detect_volume_divergence(close, volume),
        _detect_ema_crossover(close),
        _detect_macd_momentum(close),
        _detect_adx_exhaustion(high, low, close),
        _detect_sr_rejection(close, high, low, support_levels,
                             resistance_levels, atr_val),
        _detect_candle_reversal(open_, high, low, close),
    ]

    # Weighted average of reversal scores
    total_weight = sum(s.weight for s in reversal_signals)
    weighted_score = sum(s.score * s.weight for s in reversal_signals)
    reversal_pct = round((weighted_score / total_weight) * 100, 1) if total_weight > 0 else 0

    # 4. Determine action
    exit_warning = reversal_pct >= 80

    if exit_warning:
        if trend == "uptrend":
            action = "EXIT_LONG"
            reasoning = (f"⚠️ REVERSAL ALERT ({reversal_pct}%)! "
                         f"Multiple signals indicate uptrend exhaustion. "
                         f"Consider exiting long positions.")
        elif trend == "downtrend":
            action = "EXIT_SHORT"
            reasoning = (f"⚠️ REVERSAL ALERT ({reversal_pct}%)! "
                         f"Multiple signals indicate downtrend exhaustion. "
                         f"Consider covering short positions.")
        else:
            action = "EXIT_LONG"  # Default to caution
            reasoning = (f"⚠️ HIGH REVERSAL RISK ({reversal_pct}%)! "
                         f"Market indecisive with multiple warning signals.")
    elif reversal_pct >= 60:
        action = "HOLD"
        reasoning = (f"⚡ Caution — reversal probability rising ({reversal_pct}%). "
                     f"Tighten stop loss. {entry_data['reason']}")
    elif trend == "uptrend":
        action = "BUY"
        reasoning = f"Uptrend ({strength}). {entry_data['reason']}"
    elif trend == "downtrend":
        action = "SELL"
        reasoning = f"Downtrend ({strength}). {entry_data['reason']}"
    else:
        action = "HOLD"
        reasoning = f"Sideways market. {entry_data['reason']}"

    return TradeSignal(
        action=action,
        entry_price=entry_data["entry_price"],
        stop_loss=entry_data["stop_loss"],
        target=entry_data["target"],
        risk_reward=entry_data["risk_reward"],
        current_trend=trend,
        trend_strength=strength,
        reversal_probability=reversal_pct,
        reversal_signals=reversal_signals,
        reasoning=reasoning,
        exit_warning=exit_warning,
    )

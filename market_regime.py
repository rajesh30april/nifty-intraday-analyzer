"""Market Regime Detector.

Classifies the current market as TRENDING, SIDEWAYS, or VOLATILE
so the strategy router can pick the right strategy.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from enum import Enum

import indicators as ind


class MarketRegime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    SIDEWAYS = "sideways"
    VOLATILE = "volatile"


@dataclass
class RegimeResult:
    """Result of market regime detection."""
    regime: MarketRegime
    adx: float
    atr_pct: float          # ATR as % of price (volatility)
    trend_direction: str    # 'up', 'down', 'flat'
    confidence: float       # 0-100
    detail: str


def detect_regime(
    df: pd.DataFrame,
    adx_trending_threshold: float = 25.0,
    adx_sideways_threshold: float = 20.0,
    atr_volatile_pct: float = 0.8,
) -> RegimeResult:
    """Detect current market regime from OHLCV data.

    Logic:
    - ADX > 25 + DI direction  -> TRENDING (up or down)
    - ADX < 20                 -> SIDEWAYS
    - ATR% > 0.8% of price    -> VOLATILE (overrides others)

    Args:
        df: OHLCV DataFrame with at least 30 candles.
        adx_trending_threshold: ADX above this = trending.
        adx_sideways_threshold: ADX below this = sideways.
        atr_volatile_pct: ATR/price % above this = volatile.
    """
    if len(df) < 30:
        return RegimeResult(
            regime=MarketRegime.SIDEWAYS,
            adx=0, atr_pct=0, trend_direction="flat",
            confidence=0, detail="Insufficient data",
        )

    close = df["close"]
    high = df["high"]
    low = df["low"]
    price = float(close.iloc[-1])

    # ADX + DI for trend detection
    adx_data = ind.adx(high, low, close, period=14)
    adx_val = float(adx_data["adx"].iloc[-1])
    di_plus = float(adx_data["plus_di"].iloc[-1])
    di_minus = float(adx_data["minus_di"].iloc[-1])

    # ATR for volatility
    atr_val = float(ind.atr(high, low, close, period=14).iloc[-1])
    atr_pct = (atr_val / price) * 100

    # EMA slope for direction confirmation
    ema_20 = ind.ema(close, 20)
    ema_slope = float(ema_20.iloc[-1] - ema_20.iloc[-5]) / 5  # avg slope

    # Determine trend direction
    if di_plus > di_minus and ema_slope > 0:
        trend_dir = "up"
    elif di_minus > di_plus and ema_slope < 0:
        trend_dir = "down"
    else:
        trend_dir = "flat"

    # Classify regime
    if atr_pct >= atr_volatile_pct:
        regime = MarketRegime.VOLATILE
        confidence = min(atr_pct / atr_volatile_pct * 50, 95)
        detail = (
            f"HIGH VOLATILITY: ATR {atr_pct:.2f}% of price "
            f"(threshold {atr_volatile_pct}%)"
        )
    elif adx_val >= adx_trending_threshold:
        regime = (
            MarketRegime.TRENDING_UP if trend_dir == "up"
            else MarketRegime.TRENDING_DOWN
        )
        confidence = min((adx_val - 20) / 30 * 100, 95)
        detail = (
            f"TRENDING {trend_dir.upper()}: ADX={adx_val:.0f}, "
            f"DI+={di_plus:.0f} DI-={di_minus:.0f}, "
            f"EMA slope={ema_slope:.1f}"
        )
    elif adx_val <= adx_sideways_threshold:
        regime = MarketRegime.SIDEWAYS
        confidence = min((25 - adx_val) / 15 * 100, 95)
        detail = (
            f"SIDEWAYS: ADX={adx_val:.0f} (< {adx_sideways_threshold}), "
            f"no clear trend"
        )
    else:
        # Transition zone (20-25 ADX)
        regime = MarketRegime.SIDEWAYS
        confidence = 30
        detail = (
            f"TRANSITION: ADX={adx_val:.0f} (between thresholds), "
            f"leaning sideways"
        )

    return RegimeResult(
        regime=regime,
        adx=round(adx_val, 1),
        atr_pct=round(atr_pct, 3),
        trend_direction=trend_dir,
        confidence=round(confidence, 1),
        detail=detail,
    )

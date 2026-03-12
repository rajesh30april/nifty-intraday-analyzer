"""Probability scoring engine for intraday Nifty movement.

Combines multiple technical signals into a weighted probability
of bullish vs bearish movement.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field

import indicators as ind
from data_fetcher import get_todays_data


@dataclass
class Signal:
    """A single technical signal with its assessment."""
    name: str
    value: float | str
    bias: str  # 'bullish', 'bearish', 'neutral'
    strength: float  # 0.0 to 1.0
    weight: float  # importance weight
    description: str


@dataclass
class ProbabilityResult:
    """Aggregated probability assessment."""
    bullish_probability: float
    bearish_probability: float
    overall_bias: str
    confidence: str  # 'high', 'medium', 'low'
    signals: list[Signal] = field(default_factory=list)
    current_price: float = 0.0
    day_change: float = 0.0
    day_change_pct: float = 0.0
    orb_data: dict = field(default_factory=dict)


def _score_rsi(rsi_val: float) -> Signal:
    """Score RSI for intraday bias."""
    if pd.isna(rsi_val):
        return Signal("RSI (14)", 0, "neutral", 0, 0.12, "Insufficient data")

    rsi_val = round(rsi_val, 1)

    if rsi_val > 70:
        return Signal("RSI (14)", rsi_val, "bearish", 0.7, 0.12,
                      f"Overbought at {rsi_val} — pullback likely")
    elif rsi_val > 60:
        return Signal("RSI (14)", rsi_val, "bullish", 0.6, 0.12,
                      f"Strong momentum at {rsi_val}")
    elif rsi_val > 40:
        return Signal("RSI (14)", rsi_val, "neutral", 0.3, 0.12,
                      f"Neutral zone at {rsi_val}")
    elif rsi_val > 30:
        return Signal("RSI (14)", rsi_val, "bearish", 0.6, 0.12,
                      f"Weak momentum at {rsi_val}")
    else:
        return Signal("RSI (14)", rsi_val, "bullish", 0.7, 0.12,
                      f"Oversold at {rsi_val} — bounce likely")


def _score_adx(adx_val: float, plus_di: float, minus_di: float) -> Signal:
    """Score ADX for trend strength and direction."""
    if pd.isna(adx_val):
        return Signal("ADX", 0, "neutral", 0, 0.15, "Insufficient data")

    adx_val = round(adx_val, 1)
    direction = "bullish" if plus_di > minus_di else "bearish"

    if adx_val > 30:
        return Signal("ADX", adx_val, direction, 0.9, 0.15,
                      f"Strong trend (ADX={adx_val}), {direction} DI dominant")
    elif adx_val > 20:
        return Signal("ADX", adx_val, direction, 0.5, 0.15,
                      f"Moderate trend (ADX={adx_val}), {direction} leaning")
    else:
        return Signal("ADX", adx_val, "neutral", 0.2, 0.15,
                      f"No clear trend (ADX={adx_val}) — choppy market")


def _score_supertrend(direction: int) -> Signal:
    """Score Supertrend signal."""
    if direction == 1:
        return Signal("Supertrend", "Bullish", "bullish", 0.8, 0.18,
                      "Supertrend is GREEN — bullish bias")
    else:
        return Signal("Supertrend", "Bearish", "bearish", 0.8, 0.18,
                      "Supertrend is RED — bearish bias")


def _score_vwap(close: float, vwap_val: float) -> Signal:
    """Score price relative to VWAP."""
    if pd.isna(vwap_val) or vwap_val == 0:
        return Signal("VWAP", 0, "neutral", 0, 0.20, "Insufficient data")

    diff_pct = ((close - vwap_val) / vwap_val) * 100
    diff_pct = round(diff_pct, 2)

    if diff_pct > 0.3:
        return Signal("VWAP", f"+{diff_pct}%", "bullish", 0.8, 0.20,
                      f"Price {diff_pct}% ABOVE VWAP — buyers in control")
    elif diff_pct > 0:
        return Signal("VWAP", f"+{diff_pct}%", "bullish", 0.5, 0.20,
                      f"Price slightly above VWAP")
    elif diff_pct > -0.3:
        return Signal("VWAP", f"{diff_pct}%", "bearish", 0.5, 0.20,
                      f"Price slightly below VWAP")
    else:
        return Signal("VWAP", f"{diff_pct}%", "bearish", 0.8, 0.20,
                      f"Price {abs(diff_pct)}% BELOW VWAP — sellers in control")


def _score_ema_crossover(ema_9: float, ema_21: float) -> Signal:
    """Score EMA 9/21 crossover."""
    if pd.isna(ema_9) or pd.isna(ema_21):
        return Signal("EMA 9/21", "N/A", "neutral", 0, 0.12, "Insufficient data")

    diff = ((ema_9 - ema_21) / ema_21) * 100

    if diff > 0.1:
        return Signal("EMA 9/21", "Bullish", "bullish", 0.7, 0.12,
                      "EMA 9 above EMA 21 — short-term momentum up")
    elif diff < -0.1:
        return Signal("EMA 9/21", "Bearish", "bearish", 0.7, 0.12,
                      "EMA 9 below EMA 21 — short-term momentum down")
    else:
        return Signal("EMA 9/21", "Flat", "neutral", 0.2, 0.12,
                      "EMAs converged — no clear direction")


def _score_macd(macd_val: float, signal_val: float, histogram: float) -> Signal:
    """Score MACD."""
    if pd.isna(macd_val):
        return Signal("MACD", "N/A", "neutral", 0, 0.10, "Insufficient data")

    if macd_val > signal_val and histogram > 0:
        strength = min(abs(histogram) / 5, 1.0)
        return Signal("MACD", round(histogram, 2), "bullish", strength, 0.10,
                      "MACD above signal, positive histogram")
    elif macd_val < signal_val and histogram < 0:
        strength = min(abs(histogram) / 5, 1.0)
        return Signal("MACD", round(histogram, 2), "bearish", strength, 0.10,
                      "MACD below signal, negative histogram")
    else:
        return Signal("MACD", round(histogram, 2), "neutral", 0.3, 0.10,
                      "MACD crossover zone — transitioning")


def _score_volume(vol_data: dict) -> Signal | None:
    """Score volume analysis. Returns None for index data with no volume."""
    if vol_data["volume_trend"] == "not_available":
        return None  # Skip volume for indices — weight gets redistributed

    ratio = vol_data["volume_ratio"]
    trend = vol_data["volume_trend"]

    if ratio > 1.5 and trend == "increasing":
        return Signal("Volume", f"{ratio}x avg", "neutral", 0.8, 0.13,
                      f"High volume ({ratio}x avg) & increasing — strong conviction")
    elif ratio > 1.2:
        return Signal("Volume", f"{ratio}x avg", "neutral", 0.5, 0.13,
                      f"Above average volume — moderate interest")
    elif ratio < 0.7:
        return Signal("Volume", f"{ratio}x avg", "neutral", 0.3, 0.13,
                      f"Low volume ({ratio}x avg) — weak conviction, be cautious")
    else:
        return Signal("Volume", f"{ratio}x avg", "neutral", 0.4, 0.13,
                      f"Normal volume levels")


def _score_orb(orb_data: dict) -> Signal | None:
    """Score Opening Range Breakout."""
    breakout = orb_data.get("breakout", "none")

    if breakout == "bullish":
        return Signal("ORB (15m)", "Breakout ↑", "bullish", 0.85, 0.15,
                      f"Bullish ORB! Price above {orb_data['orb_high']}")
    elif breakout == "bearish":
        return Signal("ORB (15m)", "Breakdown ↓", "bearish", 0.85, 0.15,
                      f"Bearish ORB! Price below {orb_data['orb_low']}")
    else:
        return Signal("ORB (15m)", "In Range", "neutral", 0.3, 0.15,
                      f"Inside opening range ({orb_data.get('orb_low')}-{orb_data.get('orb_high')})")


def calculate_probability(df: pd.DataFrame) -> ProbabilityResult:
    """Calculate probability of bullish/bearish intraday movement.

    Args:
        df: Multi-day intraday OHLCV DataFrame (5-min candles).

    Returns:
        ProbabilityResult with all signals and final probability.
    """
    if df.empty or len(df) < 20:
        return ProbabilityResult(
            bullish_probability=50.0,
            bearish_probability=50.0,
            overall_bias="neutral",
            confidence="low",
            signals=[],
        )

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # Calculate all indicators on full data
    rsi_series = ind.rsi(close, 14)
    adx_df = ind.adx(high, low, close, 14)
    st_df = ind.supertrend(high, low, close, 10, 3.0)
    macd_df = ind.macd(close, 12, 26, 9)
    ema_9 = ind.ema(close, 9)
    ema_21 = ind.ema(close, 21)

    # VWAP on today's data only
    today_df = get_todays_data(df)
    if not today_df.empty:
        vwap_series = ind.vwap(today_df["high"], today_df["low"],
                               today_df["close"], today_df["volume"])
        vwap_val = vwap_series.iloc[-1]
        orb_data = ind.opening_range(today_df, 15)
    else:
        vwap_val = np.nan
        orb_data = {"orb_high": None, "orb_low": None, "breakout": "none"}

    vol_data = ind.volume_analysis(volume)

    # Get latest values
    latest_close = close.iloc[-1]
    latest_rsi = rsi_series.iloc[-1]
    latest_adx = adx_df["adx"].iloc[-1]
    latest_plus_di = adx_df["plus_di"].iloc[-1]
    latest_minus_di = adx_df["minus_di"].iloc[-1]
    latest_st_dir = st_df["direction"].iloc[-1]
    latest_macd = macd_df["macd"].iloc[-1]
    latest_macd_signal = macd_df["signal"].iloc[-1]
    latest_macd_hist = macd_df["histogram"].iloc[-1]
    latest_ema_9 = ema_9.iloc[-1]
    latest_ema_21 = ema_21.iloc[-1]

    # Score each signal
    signals = [
        _score_vwap(latest_close, vwap_val),
        _score_supertrend(latest_st_dir),
        _score_adx(latest_adx, latest_plus_di, latest_minus_di),
        _score_orb(orb_data),
        _score_rsi(latest_rsi),
        _score_ema_crossover(latest_ema_9, latest_ema_21),
        _score_macd(latest_macd, latest_macd_signal, latest_macd_hist),
        _score_volume(vol_data),
    ]

    signals = [s for s in signals if s is not None]

    # Calculate weighted probability
    total_weight = sum(s.weight for s in signals)
    bullish_score = 0.0
    bearish_score = 0.0

    for s in signals:
        normalized_weight = s.weight / total_weight if total_weight > 0 else 0
        if s.bias == "bullish":
            bullish_score += s.strength * normalized_weight
        elif s.bias == "bearish":
            bearish_score += s.strength * normalized_weight
        else:
            # Neutral signals push toward 50/50
            bullish_score += 0.5 * s.strength * normalized_weight
            bearish_score += 0.5 * s.strength * normalized_weight

    total_score = bullish_score + bearish_score
    if total_score > 0:
        bull_pct = round((bullish_score / total_score) * 100, 1)
        bear_pct = round(100 - bull_pct, 1)
    else:
        bull_pct = 50.0
        bear_pct = 50.0

    # Determine confidence
    score_diff = abs(bull_pct - bear_pct)
    if score_diff > 30:
        confidence = "high"
    elif score_diff > 15:
        confidence = "medium"
    else:
        confidence = "low"

    if bull_pct > 55:
        bias = "bullish"
    elif bear_pct > 55:
        bias = "bearish"
    else:
        bias = "neutral"

    # Day change
    if not today_df.empty:
        day_open = today_df["open"].iloc[0]
        day_change = latest_close - day_open
        day_change_pct = (day_change / day_open) * 100
    else:
        day_change = 0
        day_change_pct = 0

    return ProbabilityResult(
        bullish_probability=bull_pct,
        bearish_probability=bear_pct,
        overall_bias=bias,
        confidence=confidence,
        signals=signals,
        current_price=round(latest_close, 2),
        day_change=round(day_change, 2),
        day_change_pct=round(day_change_pct, 2),
        orb_data=orb_data,
    )

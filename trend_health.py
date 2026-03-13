"""Trend Health Analyzer — Is the trend continuing or reversing?

Checks 6 key signals and gives a clear visual verdict:
  1. EMA Stack (20 > 50 alignment)
  2. RSI Zone & Divergence
  3. Volume Trend (rising or falling with price)
  4. ADX Strength (trend power)
  5. Price Structure (higher highs/lows or breaking)
  6. VWAP Position (above or below)

Each signal scores +1 (continuation) or -1 (reversal).
Final score determines the verdict.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class HealthSignal:
    """A single trend health check."""
    name: str
    emoji: str
    status: str  # 'continuation', 'reversal', 'neutral'
    detail: str
    value: str  # Human-readable value


@dataclass
class TrendHealthResult:
    """Full trend health analysis."""
    verdict: str  # 'TREND CONTINUES', 'REVERSAL LIKELY', 'MIXED SIGNALS'
    verdict_emoji: str
    continuation_score: int  # How many signals say continue
    reversal_score: int  # How many signals say reverse
    total_signals: int
    confidence: str  # 'high', 'medium', 'low'
    current_trend: str  # 'uptrend', 'downtrend', 'sideways'
    signals: list[HealthSignal] = field(default_factory=list)
    summary: str = ""


def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _calc_adx(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate latest ADX value."""
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)

    # Zero out when one is larger
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr)

    dx = (plus_di - minus_di).abs() / (plus_di + minus_di) * 100
    adx = dx.rolling(period).mean()

    return float(adx.iloc[-1]) if not adx.empty and not np.isnan(adx.iloc[-1]) else 0.0


def _calc_vwap(df: pd.DataFrame) -> float:
    """Calculate VWAP for today's data."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    cum_tp_vol = (typical * df["volume"]).cumsum()
    vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
    return float(vwap.iloc[-1]) if not vwap.empty else 0.0


def _detect_rsi_divergence(close: pd.Series, rsi: pd.Series, lookback: int = 20) -> str:
    """Check for RSI divergence in recent candles."""
    if len(close) < lookback:
        return "none"

    recent_close = close.iloc[-lookback:]
    recent_rsi = rsi.iloc[-lookback:]

    # Find last two peaks in price
    mid = lookback // 2
    price_first_half = recent_close.iloc[:mid].max()
    price_second_half = recent_close.iloc[mid:].max()
    rsi_first_half = recent_rsi.iloc[:mid].max()
    rsi_second_half = recent_rsi.iloc[mid:].max()

    # Bearish divergence: price higher high, RSI lower high
    if price_second_half > price_first_half and rsi_second_half < rsi_first_half - 3:
        return "bearish"

    # Bullish divergence: price lower low, RSI higher low
    price_low_first = recent_close.iloc[:mid].min()
    price_low_second = recent_close.iloc[mid:].min()
    rsi_low_first = recent_rsi.iloc[:mid].min()
    rsi_low_second = recent_rsi.iloc[mid:].min()

    if price_low_second < price_low_first and rsi_low_second > rsi_low_first + 3:
        return "bullish"

    return "none"


def _check_price_structure(df: pd.DataFrame, lookback: int = 30) -> dict:
    """Check if price is making higher highs/lows or breaking structure."""
    if len(df) < lookback:
        return {"structure": "unknown", "detail": "Not enough data"}

    recent = df.iloc[-lookback:]
    mid = lookback // 2

    first_high = recent["high"].iloc[:mid].max()
    second_high = recent["high"].iloc[mid:].max()
    first_low = recent["low"].iloc[:mid].min()
    second_low = recent["low"].iloc[mid:].min()

    if second_high > first_high and second_low > first_low:
        return {"structure": "uptrend", "detail": "Higher Highs + Higher Lows ✅"}
    elif second_high < first_high and second_low < first_low:
        return {"structure": "downtrend", "detail": "Lower Highs + Lower Lows ✅"}
    elif second_high > first_high and second_low < first_low:
        return {"structure": "expanding", "detail": "Expanding range — volatile"}
    else:
        return {"structure": "contracting", "detail": "Contracting range — breakout soon"}


def analyze_trend_health(df: pd.DataFrame) -> TrendHealthResult:
    """Analyze whether the current trend will continue or reverse.

    Args:
        df: DataFrame with OHLCV data (at least 50 rows).

    Returns:
        TrendHealthResult with verdict and signal breakdown.
    """
    if df is None or len(df) < 50:
        return TrendHealthResult(
            verdict="INSUFFICIENT DATA",
            verdict_emoji="❓",
            continuation_score=0,
            reversal_score=0,
            total_signals=0,
            confidence="low",
            current_trend="unknown",
            summary="Need at least 50 candles for analysis.",
        )

    close = df["close"]
    signals: list[HealthSignal] = []
    continuation = 0
    reversal = 0

    # Determine current trend from EMA
    ema_20 = _calc_ema(close, 20)
    ema_50 = _calc_ema(close, 50)
    current_price = float(close.iloc[-1])
    current_ema20 = float(ema_20.iloc[-1])
    current_ema50 = float(ema_50.iloc[-1])

    if current_ema20 > current_ema50:
        current_trend = "uptrend"
    elif current_ema20 < current_ema50:
        current_trend = "downtrend"
    else:
        current_trend = "sideways"

    # ── Signal 1: EMA Stack ──────────────────────────────────
    price_above_20 = current_price > current_ema20
    ema_20_above_50 = current_ema20 > current_ema50

    if current_trend == "uptrend":
        if price_above_20 and ema_20_above_50:
            signals.append(HealthSignal(
                "EMA Stack", "📊", "continuation",
                f"Price ({current_price:.0f}) > EMA20 ({current_ema20:.0f}) > EMA50 ({current_ema50:.0f})",
                "Intact ✅",
            ))
            continuation += 1
        elif not price_above_20:
            signals.append(HealthSignal(
                "EMA Stack", "📊", "reversal",
                f"Price ({current_price:.0f}) broke below EMA20 ({current_ema20:.0f})",
                "Breaking ⚠️",
            ))
            reversal += 1
        else:
            signals.append(HealthSignal(
                "EMA Stack", "📊", "neutral",
                f"EMAs are flat — no clear stack",
                "Flat ➖",
            ))
    else:  # downtrend
        if not price_above_20 and not ema_20_above_50:
            signals.append(HealthSignal(
                "EMA Stack", "📊", "continuation",
                f"Price ({current_price:.0f}) < EMA20 ({current_ema20:.0f}) < EMA50 ({current_ema50:.0f})",
                "Bearish Intact ✅",
            ))
            continuation += 1
        elif price_above_20:
            signals.append(HealthSignal(
                "EMA Stack", "📊", "reversal",
                f"Price ({current_price:.0f}) reclaimed EMA20 ({current_ema20:.0f})",
                "Breaking Up ⚠️",
            ))
            reversal += 1
        else:
            signals.append(HealthSignal(
                "EMA Stack", "📊", "neutral",
                "EMAs are flat", "Flat ➖",
            ))

    # ── Signal 2: RSI Zone & Divergence ──────────────────────
    rsi = _calc_rsi(close, 14)
    current_rsi = float(rsi.iloc[-1]) if not rsi.empty else 50
    divergence = _detect_rsi_divergence(close, rsi)

    if divergence == "bearish" and current_trend == "uptrend":
        signals.append(HealthSignal(
            "RSI Divergence", "📉", "reversal",
            f"RSI ({current_rsi:.0f}) making lower highs while price makes higher highs!",
            f"BEARISH DIV 🚩",
        ))
        reversal += 1
    elif divergence == "bullish" and current_trend == "downtrend":
        signals.append(HealthSignal(
            "RSI Divergence", "📈", "reversal",
            f"RSI ({current_rsi:.0f}) making higher lows while price makes lower lows!",
            f"BULLISH DIV 🟢",
        ))
        reversal += 1
    else:
        # Check if RSI is in the healthy zone
        if current_trend == "uptrend" and 40 <= current_rsi <= 80:
            signals.append(HealthSignal(
                "RSI Zone", "📈", "continuation",
                f"RSI at {current_rsi:.0f} — healthy uptrend zone (40-80)",
                f"{current_rsi:.0f} ✅",
            ))
            continuation += 1
        elif current_trend == "downtrend" and 20 <= current_rsi <= 60:
            signals.append(HealthSignal(
                "RSI Zone", "📉", "continuation",
                f"RSI at {current_rsi:.0f} — healthy downtrend zone (20-60)",
                f"{current_rsi:.0f} ✅",
            ))
            continuation += 1
        else:
            zone = "overbought" if current_rsi > 70 else "oversold" if current_rsi < 30 else "neutral"
            signals.append(HealthSignal(
                "RSI Zone", "⚠️", "neutral" if zone == "neutral" else "reversal",
                f"RSI at {current_rsi:.0f} — {zone}",
                f"{current_rsi:.0f} {'⚠️' if zone != 'neutral' else '➖'}",
            ))
            if zone != "neutral":
                reversal += 1

    # ── Signal 3: Volume Trend ───────────────────────────────
    vol = df["volume"]
    vol_20 = vol.rolling(20).mean()
    recent_vol = float(vol.iloc[-5:].mean())
    avg_vol = float(vol_20.iloc[-1]) if not vol_20.empty else recent_vol

    vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

    if vol_ratio > 1.2:
        signals.append(HealthSignal(
            "Volume", "📊", "continuation",
            f"Volume {vol_ratio:.1f}x above average — strong participation",
            f"{vol_ratio:.1f}x ✅",
        ))
        continuation += 1
    elif vol_ratio < 0.7:
        signals.append(HealthSignal(
            "Volume", "📊", "reversal",
            f"Volume {vol_ratio:.1f}x below average — participation drying up",
            f"{vol_ratio:.1f}x ⚠️",
        ))
        reversal += 1
    else:
        signals.append(HealthSignal(
            "Volume", "📊", "neutral",
            f"Volume at {vol_ratio:.1f}x average — normal",
            f"{vol_ratio:.1f}x ➖",
        ))

    # ── Signal 4: ADX Strength ───────────────────────────────
    adx = _calc_adx(df)

    if adx > 25:
        signals.append(HealthSignal(
            "ADX (Trend Power)", "💪", "continuation",
            f"ADX at {adx:.0f} — strong trend in progress (>25)",
            f"{adx:.0f} ✅",
        ))
        continuation += 1
    elif adx < 20:
        signals.append(HealthSignal(
            "ADX (Trend Power)", "😴", "reversal",
            f"ADX at {adx:.0f} — trend is weak/dying (<20)",
            f"{adx:.0f} ⚠️",
        ))
        reversal += 1
    else:
        signals.append(HealthSignal(
            "ADX (Trend Power)", "🔄", "neutral",
            f"ADX at {adx:.0f} — moderate trend (20-25)",
            f"{adx:.0f} ➖",
        ))

    # ── Signal 5: Price Structure ────────────────────────────
    structure = _check_price_structure(df)

    if (current_trend == "uptrend" and structure["structure"] == "uptrend") or \
       (current_trend == "downtrend" and structure["structure"] == "downtrend"):
        signals.append(HealthSignal(
            "Price Structure", "🏗️", "continuation",
            structure["detail"],
            "Intact ✅",
        ))
        continuation += 1
    elif structure["structure"] in ("expanding", "contracting"):
        signals.append(HealthSignal(
            "Price Structure", "🏗️", "neutral",
            structure["detail"],
            "Uncertain ➖",
        ))
    else:
        signals.append(HealthSignal(
            "Price Structure", "🏗️", "reversal",
            f"Structure breaking — {structure['detail']}",
            "Breaking ⚠️",
        ))
        reversal += 1

    # ── Signal 6: VWAP Position ──────────────────────────────
    # Get today's data for VWAP
    today = df.index[-1]
    if hasattr(today, 'date'):
        today_mask = df.index.date == today.date()
        today_df = df[today_mask]
    else:
        today_df = df.tail(78)  # ~6.5 hours of 5m candles

    if len(today_df) > 5:
        vwap = _calc_vwap(today_df)
        if np.isnan(vwap) or vwap == 0:
            signals.append(HealthSignal(
                "VWAP", "📏", "neutral",
                "VWAP not available (volume data missing)",
                "N/A ➖",
            ))
        elif current_trend == "uptrend" and current_price > vwap:
            signals.append(HealthSignal(
                "VWAP", "📏", "continuation",
                f"Price ({current_price:.0f}) above VWAP ({vwap:.0f}) — bulls in control",
                f"Above ✅",
            ))
            continuation += 1
        elif current_trend == "downtrend" and current_price < vwap:
            signals.append(HealthSignal(
                "VWAP", "📏", "continuation",
                f"Price ({current_price:.0f}) below VWAP ({vwap:.0f}) — bears in control",
                f"Below ✅",
            ))
            continuation += 1
        else:
            side = "above" if current_price > vwap else "below"
            signals.append(HealthSignal(
                "VWAP", "📏", "reversal",
                f"Price ({current_price:.0f}) {side} VWAP ({vwap:.0f}) — against the trend!",
                f"Against ⚠️",
            ))
            reversal += 1
    else:
        signals.append(HealthSignal(
            "VWAP", "📏", "neutral",
            "Not enough intraday data for VWAP",
            "N/A ➖",
        ))

    # ── Final Verdict ────────────────────────────────────────
    total = continuation + reversal
    if total == 0:
        verdict = "INSUFFICIENT DATA"
        verdict_emoji = "❓"
        confidence = "low"
    elif continuation >= 5:
        verdict = "TREND CONTINUES"
        verdict_emoji = "🟢"
        confidence = "high"
    elif continuation >= 4:
        verdict = "TREND CONTINUES"
        verdict_emoji = "🟢"
        confidence = "medium"
    elif reversal >= 4:
        verdict = "REVERSAL LIKELY"
        verdict_emoji = "🔴"
        confidence = "high" if reversal >= 5 else "medium"
    elif reversal >= 3 and continuation <= 2:
        verdict = "REVERSAL BREWING"
        verdict_emoji = "🟡"
        confidence = "medium"
    else:
        verdict = "MIXED SIGNALS"
        verdict_emoji = "⚪"
        confidence = "low"

    # Build summary
    trend_label = "📈 UPTREND" if current_trend == "uptrend" else "📉 DOWNTREND"
    summary = f"Current: {trend_label} | {continuation}/{len(signals)} signals say CONTINUE, {reversal}/{len(signals)} say REVERSE"

    return TrendHealthResult(
        verdict=verdict,
        verdict_emoji=verdict_emoji,
        continuation_score=continuation,
        reversal_score=reversal,
        total_signals=len(signals),
        confidence=confidence,
        current_trend=current_trend,
        signals=signals,
        summary=summary,
    )

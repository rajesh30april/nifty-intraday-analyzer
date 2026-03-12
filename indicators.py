"""Technical indicators for intraday Nifty analysis."""

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.DataFrame:
    """Average Directional Index. Returns DataFrame with ADX, +DI, -DI."""
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    plus_dm = (high - prev_high).where((high - prev_high) > (prev_low - low), 0.0)
    plus_dm = plus_dm.where(plus_dm > 0, 0.0)

    minus_dm = (prev_low - low).where((prev_low - low) > (high - prev_high), 0.0)
    minus_dm = minus_dm.where(minus_dm > 0, 0.0)

    atr_vals = atr(high, low, close, period)

    plus_di = 100 * ema(plus_dm, period) / atr_vals.replace(0, np.nan)
    minus_di = 100 * ema(minus_dm, period) / atr_vals.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = ema(dx, period)

    return pd.DataFrame({"adx": adx_val, "plus_di": plus_di, "minus_di": minus_di})


def supertrend(
    high: pd.Series, low: pd.Series, close: pd.Series,
    period: int = 10, multiplier: float = 3.0,
) -> pd.DataFrame:
    """Supertrend indicator. Returns DataFrame with supertrend line and direction."""
    atr_vals = atr(high, low, close, period)
    hl2 = (high + low) / 2

    upper_band = hl2 + multiplier * atr_vals
    lower_band = hl2 - multiplier * atr_vals

    supertrend_line = pd.Series(np.nan, index=close.index)
    direction = pd.Series(1, index=close.index)  # 1=bullish, -1=bearish

    for i in range(1, len(close)):
        # Adjust bands based on previous values
        if lower_band.iloc[i] > lower_band.iloc[i - 1] or close.iloc[i - 1] < lower_band.iloc[i - 1]:
            pass  # keep current lower band
        else:
            lower_band.iloc[i] = lower_band.iloc[i - 1]

        if upper_band.iloc[i] < upper_band.iloc[i - 1] or close.iloc[i - 1] > upper_band.iloc[i - 1]:
            pass  # keep current upper band
        else:
            upper_band.iloc[i] = upper_band.iloc[i - 1]

        # Determine direction
        if direction.iloc[i - 1] == 1:  # was bullish
            if close.iloc[i] < lower_band.iloc[i]:
                direction.iloc[i] = -1
                supertrend_line.iloc[i] = upper_band.iloc[i]
            else:
                direction.iloc[i] = 1
                supertrend_line.iloc[i] = lower_band.iloc[i]
        else:  # was bearish
            if close.iloc[i] > upper_band.iloc[i]:
                direction.iloc[i] = 1
                supertrend_line.iloc[i] = lower_band.iloc[i]
            else:
                direction.iloc[i] = -1
                supertrend_line.iloc[i] = upper_band.iloc[i]

    return pd.DataFrame({"supertrend": supertrend_line, "direction": direction})


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Volume Weighted Average Price (cumulative for the session).

    Falls back to cumulative typical price average if volume is zero
    (common for index data like Nifty 50).
    """
    typical_price = (high + low + close) / 3

    # If volume is all zeros (index data), use simple cumulative average
    if volume.sum() == 0:
        return typical_price.expanding().mean()

    cum_tp_vol = (typical_price * volume).cumsum()
    cum_vol = volume.cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def macd(
    close: pd.Series,
    fast: int = 12, slow: int = 26, signal: int = 9,
) -> pd.DataFrame:
    """MACD indicator."""
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line

    return pd.DataFrame({
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
    })


def opening_range(
    df: pd.DataFrame, minutes: int = 15,
) -> dict:
    """Calculate Opening Range Breakout levels.

    Args:
        df: Today's intraday data.
        minutes: First N minutes to define the range.

    Returns:
        Dict with orb_high, orb_low, breakout status.
    """
    if df.empty:
        return {"orb_high": None, "orb_low": None, "breakout": "none"}

    start_time = df.index[0]
    orb_end = start_time + pd.Timedelta(minutes=minutes)
    orb_candles = df[df.index <= orb_end]

    if orb_candles.empty:
        return {"orb_high": None, "orb_low": None, "breakout": "none"}

    orb_high = orb_candles["high"].max()
    orb_low = orb_candles["low"].min()
    current_close = df["close"].iloc[-1]

    if current_close > orb_high:
        breakout = "bullish"
    elif current_close < orb_low:
        breakout = "bearish"
    else:
        breakout = "none"

    return {
        "orb_high": round(orb_high, 2),
        "orb_low": round(orb_low, 2),
        "breakout": breakout,
    }


def volume_analysis(volume: pd.Series) -> dict:
    """Analyze volume patterns. Returns N/A for index data with zero volume."""
    if volume.sum() == 0:
        return {
            "current_volume": 0,
            "avg_volume": 0,
            "volume_ratio": 0,
            "volume_trend": "not_available",
        }

    avg_vol = volume.mean()
    current_vol = volume.iloc[-1]
    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

    # Recent volume trend (last 5 candles vs previous 5)
    if len(volume) >= 10:
        recent = volume.iloc[-5:].mean()
        prior = volume.iloc[-10:-5].mean()
        vol_trend = "increasing" if recent > prior * 1.1 else (
            "decreasing" if recent < prior * 0.9 else "stable"
        )
    else:
        vol_trend = "insufficient_data"

    return {
        "current_volume": int(current_vol),
        "avg_volume": int(avg_vol),
        "volume_ratio": round(vol_ratio, 2),
        "volume_trend": vol_trend,
    }

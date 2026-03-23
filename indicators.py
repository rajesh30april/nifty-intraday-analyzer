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


def bollinger_bands(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands: SMA ± std_dev × σ."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    width = (upper - lower) / mid.replace(0, np.nan) * 100  # % width
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "width": width})


def keltner_channels(
    high: pd.Series, low: pd.Series, close: pd.Series,
    period: int = 20, multiplier: float = 1.5,
) -> pd.DataFrame:
    """Keltner Channels: EMA ± multiplier × ATR."""
    mid = ema(close, period)
    atr_v = atr(high, low, close, period)
    upper = mid + multiplier * atr_v
    lower = mid - multiplier * atr_v
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower})


def bb_squeeze(
    high: pd.Series, low: pd.Series, close: pd.Series,
    bb_period: int = 20, bb_std: float = 2.0,
    kc_period: int = 20, kc_mult: float = 1.5,
) -> pd.DataFrame:
    """TTM Squeeze — Bollinger Bands vs Keltner Channels.

    squeeze_on=True  → BB inside KC  → low volatility, energy building
    squeeze_on=False → BB outside KC → breakout, energy releasing
    momentum column  → positive = bullish, negative = bearish
    """
    bb = bollinger_bands(close, bb_period, bb_std)
    kc = keltner_channels(high, low, close, kc_period, kc_mult)

    squeeze_on = (bb["upper"] < kc["upper"]) & (bb["lower"] > kc["lower"])

    # Momentum: linear regression of (close - midpoint of BB & KC)
    delta = close - (bb["mid"] + kc["mid"]) / 2
    momentum = delta.rolling(bb_period).apply(
        lambda x: np.polyfit(range(len(x)), x, 1)[0], raw=True
    )

    return pd.DataFrame({
        "squeeze_on":  squeeze_on,
        "momentum":    momentum,
        "bb_upper":    bb["upper"],
        "bb_lower":    bb["lower"],
        "kc_upper":    kc["upper"],
        "kc_lower":    kc["lower"],
        "bb_width":    bb["width"],
    })


def camarilla_pivots(prev_high: float, prev_low: float, prev_close: float) -> dict:
    """Camarilla Pivot Points from previous day H/L/C.

    Classic Camarilla rules:
    - Price approaches H3 → short, target L3
    - Price breaks above H4 → strong long
    - Price approaches L3 → long, target H3
    - Price breaks below L4 → strong short
    """
    rng = prev_high - prev_low
    return {
        "H4": round(prev_close + rng * 1.1 / 2, 2),
        "H3": round(prev_close + rng * 1.1 / 4, 2),
        "H2": round(prev_close + rng * 1.1 / 6, 2),
        "H1": round(prev_close + rng * 1.1 / 12, 2),
        "L1": round(prev_close - rng * 1.1 / 12, 2),
        "L2": round(prev_close - rng * 1.1 / 6, 2),
        "L3": round(prev_close - rng * 1.1 / 4, 2),
        "L4": round(prev_close - rng * 1.1 / 2, 2),
    }


def central_pivot_range(prev_high: float, prev_low: float, prev_close: float) -> dict:
    """Central Pivot Range (CPR) — widely used in Indian markets.

    Width interpretation:
      Narrow CPR (< 0.2% of price) → sideways, use reversal strategies
      Wide CPR   (> 0.5% of price) → trending, trade with trend
    """
    pivot = (prev_high + prev_low + prev_close) / 3
    bc    = (prev_high + prev_low) / 2          # Bottom Central
    tc    = (pivot - bc) + pivot                 # Top Central
    r1    = 2 * pivot - prev_low
    r2    = pivot + (prev_high - prev_low)
    s1    = 2 * pivot - prev_high
    s2    = pivot - (prev_high - prev_low)
    return {
        "pivot": round(pivot, 2),
        "tc":    round(tc, 2),
        "bc":    round(bc, 2),
        "r1":    round(r1, 2),
        "r2":    round(r2, 2),
        "s1":    round(s1, 2),
        "s2":    round(s2, 2),
        "width": round(abs(tc - bc), 2),
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


def fibonacci_retracement(high: float, low: float, direction: str = "up") -> dict:
    """Calculate Fibonacci retracement levels from a swing.

    Args:
        high: Swing high price
        low: Swing low price
        direction: 'up' for uptrend (retracing from high), 
                   'down' for downtrend (retracing from low)

    Returns:
        Dictionary with retracement levels (23.6%, 38.2%, 50%, 61.8%, 78.6%)

    Usage:
        # After uptrend from 22600 to 22800:
        fib = fibonacci_retracement(22800, 22600, 'up')
        # fib['61.8'] = 22676 (strong support for pullback buy)

        # After downtrend from 22800 to 22600:
        fib = fibonacci_retracement(22800, 22600, 'down')
        # fib['61.8'] = 22724 (strong resistance for bounce sell)
    """
    range_val = high - low
    
    if direction == "up":
        # Uptrend: retracing DOWN from high
        return {
            "0.0":   round(high, 2),                      # 0% = swing high
            "23.6":  round(high - range_val * 0.236, 2),  # Shallow pullback
            "38.2":  round(high - range_val * 0.382, 2),  # Healthy pullback
            "50.0":  round(high - range_val * 0.500, 2),  # Midpoint
            "61.8":  round(high - range_val * 0.618, 2),  # GOLDEN RATIO - strong support
            "78.6":  round(high - range_val * 0.786, 2),  # Deep pullback - trend weakening
            "100.0": round(low, 2),                       # 100% = swing low
        }
    else:
        # Downtrend: retracing UP from low
        return {
            "0.0":   round(low, 2),                       # 0% = swing low
            "23.6":  round(low + range_val * 0.236, 2),   # Shallow bounce
            "38.2":  round(low + range_val * 0.382, 2),   # Healthy bounce
            "50.0":  round(low + range_val * 0.500, 2),   # Midpoint
            "61.8":  round(low + range_val * 0.618, 2),   # GOLDEN RATIO - strong resistance
            "78.6":  round(low + range_val * 0.786, 2),   # Deep bounce - reversal likely
            "100.0": round(high, 2),                      # 100% = swing high
        }


def fibonacci_extension(swing_low: float, swing_high: float, 
                        retrace_low: float, direction: str = "up") -> dict:
    """Calculate Fibonacci extension targets after a retracement.

    Args:
        swing_low: Initial swing low
        swing_high: Initial swing high
        retrace_low: Retracement low (where price pulled back to)
        direction: 'up' for bullish extension, 'down' for bearish

    Returns:
        Dictionary with extension targets (100%, 127.2%, 161.8%, 261.8%)

    Usage:
        # Uptrend: 22600 → 22800 → pullback to 22676 → where's target?
        ext = fibonacci_extension(22600, 22800, 22676, 'up')
        # ext['161.8'] = 22924 (strong target for take profit)

        # Downtrend: 22800 → 22600 → bounce to 22724 → where's target?
        ext = fibonacci_extension(22800, 22600, 22724, 'down')
        # ext['161.8'] = 22476 (strong target for take profit)
    """
    swing_range = swing_high - swing_low
    
    if direction == "up":
        # Bullish extension: targets above swing high
        return {
            "100.0":  round(swing_high, 2),                        # Original high
            "127.2":  round(swing_high + swing_range * 0.272, 2),  # First extension
            "161.8":  round(swing_high + swing_range * 0.618, 2),  # GOLDEN TARGET
            "200.0":  round(swing_high + swing_range * 1.000, 2),  # Double range
            "261.8":  round(swing_high + swing_range * 1.618, 2),  # Extreme extension
        }
    else:
        # Bearish extension: targets below swing low
        return {
            "100.0":  round(swing_low, 2),                         # Original low
            "127.2":  round(swing_low - swing_range * 0.272, 2),   # First extension
            "161.8":  round(swing_low - swing_range * 0.618, 2),   # GOLDEN TARGET
            "200.0":  round(swing_low - swing_range * 1.000, 2),   # Double range
            "261.8":  round(swing_low - swing_range * 1.618, 2),   # Extreme extension
        }

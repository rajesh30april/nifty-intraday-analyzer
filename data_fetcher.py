"""Fetch Nifty 50 intraday data from Yahoo Finance."""

import os
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

# Walmart corporate proxy
PROXY = "http://sysproxy.wal-mart.com:8080"
os.environ.setdefault("HTTP_PROXY", PROXY)
os.environ.setdefault("HTTPS_PROXY", PROXY)
os.environ.setdefault("http_proxy", PROXY)
os.environ.setdefault("https_proxy", PROXY)

NIFTY_SYMBOL = "^NSEI"


def fetch_intraday_data(
    interval: str = "5m",
    period: str = "5d",
    retries: int = 2,
) -> pd.DataFrame:
    """Fetch intraday candle data for Nifty 50.

    Args:
        interval: Candle interval - '1m', '5m', '15m', '30m', '1h'.
        period: Lookback period - '1d', '5d', '1mo'.
        retries: Number of retry attempts on failure.

    Returns:
        DataFrame with OHLCV columns and DatetimeIndex.
    """
    import time
    last_error = None
    for attempt in range(retries + 1):
        try:
            ticker = yf.Ticker(NIFTY_SYMBOL)
            df = ticker.history(period=period, interval=interval)

            # Defensive: yfinance can return None or non-DataFrame in some versions
            if df is None:
                raise ValueError(f"yfinance returned None for {NIFTY_SYMBOL} ({interval}/{period})")
            if not hasattr(df, 'columns') or not hasattr(df, 'empty'):
                raise ValueError(f"yfinance returned unexpected type: {type(df).__name__}")
            if df.empty:
                raise ValueError(f"Empty DataFrame for {NIFTY_SYMBOL} ({interval}/{period})")

            # Normalize column names to lowercase
            df = df.copy()
            df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]

            # Keep only OHLCV columns that exist
            required = ["open", "high", "low", "close", "volume"]
            available = [c for c in required if c in df.columns]
            if not available:
                raise ValueError(f"No OHLCV columns found. Got: {list(df.columns)}")
            df = df[available]

            # Drop rows where close is NaN (can happen on partial candles)
            df = df.dropna(subset=["close"])
            if df.empty:
                raise ValueError("All rows had NaN close — stale/partial data")

            return df

        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(2 ** attempt)  # exponential back-off: 1s, 2s

    raise ValueError(f"Failed after {retries + 1} attempts: {last_error}")


def fetch_daily_data(period: str = "6mo") -> pd.DataFrame:
    """Fetch daily candle data for broader context."""
    ticker = yf.Ticker(NIFTY_SYMBOL)
    df = ticker.history(period=period, interval="1d")

    if df.empty:
        raise ValueError(f"No daily data for {NIFTY_SYMBOL}")

    df.columns = [c.lower().replace(" ", "_") for c in df.columns]
    required = ["open", "high", "low", "close", "volume"]
    df = df[[c for c in required if c in df.columns]]

    return df


def get_todays_data(df: pd.DataFrame) -> pd.DataFrame:
    """Filter only today's candles from multi-day intraday data."""
    if df.empty:
        return df

    # Get the most recent trading day
    latest_date = df.index[-1].date()
    return df[df.index.date == latest_date]

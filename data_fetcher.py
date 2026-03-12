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
    last_error = None
    for attempt in range(retries + 1):
        try:
            ticker = yf.Ticker(NIFTY_SYMBOL)
            df = ticker.history(period=period, interval=interval)

            if df is None or df.empty:
                raise ValueError(f"No data returned for {NIFTY_SYMBOL} ({interval}/{period})")

            # Normalize column names
            df.columns = [c.lower().replace(" ", "_") for c in df.columns]

            # Keep only OHLCV
            required = ["open", "high", "low", "close", "volume"]
            df = df[[c for c in required if c in df.columns]]

            return df
        except Exception as e:
            last_error = e
            if attempt < retries:
                import time
                time.sleep(1)

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

"""Fetch Nifty 50 intraday data.

Primary  : Yahoo Finance (^NSEI) — spot price, no volume
Volume   : Zerodha Kite Nifty Futures — real volume, merged in
Fallback : Yahoo only if Kite not logged in (volume stays 0)
"""

import os
import datetime as _dt
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

# Walmart corporate proxy
PROXY = "http://sysproxy.wal-mart.com:8080"
os.environ.setdefault("HTTP_PROXY", PROXY)

# ── NFO instruments cache ─────────────────────────────────────────────────
# instruments('NFO') downloads ~50k rows — cache it for the session day
# so repeated calls to _fetch_futures_volume don't hammer the API.
_nfo_cache: list | None = None
_nfo_cache_date: _dt.date | None = None
_nfo_fut_token_cache: dict[str, int] = {}   # interval → token (no expiry change mid-day)


def _get_nfo_instruments() -> list:
    """Return NFO instruments, cached per calendar day."""
    global _nfo_cache, _nfo_cache_date
    today = _dt.date.today()
    if _nfo_cache is not None and _nfo_cache_date == today:
        return _nfo_cache
    from kite_integration import kite_manager  # noqa: PLC0415
    _nfo_cache = kite_manager.kite.instruments("NFO")
    _nfo_cache_date = today
    return _nfo_cache
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


def _fetch_futures_volume(interval: str, period: str) -> "pd.Series | None":
    """Fetch volume from Nifty near-month futures via Zerodha Kite.

    Returns a Series indexed by datetime, or None if unavailable.
    This is used to enrich the Yahoo spot data with real volume since
    ^NSEI (cash index) never has volume.
    """
    try:
        from kite_integration import kite_manager  # noqa: PLC0415
        if not kite_manager.is_authenticated:
            return None

        # Dynamically find near-month NIFTY FUT token (cached per day)
        # instruments('NFO') is slow (~4s) — cache token to avoid re-fetching
        today = _dt.date.today()
        if "nifty_fut" in _nfo_fut_token_cache:
            token = _nfo_fut_token_cache["nifty_fut"]
        else:
            instruments = _get_nfo_instruments()
            fut = [
                i for i in instruments
                if i["tradingsymbol"].startswith("NIFTY")
                and i["instrument_type"] == "FUT"
                and i["expiry"] >= today
            ]
            if not fut:
                return None
            fut.sort(key=lambda x: x["expiry"])
            token = fut[0]["instrument_token"]
            _nfo_fut_token_cache["nifty_fut"] = token   # cache for session

        # Map interval to Zerodha format
        interval_map = {
            "1m": "minute", "3m": "3minute", "5m": "5minute",
            "15m": "15minute", "30m": "30minute", "1h": "60minute",
        }
        kite_interval = interval_map.get(interval, "5minute")

        # Map period to days
        period_days = {
            "1d": 1, "5d": 5, "7d": 7, "14d": 14, "30d": 30,
            "60d": 60, "1mo": 30, "3mo": 60, "6mo": 60,
        }
        days = period_days.get(period, 5)
        from_date = today - _dt.timedelta(days=days + 3)  # +3 for weekends

        raw = kite_manager.kite.historical_data(
            token, from_date, today, kite_interval
        )
        if not raw:
            return None

        vol_df = pd.DataFrame(raw)[["date", "volume"]]
        vol_df["date"] = pd.to_datetime(vol_df["date"])
        vol_df = vol_df.set_index("date")["volume"]
        # Normalize to IST-aware if needed
        if vol_df.index.tz is None:
            import pytz  # noqa: PLC0415
            vol_df.index = vol_df.index.tz_localize(pytz.timezone("Asia/Kolkata"))
        return vol_df

    except Exception:  # noqa: BLE001
        return None  # silently fall back — no volume is fine


def fetch_intraday_data(
    interval: str = "5m",
    period: str = "5d",
    retries: int = 2,
    enrich_volume: bool = True,
) -> pd.DataFrame:
    """Fetch intraday candle data — spot price from Yahoo, volume from Kite.

    If Zerodha Kite is authenticated, automatically overlays real Nifty
    Futures volume onto the Yahoo spot OHLC. Falls back to volume=0 if
    Kite is not available (existing behaviour preserved).

    Args:
        interval: Candle interval - '1m', '5m', '15m', '30m', '1h'.
        period: Lookback period - '1d', '5d', '1mo'.
        retries: Number of retry attempts on failure.
        enrich_volume: If True, overlay Kite futures volume (default on).

    Returns:
        DataFrame with OHLCV columns and DatetimeIndex.
    """  # noqa: D401
    import time
    last_error = None
    for attempt in range(retries + 1):
        try:
            ticker = yf.Ticker(NIFTY_SYMBOL)
            df = ticker.history(period=period, interval=interval)

            if df is None:
                raise ValueError(f"yfinance returned None for {NIFTY_SYMBOL}")
            if not hasattr(df, "columns") or not hasattr(df, "empty"):
                raise ValueError(f"yfinance returned unexpected type: {type(df).__name__}")
            if df.empty:
                raise ValueError(f"Empty DataFrame for {NIFTY_SYMBOL} ({interval}/{period})")

            df = df.copy()
            df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
            required = ["open", "high", "low", "close", "volume"]
            available = [c for c in required if c in df.columns]
            if not available:
                raise ValueError(f"No OHLCV columns found. Got: {list(df.columns)}")
            df = df[available].dropna(subset=["close"])
            if df.empty:
                raise ValueError("All rows had NaN close")

            # ── Volume enrichment from Kite futures ──────────────────
            if enrich_volume and df["volume"].sum() == 0:
                fut_vol = _fetch_futures_volume(interval, period)
                if fut_vol is not None:
                    # Align on timestamps — reindex futures vol to spot index
                    aligned = fut_vol.reindex(df.index, method="nearest",
                                               tolerance=pd.Timedelta("2min"))
                    if aligned.notna().any():
                        df["volume"] = aligned.values
                        df["volume"] = df["volume"].fillna(0).astype(int)
                        print(f"✅ Volume enriched from Kite futures "
                              f"({df['volume'].gt(0).sum()}/{len(df)} candles)")

            return df

        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(2 ** attempt)

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

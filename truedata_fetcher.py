"""TrueData historical data fetcher for Nifty 50 backtesting.

Requires a TrueData subscription (truedata.in).
Free 7-day trial available.

Credentials are read from environment variables:
  TRUEDATA_USERNAME
  TRUEDATA_PASSWORD

Or passed directly to fetch functions.
"""

import os
import time
import pandas as pd
from datetime import datetime, timedelta

NIFTY_SYMBOL = "NIFTY-I"  # TrueData continuous contract symbol

# Interval map: our internal key → TrueData bar_size string
INTERVAL_MAP = {
    "1m":  "1 min",
    "3m":  "3 min",
    "5m":  "5 min",
    "15m": "15 min",
    "30m": "30 min",
    "1h":  "60 min",
    "1d":  "1 day",
}

# Period → timedelta map
PERIOD_MAP = {
    "5d":   timedelta(days=5),
    "30d":  timedelta(days=30),
    "60d":  timedelta(days=60),
    "90d":  timedelta(days=90),
    "6mo":  timedelta(days=180),
    "1y":   timedelta(days=365),
    "2y":   timedelta(days=730),
    "5y":   timedelta(days=1825),
}


class TrueDataCredentialError(Exception):
    """Raised when TrueData credentials are missing."""
    pass


class TrueDataFetchError(Exception):
    """Raised when TrueData data fetch fails."""
    pass


def get_credentials() -> tuple[str, str]:
    """Get TrueData credentials from env vars."""
    username = os.environ.get("TRUEDATA_USERNAME", "").strip()
    password = os.environ.get("TRUEDATA_PASSWORD", "").strip()
    if not username or not password:
        raise TrueDataCredentialError(
            "TrueData credentials not set. "
            "Set TRUEDATA_USERNAME and TRUEDATA_PASSWORD env vars."
        )
    return username, password


def set_credentials(username: str, password: str):
    """Set TrueData credentials in environment."""
    os.environ["TRUEDATA_USERNAME"] = username.strip()
    os.environ["TRUEDATA_PASSWORD"] = password.strip()


def has_credentials() -> bool:
    """Check if TrueData credentials are available."""
    return bool(
        os.environ.get("TRUEDATA_USERNAME")
        and os.environ.get("TRUEDATA_PASSWORD")
    )


def fetch_historical_data(
    interval: str = "5m",
    period: str = "6mo",
    username: str | None = None,
    password: str | None = None,
) -> pd.DataFrame:
    """Fetch historical Nifty candle data from TrueData.

    Args:
        interval: Candle interval ('1m','5m','15m','30m','1h','1d').
        period: Lookback ('5d','30d','60d','90d','6mo','1y','2y','5y').
        username: TrueData username (falls back to env var).
        password: TrueData password (falls back to env var).

    Ret    DataFrame with OHLCV columns, DatetimeIndex (IST).

    Raises:
        TrueDataCredentialError: If credentials are missing.
        TrueDataFetchError: If data fetch fails.
    """
    from truedata_ws.websocket.TD import TD

    # Resolve credentials
    uname = username or os.environ.get("TRUEDATA_USERNAME", "")
    pwd = password or os.environ.get("TRUEDATA_PASSWORD", "")

    if not uname or not pwd:
        raise TrueDataCredentialError(
            "TrueData credentials missing. Please enter your username and password."
        )

    # Resolve interval
    bar_size = INTERVAL_MAP.get(interval)
    if not bar_size:
        raise TrueDataFetchError(f"Unsupported interval: {interval}")

    # Resolve date range
    delta = PERIOD_MAP.get(period)
    if not delta:
        raise TrueDataFetchError(f"Unsupported period: {period}")

    end_time = datetime.now()
    start_time = end_time - delta

    print(f"🔗 Connecting to TrueData as '{uname}'...")

    td = None
    try:
        # Connect (port 8082 = historical only, no live feed)
        td = TD(uname, pwd, live_port=None, historical_port=8082)
        time.sleep(1)  # Brief wait for connection

        print(f"📡 Fetching {period} of {interval} Nifty data from TrueData...")

        raw = td.get_historical_data_from_start_time(
            contract=NIFTY_SYMBOL,
            delivery=None,
            start_time=start_time,
            end_time=end_time,
            bar_size=bar_size,
        )

        if raw is None or (hasattr(raw, 'empty') and raw.empty):
            raise TrueDataFetchError("No data returned from TrueData")

        df = _normalize_df(raw)
        print(f"✅ TrueData: Got {len(df)} candles from {df.index[0]} to {df.index[-1]}")
        return df

    except TrueDataCredentialError:
        raise
    except TrueDataFetchError:
        raise
    except Exception as e:
        raise TrueDataFetchError(f"TrueData fetch failed: {e}") from e
    finally:
        if td is not None:
            try:
                td.disconnect()
            except Exception:
                pass


def _normalize_df(raw) -> pd.DataFrame:
    """Normalize TrueData response to standard OHLCV DataFrame."""
    if isinstance(raw, pd.DataFrame):
        df = raw.copy()
    else:
        df = pd.DataFrame(raw)

    # Lowercase column names
    df.columns = [c.lower().strip() for c in df.columns]

    # Rename common TrueData column names
    rename_map = {
        "time": "datetime",
        "timestamp": "datetime",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "vol": "volume",
    }
    df = df.rename(columns=rename_map)

    # Set datetime index
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime")
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # Keep only OHLCV
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep]

    # Convert to numeric
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["close"])
    df = df.sort_index()
    return df

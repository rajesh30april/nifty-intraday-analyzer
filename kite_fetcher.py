"""Nifty intraday data from Zerodha Kite — single source of truth.

Returns Nifty near-month Futures OHLCV with REAL volume.
Falls back to Yahoo Finance (no volume) if Kite not authenticated.

Why futures instead of ^NSEI spot index?
  - Spot index has zero volume — it's a calculated number, not traded
  - Nifty futures IS what traders actually trade — price + volume are real
  - Futures price tracks spot within a few points (basis ~20-50pts)
  - For intraday signal generation, futures price is the right instrument
"""

from __future__ import annotations

import datetime
import pandas as pd

# Interval mappings
_KITE_INTERVAL = {
    "1m":  "minute",
    "3m":  "3minute",
    "5m":  "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h":  "60minute",
}
_PERIOD_DAYS = {
    "1d": 1, "2d": 2, "5d": 5, "7d": 7,
    "14d": 14, "30d": 30, "60d": 60,
    "1mo": 30, "3mo": 60, "6mo": 60,
}


def _near_month_fut_token() -> int:
    """Return instrument token for the nearest expiry NIFTY futures contract."""
    from kite_integration import kite_manager  # noqa: PLC0415
    instruments = kite_manager.kite.instruments("NFO")
    today = datetime.date.today()
    candidates = [
        i for i in instruments
        if i["tradingsymbol"].startswith("NIFTY")
        and i["instrument_type"] == "FUT"
        and i["expiry"] >= today
    ]
    if not candidates:
        raise RuntimeError("No active NIFTY futures found in Zerodha instruments")
    candidates.sort(key=lambda x: x["expiry"])
    return int(candidates[0]["instrument_token"])


def fetch_futures_data(
    interval: str = "5m",
    period: str = "60d",
) -> pd.DataFrame:
    """Fetch Nifty near-month futures OHLCV from Zerodha Kite.

    Args:
        interval: Candle size — '1m', '5m', '15m', '30m', '1h'.
        period  : Lookback  — '5d', '30d', '60d', etc.

    Returns:
        DataFrame with columns [open, high, low, close, volume],
        DatetimeIndex in IST, no NaN rows.

    Raises:
        RuntimeError if Kite not authenticated or no data returned.
    """
    from kite_integration import kite_manager  # noqa: PLC0415
    if not kite_manager.is_authenticated:
        raise RuntimeError(
            "Zerodha Kite not authenticated. "
            "Login via the Auto-Trader tab first."
        )

    token   = _near_month_fut_token()
    days    = _PERIOD_DAYS.get(period, 60)
    kite_iv = _KITE_INTERVAL.get(interval, "5minute")
    today   = datetime.date.today()
    from_dt = today - datetime.timedelta(days=days + 4)  # +4 buffer for weekends

    raw = kite_manager.kite.historical_data(token, from_dt, today, kite_iv)
    if not raw:
        raise RuntimeError(f"Zerodha returned no data for NIFTY FUT {kite_iv}/{period}")

    df = pd.DataFrame(raw)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")[["open", "high", "low", "close", "volume"]]
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0]  # strip any corrupt rows

    return df


def fetch_data(
    interval: str = "5m",
    period: str = "60d",
    prefer_kite: bool = True,
) -> tuple[pd.DataFrame, str]:
    """Fetch intraday OHLCV with automatic fallback.

    Tries Zerodha Kite Futures first (real volume).
    Falls back to Yahoo Finance ^NSEI (no volume) if Kite unavailable.

    Returns:
        (df, source_label) — df with OHLCV, source_label for display.
    """
    if prefer_kite:
        try:
            df = fetch_futures_data(interval=interval, period=period)
            return df, "Zerodha NIFTY FUT"
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  Kite unavailable ({exc}) — falling back to Yahoo Finance")

    # Fallback — Yahoo spot (no volume, legacy behaviour)
    from data_fetcher import fetch_intraday_data  # noqa: PLC0415
    df = fetch_intraday_data(interval=interval, period=period, enrich_volume=False)
    return df, "Yahoo Finance ^NSEI"
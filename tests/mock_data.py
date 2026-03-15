"""Mock OHLCV DataFrame factory for Nifty 50 auto-trader tests.

Generates realistic 5-min candle DataFrames for different market
scenarios without hitting any live API.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from datetime import datetime, timedelta


# ── Base market open ──────────────────────────────────────────────
MARKET_OPEN  = datetime(2026, 3, 16, 9, 15)   # Monday
MARKET_CLOSE = datetime(2026, 3, 16, 15, 30)
BASE_PRICE   = 23_200.0   # realistic Nifty level


def _make_candles(
    times: list[datetime],
    prices: list[float],
    spread: float = 10.0,
    volume: int = 50_000,
) -> pd.DataFrame:
    """Turn a list of close prices into OHLCV candles."""
    rows = []
    for i, (t, close) in enumerate(zip(times, prices)):
        prev = prices[i - 1] if i > 0 else close
        open_ = prev
        high  = max(open_, close) + spread * 0.4
        low   = min(open_, close) - spread * 0.4
        rows.append({
            "open":  round(open_, 2),
            "high":  round(high,  2),
            "low":   round(low,   2),
            "close": round(close, 2),
            "volume": volume,
        })
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(times))
    return df


def _times(n: int, start: datetime = MARKET_OPEN) -> list[datetime]:
    """Generate N timestamps 5 minutes apart starting from `start`."""
    return [start + timedelta(minutes=5 * i) for i in range(n)]


# ── Scenario builders ─────────────────────────────────────────────

def trending_up(n: int = 40, step: float = 8.0) -> pd.DataFrame:
    """Strong uptrend: each candle closes ~`step` points higher."""
    prices = [BASE_PRICE + step * i for i in range(n)]
    return _make_candles(_times(n), prices)


def trending_down(n: int = 40, step: float = 8.0) -> pd.DataFrame:
    """Strong downtrend: each candle closes ~`step` points lower."""
    prices = [BASE_PRICE - step * i for i in range(n)]
    return _make_candles(_times(n), prices)


def sideways(n: int = 40, amplitude: float = 15.0) -> pd.DataFrame:
    """Choppy sideways market oscillating around BASE_PRICE."""
    prices = [
        BASE_PRICE + amplitude * np.sin(i * 0.7)
        for i in range(n)
    ]
    return _make_candles(_times(n), prices)


def gap_up_then_trend(n: int = 40, gap: float = 100.0, step: float = 5.0) -> pd.DataFrame:
    """Big gap up at open, then steady uptrend."""
    prices = [BASE_PRICE + gap + step * i for i in range(n)]
    return _make_candles(_times(n), prices)


def gap_down_then_sell(n: int = 40, gap: float = 100.0, step: float = 5.0) -> pd.DataFrame:
    """Big gap down at open, then steady downtrend."""
    prices = [BASE_PRICE - gap - step * i for i in range(n)]
    return _make_candles(_times(n), prices)


def price_hits_target(entry: float, direction: str, sl_pts: float = 30, rr: float = 2.0,
                      n_before: int = 20) -> pd.DataFrame:
    """Return a DataFrame whose last candle's close is at the 1:2 target.

    Useful for asserting target-exit logic fires correctly.
    """
    target = entry + sl_pts * rr if direction == "long" else entry - sl_pts * rr

    # Lead-in candles trending toward target
    start_price = entry
    step = (target - start_price) / (n_before + 1)
    prices = [start_price + step * i for i in range(1, n_before + 2)]
    return _make_candles(_times(len(prices)), prices)


def price_hits_sl(entry: float, direction: str, sl_pts: float = 30,
                  n_before: int = 10) -> pd.DataFrame:
    """Return a DataFrame whose last close is AT the stop loss price."""
    sl = entry - sl_pts if direction == "long" else entry + sl_pts

    step = (sl - entry) / (n_before + 1)
    prices = [entry + step * i for i in range(1, n_before + 2)]
    return _make_candles(_times(len(prices)), prices)


def price_rallies_then_falls(entry: float, rally: float = 50.0,
                             fall_to: float | None = None,
                             n: int = 30) -> pd.DataFrame:
    """Price moves up by `rally` points then falls back (for trailing SL tests)."""
    peak      = entry + rally
    fall_end  = fall_to if fall_to is not None else entry - 10
    half      = n // 2
    prices_up = [entry + rally * (i / half) for i in range(half)]
    prices_dn = [peak - (peak - fall_end) * (i / half) for i in range(half)]
    prices    = prices_up + prices_dn
    return _make_candles(_times(len(prices)), prices)


def after_market_hours(n: int = 5) -> pd.DataFrame:
    """Candles timestamped AFTER 3:15 PM (for time-exit tests)."""
    start  = datetime(2026, 3, 16, 15, 20)
    prices = [BASE_PRICE] * n
    return _make_candles(_times(n, start), prices)


# ── Mock Zerodha instruments list ─────────────────────────────────

def mock_instruments(nifty_price: float = BASE_PRICE) -> list[dict]:
    """Return a minimal NFO instruments list with CE/PE options
    around the ATM strike so `_get_option_symbol` can find them.
    """
    from datetime import timedelta
    atm = round(nifty_price / 50) * 50

    # Tuesday nearest expiry (Nifty 50 weekly, changed from Thu → Tue Oct 2024)
    today   = datetime(2026, 3, 16)   # Monday
    days_to_tue = (1 - today.weekday()) % 7 or 7   # weekday 1 = Tuesday
    expiry  = (today + timedelta(days=days_to_tue)).strftime("%Y-%m-%d")

    instruments = []
    token = 100000
    for strike in range(atm - 150, atm + 200, 50):
        for opt in ["CE", "PE"]:
            instruments.append({
                "name":            "NIFTY",
                "instrument_type": opt,
                "strike":          float(strike),
                "expiry":          expiry,
                "tradingsymbol":   f"NIFTY{expiry.replace('-','')}{strike}{opt}",
                "instrument_token": token,
                "lot_size":        65,
            })
            token += 1
    return instruments


# ── Mock Kite order result ────────────────────────────────────────

def mock_order_id() -> str:
    return "ORDER-TEST-001"
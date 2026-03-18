"""MCX Crude Oil data fetching and instrument resolution.

Handles:
- Front-month futures token discovery (auto, cached)
- ATM option symbol resolution (CE/PE, nearest expiry)
- Intraday OHLCV data from Kite historical API
- Live spot price from kite.ltp()

All instrument lookups are cached for 4 hours — MCX expiry list
doesn't change intraday so repeated fetches are wasteful.
"""

import time
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Optional

import pandas as pd

from kite_integration import kite_manager

# ── Constants ─────────────────────────────────────────────────────
MCX_CRUDE_LOT_SIZE   = 100      # barrels per lot
MCX_CRUDE_STRIKE_STEP = 50      # option strikes at every ₹50
MCX_CRUDE_NAME       = "CRUDEOIL"
MCX_OPEN_TIME        = "09:00"  # MCX opens 9:00 AM (not 9:15 like NSE)
MCX_CLOSE_TIME       = "23:25" # exit before 23:30 close

# ── Instrument cache (TTL = 4 hours) ─────────────────────────────
_instruments_cache: list[dict] | None = None
_instruments_ts: float = 0.0
_INSTR_TTL = 4 * 3600.0  # 4 hours


def _get_mcx_instruments() -> list[dict]:
    """Return MCX instruments list, refreshed every 4 hours."""
    global _instruments_cache, _instruments_ts
    now = time.monotonic()
    if _instruments_cache and (now - _instruments_ts) < _INSTR_TTL:
        return _instruments_cache
    try:
        data = kite_manager.kite.instruments('MCX')
        _instruments_cache = data
        _instruments_ts    = now
        print(f"🛢️  MCX instruments loaded: {len(data)} symbols")
        return data
    except Exception as e:
        print(f"⚠️  MCX instruments fetch failed: {e}")
        return _instruments_cache or []


def get_crude_futures_token() -> tuple[int, str]:
    """Return (instrument_token, tradingsymbol) for front-month Crude futures.

    Picks the nearest expiry that hasn't expired yet — same logic as
    Nifty weekly expiry selection but for MCX monthlies.
    """
    instruments = _get_mcx_instruments()
    today = date.today()
    futs  = [
        i for i in instruments
        if i.get('name') == MCX_CRUDE_NAME
        and i.get('instrument_type') == 'FUT'
        and i.get('expiry') and i['expiry'] >= today
    ]
    if not futs:
        raise RuntimeError("No active CRUDEOIL futures found on MCX")
    futs.sort(key=lambda x: x['expiry'])

    # Auto-roll: skip contracts expiring within the next 2 days —
    # they have dying volume and extreme bid-ask spreads.
    # Use next month contract instead for cleaner fills.
    front = futs[0]
    days_to_expiry = (front['expiry'] - today).days
    if days_to_expiry <= 2 and len(futs) > 1:
        front = futs[1]
        print(f"🔄 Auto-rolled to {front['tradingsymbol']} "
              f"(near-month expires in {days_to_expiry}d)")

    return front['instrument_token'], front['tradingsymbol']


def get_crude_spot() -> float | None:
    """Return current MCX Crude Oil front-month futures price.

    Uses kite.ltp() — the lightest available call.
    Returns None if unauthenticated or API error.
    """
    if not kite_manager.is_authenticated:
        return None
    try:
        _, sym = get_crude_futures_token()
        data = kite_manager.kite.ltp([f"MCX:{sym}"])
        ltp  = data.get(f"MCX:{sym}", {}).get("last_price")
        return float(ltp) if ltp else None
    except Exception as e:
        print(f"⚠️  Crude spot fetch failed: {e}")
        return None


def get_crude_atm_option(
    spot: float,
    direction: str,
    strike_offset: int = 0,
) -> tuple[str, int]:
    """Return (full_symbol, instrument_token) for ATM Crude Oil option.

    direction: 'long'  → buy CE (betting crude goes up)
               'short' → buy PE (betting crude goes down)
    strike_offset: 0=ATM, 1=OTM1, -1=ITM1 etc. (in units of STRIKE_STEP)
    """
    option_type = 'CE' if direction == 'long' else 'PE'
    atm_strike  = round(spot / MCX_CRUDE_STRIKE_STEP) * MCX_CRUDE_STRIKE_STEP
    target_strike = atm_strike + strike_offset * MCX_CRUDE_STRIKE_STEP

    instruments = _get_mcx_instruments()
    today = date.today()

    opts = [
        i for i in instruments
        if i.get('name') == MCX_CRUDE_NAME
        and i.get('instrument_type') == option_type
        and i.get('strike') == float(target_strike)
        and i.get('expiry') and i['expiry'] >= today
    ]
    if not opts:
        raise RuntimeError(
            f"No MCX CRUDEOIL {option_type} at strike {target_strike} found"
        )
    opts.sort(key=lambda x: x['expiry'])
    best = opts[0]
    symbol = f"MCX:{best['tradingsymbol']}"
    print(f"🛢️  Option resolved: {symbol} (strike={target_strike}, type={option_type})")
    return symbol, best['instrument_token']


def get_crude_option_ltp(tradingsymbol: str) -> float | None:
    """Fetch LTP for a single MCX Crude Oil option."""
    if not kite_manager.is_authenticated:
        return None
    clean = tradingsymbol.replace("MCX:", "")
    try:
        data = kite_manager.kite.ltp([f"MCX:{clean}"])
        ltp  = data.get(f"MCX:{clean}", {}).get("last_price")
        return float(ltp) if ltp else None
    except Exception as e:
        print(f"⚠️  Crude option LTP failed ({tradingsymbol}): {e}")
        return None


def fetch_crude_intraday_data(
    interval: str = "5minute",
    days_back: int = 5,
) -> pd.DataFrame | None:
    """Fetch MCX Crude Oil intraday OHLCV from Kite historical API.

    Returns a DataFrame with columns: open, high, low, close, volume
    indexed by datetime.  Returns None on failure.
    """
    if not kite_manager.is_authenticated:
        return None
    try:
        token, sym = get_crude_futures_token()
        to_dt   = datetime.now()
        from_dt = to_dt - timedelta(days=days_back)
        raw = kite_manager.kite.historical_data(
            instrument_token=token,
            from_date=from_dt.strftime("%Y-%m-%d %H:%M:%S"),
            to_date=to_dt.strftime("%Y-%m-%d %H:%M:%S"),
            interval=interval,
        )
        if not raw:
            return None
        df = pd.DataFrame(raw)
        df.rename(columns={'date': 'datetime'}, inplace=True)
        df['datetime'] = pd.to_datetime(df['datetime'])
        df.set_index('datetime', inplace=True)
        df.sort_index(inplace=True)
        print(f"📊 Crude OHLCV fetched: {len(df)} candles ({sym})")
        return df
    except Exception as e:
        print(f"⚠️  Crude historical data failed: {e}")
        return None


def estimate_crude_premium(spot: float) -> float:
    """Rough ATM option premium estimate for Crude Oil.

    Crude ATM premium ≈ 0.5% of spot (slightly higher IV than Nifty).
    Used only when live LTP is unavailable.
    """
    return round(spot * 0.005, 1)
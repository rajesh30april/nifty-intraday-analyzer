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
MCX_CRUDE_LOT_SIZE      = 100   # barrels per lot (CRUDEOIL full)
MCX_CRUDE_MINI_LOT_SIZE = 10    # barrels per lot (CRUDEOILM mini)
MCX_CRUDE_STRIKE_STEP   = 50    # option strikes at every ₹50
MCX_CRUDE_NAME          = "CRUDEOIL"
MCX_CRUDE_MINI_NAME     = "CRUDEOILM"
MCX_OPEN_TIME           = "09:00"  # MCX opens 9:00 AM (not 9:15 like NSE)
MCX_CLOSE_TIME          = "23:25"  # exit before 23:30 close

# Capital threshold below which we auto-switch to CRUDEOILM mini contracts.
# Full CRUDEOIL ATM options cost ~₹1,00,000+.  Mini = 1/10th.
MCX_MINI_CAPITAL_THRESHOLD = 120_000  # use mini when capital < this (full ATM ~₹1,05,000)

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


# ── Module-level cache for the last resolved instrument ───────────
# Exposed so get_crude_status() can include it in every status response.
_last_futures_symbol:  str  = ""
_last_futures_token:   int  = 0
_last_days_to_expiry:  int  = -1


def get_crude_futures_token() -> tuple[int, str]:
    """Return (instrument_token, tradingsymbol) for front-month Crude futures.

    Picks the nearest expiry that hasn't expired yet — same logic as
    Nifty weekly expiry selection but for MCX monthlies.
    """
    global _last_futures_symbol, _last_futures_token, _last_days_to_expiry
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
    front = futs[0]
    days_to_expiry = (front['expiry'] - today).days
    if days_to_expiry <= 2 and len(futs) > 1:
        front = futs[1]
        days_to_expiry = (front['expiry'] - today).days
        print(f"🔄 Auto-rolled to {front['tradingsymbol']} "
              f"(near-month expires in {days_to_expiry}d)")

    _last_futures_symbol  = front['tradingsymbol']
    _last_futures_token   = front['instrument_token']
    _last_days_to_expiry  = days_to_expiry
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


def get_crude_lot_size(tradingsymbol: str) -> int:
    """Return the correct barrel multiplier for a MCX crude symbol.

    Zerodha's API always returns lot_size=1 for crude contracts, which
    is misleading. The real multipliers are:
      CRUDEOIL  (full) = 100 barrels per lot
      CRUDEOILM (mini) = 10  barrels per lot
    """
    return MCX_CRUDE_MINI_LOT_SIZE if "CRUDEOILM" in tradingsymbol else MCX_CRUDE_LOT_SIZE


# Minimum open-interest to consider an option liquid enough to trade.
# Crude mini options can be thin — we refuse to enter below this.
_MIN_OI_CRUDE = 50   # contracts; 0 = completely dead market


def _check_crude_option_liquidity(symbol: str, token: int) -> dict:
    """Return liquidity info for a crude option via kite.quote().

    Returns dict with keys: oi, bid_qty, ask_qty, ltp, liquid (bool).
    Falls back gracefully if quote fails.
    """
    result = {'oi': 0, 'bid_qty': 0, 'ask_qty': 0, 'ltp': 0.0, 'liquid': False}
    if not kite_manager.is_authenticated:
        result['liquid'] = True   # can't check — allow and warn
        return result
    try:
        quote = kite_manager.kite.quote([symbol])
        data  = quote.get(symbol, {})
        oi       = int(data.get('oi', 0) or 0)
        depth    = data.get('depth', {})
        bids     = depth.get('buy',  [])
        asks     = depth.get('sell', [])
        bid_qty  = sum(b.get('quantity', 0) for b in bids[:3])
        ask_qty  = sum(a.get('quantity', 0) for a in asks[:3])
        ltp      = float(data.get('last_price', 0) or 0)
        liquid   = oi >= _MIN_OI_CRUDE and (bid_qty > 0 or ask_qty > 0)
        result.update(oi=oi, bid_qty=bid_qty, ask_qty=ask_qty, ltp=ltp, liquid=liquid)
        print(f"  💧 Liquidity [{symbol}] OI={oi} bid_qty={bid_qty} ask_qty={ask_qty} "
              f"LTP=₹{ltp:.1f} → {'OK' if liquid else 'ILLIQUID ❌'}")
    except Exception as e:
        print(f"  ⚠️  Liquidity check failed for {symbol}: {e} — allowing with warning")
        result['liquid'] = True   # fail-open with warning
    return result


def get_crude_atm_option(
    spot: float,
    direction: str,
    strike_offset: int = 0,
    capital: float = 0.0,
) -> tuple[str, int, int]:
    """Return (full_symbol, instrument_token, lot_size_barrels).

    direction: 'long'  → buy CE  |  'short' → buy PE
    strike_offset: 0=ATM, positive=OTM (lower premium), negative=ITM
      For PE (short): offset +1 → one strike BELOW ATM (OTM put)
      For CE (long):  offset +1 → one strike ABOVE ATM (OTM call)
    capital: when < MCX_MINI_CAPITAL_THRESHOLD, auto-picks CRUDEOILM mini

    Performs a LIVE LIQUIDITY CHECK (OI + bid depth) before returning.
    Falls back to ATM if the target strike is illiquid.
    Raises RuntimeError if no liquid option can be found.
    """
    option_type = 'CE' if direction == 'long' else 'PE'
    atm_strike  = round(spot / MCX_CRUDE_STRIKE_STEP) * MCX_CRUDE_STRIKE_STEP

    # Strike offset convention:
    #   CE long:  positive offset → higher strike (OTM) ✔
    #   PE short: positive offset → LOWER strike (OTM for put) ✔
    # Without this correction, offset +2 on PE picks a deep-ITM put — illiquid!
    direction_sign = 1 if option_type == 'CE' else -1
    target_strike  = atm_strike + direction_sign * strike_offset * MCX_CRUDE_STRIKE_STEP

    use_mini = capital > 0 and capital < MCX_MINI_CAPITAL_THRESHOLD
    instruments = _get_mcx_instruments()
    today = date.today()

    # Build candidate strikes to try: target first, then fall back toward ATM
    strike_candidates = [target_strike]
    for step in range(1, 5):          # try up to 4 nearby strikes
        strike_candidates.append(atm_strike + direction_sign * max(0, strike_offset - step) * MCX_CRUDE_STRIKE_STEP)
        strike_candidates.append(atm_strike)   # ATM always in the fallback list
    # Deduplicate while preserving order
    seen, unique_strikes = set(), []
    for s in strike_candidates:
        if s not in seen:
            seen.add(s)
            unique_strikes.append(s)

    for candidate_strike in unique_strikes:
        is_fallback = candidate_strike != target_strike
        for name in ([MCX_CRUDE_MINI_NAME, MCX_CRUDE_NAME] if use_mini
                     else [MCX_CRUDE_NAME, MCX_CRUDE_MINI_NAME]):
            opts = [
                i for i in instruments
                if i.get('name') == name
                and i.get('instrument_type') == option_type
                and i.get('strike') == float(candidate_strike)
                and i.get('expiry') and i['expiry'] >= today
            ]
            if not opts:
                continue
            opts.sort(key=lambda x: x['expiry'])
            best   = opts[0]
            symbol = f"MCX:{best['tradingsymbol']}"
            lot_sz = get_crude_lot_size(best['tradingsymbol'])
            tag    = 'MINI' if use_mini else 'FULL'

            if is_fallback:
                print(f"  ⚠️  Strike {int(target_strike)} illiquid — trying fallback {int(candidate_strike)}")

            # ✅ LIQUIDITY GATE: refuse illiquid strikes
            liq = _check_crude_option_liquidity(symbol, best['instrument_token'])
            if not liq['liquid']:
                print(f"  ❌ Skipping {symbol} — OI={liq['oi']} bid={liq['bid_qty']} (illiquid)")
                continue

            print(f"  ✅ [{tag}] Selected {symbol} "
                  f"(strike={int(candidate_strike)}, lot={lot_sz} bbl, OI={liq['oi']})")
            return symbol, best['instrument_token'], lot_sz

    raise RuntimeError(
        f"No liquid MCX CRUDEOIL {option_type} option found near strike {int(target_strike)}. "
        f"Check market hours and option chain liquidity."
    )


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
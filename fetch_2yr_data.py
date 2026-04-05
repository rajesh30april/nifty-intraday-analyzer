"""fetch_2yr_data.py — Pull 2 years of 5-min Nifty 50 data from Zerodha.

Strategy:
  OHLC   → Nifty SPOT index (token 256265) — full 2yr history, volume=0
  Volume → Active front-month futures (real vol, last ~65-100 days only)
           Zerodha does NOT expose expired contract tokens via instruments API,
           so volume is 0 for candles older than ~100 days. That's the hard limit.

Usage:
    .venv/bin/python3 fetch_2yr_data.py

Output:
    data/nifty_5min_2yr.csv   — full dataset (OHLCV)
    data/nifty_5min_2yr.json  — summary stats
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from kite_integration import kite_manager

# ── Config ────────────────────────────────────────────────────────
YEARS_BACK     = 2
CHUNK_DAYS     = 90       # Stay under 100-day Zerodha limit (safety margin)
INTERVAL       = "5minute"
NIFTY_SPOT_TOK = 256265   # NSE Nifty 50 spot — full OHLC, volume always 0
SLEEP_SEC      = 0.4      # Rate-limit safety between API calls
OUT_DIR        = Path(__file__).parent / "data"

# Active Nifty futures contracts — only source of real volume
# Expired contract tokens are NOT available via Zerodha instruments API
ACTIVE_FUTURES = [
    (17072898, "NIFTY26APRFUT"),
    (16914178, "NIFTY26MAYFUT"),
    (15956226, "NIFTY26JUNFUT"),
]


# ── Helpers ───────────────────────────────────────────────────────

def _date_chunks(total_days: int, chunk: int):
    """Yield (from_dt, to_dt) pairs walking backwards in time."""
    to_dt = datetime.now().replace(hour=23, minute=59, second=59)
    while total_days > 0:
        from_dt     = to_dt - timedelta(days=min(chunk, total_days))
        yield from_dt, to_dt
        to_dt       = from_dt - timedelta(seconds=1)
        total_days -= chunk


def _fetch_ohlc_chunk(kite, from_dt: datetime, to_dt: datetime) -> list[dict]:
    """One Zerodha API call: Nifty Spot OHLC for a date range."""
    return kite.historical_data(
        instrument_token=NIFTY_SPOT_TOK,
        from_date=from_dt,
        to_date=to_dt,
        interval=INTERVAL,
    )


def _fetch_futures_volume(kite) -> pd.Series:
    """Fetch real volume from all active front-month futures.

    Each contract is queried for 100 days back. Timestamps are aligned
    to 5-min boundaries. Where multiple contracts overlap, we take max vol
    (front-month typically dominates).

    Returns: pd.Series indexed by tz-naive datetime, values = volume int.
    """
    to_dt   = datetime.now()
    from_dt = to_dt - timedelta(days=100)
    vol_map: dict[pd.Timestamp, int] = {}

    print("\n📦 Fetching real volume from active futures contracts...")
    for token, symbol in ACTIVE_FUTURES:
        try:
            rows = kite.historical_data(token, from_dt, to_dt, INTERVAL)
            for r in rows:
                ts  = pd.Timestamp(r["date"]).tz_localize(None).floor("5min")
                vol = int(r.get("volume") or 0)
                vol_map[ts] = max(vol_map.get(ts, 0), vol)
            print(f"  ✅ {symbol}: {len(rows):,} candles")
        except Exception as e:
            print(f"  ❌ {symbol}: {e}")
        time.sleep(SLEEP_SEC)

    if not vol_map:
        print("  ⚠️  No volume data retrieved — all candles will have volume=0")
        return pd.Series(dtype=int)

    s = pd.Series(vol_map, name="volume").sort_index()
    real_days = s[s > 0].index.normalize().nunique()
    print(f"  📊 {len(s):,} candles with real volume | {real_days} trading days")
    return s


def _market_hours_only(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only 9:15 AM – 3:30 PM IST candles."""
    h, m = df["date"].dt.hour, df["date"].dt.minute
    after_open  = (h > 9) | ((h == 9) & (m >= 15))
    before_close = (h < 15) | ((h == 15) & (m <= 30))
    return df[after_open & before_close].copy()


# ── Main ──────────────────────────────────────────────────────────

def main():
    if not kite_manager.is_authenticated:
        print("❌ Not authenticated! Open http://localhost:8000 and connect Zerodha first.")
        return

    kite       = kite_manager.kite
    total_days = YEARS_BACK * 365
    chunks     = list(_date_chunks(total_days, CHUNK_DAYS))

    print(f"📈 Fetching {YEARS_BACK}-year Nifty 5-min dataset from Zerodha")
    print(f"📦 {len(chunks)} OHLC chunks × ~{CHUNK_DAYS} days (Nifty Spot)")
    print(f"⚠️  Volume: real for last ~100 days | zero for older candles\n")

    OUT_DIR.mkdir(exist_ok=True)

    # ── Step 1: Fetch OHLC in chunks ─────────────────────────────
    all_rows: list[dict] = []
    failed = 0

    for i, (from_dt, to_dt) in enumerate(chunks, 1):
        label = f"[{i:02d}/{len(chunks)}] {from_dt.strftime('%Y-%m-%d')} → {to_dt.strftime('%Y-%m-%d')}"
        try:
            rows = _fetch_ohlc_chunk(kite, from_dt, to_dt)
            all_rows.extend(rows)
            print(f"  ✅ {label}  ({len(rows):,} candles)")
        except Exception as e:
            failed += 1
            print(f"  ❌ {label}  ERROR: {e}")
        if i < len(chunks):
            time.sleep(SLEEP_SEC)

    if not all_rows:
        print("\n💀 No OHLC data fetched. Check Zerodha connection.")
        return

    # ── Step 2: Build OHLC DataFrame ─────────────────────────────
    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = (df.sort_values("date")
            .drop_duplicates("date")
            .reset_index(drop=True))
    df = _market_hours_only(df)

    # ── Step 3: Merge real futures volume ─────────────────────────
    vol = _fetch_futures_volume(kite)
    if not vol.empty:
        key = df["date"].dt.floor("5min")
        df["volume"] = key.map(vol).fillna(0).astype(int)
    else:
        df["volume"] = 0

    candles_with_vol = int((df["volume"] > 0).sum())
    print(f"\n  ✅ {candles_with_vol:,} / {len(df):,} candles have real volume")

    # ── Step 4: Save ──────────────────────────────────────────────
    csv_path  = OUT_DIR / "nifty_5min_2yr.csv"
    json_path = OUT_DIR / "nifty_5min_2yr.json"

    df.to_csv(csv_path, index=False)

    trading_days = df["date"].dt.date.nunique()
    summary = {
        "generated_at"     : datetime.now().isoformat(),
        "interval"          : INTERVAL,
        "from"              : str(df["date"].min()),
        "to"                : str(df["date"].max()),
        "total_candles"     : len(df),
        "candles_with_vol"  : candles_with_vol,
        "trading_days"      : trading_days,
        "ohlc_chunks_ok"    : len(chunks) - failed,
        "ohlc_chunks_fail"  : failed,
        "price_range"       : {
            "low"  : float(df["low"].min()),
            "high" : float(df["high"].max()),
        },
    }
    json_path.write_text(json.dumps(summary, indent=2))

    # ── Step 5: Print summary ─────────────────────────────────────
    vol_pct = candles_with_vol / len(df) * 100
    print(f"""
╔══════════════════════════════════════════════════════╗
║          ✅  DATA FETCH COMPLETE                     ║
╠══════════════════════════════════════════════════════╣
║  Period         : {summary['from'][:10]} → {summary['to'][:10]}     ║
║  Total candles  : {len(df):>10,}                         ║
║  Trading days   : {trading_days:>10,}                         ║
║  Nifty range    : {df['low'].min():>8,.0f} – {df['high'].max():,.0f}             ║
╠══════════════════════════════════════════════════════╣
║  Volume (real)  : {candles_with_vol:>10,} candles ({vol_pct:.1f}%)       ║
║  Volume (zero)  : {len(df)-candles_with_vol:>10,} candles (older data)    ║
╠══════════════════════════════════════════════════════╣
║  📄 {str(csv_path):<48} ║
║  📊 {str(json_path):<48} ║
╚══════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()

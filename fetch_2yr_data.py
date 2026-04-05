"""fetch_2yr_data.py — Pull 2 years of 5-min Nifty 50 data from Zerodha.

Zerodha caps 5-minute data at 100 days per API call.
This script chunks the 2-year window into 100-day slices,
fetches each, stitches them, deduplicates, and saves to CSV + JSON.

Usage:
    .venv/bin/python3 fetch_2yr_data.py

Output:
    data/nifty_5min_2yr.csv
    data/nifty_5min_2yr.json   (summary stats)
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from kite_integration import kite_manager

# ── Config ────────────────────────────────────────────────────────
YEARS_BACK      = 2
CHUNK_DAYS      = 90          # Stay under 100-day limit (safety margin)
INTERVAL        = "5minute"
NIFTY_SPOT_TOK  = 256265      # NSE Nifty 50 index — full OHLC history, volume=0
SLEEP_BETWEEN   = 0.4         # seconds between API calls (rate-limit safety)
OUT_DIR         = Path(__file__).parent / "data"

# ── Helpers ───────────────────────────────────────────────────────

def _chunks(total_days: int, chunk: int):
    """Yield (from_dt, to_dt) pairs covering total_days in reverse chunks."""
    to_dt = datetime.now().replace(hour=23, minute=59, second=59)
    while total_days > 0:
        from_dt = to_dt - timedelta(days=min(chunk, total_days))
        yield from_dt, to_dt
        to_dt      = from_dt - timedelta(seconds=1)
        total_days -= chunk


def fetch_chunk(kite, from_dt: datetime, to_dt: datetime) -> list[dict]:
    """Single Zerodha API call for one chunk."""
    return kite.historical_data(
        instrument_token=NIFTY_SPOT_TOK,
        from_date=from_dt,
        to_date=to_dt,
        interval=INTERVAL,
    )


# ── Main ─────────────────────────────────────────────────────────

def main():
    # ── Auth check ───────────────────────────────────────────────
    if not kite_manager.is_authenticated:
        print("❌ Not authenticated! Open http://localhost:8000 and connect Zerodha first.")
        return

    kite      = kite_manager.kite
    total_days = YEARS_BACK * 365
    chunks     = list(_chunks(total_days, CHUNK_DAYS))
    total      = len(chunks)

    print(f"🛢  Fetching {YEARS_BACK}yr of Nifty 5-min data")
    print(f"📦  {total} chunks × ~{CHUNK_DAYS} days each")
    print(f"⚠️  Volume will be 0 (Nifty Spot index — OHLC is accurate)\n")

    OUT_DIR.mkdir(exist_ok=True)
    all_rows: list[dict] = []
    failed = 0

    for i, (from_dt, to_dt) in enumerate(chunks, 1):
        label = f"[{i:02d}/{total}] {from_dt.strftime('%Y-%m-%d')} → {to_dt.strftime('%Y-%m-%d')}"
        try:
            rows = fetch_chunk(kite, from_dt, to_dt)
            all_rows.extend(rows)
            print(f"  ✅ {label}  ({len(rows)} candles)")
        except Exception as e:
            failed += 1
            print(f"  ❌ {label}  ERROR: {e}")

        if i < total:
            time.sleep(SLEEP_BETWEEN)

    if not all_rows:
        print("\n💀 No data fetched. Check your Zerodha connection.")
        return

    # ── Build DataFrame ──────────────────────────────────────────
    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)

    # Only keep market hours (9:15 AM – 3:30 PM IST)
    df = df[
        (df["date"].dt.hour > 9) |
        ((df["date"].dt.hour == 9) & (df["date"].dt.minute >= 15))
    ]
    df = df[
        (df["date"].dt.hour < 15) |
        ((df["date"].dt.hour == 15) & (df["date"].dt.minute <= 30))
    ]

    # ── Save CSV ─────────────────────────────────────────────────
    csv_path = OUT_DIR / "nifty_5min_2yr.csv"
    df.to_csv(csv_path, index=False)

    # ── Summary stats ────────────────────────────────────────────
    trading_days = df["date"].dt.date.nunique()
    summary = {
        "generated_at"  : datetime.now().isoformat(),
        "interval"       : INTERVAL,
        "from"           : str(df["date"].min()),
        "to"             : str(df["date"].max()),
        "total_candles"  : len(df),
        "trading_days"   : trading_days,
        "chunks_fetched" : total - failed,
        "chunks_failed"  : failed,
        "price_range"    : {
            "low"  : float(df["low"].min()),
            "high" : float(df["high"].max()),
        },
    }

    json_path = OUT_DIR / "nifty_5min_2yr.json"
    json_path.write_text(json.dumps(summary, indent=2))

    # ── Print summary ────────────────────────────────────────────
    print(f"""
╔══════════════════════════════════════════════════╗
║        ✅  DATA FETCH COMPLETE                   ║
╠══════════════════════════════════════════════════╣
║  Period        : {summary['from'][:10]} → {summary['to'][:10]}   ║
║  Total candles : {summary['total_candles']:,}                       ║
║  Trading days  : {trading_days}                            ║
║  Nifty range   : {summary['price_range']['low']:,.0f} – {summary['price_range']['high']:,.0f}            ║
║  Chunks OK/Fail: {total - failed}/{failed}                            ║
╠══════════════════════════════════════════════════╣
║  📄 CSV  : {str(csv_path):<38} ║
║  📊 JSON : {str(json_path):<38} ║
╚══════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    main()

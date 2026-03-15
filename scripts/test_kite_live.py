#!/usr/bin/env python3
"""
Kite Connect Live API Test Script
==================================
Run this BEFORE going live to verify every API call the auto-trader makes.

Levels:
  Level 1 — Read-only : margins, instruments, quote/LTP     (safe anytime)
  Level 2 — Dry run   : compute & print exact order params  (safe anytime)
  Level 3 — Order test: place deep-OTM LIMIT + cancel it   (real API hit, no fill risk)

Usage:
  cd ~/nifty-intraday-analyzer
  source .venv/bin/activate
  python3 scripts/test_kite_live.py            # Level 1 + 2 only
  python3 scripts/test_kite_live.py --order    # Level 1 + 2 + 3
"""

import sys, os, time, argparse
from datetime import datetime, timedelta, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kite_integration import kite_manager

# ── Console helpers ───────────────────────────────────────────────────────
PASS  = "\033[92m✅\033[0m"
FAIL  = "\033[91m❌\033[0m"
WARN  = "\033[93m⚠️ \033[0m"
INFO  = "\033[94mℹ️ \033[0m"
BOLD  = "\033[1m"
RESET = "\033[0m"

def section(title):
    print(f"\n{BOLD}{'─'*60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'─'*60}{RESET}")

def ok(msg):   print(f"  {PASS} {msg}")
def err(msg):  print(f"  {FAIL} {msg}")
def warn(msg): print(f"  {WARN} {msg}")
def info(msg): print(f"  {INFO} {msg}")

# ══════════════════════════════════════════════════════════════════════════
# LEVEL 1 — Read-only checks
# ══════════════════════════════════════════════════════════════════════════

def check_auth():
    section("Level 1a — Kite Authentication")
    if not kite_manager.is_authenticated:
        err("Not authenticated. Log in at http://localhost:8000/login first.")
        return False
    ok("Kite session is active")
    try:
        profile = kite_manager.kite.profile()
        ok(f"Logged in as : {profile['user_name']} ({profile['user_id']})")
        ok(f"Broker       : {profile['broker']}")
        ok(f"Email        : {profile['email']}")
    except Exception as e:
        warn(f"Profile fetch failed: {e}")
    return True


def check_margins():
    section("Level 1b — Margins API  (equity = NSE + F&O combined)")
    margins = kite_manager.kite.margins(segment="equity")
    print(f"  Raw response keys : {list(margins.keys())}")

    net      = float(margins.get("net", 0))
    avail    = margins.get("available", {})
    utilised = margins.get("utilised", {})

    ok(f"net (total usable)   : Rs {net:>12,.2f}")
    ok(f"live_balance         : Rs {float(avail.get('live_balance', 0)):>12,.2f}")
    ok(f"opening_balance      : Rs {float(avail.get('opening_balance', 0)):>12,.2f}")
    ok(f"utilised.debits      : Rs {float(utilised.get('debits', 0)):>12,.2f}")
    ok(f"utilised.exposure    : Rs {float(utilised.get('exposure', 0)):>12,.2f}")

    if net < 10_000:
        err(f"Net margin Rs {net:,.0f} is below Rs 10,000 — top up before trading!")
    else:
        ok(f"Margin is sufficient for trading")
    return net


def check_instruments():
    section("Level 1c — NFO Instruments (Nifty options only)")
    all_nfo = kite_manager.kite.instruments("NFO")
    nifty   = [i for i in all_nfo if i["name"] == "NIFTY"]
    ok(f"Total NFO instruments : {len(all_nfo):,}")
    ok(f"NIFTY options only    : {len(nifty):,}")

    today = date.today()
    days  = (1 - today.weekday()) % 7   # days to Tuesday
    if days == 0 and datetime.now().hour >= 15:
        days = 7
    expiry_date = today + timedelta(days=max(days, 1))
    expiry_str  = expiry_date.strftime("%Y-%m-%d")
    info(f"Nearest Tuesday expiry : {expiry_date.strftime('%A %d %b %Y')}  ({expiry_str})")

    week_opts = [i for i in nifty if str(i["expiry"]) == expiry_str]
    if week_opts:
        ok(f"Options for this expiry : {len(week_opts)} strikes")
        strikes = sorted(set(i["strike"] for i in week_opts))
        info(f"Strike range : {min(strikes):,.0f} to {max(strikes):,.0f}  ({len(strikes)} strikes, step 50)")
    else:
        err(f"No options found for expiry {expiry_str} — check expiry day!")
        expiries = sorted(set(str(i["expiry"]) for i in nifty))[:6]
        warn(f"Available expiries (first 6): {expiries}")

    return nifty, expiry_str


def check_live_quote():
    section("Level 1d — Live Nifty 50 Spot Quote")
    quote = kite_manager.get_live_quote()
    if not quote:
        err("Could not fetch Nifty live quote (market closed or Kite issue)")
        return 0.0
    price = float(quote.get("last_price", 0))
    ohlc  = quote.get("ohlc", {})
    ok(f"Last price : Rs {price:>10,.2f}")
    ok(f"Open       : Rs {float(ohlc.get('open',  0)):>10,.2f}")
    ok(f"High       : Rs {float(ohlc.get('high',  0)):>10,.2f}")
    ok(f"Low        : Rs {float(ohlc.get('low',   0)):>10,.2f}")
    ok(f"Change     :    {float(quote.get('change', 0)):>+9.2f}%")
    return price


def check_option_ltp(nifty_price, instruments, expiry_str):
    section("Level 1e — Option LTP Quote  (1-OTM CE)")
    atm    = round(nifty_price / 50) * 50
    strike = atm + 50
    info(f"Nifty: {nifty_price:.0f}  |  ATM: {atm}  |  1-OTM CE strike: {strike}")

    matches = [
        i for i in instruments
        if i["strike"] == float(strike)
        and i["instrument_type"] == "CE"
        and str(i["expiry"]) == expiry_str
    ]
    if not matches:
        err(f"No CE found at strike {strike} expiry {expiry_str}")
        return "", 0.0

    symbol = matches[0]["tradingsymbol"]
    token  = matches[0]["instrument_token"]
    info(f"Symbol : {symbol}")
    info(f"Token  : {token}")

    ltp = kite_manager.get_option_ltp(symbol)
    if ltp:
        ok(f"Live LTP : Rs {ltp:,.2f}")
    else:
        warn("LTP returned None — market may be closed; dry-run will use Rs 0")
    return symbol, ltp or 0.0


def check_positions():
    section("Level 1f — Open Positions in Zerodha")
    positions = kite_manager.kite.positions()
    net_pos   = [p for p in positions.get("net", []) if p.get("quantity", 0) != 0]
    day_pos   = positions.get("day", [])
    if not net_pos:
        ok("No open positions — clean slate")
    else:
        warn(f"{len(net_pos)} open position(s) detected:")
        for p in net_pos:
            print(f"     {p['tradingsymbol']:30s}  qty={p['quantity']:+d}  "
                  f"avg=Rs {p.get('average_price',0):,.2f}  "
                  f"pnl=Rs {p.get('pnl',0):+,.2f}")
    info(f"Day trades today : {len(day_pos)}")


# ══════════════════════════════════════════════════════════════════════════
# LEVEL 2 — Dry run: show exact order params without sending
# ══════════════════════════════════════════════════════════════════════════

def dry_run_orders(symbol, ltp, nifty_price):
    section("Level 2 — Dry Run: Exact Order Parameters (nothing sent to Zerodha)")

    LOT_SIZE  = 65
    CAPITAL   = 96_000.0
    SL_POINTS = 30.0
    DELTA     = 0.5

    if ltp > 0:
        lots = max(1, int(CAPITAL / (ltp * LOT_SIZE)))
        qty  = lots * LOT_SIZE
    else:
        lots, qty = 1, LOT_SIZE

    sl_trigger = max(round((ltp or 150.0) - SL_POINTS * DELTA, 1), 1.0)

    kite = kite_manager.kite

    entry_params = dict(
        variety          = kite.VARIETY_REGULAR,
        exchange         = "NFO",
        tradingsymbol    = symbol,
        transaction_type = kite.TRANSACTION_TYPE_BUY,
        quantity         = qty,
        product          = kite.PRODUCT_MIS,
        order_type       = kite.ORDER_TYPE_MARKET,
        validity         = "DAY",
    )
    slm_params = dict(
        variety          = kite.VARIETY_REGULAR,
        exchange         = "NFO",
        tradingsymbol    = symbol,
        transaction_type = kite.TRANSACTION_TYPE_SELL,
        quantity         = qty,
        product          = kite.PRODUCT_MIS,
        order_type       = kite.ORDER_TYPE_SLM,
        trigger_price    = sl_trigger,
        validity         = "DAY",
    )
    exit_params = dict(
        variety          = kite.VARIETY_REGULAR,
        exchange         = "NFO",
        tradingsymbol    = symbol,
        transaction_type = kite.TRANSACTION_TYPE_SELL,
        quantity         = qty,
        product          = kite.PRODUCT_MIS,
        order_type       = kite.ORDER_TYPE_MARKET,
        validity         = "DAY",
    )

    def show(label, params):
        print(f"\n  {BOLD}[{label}]{RESET}")
        for k, v in params.items():
            print(f"    {k:<22} = {v!r}")

    show("ENTRY  place_order()", entry_params)
    show("SL-M   place_order()", slm_params)
    show("EXIT   place_order()", exit_params)

    print()
    ok(f"Capital Rs {CAPITAL:,.0f} / (LTP Rs {ltp:.1f} x {LOT_SIZE}) = {lots} lots = {qty} units")
    ok(f"SL-M trigger = LTP Rs {ltp:.1f} - (SL {SL_POINTS}pts x delta {DELTA}) = Rs {sl_trigger}")

    # Verify all string constants match SDK
    checks = [
        ("VARIETY_REGULAR",  kite.VARIETY_REGULAR,  "regular"),
        ("PRODUCT_MIS",      kite.PRODUCT_MIS,       "MIS"),
        ("ORDER_TYPE_MARKET",kite.ORDER_TYPE_MARKET, "MARKET"),
        ("ORDER_TYPE_SLM",   kite.ORDER_TYPE_SLM,    "SL-M"),
        ("TRANSACTION_TYPE_BUY",  kite.TRANSACTION_TYPE_BUY,  "BUY"),
        ("TRANSACTION_TYPE_SELL", kite.TRANSACTION_TYPE_SELL, "SELL"),
    ]
    print()
    info("Kite SDK constant verification:")
    all_ok = True
    for name, actual, expected in checks:
        if actual == expected:
            ok(f"  {name:28s} = {actual!r}")
        else:
            err(f"  {name:28s} = {actual!r}  (expected {expected!r})")
            all_ok = False
    if all_ok:
        ok("All SDK constants match expected values")

    return entry_params, slm_params, exit_params


# ══════════════════════════════════════════════════════════════════════════
# LEVEL 3 — Real API round-trip: LIMIT @ Rs 0.05 + cancel
# ══════════════════════════════════════════════════════════════════════════

def order_cancel_test(symbol):
    section("Level 3 — Real Order Test (LIMIT BUY @ Rs 0.05 → immediately CANCEL)")
    warn("This places a REAL order on Zerodha. It will NOT fill (price is Rs 0.05).")
    warn(f"Symbol: {symbol}  |  Qty: 65 (1 lot)  |  Price: Rs 0.05")
    print()

    kite = kite_manager.kite

    # Step 1: Place
    print(f"  Placing LIMIT BUY @ Rs 0.05 ...", end=" ", flush=True)
    try:
        order_id = kite.place_order(
            variety          = kite.VARIETY_REGULAR,
            exchange         = "NFO",
            tradingsymbol    = symbol,
            transaction_type = kite.TRANSACTION_TYPE_BUY,
            quantity         = 65,
            product          = kite.PRODUCT_MIS,
            order_type       = kite.ORDER_TYPE_LIMIT,
            price            = 0.05,
            validity         = "DAY",
        )
        ok(f"Order accepted!  order_id = {order_id}")
    except Exception as e:
        err(f"place_order() failed: {e}")
        return

    # Step 2: Cancel
    time.sleep(1)
    print(f"  Cancelling order {order_id} ...", end=" ", flush=True)
    try:
        kite.cancel_order(
            variety  = kite.VARIETY_REGULAR,
            order_id = str(order_id),
        )
        ok("Cancelled")
    except Exception as e:
        err(f"cancel_order() failed: {e}  — cancel manually in Kite app!")
        return

    # Step 3: Confirm status
    time.sleep(1)
    try:
        orders    = kite.orders()
        our_order = next((o for o in orders if str(o["order_id"]) == str(order_id)), None)
        if our_order:
            status = our_order.get("status", "?")
            if status == "CANCELLED":
                ok(f"Final status: CANCELLED  — full round-trip confirmed!")
            else:
                warn(f"Final status: {status}  (expected CANCELLED)")
        else:
            warn("Order not found in today's orders — check Kite app manually")
    except Exception as e:
        warn(f"Could not verify order status: {e}")


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Kite API pre-live test")
    parser.add_argument(
        "--order", action="store_true",
        help="Level 3: place + cancel a real 1-lot LIMIT order to verify API end-to-end"
    )
    args = parser.parse_args()

    print()
    print(f"{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  Kite API Pre-Live Test Script{RESET}")
    print(f"{BOLD}  {datetime.now().strftime('%A  %d %b %Y   %H:%M:%S')}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")

    if not check_auth():
        print(f"\n{FAIL} Cannot proceed without Kite auth. Exiting.")
        sys.exit(1)

    try:
        net_margin = check_margins()
    except Exception as e:
        err(f"Margins check exception: {e}")
        sys.exit(1)

    try:
        instruments, expiry_str = check_instruments()
    except Exception as e:
        err(f"Instruments check exception: {e}")
        sys.exit(1)

    nifty_price = check_live_quote()
    if nifty_price <= 0:
        warn("Live price is 0 (market closed?) — continuing with dry-run using dummy values")
        nifty_price = 23500.0

    symbol, ltp = check_option_ltp(nifty_price, instruments, expiry_str)
    if not symbol:
        err("No option symbol found — cannot continue.")
        sys.exit(1)

    check_positions()
    dry_run_orders(symbol, ltp, nifty_price)

    if args.order:
        answer = input(
            f"\n  Place a REAL 1-lot LIMIT order @ Rs 0.05 on {symbol}? [yes/no]: "
        ).strip().lower()
        if answer == "yes":
            order_cancel_test(symbol)
        else:
            info("Level 3 skipped.")

    section("Final Summary")
    print(f"  Symbol     : {symbol}")
    print(f"  Option LTP : Rs {ltp:,.2f}" if ltp else "  Option LTP : (unavailable)")
    print(f"  Nifty spot : Rs {nifty_price:,.2f}")
    print(f"  Margin net : Rs {net_margin:,.0f}")
    print(f"  Expiry     : {expiry_str}")
    print()
    if ltp > 0 and net_margin > 10_000:
        ok("All systems green. Ready for live trading tomorrow!")
    else:
        warn("Some checks need attention — review above before going live.")
    print()


if __name__ == "__main__":
    main()

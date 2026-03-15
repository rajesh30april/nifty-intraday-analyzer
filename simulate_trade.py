#!/usr/bin/env python3
"""
Auto-Trader Dry-Run Simulator
==============================
Mirrors auto_trader.py exactly — uses the SAME strategy selection logic.
Default: smart_router (evaluates ALL strategies, picks the best one).

Usage:
  python3 simulate_trade.py                  # uses smart_router (default)
  python3 simulate_trade.py orb              # force a specific strategy
  python3 simulate_trade.py ema_crossover
"""

import os, sys, time
os.environ["LIVE_TRADING"]       = "false"
os.environ["SL_POINTS"]          = "30"
os.environ["TRAILING_SL_POINTS"] = "15"

import pandas as pd
import numpy as np

# ── Colours ───────────────────────────────────────────────────────
G  = "\033[92m"; R  = "\033[91m"; Y  = "\033[93m"
B  = "\033[94m"; W  = "\033[97m"; DIM= "\033[2m"; RST= "\033[0m"

def hdr(t):   print(f"\n{B}{'─'*60}{RST}\n{W} {t}{RST}\n{B}{'─'*60}{RST}")
def trade(t): print(f"  {W}{t}{RST}")
def warn(t):  print(f"  {Y}⚠️  {t}{RST}")

# ── Build OHLCV dataframe ─────────────────────────────────────────
def make_candles():
    """
    25 candles of yesterday history (so rolling(20) has data from start)
    + today: clear ORB breakdown at 10:00 with volume surge
    """
    rng = np.random.default_rng(42)

    hist_times = pd.date_range("2025-03-11 09:15", periods=25, freq="5min")
    hist_rows, p = [], 23500.0
    for _ in hist_times:
        c = p + float(rng.integers(-8, 9))
        hist_rows.append({
            "open": p,
            "high": max(p, c) + float(rng.integers(3, 15)),
            "low":  min(p, c) - float(rng.integers(3, 15)),
            "close": c,
            "volume": int(rng.integers(90_000, 160_000)),
        })
        p = c

    today_times = pd.date_range("2025-03-12 09:15", periods=22, freq="5min")
    today_closes = [
        23450, 23480, 23460,   # ORB window → range ≈ 23436..23494
        23445, 23438, 23442,   # consolidation
        23430, 23435, 23428,   # near ORB low
        23390,                 # ★ 10:00 BREAKDOWN — big volume
        23370, 23355, 23340,
        23360, 23345, 23340,
        23310, 23295, 23290,
        23300, 23310, 23295,
    ]
    avg_vol = 125_000
    today_rows, prev = [], 23460.0
    for i, c in enumerate(today_closes):
        vol = int(avg_vol * 2.1) if i == 9 else int(rng.integers(80_000, 140_000))
        today_rows.append({
            "open": prev,
            "high": max(prev, c) + int(rng.integers(3, 18)),
            "low":  min(prev, c) - int(rng.integers(3, 18)),
            "close": c,
            "volume": vol,
        })
        prev = c

    return pd.concat([
        pd.DataFrame(hist_rows,  index=hist_times),
        pd.DataFrame(today_rows, index=today_times),
    ])

def fake_ticks(frm, to, n=10):
    return [round(frm + (to - frm) * i / (n - 1), 2) for i in range(n)]

# ── Main ──────────────────────────────────────────────────────────
def run():
    # ── Strategy selection — same as auto_trader.py ───────────────
    from strategies.registry import get as get_strategy
    from strategies import loader  # noqa: registers all strategies

    strategy_id = sys.argv[1] if len(sys.argv) > 1 else "smart_router"
    strat_info  = get_strategy(strategy_id)
    if not strat_info:
        print(f"{R}Unknown strategy: {strategy_id}{RST}")
        print("Available:", ", ".join(["smart_router", "orb", "ema_crossover",
              "vwap_reversion", "rsi_reversal", "supertrend", "macd_momentum"]))
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  🤖  AUTO-TRADER DRY-RUN SIMULATOR  |  📝 PAPER MODE")
    print(f"  📅  2025-03-12")
    print(f"  🧠  Strategy: {strat_info.emoji} {strat_info.name}  (id={strategy_id})")
    if strategy_id == "smart_router":
        print(f"  {DIM}  → Will evaluate ALL strategies and pick the best one{RST}")
    print(f"{'='*60}")

    df = make_candles()

    SL_PTS    = 30.0
    TRAIL_PTS = 15.0
    RR        = 2.0

    entered     = False
    entry_price = stop_loss = target = None
    direction   = highest = lowest = None
    picked_strategy = None

    today_mask = df.index.date == pd.Timestamp("2025-03-12").date()
    today_idx  = [i for i, m in enumerate(today_mask) if m]

    hdr("PHASE 1 — 5-min candle loop  (scanning for entry)")
    print(f"  {DIM}Each row = one 5-min candle. Strategy evaluated on every close.{RST}\n")

    for i in today_idx:
        candle_df = df.iloc[: i + 1]
        candle    = df.iloc[i]
        t         = df.index[i].strftime("%H:%M")
        price     = candle["close"]

        if entered:
            break

        # ── Same call as auto_trader.py line 618 ─────────────────
        signal = strat_info.evaluate(candle_df)
        met    = sum(1 for c in signal.conditions if c.met)
        total  = len(signal.conditions)

        if total == 0:
            print(f"  {DIM}🕯 [{t}]  ₹{price:,.0f}  {signal.reason}{RST}")
            time.sleep(0.02)
            continue

        # Show which strategy the smart_router picked
        picked = ""
        if strategy_id == "smart_router" and hasattr(signal, "picked_strategy"):
            picked = f"  {Y}[picked: {signal.picked_strategy}]{RST}"

        print(f"\n  {B}🕯 [{t}]{RST}  ₹{price:,.0f}  ({met}/{total} met){picked}")
        for cond in signal.conditions:
            icon = f"{G}✅{RST}" if cond.met else f"{R}❌{RST}"
            print(f"       {icon}  {cond.name:<24}{DIM}{cond.detail}{RST}")

        if not signal.should_enter:
            print(f"       {Y}→ {signal.reason}{RST}")
            time.sleep(0.05)
            continue

        # ── ENTRY ─────────────────────────────────────────────────
        direction   = signal.direction.value
        entry_price = price
        if direction == "short":
            stop_loss = round(entry_price + SL_PTS, 2)
            target    = round(entry_price - SL_PTS * RR, 2)
            lowest    = entry_price
        else:
            stop_loss = round(entry_price - SL_PTS, 2)
            target    = round(entry_price + SL_PTS * RR, 2)
            highest   = entry_price
        entered = True
        picked_strategy = getattr(signal, "picked_strategy", strategy_id)

        # ── Strike & Quantity calculation (mirrors _get_option_symbol) ──
        CAPITAL      = 96_000          # from env TRADING_CAPITAL default
        LOT_SIZE     = 75              # Nifty lot size (changed to 75 from Apr 2024)
        DEFAULT_QTY  = 780             # 12 lots × 65 (legacy config) — kept as-is
        option_type  = "CE" if direction == "long" else "PE"
        atm_strike   = round(entry_price / 50) * 50
        # 1 strike OTM → cheaper premium → more quantity for same capital
        otm_strike   = atm_strike + 50 if direction == "long" else atm_strike - 50
        # Estimated OTM premium (ATM delta≈0.5, OTM delta≈0.35)
        est_premium  = round(entry_price * 0.003, 0)   # rough: ~0.3% of Nifty for 1-OTM
        lots_possible = int(CAPITAL / (est_premium * LOT_SIZE))

        hdr("PHASE 1.5 — Strike & Quantity Selection")
        print(f"  {B}Nifty spot at entry : ₹{entry_price:,.0f}{RST}")
        print()
        print(f"  {W}Step 1 — Find ATM strike{RST}")
        print(f"  {DIM}  Round {entry_price:.0f} to nearest 50 → {atm_strike}{RST}")
        print()
        print(f"  {W}Step 2 — Go 1 strike OTM (cheaper premium){RST}")
        print(f"  {DIM}  Direction = {'SHORT → bearish → buy PE' if direction=='short' else 'LONG → bullish → buy CE'}{RST}")
        print(f"  {DIM}  ATM = {atm_strike}  →  OTM = {atm_strike} {'- 50' if direction=='short' else '+ 50'} = {otm_strike}{RST}")
        print(f"  {G}  ✅ Strike chosen : {otm_strike} {option_type}{RST}")
        print()
        print(f"  {W}Step 3 — Why OTM not ATM?{RST}")
        print(f"  {DIM}  ATM premium ≈ ₹150–200   (delta 0.50){RST}")
        print(f"  {DIM}  OTM premium ≈ ₹80–120    (delta 0.35){RST}")
        print(f"  {DIM}  Cheaper premium → more lots for same capital → more P&L per point{RST}")
        print(f"  {DIM}  Downside: delta is lower, moves less per Nifty point{RST}")
        print()
        print(f"  {W}Step 4 — Quantity{RST}")
        print(f"  {DIM}  Config  : DEFAULT_QUANTITY = {DEFAULT_QTY}  (from .env){RST}")
        print(f"  {DIM}  = 12 lots × 65 units/lot  (old Nifty lot size){RST}")
        print(f"  {DIM}  NOTE: Current Nifty lot size is 75.  You may want to update this.{RST}")
        print(f"  {DIM}  At ₹{est_premium:.0f} est. premium → capital needed = {DEFAULT_QTY} × ₹{est_premium:.0f} = ₹{DEFAULT_QTY*est_premium:,.0f}{RST}")
        print(f"  {G}  ✅ Quantity chosen : {DEFAULT_QTY} units  (12 lots){RST}")

        hdr("PHASE 2 — Entry order placed  🚀")
        arrow = "⬇ SHORT" if direction == "short" else "⬆ LONG"
        trade(f"🚀 [{t}]  {arrow}  @ ₹{entry_price:,.2f}")
        if strategy_id == "smart_router":
            trade(f"   Picked by  : 🧠 Smart Router → {picked_strategy}")
        trade(f"   Instrument : {otm_strike} {option_type}  (1 strike OTM)")
        trade(f"   Quantity   : {DEFAULT_QTY} units  (12 lots × 65)")
        trade(f"   Stop Loss  : ₹{stop_loss:,.2f}  ({SL_PTS:.0f} pts risk on Nifty)")
        trade(f"   Target     : ₹{target:,.2f}  ({SL_PTS*RR:.0f} pts reward | RR {RR:.0f}:1)")
        trade(f"   SL-M order : ✅ placed at exchange (crash backstop)")

    if not entered:
        warn("No entry signal triggered in this simulation run.")
        warn(f"Strategy '{strategy_id}' found no valid setup in today's candles.")
        sys.exit(0)

    # ── Phase 3: tick guard ───────────────────────────────────────
    hdr("PHASE 3 — Tick guard  (every ~1 second)")
    print(f"  {DIM}· = tick  |  🔄 = trailing SL update{RST}\n")

    journey = [
        ("10:00", entry_price,       entry_price - 20),
        ("10:05", entry_price - 20,  entry_price - 50),
        ("10:10", entry_price - 50,  entry_price - 35),  # pullback
        ("10:15", entry_price - 35,  entry_price - 65),
        ("10:20", entry_price - 65,  target - 2),
    ]

    sl_updates, final_exit = 0, None

    for label, frm, to in journey:
        if final_exit:
            break
        ticks = fake_ticks(frm, to)
        print(f"  {B}⏱ {label}{RST}  {DIM}₹{frm:,.1f}→₹{to:,.1f}{RST}  ", end="")

        for price in ticks:
            if final_exit:
                break

            # trailing SL (same logic as _manage_active_trade)
            if direction == "short":
                if price < lowest:
                    lowest = price
                    new_sl = round(lowest + TRAIL_PTS, 2)
                    if new_sl < stop_loss:
                        old, stop_loss = stop_loss, new_sl
                        sl_updates += 1
                        print(f"\n  {Y}  🔄 Trail SL ₹{old:,.2f}→₹{stop_loss:,.2f}"
                              f" (₹{price:,.2f}){RST}\n     ", end="")
            else:
                if price > highest:
                    highest = price
                    new_sl = round(highest - TRAIL_PTS, 2)
                    if new_sl > stop_loss:
                        old, stop_loss = stop_loss, new_sl
                        sl_updates += 1
                        print(f"\n  {Y}  🔄 Trail SL ₹{old:,.2f}→₹{stop_loss:,.2f}"
                              f" (₹{price:,.2f}){RST}\n     ", end="")

            if (direction == "short" and price >= stop_loss) or \
               (direction == "long"  and price <= stop_loss):
                print(f"\n  {R}  🛑 SL HIT @ ₹{price:,.2f}  (SL=₹{stop_loss:,.2f}){RST}")
                final_exit = ("⚡ Stop-loss hit", price)
                break

            if (direction == "short" and price <= target) or \
               (direction == "long"  and price >= target):
                print(f"\n  {G}  🎯 TARGET HIT @ ₹{price:,.2f}!{RST}")
                final_exit = ("🎯 Target hit", price)
                break

            print("·", end="", flush=True)
            time.sleep(0.04)
        print()

    # ── Phase 4: exit ─────────────────────────────────────────────
    hdr("PHASE 4 — Exit & P&L")

    reason, exit_px = final_exit or ("⏰ Time exit 15:15", entry_price)
    pnl_pts  = (entry_price - exit_px) if direction == "short" else (exit_px - entry_price)

    # ── Real P&L calculation ──────────────────────────────────────
    # Nifty options P&L = option premium change × quantity
    # We don't have real option prices here, so we use delta approximation:
    #   1-OTM option delta ≈ 0.35
    #   Premium change ≈ Nifty pts × delta
    QTY        = 780           # 12 lots × 65 units (from DEFAULT_QUANTITY)
    DELTA      = 0.35          # 1-OTM PE/CE delta approximation
    LOT_SIZE   = 65            # units per lot as configured
    LOTS       = QTY // LOT_SIZE

    option_pts = round(pnl_pts * DELTA, 1)        # option premium change
    pnl_full   = round(option_pts * QTY, 0)       # full position P&L
    pnl_per_lot= round(option_pts * LOT_SIZE, 0)  # per lot

    trade(f"🏁 EXIT — {reason}")
    trade(f"   Nifty move : {'+' if pnl_pts>=0 else ''}{pnl_pts:.1f} pts")
    trade(f"   Option Δ  : {DELTA} (1-OTM approx)  →  premium moved ~₹{option_pts:.1f}")
    print()
    trade(f"   Quantity  : {QTY} units  ({LOTS} lots × {LOT_SIZE})")
    trade(f"   Per lot   : {'+' if pnl_per_lot>=0 else ''}₹{pnl_per_lot:,.0f}  ({LOT_SIZE} units)")
    trade(f"   FULL P&L  : {'+' if pnl_full>=0 else ''}₹{pnl_full:,.0f}  (all {LOTS} lots)")
    print()
    trade(f"   Trail SL  : {sl_updates} moves (locked in profit)")
    trade(f"   SL-M order: Cancelled before exit ✅")

    print()
    bar = "━" * 52
    if pnl_full > 0:
        print(f"  {G}{bar}")
        print(f"  {G}   🟢  TRADE WIN")
        print(f"  {G}   Per lot : +₹{pnl_per_lot:,.0f}")
        print(f"  {G}   TOTAL   : +₹{pnl_full:,.0f}  ({LOTS} lots)")
        print(f"  {G}{bar}{RST}")
    elif pnl_full < 0:
        print(f"  {R}{bar}")
        print(f"  {R}   🔴  TRADE LOSS")
        print(f"  {R}   Per lot : ₹{pnl_per_lot:,.0f}")
        print(f"  {R}   TOTAL   : ₹{pnl_full:,.0f}  ({LOTS} lots)")
        print(f"  {R}{bar}{RST}")
    else:
        print(f"  {Y}   ⚪  BREAK EVEN{RST}")

    hdr("Trade Summary")
    # Recompute for summary block
    _atm  = round(entry_price / 50) * 50
    _otm  = _atm - 50 if direction == "short" else _atm + 50
    _otype= "PE" if direction == "short" else "CE"
    _qty  = 780

    rows = [
        ("Strategy",    f"{strat_info.emoji} {strat_info.name}"),
        ("Picked by",   f"Smart Router → {picked_strategy}"
                        if strategy_id == "smart_router" else strategy_id),
        ("Mode",        "📝 PAPER  (no real money)"),
        ("Direction",   "SHORT ⬇" if direction == "short" else "LONG ⬆"),
        ("Instrument",  f"{_otm} {_otype}  (1 OTM from ATM {_atm})"),
        ("Quantity",    f"{_qty} units  (12 lots × 65)"),
        ("Entry",       f"₹{entry_price:,.2f}"),
        ("Initial SL",  f"₹{entry_price+SL_PTS if direction=='short' else entry_price-SL_PTS:,.2f}"
                        f"  ({SL_PTS:.0f} pts)"),
        ("Final SL",    f"₹{stop_loss:,.2f}  (trailed {sl_updates}×)"),
        ("Target",      f"₹{target:,.2f}  (RR {RR:.0f}:1)"),
        ("Exit",        f"{reason} @ ₹{exit_px:,.2f}"),
        ("Nifty move",  f"{'+' if pnl_pts>=0 else ''}{pnl_pts:.1f} pts"),
        ("Option Δ",    f"{DELTA} → premium moved ~₹{option_pts:.1f}"),
        ("Per lot P&L", f"{'+' if pnl_per_lot>=0 else ''}₹{pnl_per_lot:,.0f}  ({LOT_SIZE} units)"),
        ("TOTAL P&L",   f"{'+' if pnl_full>=0 else ''}₹{pnl_full:,.0f}  ({LOTS} lots × {LOT_SIZE})"),
    ]
    for k, v in rows:
        print(f"  {DIM}{k:<14}{RST}  {W}{v}{RST}")
    print()

if __name__ == "__main__":
    run()

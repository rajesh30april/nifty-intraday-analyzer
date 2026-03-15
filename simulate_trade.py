#!/usr/bin/env python3
"""
Auto-Trader Dry-Run Simulator
==============================
Simulates a complete trading day — no Zerodha, no real money.

Shows step by step:
  Phase 1 → Candle loop evaluates ORB conditions each 5-min candle
  Phase 2 → Entry fires when all conditions met
  Phase 3 → Tick guard runs SL / trailing SL / target checks every ~1s
  Phase 4 → Exit and P&L
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

def hdr(t):    print(f"\n{B}{'─'*60}{RST}\n{W} {t}{RST}\n{B}{'─'*60}{RST}")
def trade(t):  print(f"  {W}{t}{RST}")
def warn(t):   print(f"  {Y}⚠️  {t}{RST}")

# ── Build OHLCV dataframe ─────────────────────────────────────────
def make_candles():
    """
    25 candles of 'yesterday' history  (needed so rolling(20) has data)
    + today's candles:
      09:15-09:29  ORB builds (range: ~23428–23492)
      09:30-09:55  consolidation near ORB low
      10:00        ★ BREAKDOWN — high volume, closes well below ORB low
      10:05+       downtrend continues
    """
    rng = np.random.default_rng(42)

    # ── Yesterday: 25 candles ending at 23460 ────────────────────
    hist_times = pd.date_range("2025-03-11 09:15", periods=25, freq="5min")
    hist_rows = []
    p = 23500.0
    for _ in hist_times:
        c = p + float(rng.integers(-8, 9))
        h = max(p, c) + float(rng.integers(3, 15))
        l = min(p, c) - float(rng.integers(3, 15))
        vol = int(rng.integers(90_000, 160_000))
        hist_rows.append({"open": p, "high": h, "low": l, "close": c, "volume": vol})
        p = c

    # ── Today ─────────────────────────────────────────────────────
    today_times = pd.date_range("2025-03-12 09:15", periods=22, freq="5min")
    today_closes = [
        23450, 23480, 23460,       # ORB window → range ≈ 23428..23492
        23445, 23438, 23442,       # consolidation
        23430, 23435, 23428,       # near ORB low
        23390,                     # ★ 10:00  BREAKDOWN  (high volume!)
        23370, 23355, 23340,       # downtrend
        23360, 23345, 23340,       # slight bounce
        23310, 23295, 23290,       # near target
        23300, 23310, 23295,       # post-exit (ignored)
    ]
    avg_hist_vol = 125_000         # approximate avg of history candles

    today_rows = []
    prev = 23460.0
    for i, c in enumerate(today_closes):
        h = max(prev, c) + int(rng.integers(3, 18))
        l = min(prev, c) - int(rng.integers(3, 18))
        if i == 9:                 # 10:00 breakout candle — big volume surge
            vol = int(avg_hist_vol * 2.1)   # 2.1x average → clearly passes 1.2x
        else:
            vol = int(rng.integers(80_000, 140_000))
        today_rows.append({"open": prev, "high": h, "low": l, "close": c, "volume": vol})
        prev = c

    hist_df  = pd.DataFrame(hist_rows,  index=hist_times)
    today_df = pd.DataFrame(today_rows, index=today_times)
    return pd.concat([hist_df, today_df])

def fake_ticks(frm, to, n=10):
    return [round(frm + (to - frm) * i / (n - 1), 2) for i in range(n)]

# ── Main ──────────────────────────────────────────────────────────
def run():
    print(f"\n{'='*60}")
    print(f"  🤖  AUTO-TRADER DRY-RUN SIMULATOR  |  📝 PAPER MODE")
    print(f"  📅  2025-03-12  |  Strategy: Opening Range Breakout (ORB)")
    print(f"{'='*60}")

    df = make_candles()
    from strategies.orb import evaluate_orb

    SL_PTS    = 30.0
    TRAIL_PTS = 15.0
    RR        = 2.0

    entered     = False
    entry_price = stop_loss = target = None
    direction   = highest = lowest = None

    # Only show today's candles in the loop
    today_mask = df.index.date == pd.Timestamp("2025-03-12").date()
    today_idx  = [i for i, m in enumerate(today_mask) if m]

    hdr("PHASE 1 — 5-min candle loop  (scanning for entry)")
    print(f"  {DIM}Each row below = one 5-min candle close{RST}\n")

    for i in today_idx:
        candle_df = df.iloc[: i + 1]   # full history up to this candle
        candle    = df.iloc[i]
        t         = df.index[i].strftime("%H:%M")
        price     = candle["close"]

        if entered:
            break

        signal = evaluate_orb(candle_df)
        met    = sum(1 for c in signal.conditions if c.met)
        total  = len(signal.conditions)

        if total == 0:
            print(f"  {DIM}🕯 [{t}]  ₹{price:,.0f}  building ORB range...{RST}")
            time.sleep(0.02)
            continue

        print(f"\n  {B}🕯 [{t}]{RST}  close=₹{price:,.0f}  ({met}/{total} conditions met)")
        for cond in signal.conditions:
            icon = f"{G}✅{RST}" if cond.met else f"{R}❌{RST}"
            print(f"       {icon}  {cond.name:<22} {DIM}{cond.detail}{RST}")

        if not signal.should_enter:
            print(f"       {Y}→ waiting...{RST}")
            time.sleep(0.06)
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

        hdr("PHASE 2 — Entry order placed  🚀")
        arrow = "⬇ SHORT" if direction == "short" else "⬆ LONG"
        trade(f"🚀 [{t}]  {arrow}  @ ₹{entry_price:,.2f}")
        trade(f"   Stop Loss  : ₹{stop_loss:,.2f}  ({SL_PTS:.0f} pts risk)")
        trade(f"   Target     : ₹{target:,.2f}  ({SL_PTS*RR:.0f} pts reward  |  RR {RR:.0f}:1)")
        trade(f"   SL-M order : ✅ placed at exchange (crash backstop)")

    if not entered:
        warn("No entry signal triggered in this simulation run.")
        sys.exit(0)

    # ── Phase 3 ───────────────────────────────────────────────────
    hdr("PHASE 3 — Tick guard  (every ~1 second)")
    print(f"  {DIM}Dots = normal ticks.  🔄 = trailing SL update.{RST}\n")

    journey = [
        ("10:00", entry_price,       entry_price - 20),
        ("10:05", entry_price - 20,  entry_price - 50),
        ("10:10", entry_price - 50,  entry_price - 35),   # pullback
        ("10:15", entry_price - 35,  entry_price - 65),
        ("10:20", entry_price - 65,  target - 2),
    ]

    sl_updates = 0
    final_exit = None

    for label, frm, to in journey:
        if final_exit:
            break
        ticks = fake_ticks(frm, to)
        print(f"  {B}⏱ {label}{RST}  {DIM}₹{frm:,.1f} → ₹{to:,.1f}{RST}  ", end="")

        for price in ticks:
            if final_exit:
                break

            # trailing SL
            if direction == "short":
                if price < lowest:
                    lowest = price
                    new_sl = round(lowest + TRAIL_PTS, 2)
                    if new_sl < stop_loss:
                        old = stop_loss
                        stop_loss = new_sl
                        sl_updates += 1
                        print(f"\n  {Y}  🔄 Trail SL: ₹{old:,.2f} → ₹{stop_loss:,.2f}"
                              f"  (price ₹{price:,.2f}){RST}")
                        print(f"     ", end="")
            else:
                if price > highest:
                    highest = price
                    new_sl = round(highest - TRAIL_PTS, 2)
                    if new_sl > stop_loss:
                        old = stop_loss
                        stop_loss = new_sl
                        sl_updates += 1
                        print(f"\n  {Y}  🔄 Trail SL: ₹{old:,.2f} → ₹{stop_loss:,.2f}"
                              f"  (price ₹{price:,.2f}){RST}")
                        print(f"     ", end="")

            # SL hit?
            if (direction == "short" and price >= stop_loss) or \
               (direction == "long"  and price <= stop_loss):
                print(f"\n  {R}  🛑 SL HIT @ ₹{price:,.2f}  (SL was ₹{stop_loss:,.2f}){RST}")
                final_exit = ("⚡ Stop-loss hit", price)
                break

            # Target hit?
            if (direction == "short" and price <= target) or \
               (direction == "long"  and price >= target):
                print(f"\n  {G}  🎯 TARGET HIT @ ₹{price:,.2f}!{RST}")
                final_exit = ("🎯 Target hit", price)
                break

            print("·", end="", flush=True)
            time.sleep(0.04)
        print()

    # ── Phase 4 ───────────────────────────────────────────────────
    hdr("PHASE 4 — Exit & P&L")

    reason, exit_px = final_exit or ("⏰ Time exit 15:15", entry_price)
    pnl_pts = (entry_price - exit_px) if direction == "short" else (exit_px - entry_price)
    pnl_rs  = pnl_pts * 75

    trade(f"🏁 EXIT  —  {reason}")
    trade(f"   Entry      : ₹{entry_price:,.2f}")
    trade(f"   Exit       : ₹{exit_px:,.2f}")
    trade(f"   P&L        : {'+' if pnl_pts>=0 else ''}{pnl_pts:.1f} pts"
          f"  /  {'+' if pnl_rs>=0 else ''}₹{pnl_rs:,.0f}  (1 lot ≈ 75 units)")
    trade(f"   Trail moves: {sl_updates}x")
    trade(f"   SL-M order : Cancelled before exit ✅  (no double-fill)")

    print()
    if pnl_rs > 0:
        print(f"  {G}{'━'*52}{RST}")
        print(f"  {G}   🟢  TRADE WIN   +₹{pnl_rs:,.0f}{RST}")
        print(f"  {G}{'━'*52}{RST}")
    elif pnl_rs < 0:
        print(f"  {R}{'━'*52}{RST}")
        print(f"  {R}   🔴  TRADE LOSS   ₹{pnl_rs:,.0f}{RST}")
        print(f"  {R}{'━'*52}{RST}")
    else:
        print(f"  {Y}   ⚪  BREAK EVEN{RST}")

    hdr("Trade Summary")
    rows = [
        ("Strategy",     "Opening Range Breakout (ORB)"),
        ("Mode",         "📝 PAPER  (simulation, no real money)"),
        ("Direction",    "SHORT ⬇" if direction == "short" else "LONG ⬆"),
        ("Entry",        f"₹{entry_price:,.2f}"),
        ("Initial SL",   f"₹{entry_price+SL_PTS if direction=='short' else entry_price-SL_PTS:,.2f}"
                         f"  ({SL_PTS:.0f} pts)"),
        ("Final SL",     f"₹{stop_loss:,.2f}  (trailed {sl_updates}×)"),
        ("Target",       f"₹{target:,.2f}  (RR {RR:.0f}:1)"),
        ("Exit reason",  reason),
        ("Exit price",   f"₹{exit_px:,.2f}"),
        ("P&L (pts)",    f"{'+' if pnl_pts>=0 else ''}{pnl_pts:.1f}"),
        ("P&L (₹)",      f"{'+' if pnl_rs>=0 else ''}₹{pnl_rs:,.0f}  (1 lot)"),
    ]
    for k, v in rows:
        print(f"  {DIM}{k:<14}{RST}  {W}{v}{RST}")
    print()

if __name__ == "__main__":
    run()

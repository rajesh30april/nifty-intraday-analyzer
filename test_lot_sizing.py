"""Interactive lot-sizing test.

Tests _resolve_quantity() directly with different capital and premium
combinations. No Kite connection needed — pure math verification.

Usage:
    python test_lot_sizing.py
    python test_lot_sizing.py --capital 50000 --premium 120
    python test_lot_sizing.py --nifty 24500          # uses 0.35% estimate
"""

import sys
import os

# ── Patch env so auto_trader imports without Kite credentials ────
os.environ.setdefault("KITE_API_KEY",    "test")
os.environ.setdefault("KITE_API_SECRET", "test")
os.environ.setdefault("LIVE_TRADING",    "false")

from auto_trader import _resolve_quantity, _estimate_premium_fallback, state, LOT_SIZE


SEP  = "─" * 72
SEP2 = "═" * 72


def _lots(capital: float, premium: float) -> int:
    """Return lot count for given capital and premium."""
    cost_per_lot = premium * LOT_SIZE
    return int(capital / cost_per_lot)


def _cost(lots: int, premium: float) -> float:
    return lots * LOT_SIZE * premium


def run_table(capitals: list[float], premiums: list[float]) -> None:
    """Print a grid: rows = capital, cols = premium."""
    print(f"\n{SEP2}")
    print(f"  LOT SIZING TABLE   (lot size = {LOT_SIZE} units)")
    print(f"  formula: lots = floor(capital / (premium × {LOT_SIZE}))")
    print(SEP2)

    # Header row
    header = f"{'Capital':>12s}  "
    for p in premiums:
        header += f"  prem={p:<5.0f}"
    print(header)
    print(SEP)

    for cap in capitals:
        row = f"  ₹{cap:>10,.0f}  "
        for p in premiums:
            lots = _lots(cap, p)
            cost = _cost(lots, p)
            if lots == 0:
                row += f"  {'—':^10s}"
            else:
                row += f"  {lots}L / ₹{cost:,.0f}"
        print(row)

    print(SEP)
    print("  Format: Xlots / ₹cost_deployed")
    print()


def run_single(capital: float, premium: float, nifty_price: float) -> None:
    """Detailed single scenario using the actual _resolve_quantity function."""
    state.capital  = capital
    state.qty_mode = "capital"

    print(f"\n{SEP2}")
    print(f"  SINGLE SCENARIO")
    print(SEP2)
    print(f"  Capital       : ₹{capital:,.0f}")
    print(f"  Premium (live): ₹{premium:.1f}")
    print(f"  Nifty spot    : ₹{nifty_price:,.0f}")
    print(f"  Est. premium  : ₹{_estimate_premium_fallback(nifty_price):.1f}  (0.35% fallback)")
    print(f"  Lot size      : {LOT_SIZE} units")
    print(SEP)

    # With real LTP
    qty_live, cost_live = _resolve_quantity(nifty_price, real_premium=premium)
    lots_live = qty_live // LOT_SIZE
    print(f"  ✅ Using live premium ₹{premium:.1f}:")
    print(f"     lots    = {lots_live}")
    print(f"     qty     = {qty_live} units")
    print(f"     cost    = ₹{lots_live * LOT_SIZE * premium:,.0f}")
    print(f"     leftover= ₹{capital - lots_live * LOT_SIZE * premium:,.0f} (stays in account)")

    # With estimated premium
    qty_est, cost_est = _resolve_quantity(nifty_price, real_premium=None)
    lots_est = qty_est // LOT_SIZE
    print()
    print(f"  📐 Using estimated premium ₹{_estimate_premium_fallback(nifty_price):.1f} (Kite down):")
    print(f"     lots    = {lots_est}")
    print(f"     qty     = {qty_est} units")
    est_p = _estimate_premium_fallback(nifty_price)
    print(f"     cost    = ₹{lots_est * LOT_SIZE * est_p:,.0f}")
    print(SEP2)
    print()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Lot sizing calculator")
    parser.add_argument("--capital",  type=float, default=None,
                        help="Capital in ₹ (single scenario)")
    parser.add_argument("--premium",  type=float, default=None,
                        help="Option LTP in ₹ (single scenario)")
    parser.add_argument("--nifty",    type=float, default=24500,
                        help="Nifty spot price (for premium estimate fallback)")
    args = parser.parse_args()

    # ── Table mode: show a full grid ──────────────────────────────
    capitals = [
        10_000, 20_000, 30_000, 50_000,
        75_000, 1_00_000, 1_50_000, 2_00_000,
    ]
    premiums = [50, 80, 100, 120, 150, 200]

    run_table(capitals, premiums)

    # ── Single scenario mode ──────────────────────────────────────
    cap = args.capital or 96_000     # your default
    prem = args.premium or 100.0
    nifty = args.nifty

    run_single(cap, prem, nifty)

    # ── Sanity-check a few values manually ───────────────────────
    print(f"{SEP2}")
    print("  MANUAL SANITY CHECKS")
    print(SEP)
    checks = [
        (10_000, 80,  "tight capital"   ),
        (50_000, 100, "mid capital"     ),
        (96_000, 120, "your default cap"),
        (2_00_000, 80, "2L capital"     ),
        (30_000, 200, "expensive prem" ),
        (5_000,  200, "can't afford"   ),
    ]
    for cap, prem, label in checks:
        lots = _lots(cap, prem)
        cost = _cost(lots, prem)
        leftover = cap - cost
        status = "❌ cannot afford" if lots == 0 else f"✅ {lots}L = {lots*LOT_SIZE} units"
        print(f"  {label:<20s}  ₹{cap:>8,.0f}  @₹{prem:<4.0f}  → {status}")
        if lots > 0:
            print(f"  {'':20s}    cost ₹{cost:>8,.0f}  leftover ₹{leftover:>8,.0f}")
    print(SEP2)


if __name__ == "__main__":
    main()

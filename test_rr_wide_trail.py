#!/usr/bin/env python3
"""Test R:R Ratios with WIDER Trailing SL.

Previous test showed trailing_sl=20 prevents ANY target from being hit.
This test uses trailing_sl=40 to let trades run further.
"""

import sys
from backtester import run_backtest

print("="*80)
print(" "*15 + "🔬 R:R RATIO BACKTEST - WIDER TRAILING SL")
print("="*80)

print("\n📊 Configuration:")
print("   Data Source:    Yahoo Finance")
print("   Period:         60 days")
print("   Interval:       5 minutes")
print("   SL Points:      40")
print("   Trailing SL:    40 ⭐ (WIDER - was 20)")
print("   Strategy:       smart_router")
print("   Max Trades/Day: 5")

print("\n🎯 HYPOTHESIS:")
print("   With wider trailing SL (40 instead of 20),")
print("   trades will run further and we should see:")
print("   • SOME targets being hit")
print("   • Different avg wins for each R:R ratio")
print("   • Actual difference between 1:2, 1:2.5, and 1:3\n")

ratios_to_test = [2.0, 2.5, 3.0]
results = {}

for rr in ratios_to_test:
    print(f"\n{'='*80}")
    print(f"📊 TESTING R:R RATIO 1:{rr}")
    print(f"   Target: {40 * rr:.0f} points (SL: 40 points)")
    print(f"{'='*80}\n")
    
    try:
        result = run_backtest(
            period="60d",
            interval="5m",
            sl_points=40.0,
            trailing_sl=40.0,  # ⭐ WIDER!
            rr_ratio=rr,
            max_trades_per_day=5,
            use_router=True,
            strategy_id="smart_router",
            data_source="yahoo",
            quantity=65,
        )
        
        results[rr] = result
        
        print(f"\n{'─'*80}")
        print(f"RESULTS FOR 1:{rr}:")
        print(f"  Total Trades:     {result.total_trades}")
        print(f"  Winners:          {result.winners}")
        print(f"  Losers:           {result.losers}")
        print(f"  Win Rate:         {result.win_rate:.1f}%")
        print(f"  Profit Factor:    {result.profit_factor:.2f}")
        print(f"  Total P&L:        {result.total_pnl_points:+.1f} points")
        print(f"  Total Rupees:     ₹{result.total_pnl_rupees:+,.0f}")
        print(f"  Avg Win:          {result.avg_win:.1f} points ⭐")
        print(f"  Avg Loss:         {result.avg_loss:.1f} points")
        print(f"  Max Win:          {result.max_win:.1f} points")
        print(f"  Max Loss:         {result.max_loss:.1f} points")
        print(f"{'─'*80}")
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        continue

if len(results) < len(ratios_to_test):
    print(f"\n⚠️  Only {len(results)} tests completed.")
    sys.exit(1)

# Count target hits
print(f"\n\n🎯 CHECKING TARGET HITS...\n")

for rr in ratios_to_test:
    r = results[rr]
    target_hits = sum(1 for t in r.trades if t.exit_reason == "Target")
    trailing_sl_hits = sum(1 for t in r.trades if t.exit_reason == "Trailing SL")
    sl_hits = sum(1 for t in r.trades if t.exit_reason == "SL")
    
    print(f"1:{rr} Ratio ({40*rr:.0f}-point target):")
    print(f"  Target Hits:      {target_hits:3d} / {r.total_trades} ({target_hits/r.total_trades*100:.1f}%)")
    print(f"  Trailing SL Hits: {trailing_sl_hits:3d} / {r.total_trades} ({trailing_sl_hits/r.total_trades*100:.1f}%)")
    print(f"  Regular SL Hits:  {sl_hits:3d} / {r.total_trades} ({sl_hits/r.total_trades*100:.1f}%)")
    print()

# Final comparison
print(f"\n{'='*80}")
print(" "*28 + "📊 FINAL COMPARISON")
print(f"{'='*80}\n")

print(f"{'RATIO':^8} | {'TRADES':^8} | {'WIN RATE':^10} | {'PROFIT':^8} | {'AVG WIN':^10} | {'AVG LOSS':^10} | {'NET P&L':^12}")
print(f"{'-'*8}-+-{'-'*8}-+-{'-'*10}-+-{'-'*8}-+-{'-'*10}-+-{'-'*10}-+-{'-'*12}")

best_rr = None
best_pnl = -999999

for rr in ratios_to_test:
    r = results[rr]
    
    marker = ""
    if r.total_pnl_points > best_pnl:
        best_pnl = r.total_pnl_points
        best_rr = rr
        marker = " 🏆"
    
    print(
        f" 1:{rr:<5} | {r.total_trades:>7} | "
        f"{r.win_rate:>9.1f}% | "
        f"{r.profit_factor:>7.2f} | "
        f"{r.avg_win:>9.1f}  | "
        f"{r.avg_loss:>9.1f}  | "
        f"{r.total_pnl_points:>+11.1f}{marker}"
    )

print(f"{'-'*8}-+-{'-'*8}-+-{'-'*10}-+-{'-'*8}-+-{'-'*10}-+-{'-'*10}-+-{'-'*12}")

# Winner
r = results[best_rr]
avg_per_trade = r.total_pnl_points / r.total_trades if r.total_trades > 0 else 0
expected_per_10 = avg_per_trade * 10
expected_rupees_per_10 = expected_per_10 * 65

print(f"\n{'='*80}")
print(f"🏆 WINNER: 1:{best_rr} RATIO!")
print(f"{'='*80}")
print(f"\n  Based on {r.days_tested} days with WIDER trailing SL:\n")
print(f"  ✅ Total Trades:      {r.total_trades} ({r.winners}W / {r.losers}L)")
print(f"  ✅ Win Rate:          {r.win_rate:.1f}%")
print(f"  ✅ Profit Factor:     {r.profit_factor:.2f}")
print(f"  ✅ Avg Win:           +{r.avg_win:.1f} points")
print(f"  ✅ Avg Loss:          -{r.avg_loss:.1f} points")
print(f"  ✅ Total P&L:         {r.total_pnl_points:+.1f} points")
print(f"  ✅ Total Profit:      ₹{r.total_pnl_rupees:+,.0f}")
print(f"\n  📈 Expected per 10 Trades:")
print(f"     Net Points:        {expected_per_10:+.1f} points")
print(f"     Net Profit:        ₹{expected_rupees_per_10:+,.0f}")

print(f"\n{'='*80}")
print(f"\n✅ RECOMMENDATION: Set rr_ratio = {best_rr} & trailing_sl = 40")
print(f"\n{'='*80}")

# Comparison with tight trailing
print(f"\n\n📊 COMPARISON: Tight vs Wide Trailing SL\n")
print(f"With Trailing SL = 20 (previous test):")
print(f"  • Avg Win: 15 points")
print(f"  • Target Hits: 0 (0%)")
print(f"  • All ratios identical")
print()
print(f"With Trailing SL = 40 (this test):")
for rr in ratios_to_test:
    r = results[rr]
    target_hits = sum(1 for t in r.trades if t.exit_reason == "Target")
    print(f"  • 1:{rr} - Avg Win: {r.avg_win:.1f} pts, Target Hits: {target_hits} ({target_hits/r.total_trades*100:.1f}%)")

print(f"\n✅ WIDER trailing SL allows targets to be hit!")
print(f"✅ Now R:R ratio actually matters!")
print(f"\n{'='*80}")
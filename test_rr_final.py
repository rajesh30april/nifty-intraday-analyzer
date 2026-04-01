#!/usr/bin/env python3
"""Test Different R:R Ratios Using Existing Backtester.

Runs the backtester 3 times with ratios 1:2, 1:2.5, and 1:3.
Provides REAL comparison data.
"""

import sys
from backtester import run_backtest

print("="*80)
print(" "*20 + "🔬 R:R RATIO BACKTEST - REAL DATA")
print("="*80)

print("\n📊 Configuration:")
print("   Data Source:    Yahoo Finance")
print("   Period:         60 days")
print("   Interval:       5 minutes")
print("   SL Points:      40 (NEW recommended)")
print("   Trailing SL:    20 (NEW recommended)")
print("   Strategy:       smart_router (all strategies combined)")
print("   Max Trades/Day: 5")

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
            trailing_sl=20.0,
            rr_ratio=rr,
            max_trades_per_day=5,
            use_router=True,
            strategy_id="smart_router",
            data_source="yahoo",
            quantity=65,
        )
        
        results[rr] = result
        
        # Print summary
        print(f"\n{'─'*80}")
        print(f"RESULTS FOR 1:{rr}:")
        print(f"  Total Trades:     {result.total_trades}")
        print(f"  Winners:          {result.winners}")
        print(f"  Losers:           {result.losers}")
        print(f"  Win Rate:         {result.win_rate:.1f}%")
        print(f"  Profit Factor:    {result.profit_factor:.2f}")
        print(f"  Total P&L:        {result.total_pnl_points:+.1f} points")
        print(f"  Total Rupees:     ₹{result.total_pnl_rupees:+,.0f}")
        print(f"  Avg Win:          {result.avg_win:.1f} points")
        print(f"  Avg Loss:         {result.avg_loss:.1f} points")
        print(f"  Max Win:          {result.max_win:.1f} points")
        print(f"  Max Loss:         {result.max_loss:.1f} points")
        print(f"  Days Tested:      {result.days_tested}")
        print(f"{'─'*80}")
        
    except Exception as e:
        print(f"\n❌ ERROR testing 1:{rr}: {e}")
        import traceback
        traceback.print_exc()
        continue

if len(results) < len(ratios_to_test):
    print(f"\n⚠️  Only {len(results)} of {len(ratios_to_test)} tests completed.")
    sys.exit(1)

# Final comparison
print(f"\n\n{'='*80}")
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

# Winner announcement
r = results[best_rr]

# Calculate expected per 10 trades
if r.total_trades > 0:
    avg_pnl_per_trade = r.total_pnl_points / r.total_trades
    expected_per_10 = avg_pnl_per_trade * 10
    expected_rupees_per_10 = expected_per_10 * 65
else:
    expected_per_10 = 0
    expected_rupees_per_10 = 0

print(f"\n{'='*80}")
print(f"🏆 WINNER: 1:{best_rr} RATIO!")
print(f"{'='*80}")
print(f"\n  Based on {r.days_tested} days of REAL backtest data:\n")
print(f"  ✅ Total Trades:      {r.total_trades} ({r.winners}W / {r.losers}L)")
print(f"  ✅ Win Rate:          {r.win_rate:.1f}%")
print(f"  ✅ Profit Factor:     {r.profit_factor:.2f}")
print(f"  ✅ Avg Win:           +{r.avg_win:.1f} points")
print(f"  ✅ Avg Loss:          -{r.avg_loss:.1f} points")
print(f"  ✅ Total P&L:         {r.total_pnl_points:+.1f} points")
print(f"  ✅ Total Profit:      ₹{r.total_pnl_rupees:+,.0f}")
print(f"\n  📈 Expected per 10 Trades:")
print(f"     Net Points:        {expected_per_10:+.1f} points")
print(f"     Net Profit:        ₹{expected_rupees_per_10:+,.0f} (at 65 qty)")

print(f"\n{'='*80}")
print(f"\n✅ RECOMMENDATION: Set rr_ratio = {best_rr} in your UI settings!")
print(f"\n{'='*80}")

# Show detailed comparison
print(f"\n\n📋 DETAILED BREAKDOWN:\n")

for rr in ratios_to_test:
    r = results[rr]
    avg_per_trade = r.total_pnl_points / r.total_trades if r.total_trades > 0 else 0
    
    print(f"1:{rr} Ratio:")
    print(f"  Target: {40 * rr:.0f} points")
    print(f"  Trades: {r.total_trades} ({r.winners}W/{r.losers}L)")
    print(f"  Win Rate: {r.win_rate:.1f}%")
    print(f"  Total P&L: {r.total_pnl_points:+.1f} pts (₹{r.total_pnl_rupees:+,.0f})")
    print(f"  Avg/Trade: {avg_per_trade:+.1f} pts")
    print(f"  Profit Factor: {r.profit_factor:.2f}")
    print()

print(f"\n🎯 CONCLUSION:")
print(f"   Based on REAL backtested data from Yahoo Finance,")
print(f"   the 1:{best_rr} ratio produced the best results.")
print(f"\n   This is NOT a guess - these are actual simulated trades!")
print(f"\n{'='*80}")
#!/usr/bin/env python3
"""Run R:R Ratio Backtests by calling calibrator with different settings.

This script tests 1:2, 1:2.5, and 1:3 ratios by running the calibrator
multiple times with different RR settings and comparing results.
"""

import json
import sys
from pathlib import Path

print("="*80)
print(" "*20 + "🔬 R:R RATIO BACKTEST - REAL DATA")
print("="*80)

print("\n📊 This will run 3 separate backtests with different R:R ratios.")
print("   Each backtest takes ~30-60 seconds.")
print("   Total time: ~2-3 minutes\n")

ratios_to_test = [2.0, 2.5, 3.0]
sl_points = 40.0
trail_sl = 20.0

results_by_ratio = {}

for rr in ratios_to_test:
    print(f"\n{'='*80}")
    print(f"📊 TESTING R:R RATIO 1:{rr}")
    print(f"   SL: {sl_points} points | Target: {sl_points * rr:.0f} points")
    print(f"{'='*80}\n")
    
    # Modify calibrator constants temporarily
    calibrator_path = Path("calibrator.py")
    original_content = calibrator_path.read_text()
    
    # Update the constants
    modified_content = original_content
    modified_content = modified_content.replace(
        f"CAL_SL_POINTS   = 30.0",
        f"CAL_SL_POINTS   = {sl_points}"
    )
    modified_content = modified_content.replace(
        f"CAL_TRAIL_SL    = 15.0",
        f"CAL_TRAIL_SL    = {trail_sl}"
    )
    modified_content = modified_content.replace(
        f"CAL_RR          = 2.0",
        f"CAL_RR          = {rr}"
    )
    
    calibrator_path.write_text(modified_content)
    
    # Run calibrator
    print(f"Running calibrator...\n")
    
    try:
        # Import and run
        import importlib
        import calibrator
        
        # Reload module to pick up new constants
        importlib.reload(calibrator)
        
        from data_fetcher import fetch_intraday_data
        
        print("Fetching 60 days of historical data...")
        df = fetch_intraday_data('5minute', days_back=60)
        
        if df is None or len(df) < 100:
            print("❌ Failed to fetch data!")
            calibrator_path.write_text(original_content)  # Restore
            sys.exit(1)
        
        print(f"✅ Loaded {len(df)} candles across {len(set(df.index.date))} days\n")
        
        # Run calibration
        results = calibrator.run_calibration(df, verbose=True)
        
        # Save results for this ratio
        results_by_ratio[rr] = results
        
        # Calculate aggregate stats
        all_trades = sum(r.get('trades', 0) for r in results.values() if 'trades' in r)
        all_winners = sum(r.get('winners', 0) for r in results.values() if 'winners' in r)
        all_losers = sum(r.get('losers', 0) for r in results.values() if 'losers' in r)
        
        total_win_pts = sum(
            r.get('avg_win_pts', 0) * r.get('winners', 0) 
            for r in results.values() if 'avg_win_pts' in r
        )
        total_loss_pts = sum(
            r.get('avg_loss_pts', 0) * r.get('losers', 0) 
            for r in results.values() if 'avg_loss_pts' in r
        )
        
        wr = (all_winners / all_trades * 100) if all_trades > 0 else 0
        pf = (total_win_pts / total_loss_pts) if total_loss_pts > 0 else 0
        avg_win = total_win_pts / all_winners if all_winners > 0 else 0
        avg_loss = total_loss_pts / all_losers if all_losers > 0 else 0
        
        print(f"\n{'─'*80}")
        print(f"AGGREGATE FOR 1:{rr}:")
        print(f"  Win Rate:      {wr:.1f}%")
        print(f"  Profit Factor: {pf:.2f}")
        print(f"  Total Trades:  {all_trades} ({all_winners}W / {all_losers}L)")
        print(f"  Avg Win:       +{avg_win:.1f} points")
        print(f"  Avg Loss:      -{avg_loss:.1f} points")
        print(f"{'─'*80}")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Restore original calibrator
        calibrator_path.write_text(original_content)
        print(f"\n✅ Restored calibrator.py")

# Final comparison
if len(results_by_ratio) < len(ratios_to_test):
    print(f"\n⚠️  Only {len(results_by_ratio)} of {len(ratios_to_test)} backtests completed.")
    print("Cannot provide full comparison.\n")
    sys.exit(1)

print(f"\n\n{'='*80}")
print(" "*28 + "📊 FINAL RESULTS")
print(f"{'='*80}\n")

print(f"{'RATIO':^8} | {'WIN RATE':^10} | {'PROFIT':^8} | {'AVG WIN':^10} | {'AVG LOSS':^10} | {'NET/10':^10}")
print(f"{'-'*8}-+-{'-'*10}-+-{'-'*8}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}")

comparison = {}

for rr in ratios_to_test:
    results = results_by_ratio[rr]
    
    all_trades = sum(r.get('trades', 0) for r in results.values() if 'trades' in r)
    all_winners = sum(r.get('winners', 0) for r in results.values() if 'winners' in r)
    all_losers = all_trades - all_winners
    
    total_win_pts = sum(
        r.get('avg_win_pts', 0) * r.get('winners', 0) 
        for r in results.values() if 'avg_win_pts' in r
    )
    total_loss_pts = sum(
        r.get('avg_loss_pts', 0) * r.get('losers', 0) 
        for r in results.values() if 'avg_loss_pts' in r
    )
    
    wr = (all_winners / all_trades * 100) if all_trades > 0 else 0
    pf = (total_win_pts / total_loss_pts) if total_loss_pts > 0 else 0
    avg_win = total_win_pts / all_winners if all_winners > 0 else 0
    avg_loss = total_loss_pts / all_losers if all_losers > 0 else 0
    
    # Net per 10 trades
    win_pts = (wr / 100) * 10 * avg_win
    loss_pts = ((100 - wr) / 100) * 10 * avg_loss
    net_pts = win_pts - loss_pts
    
    comparison[rr] = {
        'win_rate': wr,
        'profit_factor': pf,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'net_pts_per_10': net_pts,
        'trades': all_trades,
        'winners': all_winners,
        'losers': all_losers,
    }
    
    print(
        f" 1:{rr:<5} | {wr:>9.1f}% | "
        f"{pf:>7.2f} | "
        f"{avg_win:>9.1f}  | "
        f"{avg_loss:>9.1f}  | "
        f"{net_pts:>9.1f}"
    )

print(f"{'-'*8}-+-{'-'*10}-+-{'-'*8}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}")

# Find best
best_rr = max(comparison.keys(), key=lambda r: comparison[r]['net_pts_per_10'])
c = comparison[best_rr]

print(f"\n{'='*80}")
print(f"🏆 WINNER: 1:{best_rr} RATIO!")
print(f"{'='*80}")
print(f"\n  Based on REAL backtest data:\n")
print(f"  ✅ Win Rate:          {c['win_rate']:.1f}%")
print(f"  ✅ Profit Factor:     {c['profit_factor']:.2f}")
print(f"  ✅ Avg Win:           +{c['avg_win']:.1f} points")
print(f"  ✅ Avg Loss:          -{c['avg_loss']:.1f} points")
print(f"  ✅ Total Trades:      {c['trades']} ({c['winners']}W / {c['losers']}L)")
print(f"\n  📈 Expected per 10 Trades:")
print(f"     Net Points:        {c['net_pts_per_10']:+.1f} points")
print(f"     Net Profit:        ₹{c['net_pts_per_10'] * 65:+,.0f} (at 65 qty)")

print(f"\n{'='*80}")
print(f"\n✅ RECOMMENDATION: Set rr_ratio = {best_rr} in your UI settings!")
print(f"\n{'='*80}")

# Save results
results_file = Path("rr_backtest_results.json")
with open(results_file, 'w') as f:
    json.dump(comparison, f, indent=2)
print(f"\n📁 Detailed results saved to: {results_file}")
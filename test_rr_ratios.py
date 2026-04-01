"""Backtest different Risk-Reward ratios to find the optimal setting.

Tests 1:2, 1:2.5, and 1:3 ratios with actual historical data.
Provides REAL win rates and profit factors instead of theoretical guesses.
"""

import pandas as pd
from calibrator import _backtest_strategy_solo
from data_fetcher import fetch_intraday_data

print("="*80)
print(" " * 20 + "RISK-REWARD RATIO BACKTEST")
print("="*80)

print("\n📊 Fetching historical data...")
df = fetch_intraday_data('5minute', days_back=60)

if df is None or len(df) < 100:
    print("❌ Not enough data to backtest!")
    exit(1)

print(f"✅ Loaded {len(df)} candles across {len(set(df.index.date))} days")

# Test different R:R ratios
ratios_to_test = [2.0, 2.5, 3.0]
sl_points = 40.0  # New recommended SL
trailing_sl = 20.0  # New recommended trailing

print(f"\n🎯 Testing with:")
print(f"   SL Points:      {sl_points}")
print(f"   Trailing SL:    {trailing_sl}")
print(f"   Strategies:     All available")
print(f"   R:R Ratios:     {ratios_to_test}")

print("\n" + "="*80)
print("RUNNING BACKTESTS...")
print("="*80)

# Import strategies
import strategies.loader  # noqa: F401
from strategies.registry import all_strategies

# Get all tradeable strategies (exclude meta)
strats = [
    s for s in all_strategies()
    if s.id not in ("smart_router", "meta_router")
]

print(f"\nTesting {len(strats)} strategies with {len(ratios_to_test)} R:R ratios each...\n")

results_by_ratio = {}

for rr in ratios_to_test:
    print(f"\n{'='*80}")
    print(f"📊 TESTING R:R RATIO 1:{rr}")
    print(f"{'='*80}")
    print(f"   Target: {sl_points * rr:.0f} points (SL: {sl_points:.0f} points)\n")
    
    all_trades = 0
    all_winners = 0
    all_losers = 0
    total_win_pts = 0.0
    total_loss_pts = 0.0
    
    for strat in strats:
        print(f"   {strat.emoji} {strat.name:25s} ... ", end="", flush=True)
        
        stats = _backtest_strategy_solo(
            strat.id,
            df,
            sl_points=sl_points,
            trailing_sl=trailing_sl,
            rr_ratio=rr,
            max_trades=5  # Allow more trades per day for realistic testing
        )
        
        if "error" in stats:
            print(f"ERROR: {stats['error']}")
            continue
        
        print(
            f"{stats['win_rate']:5.1f}% WR  "
            f"({stats['winners']:3d}W/{stats['losers']:3d}L)  "
            f"PF={stats['profit_factor']:4.2f}  "
            f"{stats['trades_per_day']:.1f}/day"
        )
        
        all_trades += stats['trades']
        all_winners += stats['winners']
        all_losers += stats['losers']
        total_win_pts += stats['avg_win_pts'] * stats['winners']
        total_loss_pts += stats['avg_loss_pts'] * stats['losers']
    
    # Calculate aggregate stats
    overall_win_rate = (all_winners / all_trades * 100) if all_trades > 0 else 0
    overall_pf = (total_win_pts / total_loss_pts) if total_loss_pts > 0 else 0
    avg_win = total_win_pts / all_winners if all_winners > 0 else 0
    avg_loss = total_loss_pts / all_losers if all_losers > 0 else 0
    
    results_by_ratio[rr] = {
        'trades': all_trades,
        'winners': all_winners,
        'losers': all_losers,
        'win_rate': overall_win_rate,
        'profit_factor': overall_pf,
        'avg_win_pts': avg_win,
        'avg_loss_pts': avg_loss,
    }
    
    print(f"\n   {'─'*76}")
    print(f"   AGGREGATE: {overall_win_rate:.1f}% WR | "
          f"{all_winners}W/{all_losers}L | "
          f"PF={overall_pf:.2f} | "
          f"Avg Win: {avg_win:.1f}pts | "
          f"Avg Loss: {avg_loss:.1f}pts")
    print(f"   {'─'*76}")

# Final comparison
print(f"\n\n{'='*80}")
print(" " * 25 + "📊 FINAL COMPARISON")
print(f"{'='*80}")
print(f"\n{'RATIO':^10} | {'WIN RATE':^10} | {'PROFIT FACTOR':^15} | "
      f"{'AVG WIN':^12} | {'AVG LOSS':^12} | {'NET PTS/10':^12}")
print(f"{'-'*10}-+-{'-'*10}-+-{'-'*15}-+-{'-'*12}-+-{'-'*12}-+-{'-'*12}")

for rr in ratios_to_test:
    r = results_by_ratio[rr]
    
    # Calculate net points per 10 trades
    win_pts_per_10 = (r['win_rate'] / 100) * 10 * r['avg_win_pts']
    loss_pts_per_10 = ((100 - r['win_rate']) / 100) * 10 * r['avg_loss_pts']
    net_pts_per_10 = win_pts_per_10 - loss_pts_per_10
    
    print(
        f"  1:{rr:<6} | {r['win_rate']:>9.1f}% | {r['profit_factor']:>14.2f} | "
        f"{r['avg_win_pts']:>11.1f}  | {r['avg_loss_pts']:>11.1f}  | "
        f"{net_pts_per_10:>11.1f}"
    )

print(f"{'-'*10}-+-{'-'*10}-+-{'-'*15}-+-{'-'*12}-+-{'-'*12}-+-{'-'*12}")

# Find the best ratio
best_ratio = max(results_by_ratio.keys(), 
                 key=lambda r: (results_by_ratio[r]['win_rate'] / 100 * 10 * results_by_ratio[r]['avg_win_pts'] -
                               ((100 - results_by_ratio[r]['win_rate']) / 100) * 10 * results_by_ratio[r]['avg_loss_pts']))

print(f"\n{'='*80}")
print(f"🏆 WINNER: 1:{best_ratio} RATIO!")
print(f"{'='*80}")

r = results_by_ratio[best_ratio]
print(f"\n  ✅ Win Rate:        {r['win_rate']:.1f}%")
print(f"  ✅ Profit Factor:   {r['profit_factor']:.2f}")
print(f"  ✅ Avg Win:         {r['avg_win_pts']:.1f} points")
print(f"  ✅ Avg Loss:        {r['avg_loss_pts']:.1f} points")
print(f"  ✅ Total Trades:    {r['trades']}")
print(f"  ✅ Winners/Losers:  {r['winners']}W / {r['losers']}L")

win_pts_per_10 = (r['win_rate'] / 100) * 10 * r['avg_win_pts']
loss_pts_per_10 = ((100 - r['win_rate']) / 100) * 10 * r['avg_loss_pts']
net_pts_per_10 = win_pts_per_10 - loss_pts_per_10

print(f"\n  📈 Expected Net Points per 10 Trades: {net_pts_per_10:.1f} points")
print(f"  💰 Approx Profit per 10 Trades:       ₹{net_pts_per_10 * 65:.0f} (at 65 qty)")

print(f"\n{'='*80}")
print(f"\n✅ RECOMMENDATION: Set rr_ratio = {best_ratio} in your settings!")
print(f"\n{'='*80}")
#!/usr/bin/env python3
"""Simple R:R Ratio Backtest - Uses Existing Calibrator Logic.

This script modifies the calibrator to test 1:2, 1:2.5, and 1:3 ratios.
Provides REAL backtested results instead of theoretical guesses.
"""

import sys
import pandas as pd
from pathlib import Path
from datetime import time as dt_time

# Constants from calibrator
CAL_SL_POINTS = 40.0  # NEW recommended SL
CAL_TRAIL_SL = 20.0   # NEW recommended trailing
CAL_MAX_TRADES = 5    # Realistic daily cap
ENTRY_START = dt_time(9, 18)
EXIT_TIME = dt_time(15, 15)

def _calc_pnl(direction: str, entry: float, exit_: float) -> float:
    return (exit_ - entry) if direction == "long" else (entry - exit_)

def backtest_with_rr(
    strat_id: str,
    df: pd.DataFrame,
    rr_ratio: float,
) -> dict:
    """Run backtest with specified R:R ratio."""
    from strategies.registry import all_strategies
    
    strat = next((s for s in all_strategies() if s.id == strat_id), None)
    if strat is None:
        return {"error": f"Strategy {strat_id!r} not found"}
    
    trades, winners = 0, 0
    total_win_pts, total_loss_pts = 0.0, 0.0
    
    dates = sorted(set(df.index.date))
    for day in dates:
        day_df = df[df.index.date == day]
        if len(day_df) < 5:
            continue
        
        in_trade = False
        trades_today = 0
        entry_price = 0.0
        direction = ""
        stop_loss = 0.0
        target = 0.0
        highest = 0.0
        lowest = float("inf")
        last_exit_i = None
        
        for i in range(1, len(day_df)):
            candle_time = day_df.index[i].time()
            candle = day_df.iloc[i]
            price = float(candle["close"])
            high = float(candle["high"])
            low = float(candle["low"])
            
            if candle_time < ENTRY_START:
                continue
            
            # Force exit at end of day
            if candle_time >= EXIT_TIME and in_trade:
                pnl = _calc_pnl(direction, entry_price, price)
                trades += 1
                if pnl > 0:
                    winners += 1
                    total_win_pts += pnl
                else:
                    total_loss_pts += abs(pnl)
                in_trade = False
                continue
            
            if candle_time >= EXIT_TIME:
                continue
            
            if in_trade:
                if direction == "long":
                    highest = max(highest, high)
                    stop_loss = max(stop_loss, highest - CAL_TRAIL_SL)
                    if low <= stop_loss:
                        pnl = _calc_pnl(direction, entry_price, stop_loss)
                        trades += 1
                        if pnl > 0:
                            winners += 1
                            total_win_pts += pnl
                        else:
                            total_loss_pts += abs(pnl)
                        in_trade = False
                        last_exit_i = i
                    elif high >= target:
                        pnl = _calc_pnl(direction, entry_price, target)
                        trades += 1
                        winners += 1
                        total_win_pts += pnl
                        in_trade = False
                        last_exit_i = i
                else:  # short
                    lowest = min(lowest, low)
                    stop_loss = min(stop_loss, lowest + CAL_TRAIL_SL)
                    if high >= stop_loss:
                        pnl = _calc_pnl(direction, entry_price, stop_loss)
                        trades += 1
                        if pnl > 0:
                            winners += 1
                            total_win_pts += pnl
                        else:
                            total_loss_pts += abs(pnl)
                        in_trade = False
                        last_exit_i = i
                    elif low <= target:
                        pnl = _calc_pnl(direction, entry_price, target)
                        trades += 1
                        winners += 1
                        total_win_pts += pnl
                        in_trade = False
                        last_exit_i = i
                continue
            
            if trades_today >= CAL_MAX_TRADES:
                continue
            if last_exit_i is not None and i - last_exit_i < 1:
                continue
            
            # Evaluate strategy
            current_ts = day_df.index[i]
            lookback_df = df[df.index <= current_ts]
            try:
                signal = strat.evaluate(lookback_df)
            except Exception:
                continue
            
            if not signal.should_enter or signal.direction is None:
                continue
            
            in_trade = True
            trades_today += 1
            direction = signal.direction.value
            entry_price = price
            highest = high
            lowest = low
            
            if direction == "long":
                stop_loss = entry_price - CAL_SL_POINTS
                target = entry_price + CAL_SL_POINTS * rr_ratio
            else:
                stop_loss = entry_price + CAL_SL_POINTS
                target = entry_price - CAL_SL_POINTS * rr_ratio
    
    losers = trades - winners
    win_rate = winners / trades * 100 if trades > 0 else 0.0
    pf = total_win_pts / total_loss_pts if total_loss_pts > 0 else 0.0
    tpd = trades / max(len(dates), 1)
    avg_win = total_win_pts / winners if winners > 0 else 0.0
    avg_loss = total_loss_pts / losers if losers > 0 else 0.0
    
    return {
        "trades": trades,
        "winners": winners,
        "losers": losers,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(pf, 2),
        "trades_per_day": round(tpd, 2),
        "avg_win_pts": round(avg_win, 1),
        "avg_loss_pts": round(avg_loss, 1),
    }

# Main execution
if __name__ == "__main__":
    print("="*80)
    print(" "*20 + "🔬 R:R RATIO BACKTEST - REAL DATA")
    print("="*80)
    
    # Try to load cached data first
    cache_file = Path("nifty_5m_60d.pkl")
    
    if cache_file.exists():
        print("\n✅ Loading cached data...")
        df = pd.read_pickle(cache_file)
        print(f"   Loaded {len(df)} candles from cache")
    else:
        print("\n📊 Fetching historical data (this may take a minute)...")
        print("   Source: Kite (via auto_trader session)")
        
        try:
            from kite_integration import kite_manager
            
            if not kite_manager.is_authenticated:
                print("\n❌ ERROR: Zerodha not logged in!")
                print("\n💡 Solution:")
                print("   1. Open your web UI (http://localhost:5000)")
                print("   2. Login to Zerodha via Auto Trader tab")
                print("   3. Run this script again")
                sys.exit(1)
            
            # Fetch 60 days of 5-min data
            raw = kite_manager.get_historical_data(interval="5minute", days=60)
            
            if not raw:
                print("❌ Failed to fetch data from Kite!")
                sys.exit(1)
            
            # Convert to DataFrame
            df = pd.DataFrame(raw)
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            df.rename(columns={
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'volume'
            }, inplace=True)
            
            # Cache it
            df.to_pickle(cache_file)
            print(f"   ✅ Cached data to {cache_file}")
            
        except Exception as e:
            print(f"\n❌ Error fetching data: {e}")
            print("\n💡 Alternative: Use TrueData or run calibrator.py first")
            sys.exit(1)
    
    days = len(set(df.index.date))
    print(f"   📊 {len(df)} candles across {days} trading days")
    
    # Test parameters
    ratios_to_test = [2.0, 2.5, 3.0]
    
    print(f"\n🎯 Test Configuration:")
    print(f"   SL Points:       {CAL_SL_POINTS}")
    print(f"   Trailing SL:     {CAL_TRAIL_SL}")
    print(f"   R:R Ratios:      {ratios_to_test}")
    print(f"   Max Trades/Day:  {CAL_MAX_TRADES}")
    
    # Import strategies
    import strategies.loader
    from strategies.registry import all_strategies
    
    strats = [
        s for s in all_strategies()
        if s.id not in ("smart_router", "meta_router")
    ]
    
    print(f"\n🧪 Testing {len(strats)} strategies × {len(ratios_to_test)} ratios...\n")
    
    results_by_ratio = {}
    
    for rr in ratios_to_test:
        print(f"\n{'='*80}")
        print(f"📊 RATIO 1:{rr} (Target: {CAL_SL_POINTS * rr:.0f} points)")
        print(f"{'='*80}\n")
        
        all_trades = 0
        all_winners = 0
        total_win_pts = 0.0
        total_loss_pts = 0.0
        
        for strat in strats:
            print(f"   {strat.emoji} {strat.name:25s} ... ", end="", flush=True)
            
            stats = backtest_with_rr(strat.id, df, rr)
            
            if "error" in stats:
                print(f"SKIP")
                continue
            
            if stats['trades'] == 0:
                print(f"NO TRADES")
                continue
            
            print(
                f"{stats['win_rate']:5.1f}% | "
                f"{stats['winners']:2d}W/{stats['losers']:2d}L | "
                f"PF={stats['profit_factor']:4.2f}"
            )
            
            all_trades += stats['trades']
            all_winners += stats['winners']
            total_win_pts += stats['avg_win_pts'] * stats['winners']
            total_loss_pts += stats['avg_loss_pts'] * (stats['trades'] - stats['winners'])
        
        # Aggregate
        all_losers = all_trades - all_winners
        overall_wr = (all_winners / all_trades * 100) if all_trades > 0 else 0
        overall_pf = (total_win_pts / total_loss_pts) if total_loss_pts > 0 else 0
        avg_win = total_win_pts / all_winners if all_winners > 0 else 0
        avg_loss = total_loss_pts / all_losers if all_losers > 0 else 0
        
        results_by_ratio[rr] = {
            'trades': all_trades,
            'winners': all_winners,
            'losers': all_losers,
            'win_rate': overall_wr,
            'profit_factor': overall_pf,
            'avg_win_pts': avg_win,
            'avg_loss_pts': avg_loss,
        }
        
        print(f"\n   {'─'*76}")
        print(
            f"   TOTAL: {overall_wr:.1f}% WR | "
            f"{all_winners}W/{all_losers}L | "
            f"PF={overall_pf:.2f} | "
            f"Avg: +{avg_win:.1f} / -{avg_loss:.1f}pts"
        )
        print(f"   {'─'*76}")
    
    # Final comparison
    print(f"\n\n{'='*80}")
    print(" "*28 + "📊 FINAL RESULTS")
    print(f"{'='*80}\n")
    
    print(f"{'RATIO':^8} | {'WIN RATE':^10} | {'PROFIT':^8} | {'AVG WIN':^10} | {'AVG LOSS':^10} | {'NET/10 TRADES':^15}")
    print(f"{'-'*8}-+-{'-'*10}-+-{'-'*8}-+-{'-'*10}-+-{'-'*10}-+-{'-'*15}")
    
    best_rr = None
    best_net = -999999
    
    for rr in ratios_to_test:
        r = results_by_ratio[rr]
        
        # Net points per 10 trades
        win_pts = (r['win_rate'] / 100) * 10 * r['avg_win_pts']
        loss_pts = ((100 - r['win_rate']) / 100) * 10 * r['avg_loss_pts']
        net_pts = win_pts - loss_pts
        net_rupees = net_pts * 65  # At 65 qty
        
        marker = ""
        if net_pts > best_net:
            best_net = net_pts
            best_rr = rr
            marker = " 🏆"
        
        print(
            f" 1:{rr:<5} | {r['win_rate']:>9.1f}% | "
            f"{r['profit_factor']:>7.2f} | "
            f"{r['avg_win_pts']:>9.1f}  | "
            f"{r['avg_loss_pts']:>9.1f}  | "
            f"{net_pts:>7.1f} pts{marker}"
        )
    
    print(f"{'-'*8}-+-{'-'*10}-+-{'-'*8}-+-{'-'*10}-+-{'-'*10}-+-{'-'*15}")
    
    # Winner announcement
    r = results_by_ratio[best_rr]
    win_pts = (r['win_rate'] / 100) * 10 * r['avg_win_pts']
    loss_pts = ((100 - r['win_rate']) / 100) * 10 * r['avg_loss_pts']
    net_pts = win_pts - loss_pts
    net_rupees = net_pts * 65
    
    print(f"\n{'='*80}")
    print(f"🏆 WINNER: 1:{best_rr} RATIO!")
    print(f"{'='*80}")
    print(f"\n  Based on {days} days of REAL backtest data:\n")
    print(f"  ✅ Win Rate:          {r['win_rate']:.1f}%")
    print(f"  ✅ Profit Factor:     {r['profit_factor']:.2f}")
    print(f"  ✅ Avg Win:           +{r['avg_win_pts']:.1f} points")
    print(f"  ✅ Avg Loss:          -{r['avg_loss_pts']:.1f} points")
    print(f"  ✅ Total Trades:      {r['trades']} ({r['winners']}W / {r['losers']}L)")
    print(f"\n  📈 Expected per 10 Trades:")
    print(f"     Net Points:        {net_pts:+.1f} points")
    print(f"     Net Profit:        ₹{net_rupees:+,.0f} (at 65 qty)")
    
    print(f"\n{'='*80}")
    print(f"\n✅ RECOMMENDATION: Set rr_ratio = {best_rr} in your UI settings!")
    print(f"\n{'='*80}")
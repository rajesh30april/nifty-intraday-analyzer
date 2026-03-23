import json

print("=" * 80)
print(" " * 20 + "STOP LOSS ANALYSIS - ARE SLs TOO TIGHT?" + " " * 20)
print("=" * 80)

# Load trades
with open('.state_snapshot.json') as f:
    data = json.load(f)

trades = data['trades_today']

print(f"\n📊 CONFIGURATION:")
print("-" * 80)
print(f"  SL Points Setting:       {data['sl_points']} points")
print(f"  Trailing SL Points:      {data['trailing_sl_points']} points")
print(f"  Trail Mode:              {data['trail_mode']}")
print(f"  Trail ATR Multiplier:    {data['trail_atr_mult']}")

print(f"\n🔍 DETAILED TRADE ANALYSIS:")
print("=" * 80)

losing_trades = [t for t in trades if t.get('pnl', 0) < 0]

for i, t in enumerate(losing_trades, 1):
    entry_price = t['entry_price']
    sl_price = t['stop_loss']
    exit_price = t.get('exit_price')
    entry_prem = t['entry_premium']
    exit_prem = t.get('exit_premium')
    pnl = t.get('pnl', 0)
    reason = t.get('exit_reason', '')
    
    # Calculate SL distance
    if t['direction'] == 'short':
        sl_distance = sl_price - entry_price
    else:
        sl_distance = entry_price - sl_price
    
    # Calculate premium change
    prem_change = exit_prem - entry_prem if exit_prem else 0
    prem_change_pct = (prem_change / entry_prem * 100) if entry_prem else 0
    
    # Determine if it's a Premium SL or Nifty SL
    is_premium_sl = "Premium SL" in reason
    sl_type = "PREMIUM SL" if is_premium_sl else "NIFTY SL"
    
    print(f"\n{'=' * 80}")
    print(f"TRADE #{i} - {t['timestamp'][11:19]}")
    print(f"{'=' * 80}")
    print(f"  Direction:               {t['direction'].upper()}")
    print(f"  Entry Price:             Rs {entry_price:.2f}")
    print(f"  Stop Loss:               Rs {sl_price:.2f}")
    print(f"  Exit Price:              Rs {exit_price:.2f}")
    tight_marker = "WARNING TIGHT!" if sl_distance < 15 else "OK"
    print(f"  SL Distance:             {sl_distance:.2f} points ({tight_marker})")
    print(f"  Expected SL Distance:    {data['sl_points']} points")
    print(f"")
    print(f"  Entry Premium:           Rs {entry_prem:.2f}")
    print(f"  Exit Premium:            Rs {exit_prem:.2f}")
    print(f"  Premium Change:          Rs {prem_change:.2f} ({prem_change_pct:.1f}%)")
    print(f"")
    print(f"  P&L:                     Rs {pnl:.2f}")
    print(f"  Exit Reason:             {reason}")
    print(f"  SL Type:                 {sl_type}")
    
    # Analysis
    if sl_distance < 15:
        print(f"\n  ⚠️  WARNING: SL is VERY TIGHT ({sl_distance:.1f} points < 15 points)")
    elif sl_distance < data['sl_points'] * 0.8:
        print(f"\n  ⚠️  WARNING: SL is tighter than expected ({sl_distance:.1f} < {data['sl_points']})")
    
    if is_premium_sl:
        print(f"  💡 Note: Premium SL triggered (not Nifty price SL)")

print(f"\n{'=' * 80}")
print(f"📊 SUMMARY:")
print(f"{'=' * 80}")

# Calculate statistics
sl_distances = []
for t in losing_trades:
    if t['direction'] == 'short':
        sl_dist = t['stop_loss'] - t['entry_price']
    else:
        sl_dist = t['entry_price'] - t['stop_loss']
    sl_distances.append(sl_dist)

avg_sl_distance = sum(sl_distances) / len(sl_distances) if sl_distances else 0
min_sl_distance = min(sl_distances) if sl_distances else 0
max_sl_distance = max(sl_distances) / len(sl_distances) if sl_distances else 0

tight_sls = sum(1 for d in sl_distances if d < 15)
premium_sl_count = sum(1 for t in losing_trades if "Premium SL" in t.get('exit_reason', ''))

print(f"\n  Total Losing Trades:     {len(losing_trades)}")
print(f"  Premium SL Triggers:     {premium_sl_count}/{len(losing_trades)} ({premium_sl_count/len(losing_trades)*100:.0f}%)")
print(f"  Tight SLs (<15 pts):     {tight_sls}/{len(losing_trades)} ({tight_sls/len(losing_trades)*100:.0f}%)")
print(f"")
print(f"  Configured SL:           {data['sl_points']} points")
tighter_marker = "TIGHTER!" if avg_sl_distance < data['sl_points'] else "OK"
print(f"  Average Actual SL:       {avg_sl_distance:.1f} points ({tighter_marker})")
too_tight_marker = "TOO TIGHT!" if min_sl_distance < 10 else ""
print(f"  Tightest SL:             {min_sl_distance:.1f} points {too_tight_marker}")
print(f"  Widest SL:               {max(sl_distances):.1f} points")

print(f"\n{'=' * 80}")
print(f"💡 DIAGNOSIS:")
print(f"{'=' * 80}")

if avg_sl_distance < data['sl_points'] * 0.7:
    print(f"\n  🔴 PROBLEM DETECTED: Stop losses are TOO TIGHT!")
    print(f"")
    print(f"  Expected SL:             {data['sl_points']} points")
    print(f"  Actual Average SL:       {avg_sl_distance:.1f} points")
    print(f"  Difference:              {data['sl_points'] - avg_sl_distance:.1f} points tighter!")
    print(f"")
    print(f"  This explains why you're getting stopped out frequently!")
elif premium_sl_count > len(losing_trades) * 0.5:
    print(f"\n  ⚠️  ISSUE: Premium SL is triggering too often")
    print(f"")
    print(f"  {premium_sl_count}/{len(losing_trades)} losses were due to Premium SL")
    print(f"  Premium SL might be too aggressive")

print(f"\n{'=' * 80}")
print(f"\n🔧 RECOMMENDATIONS:")
print(f"{'=' * 80}")

if avg_sl_distance < data['sl_points'] * 0.7:
    print(f"\n1. INCREASE SL POINTS")
    print(f"   Current: {data['sl_points']} points")
    print(f"   Recommended: 40-50 points (for more breathing room)")
    print(f"")
    print(f"2. CHECK TRAILING SL LOGIC")
    print(f"   Trail mode: {data['trail_mode']}")
    print(f"   Trail ATR mult: {data['trail_atr_mult']}")
    print(f"   This might be tightening SLs too aggressively")
    print(f"")
    print(f"3. REVIEW PREMIUM SL THRESHOLD")
    print(f"   {premium_sl_count}/{len(losing_trades)} losses were Premium SL hits")
    print(f"   Consider loosening premium SL tolerance")

print(f"\n" + "=" * 80)

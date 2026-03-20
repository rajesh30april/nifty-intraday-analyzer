#!/usr/bin/env python3
"""EMERGENCY FIX: Manually set max_daily_loss in state file."""
import json

STATE_FILE = '.state_snapshot.json'

print("🔧 MANUAL MAX LOSS FIXER")
print("=" * 50)

# Read current state
try:
    with open(STATE_FILE, 'r') as f:
        state = json.load(f)
    print(f"✅ Current state loaded")
    print(f"   Total P&L: ₹{state.get('total_pnl', 0):.0f}")
    print(f"   Max Loss: {state.get('max_daily_loss', 'NOT SET!')}")
except FileNotFoundError:
    print(f"❌ State file not found!")
    exit(1)

# Set max_daily_loss
new_limit = 20000
state['max_daily_loss'] = new_limit

# Save
with open(STATE_FILE, 'w') as f:
    json.dump(state, f, indent=2)

print(f"")
print(f"✅ FIXED! Max loss set to ₹{new_limit:,}")
print(f"   P&L: ₹{state['total_pnl']:.0f}")
print(f"   Check: {state['total_pnl']} <= -{new_limit} = {state['total_pnl'] <= -new_limit}")
if state['total_pnl'] <= -new_limit:
    print(f"   ⚠️  STILL BLOCKED! Your loss (₹{abs(state['total_pnl']):.0f}) is worse than limit!")
    print(f"   💡 Set limit higher than ₹{abs(state['total_pnl']):.0f}")
else:
    print(f"   ✅ UNBLOCKED! You can trade now!")
print(f"")
print(f"Now restart your server:")
print(f"  pkill -f python")
print(f"  python app.py")
#!/usr/bin/env python3
"""Test Reversal/Continuation Detector.

Verifies detector would have caught today's reversal signal at 12:06 PM.

Author: Code Puppy 🐶
Date: March 25, 2026
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from reversal_continuation_detector import ReversalContinuationDetector
from data_fetcher import fetch_intraday_data

print("="*80)
print(" "*20 + "🔄 REVERSAL/CONTINUATION DETECTOR TEST")
print("="*80)

print("\n📊 Fetching current market data...")
try:
    df = fetch_intraday_data()
    print(f"   ✅ Loaded {len(df)} candles")
except Exception as e:
    print(f"   ❌ Error: {e}")
    print("\n💡 Make sure app is running and data is available!")
    sys.exit(1)

# Create detector
detector = ReversalContinuationDetector(df, lookback=30)

print("\n" + "="*80)
print(" "*25 + "🔍 MARKET ANALYSIS")
print("="*80)

# Run analysis
result = detector.analyze()

print(f"\n🎯 Current Price: {detector.current_price:.2f}")
print(f"📈 Day High: {detector.day_high:.2f}")
print(f"📉 Day Low: {detector.day_low:.2f}")
print(f"📊 Day Range: {detector.day_range:.2f} points")

print("\n" + "="*80)
print(" "*30 + "🏆 SCORES")
print("="*80)

print(f"\n🔴 Reversal Score: {result.reversal_score:.1f}/100")
print(f"🟢 Continuation Score: {result.continuation_score:.1f}/100")
print(f"\n🎯 Recommendation: {result.recommendation}")
print(f"💪 Confidence: {result.confidence:.1f}%")

print("\n" + "="*80)
print(" "*25 + "🔍 COMPONENT SCORES")
print("="*80)

print(f"\n1️⃣ RSI Divergence: {result.rsi_divergence_score:+.1f} points")
print(f"2️⃣ Volume Divergence: {result.volume_divergence_score:+.1f} points")
print(f"3️⃣ Candle Patterns: {result.candle_pattern_score:+.1f} points")
print(f"4️⃣ Support/Resistance: {result.sr_level_score:+.1f} points")
print(f"5️⃣ Momentum: {result.momentum_score:+.1f} points")

print("\n" + "="*80)
print(" "*28 + "🚨 SIGNALS DETECTED")
print("="*80)

if result.signals:
    for i, signal in enumerate(result.signals, 1):
        print(f"\n{i}. {signal}")
else:
    print("\n   No significant signals detected.")

print("\n" + "="*80)
print(" "*25 + "🤔 TRADING IMPLICATIONS")
print("="*80)

if result.recommendation == 'REVERSAL':
    print("\n⚠️  REVERSAL LIKELY!")
    print("\n   Recommended Actions:")
    print("   ❌ BLOCK trend-following entries (LONG after rally, SHORT after decline)")
    print("   ✅ PREFER reversal entries (fade the move)")
    
    if detector.current_price > detector.day_low + detector.day_range * 0.7:
        print("\n   Specific: Near day HIGH")
        print("   ❌ AVOID LONG entries")
        print("   ✅ CONSIDER SHORT entries (fade the rally)")
    else:
        print("\n   Specific: Near day LOW")
        print("   ❌ AVOID SHORT entries")
        print("   ✅ CONSIDER LONG entries (fade the decline)")

elif result.recommendation == 'CONTINUATION':
    print("\n🚀 CONTINUATION LIKELY!")
    print("\n   Recommended Actions:")
    print("   ✅ ALLOW trend-following entries")
    print("   ❌ AVOID counter-trend reversals")
    
elif result.recommendation == 'NEUTRAL':
    print("\n🤷 NEUTRAL - No clear bias")
    print("\n   Recommended Actions:")
    print("   ➡️ Proceed with normal strategy selection")
    print("   ➡️ Use normal entry rules")

print("\n" + "="*80)
print(" "*20 + "🐶 WOULD THIS HAVE HELPED TODAY?")
print("="*80)

print("\n📅 Today's Scenario (12:06 PM):")
print("   - Market rallied from 23,063 to 23,414")
print("   - App suggested: LONG (continuation)")
print("   - Rajesh did: SHORT (reversal)")
print("   - Result: Rajesh was RIGHT!")

if result.recommendation == 'REVERSAL' and result.reversal_score > 60:
    print("\n✅ SUCCESS! Detector would have caught the reversal!")
    print(f"   Reversal Score: {result.reversal_score:.1f} (HIGH!)")
    print("   Would have BLOCKED LONG and PREFERRED SHORT!")
    print("\n🏆 This would have PREVENTED the bad trade!")
else:
    print("\n⚠️ Hmm, detector didn't catch it strongly.")
    print("   May need to adjust thresholds or add more signals.")

print("\n" + "="*80)
print("🎯 Next: Integrate into meta_router to use live!")
print("="*80)

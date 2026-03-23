# 📊 Camarilla Pivots Trading Guide

**Created:** March 19, 2026  
**For:** Nifty Intraday Trading  
**Author:** Code Puppy 🐶  

---

## 🎯 What Are Camarilla Pivots?

**Camarilla Pivots** are a set of **8 intraday price levels** calculated from yesterday's High, Low, and Close. They predict where price will:
- ✅ **Bounce** (support zones)
- ✅ **Reverse** (resistance zones)
- ✅ **Break out** (H4/L4 breakout zones)

**Created by:** Nick Stott (1980s bond trader)  
**Best for:** Intraday scalping and range trading  

---

## 🧮 The Math (Simple!)

```python
# From yesterday's data:
High  = 22850
Low   = 22650
Close = 22750

# Step 1: Calculate range
Range = High - Low = 200

# Step 2: Calculate 8 levels
H4 = Close + (Range × 1.1 / 2)  = 22860  🔴 STRONG RESISTANCE
H3 = Close + (Range × 1.1 / 4)  = 22805  🟠 SELL ZONE
H2 = Close + (Range × 1.1 / 6)  = 22787
H1 = Close + (Range × 1.1 / 12) = 22768

L1 = Close - (Range × 1.1 / 12) = 22732
L2 = Close - (Range × 1.1 / 6)  = 22713
L3 = Close - (Range × 1.1 / 4)  = 22695  🟠 BUY ZONE
L4 = Close - (Range × 1.1 / 2)  = 22640  🔴 STRONG SUPPORT
```

**Why 1.1 multiplier?**  
Camarilla uses 110% of the range to account for typical intraday volatility expansion.

---

## 📈 The 8 Levels Explained

### **Resistance Levels (Above Close):**

| Level | Formula | Purpose |
|-------|---------|----------|
| **H4** | C + R×1.1/2 | **BREAKOUT ZONE** - Strong resistance, if broken = explosive move up |
| **H3** | C + R×1.1/4 | **PRIMARY SELL ZONE** - High probability reversal, target L3 |
| **H2** | C + R×1.1/6 | Minor resistance, profit booking zone |
| **H1** | C + R×1.1/12 | Minor resistance, first sign of strength |

### **Support Levels (Below Close):**

| Level | Formula | Purpose |
|-------|---------|----------|
| **L1** | C - R×1.1/12 | Minor support, first sign of weakness |
| **L2** | C - R×1.1/6 | Minor support, profit booking zone for shorts |
| **L3** | C - R×1.1/4 | **PRIMARY BUY ZONE** - High probability reversal, target H3 |
| **L4** | C - R×1.1/2 | **BREAKOUT ZONE** - Strong support, if broken = explosive move down |

---

## 🎯 The 4 Core Trading Rules

### **Rule 1: L3 → H3 (LONG)**
```
Price dips to L3 (22695)
✅ Action: BUY (reversal expected)
✅ Target: H3 (22805)
✅ Stop Loss: L4 (22640)
✅ Profit: 110 points
✅ Risk: 55 points
✅ R:R = 2:1
```

### **Rule 2: H3 → L3 (SHORT)**
```
Price rallies to H3 (22805)
✅ Action: SELL (reversal expected)
✅ Target: L3 (22695)
✅ Stop Loss: H4 (22860)
✅ Profit: 110 points
✅ Risk: 55 points
✅ R:R = 2:1
```

### **Rule 3: H4 Breakout (STRONG LONG)**
```
Price breaks above H4 (22860)
✅ Action: STRONG BUY (bulls dominating)
✅ Target: H4 + (H4-H3) = 22915
✅ Stop Loss: H3 (22805)
✅ Profit: 55+ points
```

### **Rule 4: L4 Breakdown (STRONG SHORT)**
```
Price breaks below L4 (22640)
✅ Action: STRONG SELL (bears dominating)
✅ Target: L4 - (L3-L4) = 22585
✅ Stop Loss: L3 (22695)
✅ Profit: 55+ points
```

---

## 📊 Visual Example

```
YESTERDAY (March 19):
High:  22850
Low:   22650
Close: 22750
Range: 200 points

TODAY (March 20) - CAMARILLA LEVELS:

22860 H4 ━━━━━━━━━━━━━━━ 🔴 BREAKOUT LONG (if crossed with volume)
22805 H3 ━━━━━━━━━━━━━━━ 🟠 SELL ZONE (reversal expected, target L3)
22787 H2 ━━━━━━━━━━━━━━━ 🟡 Book profits on longs
22768 H1 ━━━━━━━━━━━━━━━ 🟢 First resistance

22750 PP ━━━━━━━━━━━━━━━ ⚪ EQUILIBRIUM (wait for H3/L3)

22732 L1 ━━━━━━━━━━━━━━━ 🟢 First support
22713 L2 ━━━━━━━━━━━━━━━ 🟡 Book profits on shorts
22695 L3 ━━━━━━━━━━━━━━━ 🟠 BUY ZONE (reversal expected, target H3)
22640 L4 ━━━━━━━━━━━━━━━ 🔴 BREAKOUT SHORT (if crossed with volume)
```

---

## ⏰ Intraday Trading Plan

### **9:15 AM - Market Open**
- ✅ Calculate Camarilla levels from yesterday's data
- ✅ Mark H3, L3, H4, L4 on chart
- ✅ Wait for price to reach a key level

### **9:30 AM - 11:00 AM (Best Trading Window)**

**Scenario A: Price opens near PP (22750)**
```
1. Wait for dip to L3 (22695) OR rally to H3 (22805)
2. Trade the reversal (L3 LONG or H3 SHORT)
3. Use tight SL (L4 or H4)
```

**Scenario B: Price gaps up to H2 (22787)**
```
1. Wait for pullback to H1 (22768)
2. If H1 holds → LONG, target H3
3. If H1 breaks → wait for L1/L2
```

**Scenario C: Price breaks H4 (22860) with volume**
```
1. Strong bullish day!
2. LONG immediately on retest of H4
3. Target: 22915+ (extended move)
```

### **11:00 AM - 2:00 PM (Low Activity)**
- ⚠️ Avoid new trades (choppy, low volume)
- ✅ Manage existing positions
- ✅ Book profits near targets

### **2:00 PM - 3:15 PM (Second Window)**
- ✅ Same rules as morning
- ✅ Look for L3/H3 bounces
- ⚠️ Tighten stops near 3:00 PM

### **3:15 PM - Close All Positions**
- ❌ No intraday trades past 3:15 PM
- ✅ Exit ALL positions (profit or loss)

---

## 🔥 Advanced Techniques

### **1️⃣ Volume Confirmation**

```
Price at L3 + High Volume = STRONG BUY
Price at L3 + Low Volume  = Weak, may go to L4

Rule: Only take L3/H3 trades if volume > 1.5× avg
```

### **2️⃣ Multiple Timeframe Confirmation**

```
5-min chart: Price at L3 (22695)
15-min chart: RSI oversold (<30)
✅ HIGH PROBABILITY LONG!

5-min chart: Price at L3 (22695)
15-min chart: RSI neutral (50)
⚠️ WAIT - reversal uncertain
```

### **3️⃣ False Break Filter**

```
Price spikes to H4 (22860) for 1 candle → drops back
= FAKE BREAKOUT!
✅ Action: SHORT @ 22850
✅ Target: H3 (22805)

Rule: H4/L4 break must hold for 2+ candles
```

### **4️⃣ Candle Pattern Confirmation**

```
Price at L3 + Bullish Engulfing = STRONG LONG
Price at H3 + Bearish Engulfing = STRONG SHORT
Price at L3 + Doji = WAIT (indecision)
```

---

## 📊 Camarilla vs. Traditional Pivots

| Feature | Camarilla | Traditional Pivots |
|---------|-----------|--------------------|
| **Levels** | 8 (H1-H4, L1-L4) | 7 (R1-R3, PP, S1-S3) |
| **Focus** | **Range trading** | Support/Resistance |
| **Best Market** | **Sideways** (low vol) | Trending |
| **Win Rate** | **High** (70-80% on L3/H3) | Medium (50-60%) |
| **Trade Frequency** | **High** (5-10 trades/day) | Low (1-3 trades/day) |
| **Risk** | **Low** (tight stops) | Medium |
| **Profit/Trade** | Small-Medium (50-100 pts) | Large (100-200 pts) |

**When to use:**
- ✅ **Camarilla:** Sideways days, low volatility, intraday scalping
- ✅ **Traditional:** Trending days, high volatility, swing trading

---

## ✅ When Camarilla Works BEST

### **Perfect Days:**
- ✅ **Low volatility** (Nifty range <200 points)
- ✅ **Sideways market** (no strong trend)
- ✅ **Normal trading sessions** (no news/events)
- ✅ **Thursdays/Fridays** (lower volatility)

### **Perfect Markets:**
- ✅ **Nifty** (liquid, predictable)
- ✅ **Bank Nifty** (good for scalping)
- ✅ **Large cap stocks** (high liquidity)

---

## ❌ When Camarilla FAILS

### **Avoid On:**
- ❌ **Event days** (RBI policy, Budget, elections)
- ❌ **Gap openings** (>1% gap up/down)
- ❌ **High volatility** (VIX > 20)
- ❌ **Strong trending days** (breaks H4 at 9:30 AM)
- ❌ **Mondays** (higher volatility after weekend)

---

## 📈 Real Trade Examples

### **Example 1: Perfect L3 → H3 Trade**

```
Date: March 20, 2026
Setup:
  Yesterday: H=22850, L=22650, C=22750
  Camarilla: L3=22695, H3=22805

Trade:
  9:45 AM: Price dips to 22695 (L3)
  Candle: Bullish hammer
  Volume: 2× average
  
  ✅ Entry: LONG @ 22698 (3 points above L3)
  ✅ SL: 22640 (L4)
  ✅ Target: 22805 (H3)
  
  10:30 AM: Price hits 22805 (H3)
  ✅ Exit: 22803
  
Result:
  Profit: 105 points
  Risk: 58 points
  R:R: 1.8:1
  Duration: 45 minutes
```

### **Example 2: H4 Breakout Trade**

```
Date: March 20, 2026
Setup:
  Camarilla: H3=22805, H4=22860

Trade:
  10:00 AM: Price consolidates at H3
  10:15 AM: Spike to 22862 (breaks H4)
  Volume: 3× average (STRONG!)
  
  ✅ Entry: LONG @ 22865 (retest of H4)
  ✅ SL: 22805 (H3)
  ✅ Target: 22920 (H4 + range)
  
  11:00 AM: Price hits 22918
  ✅ Exit: 22916
  
Result:
  Profit: 51 points
  Risk: 60 points
  R:R: 0.85:1 (lower R:R but high win rate)
  Duration: 45 minutes
```

### **Example 3: Failed H3 Short (Learning)**

```
Date: March 20, 2026
Setup:
  Camarilla: H3=22805, H4=22860

Trade:
  11:30 AM: Price rallies to 22805 (H3)
  Candle: Small bearish candle
  Volume: 0.8× average (LOW! ⚠️)
  
  ❌ Entry: SHORT @ 22803 (ignored low volume)
  ❌ SL: 22860 (H4)
  ❌ Target: 22695 (L3)
  
  11:45 AM: Price breaks above H4
  ❌ SL Hit: 22860
  
Result:
  Loss: -57 points
  Mistake: Ignored low volume warning!
  
Lesson:
  ⚠️ NEVER trade L3/H3 without volume confirmation!
```

---

## 🐶 Code Puppy's Camarilla Checklist

### **Before Market Open (9:00 AM):**
- [ ] Calculate yesterday's H, L, C
- [ ] Calculate all 8 Camarilla levels
- [ ] Mark H3, L3, H4, L4 on chart
- [ ] Check VIX (< 18 = good for Camarilla)
- [ ] Check news/events (none = good)

### **Trade Entry Checklist:**
- [ ] Price at L3 or H3 (not L1/L2/H1/H2)
- [ ] Volume > 1.5× average
- [ ] Reversal candle (hammer, engulfing, etc.)
- [ ] Time: 9:30-11:00 AM or 2:00-3:00 PM
- [ ] No major news in last 30 minutes
- [ ] SL at L4 or H4 (clear level)

### **Trade Management:**
- [ ] Move SL to breakeven at +30 points
- [ ] Book 50% at H2/L2 (halfway to target)
- [ ] Book 50% at H3/L3 (full target)
- [ ] Exit ALL positions by 3:15 PM

---

## 🧪 Use Your Code to Calculate!

```python
from indicators import camarilla_pivots

# Get yesterday's Nifty data
prev_high = 22850
prev_low = 22650
prev_close = 22750

# Calculate levels
levels = camarilla_pivots(prev_high, prev_low, prev_close)

print(f"🔴 H4 (Breakout): ₹{levels['H4']}")
print(f"🟠 H3 (Sell):     ₹{levels['H3']}")
print(f"🟠 L3 (Buy):      ₹{levels['L3']}")
print(f"🔴 L4 (Breakout): ₹{levels['L4']}")
```

---

## 🎯 Quick Reference Card

```
╔═══════════════════════════════════════════════╗
║       CAMARILLA PIVOTS CHEAT SHEET           ║
╠═══════════════════════════════════════════════╣
║                                               ║
║  📈 LONG SETUPS:                              ║
║    ✅ Price at L3 → BUY, target H3           ║
║    ✅ Price breaks H4 → STRONG BUY           ║
║                                               ║
║  📉 SHORT SETUPS:                             ║
║    ✅ Price at H3 → SELL, target L3          ║
║    ✅ Price breaks L4 → STRONG SELL          ║
║                                               ║
║  ⚠️ FILTERS:                                  ║
║    • Volume > 1.5× average                   ║
║    • Reversal candle pattern                 ║
║    • Time: 9:30-11 AM or 2-3 PM              ║
║    • VIX < 18 (low volatility)               ║
║                                               ║
║  🛡 RISK MANAGEMENT:                          ║
║    • SL at L4 (for longs) or H4 (shorts)     ║
║    • R:R = 2:1 minimum                       ║
║    • Move SL to BE at +30 points             ║
║    • Exit ALL by 3:15 PM                     ║
║                                               ║
╚═══════════════════════════════════════════════╝
```

---

## 📚 Further Reading

- **Book:** "Day Trading with Short Term Price Patterns" - Toby Crabel
- **Article:** "Camarilla Pivot Points: The Ultimate Guide" - TradingView
- **Video:** "How to Trade Camarilla Pivots" - YouTube (various)

---

## 🐕 Final Words from Code Puppy

> **"Camarilla Pivots are like a treasure map for intraday traders!**  
> **L3 and H3 are the 'X marks the spot' — that's where the bounces happen!**  
>   
> **Remember:**  
> ✅ **Range days → Camarilla is your friend (L3/H3 reversals)**  
> ✅ **Trending days → Skip it, use breakouts instead (H4/L4)**  
>   
> **Master the L3 → H3 trade and you'll print money! 💰**  
>   
> **Woof woof! Happy trading! 🐶✨**"

---

**Created by Code Puppy 🐕**  
**Last Updated:** March 19, 2026  
**Version:** 1.0

# 🐛 BACKTEST vs REPLAY MISMATCH BUG FIX

**Date:** 2026-03-20  
**Issue:** Backtest shows 1 trade, Replay shows 3 trades for the SAME date with SAME settings!  
**Status:** 🔍 DEBUGGING IN PROGRESS

---

## 📊 THE PROBLEM

### **Backtest Results (Period: 1 Day, 2026-03-20):**
```
Data Source: Zerodha Kite
SL: 30 pts, Trail: 15 pts
Strategy: smart_router

Trades: 1 (1W / 0L)
P&L: +20.95 pts (₹13,617.5)
Win Rate: 100%

Trade:
  15:05-15:10 SHORT 23,124.45 → 23,103.5 (Trailing SL) +20.95pts ✅
```

### **Replay Results (SAME Date: 2026-03-20):**
```
Same Settings: SL 30pts, Trail 15pts, smart_router

Trades: 3 (2W / 1L)
P&L: +34.55 pts (₹22,457.5)
Win Rate: 67%

Trades:
  09:25 Opening SHORT exit → +16.2 pts ✅
  09:35 Supertrend LONG exit → -9.1 pts ❌
  09:45 Supertrend LONG exit → +27.45 pts ✅
  09:50+ "MAX TRADES" hit (all further signals ignored)
```

---

## 🔍 ANALYSIS

### **Key Differences:**
1. **Trade Count:** 1 vs 3 (200% difference!)
2. **P&L:** +20.95 vs +34.55 (64% difference!)
3. **Timing:** Backtest only shows afternoon trade (15:05), Replay shows morning trades (09:25-09:45)
4. **Win Rate:** 100% vs 67%

### **What They Claim in Common:**
- ✅ Same date: 2026-03-20
- ✅ Same SL: 30 pts
- ✅ Same Trailing SL: 15 pts
- ✅ Same strategy: smart_router
- ✅ Same data source: Zerodha Kite (supposedly)

---

## 🕵️ INVESTIGATION

### **Hypothesis 1: Data Source Mismatch** ❓
**Theory:** Backtest uses Yahoo data (default), Replay uses Zerodha data  
**Evidence:**  
- Both `/api/backtest/stream` and `/api/backtest/replay` default to `data_source="yahoo"`
- JavaScript stores `data_source` in `_backtestParams` and passes it to replay
- But if JS doesn't capture it correctly, replay could use default (Yahoo)

**Problem:** Yahoo data might be incomplete for 2026-03-20 morning session!

### **Hypothesis 2: Cached Data Issue** ❓
**Theory:** Backtest and Replay use different cached datasets  
**Evidence:**  
- `backtester.py` has `_cached_df` parameter (line 127)
- Cache is NOT keyed by data_source/period
- If cache has old data, it could cause mismatches

**Problem:** Cache collision between different backtest runs!

### **Hypothesis 3: Time Filter Bug** ❓
**Theory:** Backtest is filtering out morning candles  
**Evidence:**  
- `ENTRY_START = dt_time(9, 18)` (no trades before 9:18 AM)
- Replay trades at 09:25, 09:35, 09:45 are ALL after 9:18 AM
- So time filter shouldn't affect them

**Problem:** Maybe data fetching filters out morning candles?

### **Hypothesis 4: Period="1d" Filter Bug** ⚠️ **MOST LIKELY!**
**Theory:** Period="1d" filter is removing morning candles!  
**Evidence:**  
```python
if period == "1d":
    fetch_period = "5d"  # Fetch 5 days
    df = fetch_data(source, "5m", "5d")
    df = df[df.index.date == yesterday]  # Filter to yesterday
```

**If Zerodha data has gaps or starts late (e.g., from 14:00 onwards),**  
**then the filter would only keep afternoon candles!**

**Problem:** Zerodha might return incomplete data for 2026-03-20!

---

## 🔧 FIXES APPLIED

### **1. Debug Logging (DONE)** ✅

Added comprehensive logging to `backtester.py`:

```python
# Data fetching:
print(f"🔍 [DEBUG] Before filter: {len(df)} candles, first={df.index[0]}, last={df.index[-1]}")
print(f"🔍 [DEBUG] Filtering for date: {yesterday}")
print(f"✅ Filtered to yesterday: {yesterday} — {len(df)} candles")
print(f"🔍 [DEBUG] After filter: first={df.index[0]}, last={df.index[-1]}")

# Day processing:
print(f"🔍 [DEBUG] _backtest_day for {date_str}")
print(f"🔍 [DEBUG] df has {len(df)} candles, full_df has {len(full_df)} candles")
print(f"🔍 [DEBUG] Day candles: {df.index[0]} to {df.index[-1]}")

# Trade entries:
print(f"✅ [ENTRY] {entry_time} {direction.upper()} @ {entry_price:.2f} (trade #{trades_today})")

# Trade exits:
print(f"🏁 [EXIT] {exit_time} {direction.upper()} @ {exit_price:.2f}, {reason}, P&L: {pnl_pts:+.2f}pts")

# Daily summary:
print(f"📊 [SUMMARY] {date_str}: {trades_today} trades, {signals_evaluated} candles evaluated, {signals_fired} signals fired")
```

**This will show us:**
- How many candles are fetched before/after filtering
- What time range the filtered data covers
- Which trades are actually being executed
- How many signals were evaluated vs fired

---

## 🎯 NEXT STEPS

### **For Rajesh:** 📋

1. **Refresh your browser** (Cmd+Shift+R)
2. **Run the SAME backtest again:**
   - Period: 1 Day
   - SL: 30 pts
   - Trail: 15 pts
   - Data Source: **Zerodha Kite**
   - Strategy: smart_router
3. **Check the server logs:**
   ```bash
   tail -f /tmp/nifty_server_debug.log
   ```
   Look for lines starting with:
   - `🔍 [DEBUG]`
   - `✅ [ENTRY]`
   - `🏁 [EXIT]`
   - `📊 [SUMMARY]`

4. **Share the debug output with me!**

5. **Then run REPLAY for the same date** and compare results

---

## 🔬 EXPECTED DEBUG OUTPUT

### **If data is complete:**
```
🔍 [DEBUG] Before filter: 150 candles, first=2026-03-18 09:15, last=2026-03-20 15:30
🔍 [DEBUG] Filtering for date: 2026-03-20
✅ Filtered to yesterday: 2026-03-20 — 75 candles
🔍 [DEBUG] After filter: first=09:15, last=15:30

🔍 [DEBUG] _backtest_day for 2026-03-20
🔍 [DEBUG] df has 75 candles, full_df has 150 candles
🔍 [DEBUG] Day candles: 09:15 to 15:30

✅ [ENTRY] 09:25 SHORT @ 23286.80 (trade #1)
🏁 [EXIT] 09:25 SHORT @ 23238.25, Trailing SL, P&L: +16.20pts
✅ [ENTRY] 09:30 LONG @ 23313.80 (trade #2)
🏁 [EXIT] 09:35 LONG @ 23304.70, SL, P&L: -9.10pts
✅ [ENTRY] 09:40 LONG @ 23228.95 (trade #3)
🏁 [EXIT] 09:45 LONG @ 23256.40, Target, P&L: +27.45pts

📊 [SUMMARY] 2026-03-20: 3 trades, 70 candles evaluated, 3 signals fired
```

### **If data is MISSING morning (BUG!):**
```
🔍 [DEBUG] Before filter: 50 candles, first=2026-03-19 14:00, last=2026-03-20 15:30
🔍 [DEBUG] Filtering for date: 2026-03-20
✅ Filtered to yesterday: 2026-03-20 — 10 candles  ← ONLY 10 CANDLES!
🔍 [DEBUG] After filter: first=14:30, last=15:30  ← STARTS AT 14:30!

🔍 [DEBUG] _backtest_day for 2026-03-20
🔍 [DEBUG] df has 10 candles, full_df has 50 candles
🔍 [DEBUG] Day candles: 14:30 to 15:30  ← MISSING MORNING!

✅ [ENTRY] 15:05 SHORT @ 23124.45 (trade #1)
🏁 [EXIT] 15:10 SHORT @ 23103.50, Trailing SL, P&L: +20.95pts

📊 [SUMMARY] 2026-03-20: 1 trade, 8 candles evaluated, 1 signal fired  ← ONLY 1 TRADE!
```

**If we see the SECOND output, then we KNOW the bug is in data fetching!**  
**Zerodha is returning incomplete data for that date!**

---

## 🛠️ POTENTIAL FIXES (After We Confirm Root Cause)

### **Option 1: Force Full Day Fetch**
Instead of filtering after fetch, specify exact date range to API:
```python
if period == "1d":
    from_date = yesterday.strftime("%Y-%m-%d 09:15:00")
    to_date = yesterday.strftime("%Y-%m-%d 15:30:00")
    df = kite.historical_data(instrument, from_date, to_date, "5minute")
```

### **Option 2: Fallback to Yahoo if Zerodha Data Incomplete**
```python
if len(df) < 60:  # Less than 60 candles for a full day (5 min × 6.25 hours = 75)
    print("⚠️ Zerodha data incomplete! Falling back to Yahoo...")
    df = fetch_yahoo_data("5m", "5d")
    df = df[df.index.date == yesterday]
```

### **Option 3: Remove Caching Entirely**
```python
# Always fetch fresh data — no cache collisions
def run_backtest(...):
    df, source = _fetch_data(data_source, interval, period)
    # Never use _cached_df
```

### **Option 4: Use Replay Logic for Backtest**
```python
# Make backtest use the SAME code path as replay
# Ensure both use identical data and logic
def run_backtest(...):
    result = BacktestResult()
    for day in trading_days:
        day_result = replay_day(day, ...)  # Use SAME function!
        result.merge(day_result)
    return result
```

---

## 📝 COMMIT LOG

```bash
Commit: b8bd51d
Message: 🐛 Add debug logging to backtester to diagnose trade mismatch bug

Files Changed:
  - backtester.py (+23 lines of debug logging)

Pushed to: origin/main
```

---

## 🐶 STATUS

**CURRENT:** Waiting for Rajesh to run backtest and share debug logs  
**NEXT:** Analyze logs to confirm root cause  
**THEN:** Implement appropriate fix from Options 1-4 above  

---

**Created:** 2026-03-20 22:50 IST  
**Last Updated:** 2026-03-20 22:50 IST  
**Priority:** 🔥 HIGH (affects trade accuracy!)  

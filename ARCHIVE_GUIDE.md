# 📁 Trade Archive & Historical Data Guide

**Never Lose Your Trade History Again!** ✅  
**Complete Documentation for Archive System**

---

## 🎯 **What This Does:**

```
OLD BEHAVIOR (Before Archive):
  trade_log.json ← Overwritten daily ❌
  Yesterday's trades? GONE! 💀
  
NEW BEHAVIOR (With Archive):
  trade_log.json ← Today's trades
  archives/
    ├── trade_log_2026-03-23.json
    ├── trade_log_2026-03-22.json
    └── trade_log_2026-03-21.json
  
  Historical data preserved FOREVER! ✅
```

---

## ✨ **Features:**

```
✅ Automatic daily archiving (at midnight)
✅ Manual archive trigger anytime
✅ Historical trade queries
✅ Last N days statistics
✅ Monthly performance summaries
✅ Automatic cleanup (keeps last 90 days)
✅ Zero configuration needed!
✅ Backward compatible (existing code still works!)
```

---

## 📂 **File Structure:**

### **Before (Old):**

```
/nifty-intraday-analyzer/
  ├── trade_log.json         ← Overwritten daily
  └── .state_snapshot.json
```

### **After (Enhanced):**

```
/nifty-intraday-analyzer/
  ├── trade_log.json         ← Today's trades
  ├── .state_snapshot.json
  ├── data_manager.py        ← NEW! Archive engine
  ├── archives/              ← NEW! Historical trades
  │   ├── trade_log_2026-03-23.json
  │   ├── trade_log_2026-03-22.json
  │   └── trade_log_2026-03-21.json
  └── summaries/             ← NEW! Monthly reports
      ├── monthly_2026-03.json
      └── monthly_2026-02.json
```

---

## 🔄 **How Auto-Archive Works:**

### **Automatic (No Action Needed!):**

```python
# At midnight (00:00:00):

1. Auto-trader loop detects date change
   Old date: 2026-03-23
   New date: 2026-03-24
   
2. Archives yesterday's trades:
   trade_log.json → archives/trade_log_2026-03-23.json
   
3. Cleanup old archives:
   Delete files older than 90 days
   
4. Ready for new day:
   trade_log.json ← New empty file
   
5. Continue trading!
   ✅ Yesterday's data saved!
   ✅ Today starts fresh!
```

---

## 🛠️ **Manual Archive:**

### **Method 1: Python Code**

```python
from data_manager import archive_today_trades

# Archive today's trades
result = archive_today_trades()
print(result['message'])
# ✅ Archived 7 trades to trade_log_2026-03-23.json

# Archive specific date
result = archive_today_trades("2026-03-22")
print(result['message'])
# ✅ Archived 5 trades to trade_log_2026-03-22.json
```

### **Method 2: API Call**

```bash
# Archive today
curl -X POST http://localhost:8000/api/archive/manual

# Archive specific date
curl -X POST "http://localhost:8000/api/archive/manual?date=2026-03-22"
```

---

## 🔍 **Querying Historical Data:**

### **1. Get Trades for Specific Date:**

```python
from data_manager import get_trades_for_date

# Get March 23 trades
trades = get_trades_for_date("2026-03-23")

if trades:
    print(f"Date: {trades['date']}")
    print(f"Total P&L: ₹{trades['total_pnl']}")
    print(f"Trades: {len(trades['trades'])}")
```

**API:**
```bash
curl http://localhost:8000/api/archive/trades/2026-03-23
```

### **2. Last N Days Statistics:**

```python
from data_manager import get_last_n_days_stats

stats = get_last_n_days_stats(7)

print(f"Period: {stats['period']}")
print(f"Total Trades: {stats['total_trades']}")
print(f"Win Rate: {stats['win_rate']:.1f}%")
print(f"Total P&L: ₹{stats['total_pnl']}")
```

**API:**
```bash
curl http://localhost:8000/api/archive/stats/last-n-days/7
```

### **3. Monthly Summary:**

```python
from data_manager import get_monthly_stats

stats = get_monthly_stats(2026, 3)

print(f"Period: {stats['period']}")
print(f"Trading Days: {stats['trading_days']}")
print(f"Total Trades: {stats['total_trades']}")
print(f"Win Rate: {stats['win_rate']:.1f}%")
print(f"Total P&L: ₹{stats['total_pnl']}")
print(f"Best Day: {stats['best_day']} (₹{stats['best_day_pnl']})")
```

**API:**
```bash
curl http://localhost:8000/api/archive/stats/monthly/2026/3
```

---

## 📊 **Using the Data Manager Class:**

```python
from data_manager import TradeDataManager

# Create manager instance
dm = TradeDataManager()

# Archive today
result = dm.archive_today()
print(result['message'])

# Get last 7 days
stats = dm.last_n_days_stats(7)
print(f"Win Rate: {stats['win_rate']:.1f}%")

# Get monthly stats
stats = dm.monthly_stats(2026, 3)
print(f"Total P&L: ₹{stats['total_pnl']}")

# Cleanup old archives
result = dm.cleanup_old(90)
print(result['message'])
```

---

## 🐶 **Code Puppy Says:**

> **"Rajesh, your trade history is now SAFE!"** 🐕
>   
> **What Changed:**  
> Before: Daily data overwritten ❌  
> After: Historical data preserved! ✅  
>   
> **How It Works:**  
> - Midnight rolls over → Auto-archive!  
> - Yesterday's trades → Saved forever!  
> - Cleanup old files → Automatic!  
> - New day → Fresh start!  
>   
> **You Get:**  
> ✅ Last 7 days stats  
> ✅ Monthly reports  
> ✅ Historical queries  
> ✅ Performance tracking  
>   
> **Zero Work Needed:**  
> Everything automatic!  
> Just trade and forget!  
>   
> **Woof woof! Your trading history is immortal now! 🐶✨**

---

**Created by Code Puppy 🐶**  
**Date:** March 19, 2026  
**Status:** ✅ **ACTIVE & ARCHIVING!**  

**Never lose a trade again! 🚀**

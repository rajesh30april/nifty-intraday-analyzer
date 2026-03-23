# 🚀 Nifty Auto-Trader - Complete Startup Guide

**Quick Start:** Open terminal, run one command, start trading! ✅  
**Complete Guide:** Everything you need to know about starting your app!  

---

## ⚡ **SUPER QUICK START (Copy & Paste)**

### **Option 1: Start Now (Manual)**

```bash
cd /Users/r0s0iv3/nifty-intraday-analyzer
./run_persistent.sh
```

Then open: **http://localhost:8000**

### **Option 2: Install Service (Auto-Start Forever)**

```bash
cd /Users/r0s0iv3/nifty-intraday-analyzer
./service.sh install
```

Then open: **http://localhost:8000**

---

## 📋 **Table of Contents**

1. [Prerequisites](#prerequisites)
2. [Starting Methods](#starting-methods)
3. [Step-by-Step Instructions](#step-by-step-instructions)
4. [Accessing the App](#accessing-the-app)
5. [Stopping the App](#stopping-the-app)
6. [Troubleshooting](#troubleshooting)
7. [Daily Usage](#daily-usage)

---

## ✅ **Prerequisites**

### **Before Starting, Check:**

```bash
# 1. Check if you're in the right directory
pwd
# Should show: /Users/r0s0iv3/nifty-intraday-analyzer

# 2. Check if virtual environment exists
ls -la .venv
# Should show: .venv directory

# 3. Check if dependencies are installed
.venv/bin/python -c "import fastapi; print('✅ Dependencies OK')"
# Should show: ✅ Dependencies OK
```

### **If Any Checks Fail:**

```bash
# Navigate to project
cd /Users/r0s0iv3/nifty-intraday-analyzer

# Create virtual environment (if missing)
python3 -m venv .venv

# Install dependencies (if missing)
.venv/bin/pip install -r requirements.txt
```

---

## 🎯 **Starting Methods**

### **Three Ways to Start:**

```
┌─────────────────────────────────────────────────────────┐
│  Method 1: Quick Manual Start                          │
├─────────────────────────────────────────────────────────┤
│  ./run_persistent.sh                                    │
│                                                         │
│  ✅ Prevents Mac from sleeping                          │
│  ✅ Starts immediately                                  │
│  ❌ Need to keep terminal open                          │
│  ❌ Stops when you close terminal                       │
│  ❌ Doesn't auto-start on reboot                        │
│                                                         │
│  Best for: Testing, one-time use                       │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  Method 2: Persistent Service (RECOMMENDED)            │
├─────────────────────────────────────────────────────────┤
│  ./service.sh install                                   │
│                                                         │
│  ✅ Prevents Mac from sleeping                          │
│  ✅ Auto-starts on login                                │
│  ✅ Runs in background (close terminal safely)          │
│  ✅ Auto-restarts if crashes                            │
│  ✅ Survives Mac restart                                │
│                                                         │
│  Best for: Daily trading, production use               │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  Method 3: Simple Start (No Sleep Prevention)          │
├─────────────────────────────────────────────────────────┤
│  .venv/bin/python start.py                              │
│                                                         │
│  ✅ Simple and quick                                    │
│  ❌ Mac CAN sleep (interrupts trading!)                 │
│  ❌ Need to keep terminal open                          │
│  ❌ No auto-restart                                     │
│                                                         │
│  Best for: Development, debugging                      │
└─────────────────────────────────────────────────────────┘
```

---

## 📝 **Step-by-Step Instructions**

### **Method 1: Quick Manual Start**

#### **Step 1: Open Terminal**

```bash
# On Mac:
# Press: Cmd + Space
# Type: Terminal
# Press: Enter
```

#### **Step 2: Navigate to Project**

```bash
cd /Users/r0s0iv3/nifty-intraday-analyzer
```

#### **Step 3: Start Server**

```bash
./run_persistent.sh
```

#### **Expected Output:**

```
🔄 Stopping existing instances...
🚀 Starting Nifty Auto-Trader (persistent mode)...
📍 Server will run at: http://localhost:8000
💡 Your Mac will NOT sleep while server is running!

To stop: Press Ctrl+C or run: lsof -ti:8000 | xargs kill

♻️  orders_placed restored from log: 6
🔄 [RECOVERY] ✅ State restored: 6 trades today, PnL=₹+1,560
🤖 Auto-trader loop started — synced to 5-min candle closes
```

#### **Step 4: Keep Terminal Open**

```
⚠️  IMPORTANT:
- Do NOT close this terminal!
- Server will stop if you close it!
- Press Ctrl+C to stop server
```

---

### **Method 2: Persistent Service (RECOMMENDED)**

#### **Step 1: Open Terminal**

```bash
# On Mac:
# Press: Cmd + Space
# Type: Terminal
# Press: Enter
```

#### **Step 2: Navigate to Project**

```bash
cd /Users/r0s0iv3/nifty-intraday-analyzer
```

#### **Step 3: Install Service (One-Time)**

```bash
./service.sh install
```

#### **Expected Output:**

```
📦 Installing Nifty Auto-Trader service...
✅ Service file installed: /Users/r0s0iv3/Library/LaunchAgents/com.nifty.autotrader.plist
✅ Service loaded and started!

🎯 Service will:
   - Start automatically on login
   - Keep running in background
   - Prevent Mac from sleeping
   - Auto-restart if it crashes

📍 Server running at: http://localhost:8000
📝 Logs: /Users/r0s0iv3/nifty-intraday-analyzer/logs/autotrader.log
```

#### **Step 4: Verify Service**

```bash
./service.sh status
```

#### **Expected Output:**

```
📊 Nifty Auto-Trader Service Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Service is LOADED
✅ Server is RUNNING (PID: 12345)
📍 URL: http://localhost:8000

📝 Recent logs:
♻️  orders_placed restored from log: 6
🔄 [RECOVERY] ✅ State restored: 6 trades today, PnL=₹+1,560
🤖 Auto-trader loop started — synced to 5-min candle closes
```

#### **Step 5: Close Terminal Safely**

```
✅ You can NOW close the terminal!
✅ Service keeps running in background!
✅ Will auto-start when you login!
```

---

### **Method 3: Simple Start (No Sleep Prevention)**

#### **Step 1: Open Terminal**

```bash
cd /Users/r0s0iv3/nifty-intraday-analyzer
```

#### **Step 2: Activate Virtual Environment**

```bash
source .venv/bin/activate
```

#### **Step 3: Start Server**

```bash
python start.py
```

#### **Expected Output:**

```
♻️  orders_placed restored from log: 6
🔄 [RECOVERY] ✅ State restored: 6 trades today, PnL=₹+1,560
🤖 Auto-trader loop started — synced to 5-min candle closes
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

#### **Step 4: Keep Terminal Open**

```
⚠️  IMPORTANT:
- Do NOT close this terminal!
- Mac CAN sleep (will interrupt trading!)
- Use Method 1 or 2 for production!
```

---

## 🌐 **Accessing the App**

### **Step 1: Open Browser**

```
Open any browser:
  - Chrome
  - Safari
  - Firefox
  - Edge
```

### **Step 2: Navigate to App**

```
Enter URL:
  http://localhost:8000

Or:
  http://127.0.0.1:8000
```

### **Step 3: You Should See:**

```
┌─────────────────────────────────────────────┐
│  📊 Nifty Auto-Trader Dashboard            │
├─────────────────────────────────────────────┤
│                                             │
│  🎯 Auto-Trader Section                     │
│    [START] [STOP] [SETTINGS]               │
│                                             │
│  📈 Live Chart                              │
│  📊 Pattern Analysis                        │
│  📝 Event Log                               │
│  💰 Trade History                           │
│                                             │
└─────────────────────────────────────────────┘
```

### **Step 4: Start Auto-Trader**

```
1. Look for: "🎯 Auto-Trader" section
2. If it shows: "⚠️ Auto-trader is NOT running"
3. Click: [START] button
4. Auto-trader will begin scanning for signals!
```

---

## ⏹️ **Stopping the App**

### **If Started with Method 1 (run_persistent.sh):**

```bash
# In the terminal where it's running:
Press: Ctrl + C

# Or from another terminal:
cd /Users/r0s0iv3/nifty-intraday-analyzer
lsof -ti:8000 | xargs kill -9
```

### **If Started with Method 2 (service.sh install):**

```bash
cd /Users/r0s0iv3/nifty-intraday-analyzer

# Stop service
./service.sh stop

# Or uninstall completely
./service.sh uninstall
```

### **If Started with Method 3 (python start.py):**

```bash
# In the terminal where it's running:
Press: Ctrl + C
```

---

## 🐛 **Troubleshooting**

### **Problem: "Port 8000 already in use"**

```bash
# Find process using port 8000
lsof -i:8000

# Kill the process
lsof -ti:8000 | xargs kill -9

# Wait 2 seconds
sleep 2

# Try starting again
./run_persistent.sh
```

### **Problem: "Module not found" or Import Errors**

```bash
# Reinstall dependencies
cd /Users/r0s0iv3/nifty-intraday-analyzer
.venv/bin/pip install -r requirements.txt

# Try starting again
./run_persistent.sh
```

### **Problem: "Cannot connect to http://localhost:8000"**

```bash
# Check if server is running
lsof -i:8000

# If nothing shown, server is not running
# Start it:
./run_persistent.sh

# If server is running but still can't connect:
# Try: http://127.0.0.1:8000
# Or clear browser cache
```

### **Problem: "Mac still going to sleep"**

```bash
# Check if caffeinate is running
ps aux | grep caffeinate

# Should show:
# caffeinate -dims python start.py

# If NOT running:
# Make sure you used Method 1 or Method 2
# Method 3 does NOT prevent sleep!

# Restart with:
./run_persistent.sh
# OR
./service.sh restart
```

### **Problem: "Service won't start"**

```bash
# Check service status
./service.sh status

# View error logs
tail -50 logs/autotrader.error.log

# Uninstall and reinstall
./service.sh uninstall
sleep 2
./service.sh install
```

### **Problem: "Auto-trader not trading"**

```bash
# 1. Check if server is running
./service.sh status

# 2. Open browser: http://localhost:8000

# 3. Check Auto-Trader section:
#    - If shows "NOT running" → Click START
#    - If shows "RUNNING" → Check event log for signals

# 4. Check settings:
#    - Click SETTINGS button
#    - Verify strategy is selected
#    - Verify max trades not reached
#    - Verify max loss not hit
```

---

## 📅 **Daily Usage**

### **Morning Routine (If Using Service):**

```
1. Wake up Mac / Login
   ✅ Service auto-starts!
   
2. Open browser: http://localhost:8000
   ✅ Dashboard loads!
   
3. Check Auto-Trader status
   - If "NOT running" → Click START
   - If "RUNNING" → You're good!
   
4. Verify settings
   - Click SETTINGS
   - Check strategy, SL, RR, etc.
   
5. Monitor trades
   - Watch event log
   - Check P&L
   - Review positions
```

### **Morning Routine (If Using Manual Start):**

```
1. Open Terminal
   
2. Run command:
   cd /Users/r0s0iv3/nifty-intraday-analyzer
   ./run_persistent.sh
   
3. Open browser: http://localhost:8000
   
4. Click START on Auto-Trader
   
5. Keep terminal open all day!
```

### **Evening Routine:**

```
1. Check final P&L
   - View dashboard
   - Export trade history
   
2. Review trades
   - What worked?
   - What didn't?
   
3. Stop auto-trader
   - Click STOP button in browser
   - Or let it run overnight (if service)
   
4. Optional: Stop server
   - If using service: ./service.sh stop
   - If manual: Press Ctrl+C
   - Or leave it running!
```

---

## 🔄 **After Mac Restart**

### **If Installed as Service:**

```
1. Login to Mac
   ✅ Service auto-starts!
   
2. Wait 10 seconds
   ✅ Server is ready!
   
3. Open browser: http://localhost:8000
   ✅ Dashboard loads!
   
4. Click START on Auto-Trader
   ✅ Ready to trade!
```

### **If Not Using Service:**

```
1. Login to Mac
   
2. Open Terminal
   
3. Run:
   cd /Users/r0s0iv3/nifty-intraday-analyzer
   ./run_persistent.sh
   
4. Open browser: http://localhost:8000
   
5. Click START on Auto-Trader
```

---

## 📊 **Service Management Commands**

### **All Commands:**

```bash
cd /Users/r0s0iv3/nifty-intraday-analyzer

# Install service (one-time)
./service.sh install

# Check status
./service.sh status

# View live logs
./service.sh logs

# Start service
./service.sh start

# Stop service
./service.sh stop

# Restart service
./service.sh restart

# Uninstall service
./service.sh uninstall
```

### **Common Workflows:**

```bash
# First time setup
./service.sh install

# Daily check
./service.sh status

# Watch what's happening
./service.sh logs

# Something wrong? Restart
./service.sh restart

# Update code? Stop, pull, restart
./service.sh stop
git pull
./service.sh start
```

---

## ✅ **Verification Checklist**

### **After Starting, Verify:**

```bash
□ Server is running
  Command: ./service.sh status
  Expected: ✅ Server is RUNNING
  
□ Port 8000 is listening
  Command: lsof -i:8000
  Expected: Shows Python process
  
□ Browser can connect
  URL: http://localhost:8000
  Expected: Dashboard loads
  
□ Auto-trader can start
  Click: START button
  Expected: Shows "RUNNING"
  
□ Logs are clean
  Command: ./service.sh logs
  Expected: No errors, sees heartbeat
  
□ Sleep prevention active (if using Method 1 or 2)
  Command: ps aux | grep caffeinate
  Expected: Shows caffeinate process
```

---

## 🎯 **Quick Reference**

### **File Locations:**

```
Project:
  /Users/r0s0iv3/nifty-intraday-analyzer/

Scripts:
  ./run_persistent.sh      - Manual runner
  ./service.sh             - Service manager
  ./start.py               - Main server file

Logs:
  logs/autotrader.log      - Main log
  logs/autotrader.error.log - Error log

Config:
  .env                     - Environment variables
  trade_log.json           - Trade history
  .state_snapshot.json     - Auto-trader state
```

### **URLs:**

```
Main Dashboard:
  http://localhost:8000
  http://127.0.0.1:8000

Auto-Trader:
  http://localhost:8000/#auto-trader

Crude Oil:
  http://localhost:8000/#crude-trader

API:
  http://localhost:8000/docs
```

### **Ports:**

```
8000 - Main app (start.py)
5000 - Alternative (if port 8000 busy)
```

---

## 📖 **Additional Resources**

```
Full Documentation:
  KEEP_RUNNING_GUIDE.md   - Sleep prevention details
  HEARTBEAT_ENHANCED.md   - Auto-trader features
  VOLUME_FILTER_GUIDE.md  - Strategy details
  TRAILING_SL_AND_RR_GUIDE.md - SL/RR management

GitHub:
  https://github.com/rajesh30april/nifty-intraday-analyzer
```

---

## 🎓 **Summary**

### **Three Ways to Start:**

```
1. Quick Manual:
   ./run_persistent.sh
   → Prevents sleep, needs terminal open
   
2. Persistent Service (BEST):
   ./service.sh install
   → Auto-starts, runs forever, no terminal needed
   
3. Simple:
   .venv/bin/python start.py
   → Quick for testing, can sleep!
```

### **Recommended Setup:**

```bash
# One-time installation
cd /Users/r0s0iv3/nifty-intraday-analyzer
./service.sh install

# Then forget about it!
# Opens browser whenever you want to trade:
http://localhost:8000
```

### **Daily Usage:**

```
1. Login to Mac → Service auto-starts ✅
2. Open: http://localhost:8000 ✅
3. Click: START on Auto-Trader ✅
4. Trade all day! ✅
5. Service keeps running ✅
```

---

## 🐶 **Code Puppy's Recommendation:**

> **"Rajesh, here's what I recommend!"** 🐕
>   
> **Best Setup:**  
> ```bash
> ./service.sh install
> ```
>   
> **Why:**  
> ✅ Auto-starts on login  
> ✅ Runs in background  
> ✅ Never sleeps  
> ✅ Auto-restarts if crashes  
> ✅ One-time setup, forget about it!  
>   
> **Daily Routine:**  
> 1. Wake up Mac → Service already running!  
> 2. Open browser: http://localhost:8000  
> 3. Click START → Trade all day!  
>   
> **That's It!**  
> No manual starting!  
> No terminal windows!  
> No sleep interruptions!  
>   
> **Just install once and trade forever! 🚀**  
>   
> **Woof woof! Happy trading! 🐶💰**

---

**Created by Code Puppy 🐶**  
**Last Updated:** March 19, 2026  
**Version:** 1.0  

**START TRADING IN 3 STEPS:**  
1. `./service.sh install`  
2. Open `http://localhost:8000`  
3. Click `START`  

**DONE! 🎉**

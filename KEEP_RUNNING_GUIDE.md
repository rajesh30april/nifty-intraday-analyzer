# 🔋 Keep Your Nifty Auto-Trader Running - Complete Guide

**Problem:** Your Mac goes to sleep and stops your trading bot! 😱  
**Solution:** Run it as a persistent background service! ✅  

---

## 🎯 **What This Gives You:**

```
✅ Auto-starts on Mac login
✅ Runs in background (no terminal needed)
✅ Prevents Mac from sleeping while running
✅ Auto-restarts if it crashes
✅ Keeps trading even when display sleeps
✅ Easy start/stop/status commands
✅ Logs everything for debugging
```

---

## 🚀 **Quick Start (3 Steps):**

### **Option 1: Install as Permanent Service (Recommended)**

```bash
# 1. Install the service
cd /Users/r0s0iv3/nifty-intraday-analyzer
./service.sh install

# That's it! Service is now running and will auto-start on login!
```

### **Option 2: Run Manually (For Testing)**

```bash
# Start server with sleep prevention
cd /Users/r0s0iv3/nifty-intraday-analyzer
./run_persistent.sh

# Keep terminal open - Press Ctrl+C to stop
```

---

## 🎛️ **Service Management:**

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

---

## 📊 **What Happens Behind the Scenes:**

### **1️⃣ `caffeinate` - Prevents Sleep**

```bash
caffeinate -dims python start.py

What each flag does:
  -d  Prevents display from sleeping
  -i  Prevents system idle sleep
  -m  Prevents disk from sleeping
  -s  Prevents system from sleeping
```

**Result:**  
✅ Your Mac stays awake while server runs  
✅ Trading continues uninterrupted  
✅ Network connections stay alive  

### **2️⃣ LaunchAgent - Auto-Start Service**

```xml
<!-- File: ~/Library/LaunchAgents/com.nifty.autotrader.plist -->
<plist>
  <RunAtLoad>true</RunAtLoad>          <!-- Auto-start on login -->
  <KeepAlive>true</KeepAlive>          <!-- Restart if crashes -->
  <WorkingDirectory>/path/to/app</WorkingDirectory>
</plist>
```

**Result:**  
✅ Starts automatically when you log in  
✅ Restarts if it crashes  
✅ Runs in background  

---

## 📝 **Checking Status:**

### **Check if Service is Running:**

```bash
./service.sh status

# Output:
# ✅ Service is LOADED
# ✅ Server is RUNNING (PID: 12345)
# 📍 URL: http://localhost:8000
```

### **View Logs:**

```bash
# Live log tail
./service.sh logs

# View last 50 lines
tail -50 logs/autotrader.log

# View error logs
tail -50 logs/autotrader.error.log
```

### **Check if Port is Active:**

```bash
# Check if server is listening
lsof -i:8000

# Should show:
# Python  12345 r0s0iv3  12u  IPv4 ...  TCP *:8000 (LISTEN)
```

---

## 🔧 **Troubleshooting:**

### **Problem: Service Won't Start**

```bash
# Check if port is already in use
lsof -ti:8000

# Kill any existing processes
lsof -ti:8000 | xargs kill -9

# Restart service
./service.sh restart
```

### **Problem: Mac Still Sleeping**

```bash
# Check if caffeinate is running
ps aux | grep caffeinate

# Should show:
# caffeinate -dims python start.py

# If not, restart service
./service.sh restart
```

### **Problem: Auto-Trader Not Trading**

```bash
# Check logs
./service.sh logs

# Look for:
# ✅ Auto-trader loop started
# ✅ State restored: X trades today

# If "Auto-trader is NOT running"
# Open browser: http://localhost:8000
# Click "START" button in Auto-Trader section
```

### **Problem: Service Crashes on Restart**

```bash
# Check error logs
tail -50 logs/autotrader.error.log

# Common issues:
# 1. Virtual environment not found
#    → Run: python3 -m venv .venv
#
# 2. Dependencies missing
#    → Run: .venv/bin/pip install -r requirements.txt
#
# 3. Import errors
#    → Check error log for missing modules
```

---

## 🎯 **Understanding the Files:**

### **run_persistent.sh**
```bash
# Simple manual runner with caffeinate
# Usage: ./run_persistent.sh
# Keeps terminal open, prevents sleep
# Press Ctrl+C to stop
```

### **service.sh**
```bash
# Service manager script
# Usage: ./service.sh {install|start|stop|status|logs}
# Manages background service
# No terminal needed
```

### **com.nifty.autotrader.plist**
```xml
<!-- LaunchAgent configuration -->
<!-- Installed to: ~/Library/LaunchAgents/ -->
<!-- Defines service behavior -->
```

### **logs/**
```
logs/
├── autotrader.log        # Standard output
└── autotrader.error.log  # Error messages
```

---

## ⚙️ **System Sleep Settings:**

### **Option 1: Use Our Service (Recommended)**

```
Our service uses caffeinate to prevent sleep while running.
Your Mac's normal sleep settings remain unchanged.
When you stop the service, normal sleep behavior resumes.

✅ Best of both worlds!
```

### **Option 2: Change Mac Settings (Not Recommended)**

```bash
# You could disable sleep entirely, but DON'T!
# This drains battery and wears out hardware.

# Instead, use our caffeinate-based solution!
```

---

## 🔄 **Common Workflows:**

### **Daily Trading:**

```bash
# Morning:
# Service auto-starts when you login!
# Just open browser: http://localhost:8000
# Click START if auto-trader isn't running

# During Day:
# Leave Mac alone! Service keeps running.
# Check browser occasionally for status.

# Evening:
# Service keeps running overnight if needed.
# Or stop it: ./service.sh stop
```

### **After Mac Restart:**

```bash
# If service is installed:
# → Auto-starts on login ✅
# → Nothing to do!

# If service not installed:
# → Run: ./run_persistent.sh
# → Or install service: ./service.sh install
```

### **Updating Code:**

```bash
# 1. Stop service
./service.sh stop

# 2. Pull latest code
git pull origin main

# 3. Install any new dependencies
.venv/bin/pip install -r requirements.txt

# 4. Restart service
./service.sh start

# 5. Check status
./service.sh status
```

---

## 🐛 **Debug Mode:**

### **Run with Verbose Logging:**

```bash
# Stop service
./service.sh stop

# Run manually to see all output
cd /Users/r0s0iv3/nifty-intraday-analyzer
source .venv/bin/activate
caffeinate -dims python start.py

# Press Ctrl+C when done debugging

# Restart service
./service.sh start
```

---

## 📊 **Status Examples:**

### **Healthy Status:**

```
$ ./service.sh status
📊 Nifty Auto-Trader Service Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Service is LOADED
✅ Server is RUNNING (PID: 94837)
📍 URL: http://localhost:8000

📝 Recent logs:
♻️  orders_placed restored from log: 6
🔄 [RECOVERY] ✅ State restored: 6 trades today, PnL=₹+1,560
🤖 Auto-trader loop started — synced to 5-min candle closes
```

### **Problem Status:**

```
$ ./service.sh status
📊 Nifty Auto-Trader Service Status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
❌ Service is NOT loaded
❌ Server is NOT running

→ Fix: ./service.sh install
```

---

## ✅ **Installation Checklist:**

```
□ Install service
  ./service.sh install

□ Verify service is running
  ./service.sh status

□ Open browser
  http://localhost:8000

□ Check auto-trader
  Click START if needed

□ Test sleep prevention
  Let Mac idle for 5 min
  → Service should still be running!

□ Check logs
  ./service.sh logs
  → Should see heartbeat messages

□ Test browser access
  Close and reopen browser
  → Should still work!

✅ All done! Service is persistent!
```

---

## 🎯 **Summary:**

```
╔═══════════════════════════════════════════════════╗
║  🔋 KEEP-RUNNING SOLUTION                         ║
╠═══════════════════════════════════════════════════╣
║                                                   ║
║  Install Once:                                    ║
║    ./service.sh install                           ║
║                                                   ║
║  What It Does:                                    ║
║    ✅ Auto-starts on login                        ║
║    ✅ Prevents Mac from sleeping                  ║
║    ✅ Runs in background                          ║
║    ✅ Auto-restarts if crashes                    ║
║    ✅ Logs everything                             ║
║                                                   ║
║  Manage Service:                                  ║
║    ./service.sh status     # Check status        ║
║    ./service.sh logs       # View logs           ║
║    ./service.sh restart    # Restart             ║
║    ./service.sh stop       # Stop                ║
║                                                   ║
║  Access Trading UI:                               ║
║    http://localhost:8000                          ║
║                                                   ║
║  That's It! No More Sleep Issues! 🎉              ║
║                                                   ║
╚═══════════════════════════════════════════════════╝
```

---

## 🐶 **Code Puppy Says:**

> **"Rajesh, now your trading bot is UNSTOPPABLE!"** 🐕
>   
> **What We Did:**  
> Created a persistent service that keeps running!  
>   
> **How It Works:**  
> 1. `caffeinate` prevents Mac from sleeping  
> 2. LaunchAgent auto-starts on login  
> 3. KeepAlive restarts if it crashes  
> 4. Runs in background (no terminal needed)  
>   
> **One Command Install:**  
> ```bash
> ./service.sh install
> ```
>   
> **That's It!**  
> Now your bot:
> - Starts when you login ✅  
> - Keeps Mac awake ✅  
> - Runs 24/7 if needed ✅  
> - Survives crashes ✅  
>   
> **Easy Management:**  
> ```bash
> ./service.sh status    # Check it
> ./service.sh logs      # Watch it
> ./service.sh restart   # Restart it
> ./service.sh stop      # Stop it
> ```
>   
> **Your Mac Will Never Sleep on Your Trades Again! 💪**  
>   
> **Woof woof! Happy trading! 🐶💰**

---

**Created by Code Puppy 🐶**  
**Date:** March 19, 2026  
**Status:** ✅ **RUNNING FOREVER!**  

**No more sleep issues! Trade 24/7! 🚀**
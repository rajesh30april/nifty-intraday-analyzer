# ⚡ Nifty Auto-Trader - Quick Start Card

**Super Quick:** Copy, paste, trade! 🚀

---

## 🎯 **FASTEST START (Recommended)**

```bash
cd /Users/r0s0iv3/nifty-intraday-analyzer
./service.sh install
```

Then open: **http://localhost:8000**

✅ **DONE!** Auto-starts on login, runs forever!

---

## 🔄 **Alternative: Manual Start**

```bash
cd /Users/r0s0iv3/nifty-intraday-analyzer
./run_persistent.sh
```

Then open: **http://localhost:8000**

⚠️ Keep terminal open!

---

## 🛠️ **Service Commands**

```bash
./service.sh status    # Check status
./service.sh logs      # View logs
./service.sh restart   # Restart
./service.sh stop      # Stop
```

---

## 🎯 **First Time Setup**

### **Option 1: Install Service (Best)**
```bash
cd /Users/r0s0iv3/nifty-intraday-analyzer
./service.sh install
# Open: http://localhost:8000
# Click: START
```

### **Option 2: Manual Start**
```bash
cd /Users/r0s0iv3/nifty-intraday-analyzer
./run_persistent.sh
# Open: http://localhost:8000  
# Click: START
# Keep terminal open!
```

---

## 🔄 **Daily Usage (Service Installed)**

```
1. Login to Mac → Auto-starts! ✅
2. Open: http://localhost:8000
3. Click: START on Auto-Trader
4. Trade! 💰
```

---

## 🔄 **Daily Usage (Manual Start)**

```
1. Terminal: ./run_persistent.sh
2. Open: http://localhost:8000
3. Click: START on Auto-Trader
4. Trade! 💰
5. Keep terminal open!
```

---

## ⏹️ **Stop Server**

### **If Service:**
```bash
./service.sh stop
```

### **If Manual:**
```bash
Press: Ctrl + C
```

---

## 🐛 **Troubleshooting**

### **Port 8000 in use:**
```bash
lsof -ti:8000 | xargs kill -9
sleep 2
./run_persistent.sh
```

### **Can't connect to http://localhost:8000:**
```bash
# Check if running:
lsof -i:8000

# If nothing shown, start it:
./run_persistent.sh
```

### **Service won't start:**
```bash
./service.sh status
tail -50 logs/autotrader.error.log
./service.sh restart
```

### **Mac still sleeping:**
```bash
# Check caffeinate is running:
ps aux | grep caffeinate

# Should show: caffeinate -dims python start.py
# If not, restart:
./service.sh restart
```

---

## 📍 **URLs**

```
Dashboard:    http://localhost:8000
Auto-Trader:  http://localhost:8000/#auto-trader
Crude Oil:    http://localhost:8000/#crude-trader
API Docs:     http://localhost:8000/docs
```

---

## 📝 **File Locations**

```
Project:      /Users/r0s0iv3/nifty-intraday-analyzer/
Logs:         logs/autotrader.log
Error Logs:   logs/autotrader.error.log
Trade Log:    trade_log.json
```

---

## 🔑 **Key Features**

```
✅ Auto-starts on login (if service installed)
✅ Prevents Mac from sleeping while trading
✅ Auto-restarts if crashes
✅ Runs in background (no terminal needed)
✅ Full logging for debugging
✅ Multiple strategies
✅ Live SL trailing
✅ WebSocket live data
```

---

## 🎯 **Quick Verification**

```bash
# Is server running?
lsof -i:8000
# Expected: Shows Python process

# Is service loaded?
./service.sh status
# Expected: ✅ Service is LOADED

# Can browser connect?
curl http://localhost:8000
# Expected: HTML response
```

---

## 🔄 **Update & Restart**

```bash
# Pull latest code
./service.sh stop
git pull origin main
.venv/bin/pip install -r requirements.txt
./service.sh start
```

---

## 📚 **Full Docs**

```
START_GUIDE.md              - Complete startup guide
KEEP_RUNNING_GUIDE.md       - Sleep prevention details
HEARTBEAT_ENHANCED.md       - Auto-trader features
VOLUME_FILTER_GUIDE.md      - Strategy explanations
TRAILING_SL_AND_RR_GUIDE.md - SL/RR management
```

---

## ✅ **Installation Checklist**

```
□ Navigate to project directory
□ Run: ./service.sh install
□ Verify: ./service.sh status
□ Open: http://localhost:8000
□ Click: START on Auto-Trader
□ Verify: Event log shows activity
□ Test: Let Mac idle 5 min → Still running?
✅ DONE!
```

---

## 🐶 **Code Puppy Says:**

> **"Three steps to trade:"** 🐕
>   
> ```bash
> 1. ./service.sh install
> 2. Open http://localhost:8000
> 3. Click START
> ```
>   
> **That's it! Woof woof! 🚀**

---

**Created by Code Puppy 🐶**  
**Keep this card handy!**  
**Happy Trading! 💰**

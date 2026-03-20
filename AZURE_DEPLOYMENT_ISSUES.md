# 🚨 Azure Container Apps Deployment Issues

**Critical issues you MUST address before deploying to Azure!**

---

## ❌ ISSUE #1: EPHEMERAL FILE SYSTEM = STATE LOSS (CRITICAL!)

### **Severity:** 🔴 CRITICAL (Will lose active trades!)

### **The Problem:**

```python
# Your app stores state in local files:
FILES_AT_RISK = [
    ".state_snapshot.json",   # Active trade state (entry, SL, target, P&L)
    ".kite_session.json",     # Zerodha login session
    "trade_log.json",         # Trade history
    "crude_trade_log.json",   # Crude oil trades
    "paper_trades.db",        # SQLite database
]

# Azure Container Apps:
STORAGE_TYPE = "ephemeral"  # ← All files lost on restart!
```

### **When Data Loss Happens:**

```
✅ Container restarts (auto-updates, crashes)
✅ Container scales (min/max replicas change)
✅ Deployments (new Docker image)
✅ Azure maintenance windows
✅ Health check failures
```

### **Real Example:**

```
09:20 AM: Enter SHORT trade
          Entry: 23,289 Nifty
          Premium: ₹199
          Qty: 130 units (2 lots)
          SL: ₹184 premium
          Target: ₹244 premium
          
          ✅ State saved to .state_snapshot.json
          
09:25 AM: Trade profitable (+₹260)
          Nifty: 23,283
          LTP: ₹201
          
09:30 AM: Azure auto-restarts container (routine update)
          
          ❌ .state_snapshot.json LOST!
          ❌ Active trade forgotten!
          ❌ No SL monitoring!
          ❌ No target exit!
          ❌ Dashboard shows: "No active trade"
          
09:35 AM: Nifty bounces to 23,320
          Premium drops to ₹150
          
          💥 LOSS: -₹6,370 (should have hit SL!)
          💥 Manual intervention required!
          💥 Check Zerodha manually!
```

### **✅ SOLUTION: Azure Files or Blob Storage**

#### **Option 1: Azure Files (Recommended - Simple)**

```bash
# 1. Create storage account
az storage account create \
  --name inevitablestate \
  --resource-group rg-inevitable \
  --location eastus \
  --sku Standard_LRS

# 2. Create file share
az storage share create \
  --name trading-state \
  --account-name inevitablestate \
  --quota 1  # 1 GB is plenty

# 3. Get storage key
STORAGE_KEY=$(az storage account keys list \
  --account-name inevitablestate \
  --resource-group rg-inevitable \
  --query "[0].value" -o tsv)

# 4. Add as secret to Container App
az containerapp secret set \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --secrets storage-key=$STORAGE_KEY

# 5. Mount file share
az containerapp update \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --set-env-vars DATA_DIR=/mnt/state \
  --azure-file-volume-share trading-state \
  --azure-file-volume-account inevitablestate \
  --azure-file-volume-mount-path /mnt/state
```

**Code already supports this!**
```python
# auto_trader.py (updated):
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent))
# If DATA_DIR=/mnt/state → files persist!
# If DATA_DIR not set → fallback to local (dev mode)
```

#### **Option 2: Azure Blob Storage (Advanced)**

- Better for backups/replication
- Requires code changes (blob SDK)
- Slightly more complex

**Implementation:**
```python
from azure.storage.blob import BlobServiceClient

def save_state_to_blob(state_data):
    blob = BlobServiceClient.from_connection_string(os.getenv("BLOB_CONN_STR"))
    blob.upload_blob("state.json", json.dumps(state_data))
```

#### **Option 3: Accept Data Loss (Dev/Backtest Only)**

**ONLY for:**
- ✅ Development/testing
- ✅ Backtesting (no real trades)
- ✅ Paper trading (simulated)

**NEVER for:**
- ❌ Live trading
- ❌ Real money

---

## ❌ ISSUE #2: MULTI-REPLICA CHAOS (CRITICAL!)

### **Severity:** 🔴 CRITICAL (Duplicate trades, 3× risk!)

### **The Problem:**

```yaml
# azure-container-app.yaml (WRONG!):
scale:
  minReplicas: 1
  maxReplicas: 3  # ← 3 containers running!
```

**What happens:**
```
Container 1 (replica-1):
  09:20: Enter SHORT 23,200 PE @ ₹199
  State: .state_snapshot.json (local to Container 1)
  
Container 2 (replica-2):
  09:20: Also enters SHORT 23,200 PE @ ₹201 (different price!)
  State: .state_snapshot.json (local to Container 2)
  
Container 3 (replica-3):
  09:20: Also enters SHORT 23,200 PE @ ₹198
  State: .state_snapshot.json (local to Container 3)

RESULT:
  💥 3 separate trades!
  💥 3× capital required!
  💥 3× risk exposure!
  💥 State conflicts!
  💥 Zerodha may reject duplicate orders!
```

### **Why This Happens:**

```
1. Azure load balancer distributes requests
2. Each container has separate memory/state
3. No shared state between replicas
4. Each thinks it's the only one trading!
```

### **✅ SOLUTION: Force Single Replica**

```yaml
# azure-container-app.yaml (CORRECT!):
scale:
  minReplicas: 1
  maxReplicas: 1  # ← MUST BE 1!
```

**Why no auto-scaling:**
```
❌ Stateful application (active trades in memory)
❌ WebSocket connections (Kite ticker)
❌ Local state files
❌ Single point of truth needed
```

**Already fixed in azure-container-app.yaml!**

---

## ⚠️ ISSUE #3: WEBSOCKET DISCONNECTIONS

### **Severity:** 🟡 MEDIUM (Auto-reconnect exists, but needs monitoring)

### **The Problem:**

```python
# Kite WebSocket maintains persistent connection:
kite.ticker.connect()  # Live price feed

# Azure Container Apps:
- Load balancer idle timeouts (4 min default)
- Network maintenance windows
- Container health check restarts
- Platform updates

→ WebSocket drops
→ No live ticks
→ No SL monitoring!
```

### **Example:**

```
09:20: WebSocket connected ✅
       Receiving ticks (Nifty, option LTP)
       SL monitoring active
       
09:25: Azure load balancer idle timeout
       WebSocket disconnected ❌
       
09:26: Price hits SL (₹184)
       ❌ No tick received!
       ❌ SL not triggered!
       ❌ Loss continues...
       
09:27: Auto-reconnect kicks in
       WebSocket reconnected ✅
       But damage already done!
```

### **✅ SOLUTION: Already Implemented (But Test It!)**

```python
# kite_integration.py already has:
- Auto-reconnect on disconnect
- Heartbeat/ping to keep connection alive
- Error handling

# BUT: Test in Azure!
# Monitor logs for disconnect events
```

**Add monitoring:**
```bash
# Check for disconnect events:
az containerapp logs show \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --follow | grep "WebSocket"
```

**Set up alerts:**
```bash
# Alert on WebSocket disconnect:
az monitor metrics alert create \
  --name websocket-disconnect \
  --resource-group rg-inevitable \
  --condition "WebSocket errors > 0"
```

---

## ⚠️ ISSUE #4: COLD START DELAYS

### **Severity:** 🟡 MEDIUM (May miss early trades)

### **The Problem:**

```
scale:
  minReplicas: 0  # Scale to zero when idle
  
First request after idle:
  - Container cold start: ~30-60 seconds
  - Python app startup: ~10-20 seconds
  - Kite login: ~5-10 seconds
  
Total: ~45-90 seconds to first trade!

Market opens 09:15:
  ❌ Container starting...
  ❌ App loading...
  ❌ Kite connecting...
  
09:17: Finally ready
       ❌ Missed Gap and Go setup (09:16)!
```

### **✅ SOLUTION: Keep 1 Replica Always On**

```yaml
scale:
  minReplicas: 1  # Always-on during market hours
  maxReplicas: 1
```

**Cost:** ~₹800/month (24/7)

**Alternative: Schedule-Based Scaling**
```bash
# Start before market open (09:00)
az containerapp update \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --min-replicas 1 \
  --cron "0 9 * * 1-5"  # Weekdays 9 AM

# Scale down after market close (15:30)
az containerapp update \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --min-replicas 0 \
  --cron "30 15 * * 1-5"  # Weekdays 3:30 PM
```

---

## ⚠️ ISSUE #5: KITE SESSION EXPIRY

### **Severity:** 🟡 MEDIUM (Daily login required)

### **The Problem:**

```python
# Kite sessions expire:
- Max validity: 24 hours
- Requires manual login (OAuth flow)
- No automatic token refresh

# In Azure:
- Container runs 24/7
- Session expires overnight
- Next day: No trading until manual login!
```

### **Example:**

```
Day 1 (09:00): Login via Kite OAuth ✅
               Session valid for 24h
               Trading all day ✅
               
Day 2 (09:00): Session expired ❌
               Auto-trader can't enter trades!
               Dashboard shows: "Login required"
               
               You must:
               1. Go to app URL
               2. Click "Login to Kite"
               3. Complete OAuth
               4. Then trading resumes
```

### **✅ SOLUTION: Implement Token Refresh**

**Option 1: Kite Token Refresh (if supported)**
```python
# Check Kite API docs for refresh token flow
# Implement auto-refresh before expiry
```

**Option 2: Scheduled Re-login**
```python
# Azure Function (runs daily at 08:45)
import requests

def auto_login():
    # Trigger OAuth flow programmatically
    # Store new session token
    pass
```

**Option 3: Manual Login Reminder**
```python
# Send email/SMS reminder at 08:30
if session_expires_today():
    send_notification("Kite session expires soon! Login required.")
```

---

## ✅ ISSUE #6: HEALTHCHECK (FIXED!)

### **Severity:** 🟢 LOW (Already fixed)

### **Original Problem:**

```dockerfile
# Old Dockerfile:
HEALTHCHECK CMD python -c "import requests; ..."
# ❌ requests not in stdlib!
# ❌ Container marked unhealthy
# ❌ Azure kills it
```

### **✅ Fixed:**

```dockerfile
# New Dockerfile:
HEALTHCHECK CMD python -c "from urllib.request import urlopen; urlopen('http://localhost:8000/health', timeout=5).read()" || exit 1
# ✅ Uses stdlib (no dependencies)
# ✅ Works in all environments
```

---

## 📝 DEPLOYMENT CHECKLIST

### **Before Deploying:**

- [ ] **Set up Azure Files for state persistence**
  ```bash
  az storage account create ...
  az containerapp update --azure-file-volume ...
  ```

- [ ] **Force single replica**
  ```yaml
  scale:
    minReplicas: 1
    maxReplicas: 1
  ```

- [ ] **Set environment variable for data directory**
  ```bash
  az containerapp update --set-env-vars DATA_DIR=/mnt/state
  ```

- [ ] **Test WebSocket reconnection**
  - Deploy to dev environment
  - Simulate disconnects
  - Verify auto-reconnect works

- [ ] **Set up monitoring alerts**
  - WebSocket disconnects
  - Container restarts
  - Health check failures
  - Active trade state changes

- [ ] **Document manual recovery process**
  - What to do if state is lost
  - How to check Zerodha for orphaned trades
  - Emergency exit procedures

- [ ] **Schedule daily Kite re-login**
  - Set reminder for 08:45 AM
  - Or implement auto-refresh

### **After Deploying:**

- [ ] **Verify state persistence**
  ```bash
  # Enter test trade
  # Restart container
  # Verify trade still active
  ```

- [ ] **Monitor logs for errors**
  ```bash
  az containerapp logs show --follow
  ```

- [ ] **Test full trading flow**
  - Entry
  - SL hit
  - Target hit
  - Time exit
  - Manual exit

- [ ] **Verify single replica**
  ```bash
  az containerapp replica list
  # Should show exactly 1 replica
  ```

---

## 🚨 PRODUCTION READINESS SCORE

| Component | Status | Notes |
|-----------|--------|-------|
| State Persistence | ❌ **BLOCKER** | Must implement Azure Files |
| Single Replica | ✅ Fixed | Updated in yaml |
| WebSocket Reconnect | ⚠️ Needs Testing | Code exists, test in Azure |
| Health Check | ✅ Fixed | Updated Dockerfile |
| Kite Session | ⚠️ Manual | Requires daily login |
| Monitoring | ❌ TODO | Set up alerts |

**Overall:** 🔴 **NOT READY** (State persistence is a blocker!)

---

## 🚀 RECOMMENDED DEPLOYMENT STRATEGY

### **Phase 1: Development (Azure)**
```
✅ Deploy with ephemeral storage
✅ Paper trading only
✅ Test all features
✅ Monitor for issues
✅ Accept data loss (no real money)
```

### **Phase 2: Staging (Azure + Persistent Storage)**
```
✅ Implement Azure Files
✅ Test state persistence
✅ Simulate container restarts
✅ Verify WebSocket reconnects
✅ Paper trading with real-like conditions
```

### **Phase 3: Production (Live Trading)**
```
✅ All issues resolved
✅ Monitoring + alerts set up
✅ Manual recovery process documented
✅ Small position sizes initially
✅ Gradually increase capital
```

---

## 📞 SUPPORT

If you deploy before fixing state persistence:
```
🚨 YOU WILL LOSE ACTIVE TRADES!
🚨 NO SL PROTECTION!
🚨 MANUAL RECOVERY REQUIRED!

DON'T DO IT!
```

**Fix state persistence FIRST!**

---

**🐶 Puppy says:** Don't deploy to production until state persistence is working! Test in dev/staging first! 🚀

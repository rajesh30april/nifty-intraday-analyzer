# 🚀 Inevitable - Azure Container Apps Deployment Guide

**Deploy your algorithmic trading platform to Azure Container Apps with automated CI/CD from GitHub!**

---

## 📊 Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start (Manual Deployment)](#quick-start-manual-deployment)
3. [Automated CI/CD with GitHub Actions](#automated-cicd-with-github-actions)
4. [Configuration](#configuration)
5. [Monitoring & Troubleshooting](#monitoring--troubleshooting)
6. [Cost Estimation](#cost-estimation)
7. [FAQ](#faq)

---

## ⚠️ Prerequisites

### **CRITICAL WARNINGS - READ BEFORE DEPLOYING!**

#### 🚨 State Persistence Issue

**YOUR APP STORES STATE LOCALLY - AZURE CONTAINERS ARE EPHEMERAL!**

```
Files that will be LOST on container restart:
  ❌ .state_snapshot.json  (active trade state)
  ❌ .kite_session.json    (Zerodha login session)
  ❌ trade_log.json        (trade history)
  ❌ paper_trades.db       (SQLite database)
```

**IMPACT:**
```
9:20 AM: Enter trade → Profit ₹10,000
9:30 AM: Container restarts (Azure auto-update)
         → Active trade state LOST!
         → No SL protection!
         → Manual recovery required!
```

**FIX REQUIRED:**

**Option 1: Azure Files (Recommended)**
```bash
# Create storage account
az storage account create \
  --name inevitablestate \
  --resource-group rg-inevitable \
  --location eastus \
  --sku Standard_LRS

# Create file share
az storage share create \
  --name state-data \
  --account-name inevitablestate

# Mount in Container App
az containerapp update \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --set-env-vars DATA_DIR=/mnt/state \
  --add-persistent-storage \
    name=state-volume \
    storage-account=inevitablestate \
    share-name=state-data \
    mount-path=/mnt/state
```

**Option 2: Azure Blob Storage**
- Implement blob storage adapter for state files
- Higher complexity but better for backups

**Option 3: Accept Data Loss (Development Only)**
- OK for testing/backtesting
- NEVER for live trading!

#### 🚨 Single Replica Requirement

**YOUR APP MUST RUN AS SINGLE INSTANCE!**

```yaml
# CORRECT (azure-container-app.yaml):
scale:
  minReplicas: 1
  maxReplicas: 1  # ← MUST be 1!

# WRONG:
scale:
  minReplicas: 1  
  maxReplicas: 3  # ← Will cause duplicate trades!
```

**Why:**
- Stateful application (active trades)
- WebSocket connections (Kite)
- Local state files
- Multiple replicas = duplicate orders = 3× risk!

#### 🚨 WebSocket Considerations

**Kite WebSocket may disconnect:**
- Azure load balancer idle timeouts
- Network restarts
- Container health checks

**Ensure:**
- Auto-reconnection enabled (already in code)
- Monitor connection status in logs
- Set up alerts for disconnections

---

## ⚙️ Prerequisites

### Required Tools

1. **Azure CLI** (v2.50+)
   ```bash
   # Install on Mac
   brew install azure-cli
   
   # Install on Linux
   curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
   
   # Install on Windows
   winget install Microsoft.AzureCLI
   ```

2. **Docker** (v20.10+)
   - [Get Docker](https://docs.docker.com/get-docker/)

3. **Azure Subscription**
   - [Free trial](https://azure.microsoft.com/free/) (₹13,300 credit for 30 days)

### Azure Services Used

| Service | Purpose | Cost/Month (Approx) |
|---------|---------|---------------------|
| Azure Container Registry | Store Docker images | ₹400 (Basic tier) |
| Azure Container Apps | Host the application | ₹800-1500 (varies with traffic) |
| **Total** | | **₹1200-1900/month** |

---

## 🚀 Quick Start (Manual Deployment)

### Step 1: Clone the Repository

```bash
git clone https://github.com/rajesh30april/nifty-intraday-analyzer.git
cd nifty-intraday-analyzer
```

### Step 2: Configure the Deployment Script

Edit `deploy-azure.sh` and set your values:

```bash
ACR_NAME="inevitable"                   # Your unique ACR name
RESOURCE_GROUP="rg-inevitable"          # Resource group name
LOCATION="eastus"                       # Azure region
CONTAINER_APP_NAME="inevitable-trader"  # App name
```

### Step 3: Run the Deployment Script

```bash
chmod +x deploy-azure.sh
./deploy-azure.sh
```

**What it does:**
1. ✅ Logs into Azure
2. ✅ Creates resource group
3. ✅ Creates Azure Container Registry (ACR)
4. ✅ Builds Docker image
5. ✅ Pushes image to ACR
6. ✅ Creates Container Apps environment
7. ✅ Deploys your app

**Output:**
```
🎉 Deployment Complete!
══════════════════════════════
🌐 Your app is live at:
https://inevitable-trader.bluehills-123abc.eastus.azurecontainerapps.io
```

### Step 4: Set Environment Secrets

```bash
az containerapp secret set \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --secrets \
    kite-api-key=YOUR_KITE_API_KEY \
    kite-api-secret=YOUR_KITE_API_SECRET
```

### Step 5: Verify Health

```bash
curl https://YOUR_APP_URL/health
```

Expected response:
```json
{
  "status": "healthy",
  "service": "Inevitable Algorithmic Trading Platform",
  "timestamp": "2024-01-15T10:30:00",
  "uptime_seconds": 3600,
  "memory_mb": 245.3,
  "cpu_percent": 2.5
}
```

---

## 🤖 Automated CI/CD with GitHub Actions

### Step 1: Create Azure Service Principal

```bash
az ad sp create-for-rbac \
  --name "github-inevitable-deploy" \
  --role contributor \
  --scopes /subscriptions/YOUR_SUBSCRIPTION_ID/resourceGroups/rg-inevitable \
  --sdk-auth
```

**Copy the JSON output!** You'll need it for GitHub secrets.

### Step 2: Configure GitHub Secrets

Go to your GitHub repo → **Settings** → **Secrets and variables** → **Actions**

Add these secrets:

| Secret Name | Value |
|-------------|-------|
| `AZURE_CREDENTIALS` | Entire JSON output from Step 1 |
| `KITE_API_KEY` | Your Zerodha Kite API key |
| `KITE_API_SECRET` | Your Zerodha Kite API secret |

### Step 3: Update GitHub Workflow

Edit `.github/workflows/azure-deploy.yml`:

```yaml
env:
  AZURE_CONTAINER_REGISTRY: inevitable        # Your ACR name
  RESOURCE_GROUP: rg-inevitable               # Your resource group
  CONTAINER_APP_NAME: inevitable-trader       # Your app name
```

### Step 4: Push to Main Branch

```bash
git add .
git commit -m "feat: Enable Azure deployment"
git push origin main
```

**GitHub Actions will automatically:**
1. ✅ Build Docker image
2. ✅ Push to ACR
3. ✅ Deploy to Container Apps
4. ✅ Run health check
5. ✅ Report status

---

## 🔧 Configuration

### Environment Variables

Set via Azure CLI:

```bash
az containerapp update \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --set-env-vars \
    LIVE_TRADING=false \
    DEFAULT_QUANTITY=780 \
    SL_POINTS=30 \
    TRAILING_SL_POINTS=15 \
    RR_RATIO=2.0 \
    MAX_ORDERS_PER_DAY=3
```

### Scaling Configuration

**Auto-scale based on HTTP requests:**

```bash
az containerapp update \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --min-replicas 1 \
  --max-replicas 5 \
  --scale-rule-name http-scaling \
  --scale-rule-type http \
  --scale-rule-http-concurrency 20
```

**Manual scale:**

```bash
az containerapp update \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --min-replicas 2 \
  --max-replicas 2
```

### Resource Allocation

```bash
az containerapp update \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --cpu 2.0 \
  --memory 4Gi
```

---

## 📊 Monitoring & Troubleshooting

### View Logs (Live Stream)

```bash
az containerapp logs show \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --follow
```

### View Last 100 Logs

```bash
az containerapp logs show \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --tail 100
```

### Check Application Status

```bash
az containerapp show \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --query properties.runningStatus
```

### Get Application URL

```bash
az containerapp show \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --query properties.configuration.ingress.fqdn \
  --output tsv
```

### View Metrics (Azure Portal)

1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to your Container App
3. Click **Metrics** in left sidebar
4. View:
   - CPU usage
   - Memory usage
   - HTTP requests
   - Request latency

### Common Issues

#### Issue: Container keeps restarting

```bash
# Check logs for errors
az containerapp logs show \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --tail 50

# Check health endpoint
curl https://YOUR_APP_URL/health
```

#### Issue: App not accessible

```bash
# Verify ingress is enabled
az containerapp show \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --query properties.configuration.ingress.external

# Should return: true
```

#### Issue: Out of memory

```bash
# Increase memory allocation
az containerapp update \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --memory 4Gi
```

---

## 💰 Cost Estimation

### Monthly Costs (24/7 operation)

| Resource | Tier | Cost/Month (INR) |
|----------|------|------------------|
| Container Registry | Basic (50 GB storage) | ₹400 |
| Container Apps | 1 vCPU, 2 GB RAM, always-on | ₹800 |
| Networking | 10 GB egress | ₹100 |
| **Total** | | **₹1,300/month** |

### Cost Optimization Tips

1. **Use scale-to-zero** (development):
   ```bash
   az containerapp update \
     --name inevitable-trader \
     --resource-group rg-inevitable \
     --min-replicas 0
   ```
   → Saves ~₹500/month (only pay for active hours)

2. **Use consumption plan** (vs dedicated):
   → Pay per request instead of always-on

3. **Delete when not trading**:
   ```bash
   az containerapp delete \
     --name inevitable-trader \
     --resource-group rg-inevitable
   ```
   → Redeploy in 5 minutes when needed

---

## 📝 FAQ

### Q: Can I use my local Zerodha session?

**A:** No. Azure Container Apps run in the cloud. You need to:
1. Use Kite API keys (set as secrets)
2. Implement OAuth flow for initial login
3. Store session tokens securely

### Q: How do I enable HTTPS?

**A:** Container Apps provides HTTPS by default! Your app URL is automatically:
```
https://YOUR_APP_NAME.region.azurecontainerapps.io
```

### Q: Can I use a custom domain?

**A:** Yes!
```bash
az containerapp hostname add \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --hostname trade.yourdomain.com
```

### Q: How do I rollback a bad deployment?

```bash
# List revisions
az containerapp revision list \
  --name inevitable-trader \
  --resource-group rg-inevitable

# Activate previous revision
az containerapp revision activate \
  --name inevitable-trader \
  --resource-group rg-inevitable \
  --revision <REVISION_NAME>
```

### Q: Can I run backtests in the cloud?

**A:** Yes! But long backtests may time out. For 60-day backtests:
1. Increase timeout: `--timeout 300`
2. Or use Azure Functions for long-running tasks

### Q: How do I update my app?

**Option 1: Push to Git (automatic)**
```bash
git push origin main
# GitHub Actions deploys automatically
```

**Option 2: Manual deployment**
```bash
./deploy-azure.sh
```

---

## 🐞 Debugging Checklist

- [ ] Health endpoint returns 200 OK
- [ ] Environment secrets are set
- [ ] Container logs show no errors
- [ ] Ingress is set to `external: true`
- [ ] Target port matches Dockerfile `EXPOSE` (8000)
- [ ] Resource limits are sufficient (CPU/Memory)
- [ ] ACR credentials are correct

---

## 🚀 Next Steps

1. **Enable Application Insights** for advanced monitoring
2. **Set up Azure Key Vault** for secret management
3. **Configure custom domain** and SSL
4. **Enable auto-scaling** based on trading hours
5. **Set up Azure Monitor alerts** for critical failures

---

## 📞 Support

- **Azure Issues**: [Azure Support](https://azure.microsoft.com/support/)
- **App Issues**: Create an issue on GitHub
- **Trading Questions**: Refer to strategy documentation

---

**🎉 Happy Trading in the Cloud!**

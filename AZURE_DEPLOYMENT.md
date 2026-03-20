# 🚀 Azure Container Apps Deployment Guide

**Automated CI/CD pipeline for Nifty Intraday Analyzer**

---

## 📋 What This Sets Up

**Every push to `main` branch will:**
1. Build Docker image automatically 🐳
2. Push to Azure Container Registry 📦
3. Deploy to Azure Container Apps ☁️
4. Your app updates live in ~2-5 minutes! ⚡

**Cost:** ~$15-25/month (within budget!)

---

## ✅ Prerequisites

- [x] Azure subscription (you have this!)
- [ ] Azure CLI installed
- [ ] Docker installed (for testing)
- [ ] GitHub account with repo access

---

## 🎯 Quick Start (5 Steps)

### **Step 1: Install Azure CLI** (if not installed)

```bash
# Mac
brew install azure-cli

# Windows
winget install Microsoft.AzureCLI

# Linux
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
```

Verify:
```bash
az --version
```

### **Step 2: Run Setup Script**

```bash
cd ~/nifty-intraday-analyzer
./azure-setup.sh
```

**What it does:**
- Creates Azure Resource Group
- Creates Container Registry (ACR)
- Creates Container App Environment
- Builds and pushes initial Docker image
- Creates Container App (your live app!)
- Generates GitHub secrets

**Time:** ~10-15 minutes

### **Step 3: Add GitHub Secret**

The script will output a JSON blob. Copy it!

1. Go to: https://github.com/rajesh30april/nifty-intraday-analyzer/settings/secrets/actions
2. Click "New repository secret"
3. Name: `AZURE_CREDENTIALS`
4. Value: Paste the entire JSON blob
5. Click "Add secret"

### **Step 4: Update Workflow File**

The script will output these values. Update `.github/workflows/azure-deploy.yml`:

```yaml
env:
  AZURE_CONTAINER_REGISTRY: niftytrader  # From script output
  RESOURCE_GROUP: rg-nifty-trader         # From script output
  CONTAINER_APP_NAME: nifty-trader-app    # From script output
  IMAGE_NAME: nifty-analyzer              # From script output
```

### **Step 5: Test Deployment**

```bash
# Commit and push
git add .
git commit -m "feat: Enable Azure auto-deploy"
git push origin main

# Watch the deployment
# Go to: https://github.com/rajesh30april/nifty-intraday-analyzer/actions
```

**Your app will be live in ~2-5 minutes!** ✅

---

## 🔍 Verify Deployment

**Check your live app:**

```bash
# Get your app URL
az containerapp show \
  --name nifty-trader-app \
  --resource-group rg-nifty-trader \
  --query properties.configuration.ingress.fqdn \
  -o tsv

# Test health endpoint
curl https://YOUR_APP_URL/health
```

**Expected response:**
```json
{"status": "healthy", "timestamp": "..."}
```

---

## 📊 Monitor Your App

### **GitHub Actions Dashboard**
https://github.com/rajesh30april/nifty-intraday-analyzer/actions

**You'll see:**
- ✅ Build status
- ✅ Deployment logs
- ✅ Time taken
- ✅ Errors (if any)

### **Azure Portal**
https://portal.azure.com

**Navigate to:**
1. Resource Groups → `rg-nifty-trader`
2. Click `nifty-trader-app`
3. See:
   - Live logs
   - Metrics (CPU, memory, requests)
   - Revisions (deployment history)
   - Scale settings

---

## 💰 Cost Breakdown

| Resource | Tier | Monthly Cost |
|----------|------|-------------|
| Container Registry | Basic | ~$5 |
| Container App | 0.5 CPU, 1GB RAM | ~$10-20 |
| Log Analytics | Pay-per-use | ~$1-3 |
| **Total** | | **~$16-28/month** |

**Within your $10-30 budget!** ✅

### **Free Tier Option** (First Month)

If you're on Azure free tier:
- First month: FREE (using credits)
- Container App: 180,000 vCPU-seconds free/month
- Registry: 10GB storage free

---

## 🔧 Common Commands

### **View Logs**
```bash
# Stream live logs
az containerapp logs show \
  --name nifty-trader-app \
  --resource-group rg-nifty-trader \
  --follow

# View recent logs
az containerapp logs show \
  --name nifty-trader-app \
  --resource-group rg-nifty-trader \
  --tail 100
```

### **Restart App**
```bash
az containerapp revision restart \
  --name nifty-trader-app \
  --resource-group rg-nifty-trader
```

### **Scale App**
```bash
# Scale to 2-5 replicas
az containerapp update \
  --name nifty-trader-app \
  --resource-group rg-nifty-trader \
  --min-replicas 2 \
  --max-replicas 5
```

### **Check Status**
```bash
az containerapp show \
  --name nifty-trader-app \
  --resource-group rg-nifty-trader \
  --query "{status:properties.runningStatus, replicas:properties.runningStatus.replicas, url:properties.configuration.ingress.fqdn}"
```

---

## 🐛 Troubleshooting

### **Build Fails**

**Problem:** Docker build fails in GitHub Actions

**Solution:**
```bash
# Test build locally first
docker build -t test .

# If it fails locally, fix Dockerfile
# Then commit and push
```

### **Deployment Fails**

**Problem:** App deployed but health check fails

**Solution:**
```bash
# Check app logs
az containerapp logs show \
  --name nifty-trader-app \
  --resource-group rg-nifty-trader \
  --tail 50

# Common issues:
# 1. Port mismatch (should be 8000)
# 2. Missing environment variables
# 3. Database connection issues
```

### **GitHub Actions Fails**

**Problem:** "Error: AZURE_CREDENTIALS not found"

**Solution:**
1. Verify secret exists: https://github.com/rajesh30april/nifty-intraday-analyzer/settings/secrets/actions
2. Name must be EXACTLY: `AZURE_CREDENTIALS`
3. Value must be the full JSON (including curly braces)

### **App is Slow**

**Problem:** Response times > 5 seconds

**Solution:**
```bash
# Increase CPU/memory
az containerapp update \
  --name nifty-trader-app \
  --resource-group rg-nifty-trader \
  --cpu 1.0 \
  --memory 2.0Gi
```

---

## 🔒 Security Best Practices

### **Secrets Management**

**DO:**
- ✅ Store API keys in Azure Key Vault
- ✅ Use managed identities
- ✅ Rotate credentials regularly

**DON'T:**
- ❌ Commit secrets to Git
- ❌ Hardcode API keys in code
- ❌ Share service principal credentials

### **Add Secrets to Container App**

```bash
# Example: Add Kite API credentials
az containerapp secret set \
  --name nifty-trader-app \
  --resource-group rg-nifty-trader \
  --secrets \
    kite-api-key="YOUR_API_KEY" \
    kite-api-secret="YOUR_API_SECRET"

# Then update app to use them
az containerapp update \
  --name nifty-trader-app \
  --resource-group rg-nifty-trader \
  --set-env-vars \
    KITE_API_KEY=secretref:kite-api-key \
    KITE_API_SECRET=secretref:kite-api-secret
```

---

## 🗑️ Cleanup (Delete Everything)

**If you want to delete all resources:**

```bash
# Delete entire resource group (removes everything!)
az group delete \
  --name rg-nifty-trader \
  --yes \
  --no-wait

# Delete service principal
az ad sp delete \
  --id sp-nifty-trader-github
```

**Cost savings:** $0/month after deletion!

---

## 📚 Learn More

- **Azure Container Apps Docs:** https://docs.microsoft.com/azure/container-apps/
- **GitHub Actions:** https://docs.github.com/actions
- **Docker Best Practices:** https://docs.docker.com/develop/dev-best-practices/

---

## ✅ Success Checklist

- [ ] Azure CLI installed
- [ ] Ran `./azure-setup.sh` successfully
- [ ] Added `AZURE_CREDENTIALS` to GitHub secrets
- [ ] Updated `azure-deploy.yml` with resource names
- [ ] Pushed to main branch
- [ ] GitHub Actions build succeeded
- [ ] App deployed successfully
- [ ] Health check passes: `curl https://YOUR_APP.azurecontainerapps.io/health`
- [ ] Auto-trader running live on Azure!

---

## 🆘 Need Help?

**Check these files:**
- `azure-config.txt` - Your resource names and URLs
- `azure-setup.sh` - Setup script (re-runnable)
- `.github/workflows/azure-deploy.yml` - Pipeline configuration

**Common Issues:**
- GitHub Actions fails → Check secrets
- App won't start → Check logs with `az containerapp logs show`
- High costs → Scale down replicas or delete unused resources

---

**Ready? Run `./azure-setup.sh` now!** 🚀

#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# Azure Container Apps Deployment Setup
# Creates all required Azure resources for CI/CD pipeline
# ═══════════════════════════════════════════════════════════════════

set -e  # Exit on any error

# ── Configuration ──────────────────────────────────────────────────
APP_NAME="nifty-trader"              # Your app name (DNS-safe)
REGION="eastus"                      # Azure region (cheap & fast)
RESOURCE_GROUP="rg-${APP_NAME}"
ACR_NAME="${APP_NAME//-/}"           # ACR names can't have hyphens
CONTAINER_APP_ENV="${APP_NAME}-env"
CONTAINER_APP_NAME="${APP_NAME}-app"
IMAGE_NAME="nifty-analyzer"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}🚀 Azure Container Apps Setup${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${YELLOW}Configuration:${NC}"
echo "  App Name:         $APP_NAME"
echo "  Region:           $REGION"
echo "  Resource Group:   $RESOURCE_GROUP"
echo "  ACR Name:         $ACR_NAME"
echo "  Container App:    $CONTAINER_APP_NAME"
echo ""
read -p "Press Enter to continue or Ctrl+C to abort..."
echo ""

# ── Check Azure CLI ────────────────────────────────────────────────
echo -e "${BLUE}📦 Checking Azure CLI...${NC}"
if ! command -v az &> /dev/null; then
    echo -e "${RED}❌ Azure CLI not found!${NC}"
    echo "Install: brew install azure-cli (Mac) or see https://aka.ms/install-azure-cli"
    exit 1
fi
echo -e "${GREEN}✅ Azure CLI found: $(az --version | head -1)${NC}"
echo ""

# ── Login to Azure ─────────────────────────────────────────────────
echo -e "${BLUE}🔐 Checking Azure login...${NC}"
if ! az account show &> /dev/null; then
    echo -e "${YELLOW}⚠️  Not logged in. Opening browser for authentication...${NC}"
    az login
fi

SUBSCRIPTION=$(az account show --query name -o tsv)
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
echo -e "${GREEN}✅ Logged in to: $SUBSCRIPTION${NC}"
echo -e "${GREEN}   Subscription ID: $SUBSCRIPTION_ID${NC}"
echo ""

# ── Install Container Apps Extension ───────────────────────────────
echo -e "${BLUE}🔧 Installing/updating Container Apps extension...${NC}"
az extension add --name containerapp --upgrade --yes &> /dev/null || true
echo -e "${GREEN}✅ Extension ready${NC}"
echo ""

# ── Register Providers ─────────────────────────────────────────────
echo -e "${BLUE}📋 Registering Azure providers...${NC}"
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
echo -e "${GREEN}✅ Providers registered${NC}"
echo ""

# ── Create Resource Group ──────────────────────────────────────────
echo -e "${BLUE}📁 Creating Resource Group: $RESOURCE_GROUP${NC}"
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$REGION" \
  --output none
echo -e "${GREEN}✅ Resource Group created${NC}"
echo ""

# ── Create Container Registry ──────────────────────────────────────
echo -e "${BLUE}🐳 Creating Azure Container Registry: $ACR_NAME${NC}"
az acr create \
  --name "$ACR_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$REGION" \
  --sku Basic \
  --admin-enabled true \
  --output none

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
echo -e "${GREEN}✅ ACR created: $ACR_LOGIN_SERVER${NC}"
echo ""

# ── Create Container App Environment ───────────────────────────────
echo -e "${BLUE}🌐 Creating Container App Environment: $CONTAINER_APP_ENV${NC}"
az containerapp env create \
  --name "$CONTAINER_APP_ENV" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$REGION" \
  --output none
echo -e "${GREEN}✅ Environment created${NC}"
echo ""

# ── Build and push initial image ───────────────────────────────────
echo -e "${BLUE}🏗️  Building initial Docker image...${NC}"
echo "   This may take 2-3 minutes..."
az acr build \
  --registry "$ACR_NAME" \
  --image "${IMAGE_NAME}:latest" \
  --file Dockerfile \
  . \
  --output table
echo -e "${GREEN}✅ Image built and pushed to ACR${NC}"
echo ""

# ── Create Container App ───────────────────────────────────────────
echo -e "${BLUE}☁️  Creating Container App: $CONTAINER_APP_NAME${NC}"

ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query passwords[0].value -o tsv)

az containerapp create \
  --name "$CONTAINER_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$CONTAINER_APP_ENV" \
  --image "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:latest" \
  --registry-server "$ACR_LOGIN_SERVER" \
  --registry-username "$ACR_USERNAME" \
  --registry-password "$ACR_PASSWORD" \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 3 \
  --cpu 0.5 \
  --memory 1.0Gi \
  --output none

APP_URL=$(az containerapp show \
  --name "$CONTAINER_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn \
  -o tsv)

echo -e "${GREEN}✅ Container App created${NC}"
echo -e "${GREEN}🌐 App URL: https://$APP_URL${NC}"
echo ""

# ── Create Service Principal for GitHub Actions ────────────────────
echo -e "${BLUE}🔑 Creating Service Principal for GitHub Actions...${NC}"

SP_NAME="sp-${APP_NAME}-github"
SCOPE="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}"

# Create service principal with Contributor role
SP_JSON=$(az ad sp create-for-rbac \
  --name "$SP_NAME" \
  --role Contributor \
  --scopes "$SCOPE" \
  --sdk-auth)

echo -e "${GREEN}✅ Service Principal created${NC}"
echo ""

# ── Output GitHub Secrets ──────────────────────────────────────────
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}🎉 Setup Complete!${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${YELLOW}📋 Next Steps:${NC}"
echo ""
echo "1️⃣  Add these secrets to GitHub:${NC}"
echo "   Go to: https://github.com/rajesh30april/nifty-intraday-analyzer/settings/secrets/actions"
echo ""
echo -e "   ${YELLOW}Secret Name: AZURE_CREDENTIALS${NC}"
echo "   Value:"
echo "$SP_JSON"
echo ""
echo -e "2️⃣  Update .github/workflows/azure-deploy.yml:${NC}"
echo ""
echo "   env:"
echo "     AZURE_CONTAINER_REGISTRY: $ACR_NAME"
echo "     RESOURCE_GROUP: $RESOURCE_GROUP"
echo "     CONTAINER_APP_NAME: $CONTAINER_APP_NAME"
echo "     IMAGE_NAME: $IMAGE_NAME"
echo ""
echo -e "3️⃣  Your app is live at:${NC}"
echo -e "   ${GREEN}https://$APP_URL${NC}"
echo ""
echo -e "4️⃣  Test the deployment:${NC}"
echo "   curl https://$APP_URL/health"
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}💰 Cost Estimate: ~$15-25/month${NC}"
echo "   - Container Registry (Basic): ~$5/month"
echo "   - Container App (0.5 CPU, 1GB): ~$10-20/month"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"

# Save config to file for reference
cat > azure-config.txt << EOF
# Azure Deployment Configuration
# Generated: $(date)

RESOURCE_GROUP=$RESOURCE_GROUP
REGION=$REGION
ACR_NAME=$ACR_NAME
ACR_LOGIN_SERVER=$ACR_LOGIN_SERVER
CONTAINER_APP_ENV=$CONTAINER_APP_ENV
CONTAINER_APP_NAME=$CONTAINER_APP_NAME
APP_URL=https://$APP_URL
IMAGE_NAME=$IMAGE_NAME
SUBSCRIPTION_ID=$SUBSCRIPTION_ID

# GitHub Secret (copy to GitHub Actions secrets)
AZURE_CREDENTIALS<<CREDENTIALS
$SP_JSON
CREDENTIALS
EOF

echo -e "${GREEN}📄 Configuration saved to: azure-config.txt${NC}"
echo ""

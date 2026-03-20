#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# Inevitable - Azure Container Apps Deployment Script
# Manual deployment for initial setup or troubleshooting
# ═══════════════════════════════════════════════════════════════════

set -e  # Exit on error

# ── Configuration (EDIT THESE) ──────────────────────────────────────────
ACR_NAME="<YOUR_ACR_NAME>"                  # e.g., inevitable
RESOURCE_GROUP="<YOUR_RESOURCE_GROUP>"      # e.g., rg-inevitable
LOCATION="eastus"                           # Azure region
CONTAINER_APP_ENV="inevitable-env"          # Container Apps Environment
CONTAINER_APP_NAME="inevitable-trader"      # Your app name
IMAGE_NAME="inevitable"

# ── Colors ─────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}🚀 Inevitable - Azure Container Apps Deployment${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo ""

# ── Check Azure CLI ─────────────────────────────────────────────────
if ! command -v az &> /dev/null; then
    echo -e "${RED}❌ Azure CLI not found. Install: https://aka.ms/install-az-cli${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Azure CLI installed"

# ── Check Docker ───────────────────────────────────────────────────
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker not found. Install: https://docs.docker.com/get-docker/${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Docker installed"
echo ""

# ── Step 1: Azure Login ─────────────────────────────────────────────
echo -e "${YELLOW}[①/6]${NC} Logging into Azure..."
az login
echo -e "${GREEN}✓${NC} Azure login successful"
echo ""

# ── Step 2: Create Resource Group ────────────────────────────────────
echo -e "${YELLOW}[②/6]${NC} Creating resource group '${RESOURCE_GROUP}'..."
az group create --name $RESOURCE_GROUP --location $LOCATION
echo -e "${GREEN}✓${NC} Resource group created"
echo ""

# ── Step 3: Create Container Registry ────────────────────────────────
echo -e "${YELLOW}[③/6]${NC} Creating Azure Container Registry '${ACR_NAME}'..."
az acr create \
  --resource-group $RESOURCE_GROUP \
  --name $ACR_NAME \
  --sku Basic \
  --admin-enabled true

echo -e "${GREEN}✓${NC} Container Registry created"
echo ""

# ── Step 4: Build and Push Docker Image ───────────────────────────────
echo -e "${YELLOW}[④/6]${NC} Building and pushing Docker image..."
az acr login --name $ACR_NAME

IMAGE_TAG="${ACR_NAME}.azurecr.io/${IMAGE_NAME}:latest"
docker build -t $IMAGE_TAG .
docker push $IMAGE_TAG

echo -e "${GREEN}✓${NC} Image pushed to ACR: ${IMAGE_TAG}"
echo ""

# ── Step 5: Create Container Apps Environment ───────────────────────────
echo -e "${YELLOW}[⑤/6]${NC} Creating Container Apps Environment..."
az containerapp env create \
  --name $CONTAINER_APP_ENV \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION

echo -e "${GREEN}✓${NC} Container Apps Environment created"
echo ""

# ── Step 6: Create Container App ────────────────────────────────────
echo -e "${YELLOW}[⑥/6]${NC} Deploying Container App..."

# Get ACR credentials
ACR_USERNAME=$(az acr credential show --name $ACR_NAME --query username --output tsv)
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query passwords[0].value --output tsv)

az containerapp create \
  --name $CONTAINER_APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --environment $CONTAINER_APP_ENV \
  --image $IMAGE_TAG \
  --registry-server "${ACR_NAME}.azurecr.io" \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --target-port 8000 \
  --ingress external \
  --cpu 1.0 \
  --memory 2Gi \
  --min-replicas 1 \
  --max-replicas 3

echo -e "${GREEN}✓${NC} Container App deployed"
echo ""

# ── Get App URL ────────────────────────────────────────────────────
APP_URL=$(az containerapp show \
  --name $CONTAINER_APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --query properties.configuration.ingress.fqdn \
  --output tsv)

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}🎉 Deployment Complete!${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${GREEN}🌐 Your app is live at:${NC}"
echo -e "${BLUE}https://${APP_URL}${NC}"
echo ""
echo -e "${YELLOW}🔧 Next Steps:${NC}"
echo -e "1. Set environment secrets:"
echo -e "   ${BLUE}az containerapp secret set --name $CONTAINER_APP_NAME --resource-group $RESOURCE_GROUP --secrets kite-api-key=YOUR_KEY kite-api-secret=YOUR_SECRET${NC}"
echo ""
echo -e "2. View logs:"
echo -e "   ${BLUE}az containerapp logs show --name $CONTAINER_APP_NAME --resource-group $RESOURCE_GROUP --tail 100${NC}"
echo ""
echo -e "3. Update app:"
echo -e "   ${BLUE}az containerapp update --name $CONTAINER_APP_NAME --resource-group $RESOURCE_GROUP --image $IMAGE_TAG${NC}"
echo ""
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"

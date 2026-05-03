#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Inevitable — One-time Vultr Server Setup Script
# Run this ONCE on the server via Vultr console
# Server: 139.84.212.110
# ═══════════════════════════════════════════════════════════════

set -e

echo "═══════════════════════════════════════════════════════"
echo "🚀 Setting up Inevitable Trading Server"
echo "═══════════════════════════════════════════════════════"

# ── 1. Update system ─────────────────────────────────────────
echo "📦 Updating system..."
apt-get update && apt-get upgrade -y

# ── 2. Install Docker ────────────────────────────────────────
echo "🐳 Installing Docker..."
apt-get install -y ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io
systemctl enable docker
systemctl start docker
echo "✅ Docker installed!"

# ── 3. Create app directory ───────────────────────────────────
echo "📁 Creating app directory..."
mkdir -p /app
cd /app

# ── 4. Clone repo ─────────────────────────────────────────────
echo "📥 Cloning repository..."
git clone https://github.com/rajesh30april/nifty-intraday-analyzer.git inevitable
cd inevitable
echo "✅ Repo cloned!"

# ── 5. Create .env file ───────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo "⚙️  Creating .env file — fill in your credentials!"
echo "═══════════════════════════════════════════════════════"
cat > /app/inevitable/.env << 'EOF'
# ── Zerodha Kite Credentials ──────────────────────────────────
KITE_API_KEY=your_api_key_here
KITE_API_SECRET=your_api_secret_here
KITE_ACCESS_TOKEN=your_access_token_here

# ── Trading Settings ──────────────────────────────────────────
LIVE_TRADING=false
DEFAULT_QUANTITY=780
TRADING_CAPITAL=96000
SL_POINTS=30
TRAILING_SL_POINTS=15
RR_RATIO=2.0
MAX_ORDERS_PER_DAY=3

# ── Crude Settings ────────────────────────────────────────────
CRUDE_LIVE=false
CRUDE_CAPITAL=50000
EOF
echo "✅ .env file created at /app/inevitable/.env"
echo "⚠️  Edit it with: nano /app/inevitable/.env"

# ── 6. Set up SSH key for GitHub Actions ─────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo "🔑 Generating SSH key for GitHub Actions..."
echo "═══════════════════════════════════════════════════════"
ssh-keygen -t ed25519 -C "github-actions-deploy" -f /root/.ssh/github_actions -N ""
cat /root/.ssh/github_actions.pub >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

echo ""
echo "═══════════════════════════════════════════════════════"
echo "📋 COPY THIS PRIVATE KEY → GitHub Secret VULTR_SSH_KEY"
echo "═══════════════════════════════════════════════════════"
cat /root/.ssh/github_actions
echo "═══════════════════════════════════════════════════════"

# ── 7. Build and start app ────────────────────────────────────
echo ""
echo "🏗️  Building Docker image (first time takes ~5 mins)..."
cd /app/inevitable
docker build -t inevitable:latest .

echo "▶️  Starting app..."
docker run -d \
  --name inevitable \
  --restart unless-stopped \
  -p 8000:8000 \
  --env-file /app/inevitable/.env \
  -v /app/inevitable/data:/app/data \
  -v /app/inevitable/logs:/app/logs \
  inevitable:latest

echo ""
echo "═══════════════════════════════════════════════════════"
echo "✅ Setup Complete!"
echo "🌐 App running at: http://139.84.212.110:8000"
echo "🔒 Static IP: 139.84.212.110 — whitelist in Zerodha!"
echo ""
echo "Next steps:"
echo "1. Edit .env:  nano /app/inevitable/.env"
echo "2. Add VULTR_SSH_KEY to GitHub secrets"
echo "3. Every git push auto-deploys! 🚀"
echo "═══════════════════════════════════════════════════════"

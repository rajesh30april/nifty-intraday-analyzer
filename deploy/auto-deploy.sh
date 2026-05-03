#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Inevitable — Auto Deploy Script
# Runs every minute via cron. If new code on GitHub → redeploy!
# No SSH keys, no GitHub Actions needed!
# ═══════════════════════════════════════════════════════════════

cd /app/inevitable

# Check if there are new commits on GitHub
git fetch origin main --quiet

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0  # No changes, do nothing
fi

echo "$(date) — New code detected! Deploying..."

# Pull latest code
git pull origin main

# Build new Docker image
docker build -t inevitable:latest .

# Restart container
docker stop inevitable 2>/dev/null || true
docker rm inevitable 2>/dev/null || true
docker run -d \
    --name inevitable \
    --restart unless-stopped \
    -p 8000:8000 \
    --env-file /app/inevitable/.env \
    -v /app/inevitable/data:/app/data \
    -v /app/inevitable/logs:/app/logs \
    inevitable:latest

echo "$(date) — Deploy done! App restarted."

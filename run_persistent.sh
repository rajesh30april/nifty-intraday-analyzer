#!/bin/bash
# 🔋 Nifty Auto-Trader - Persistent Runner
# Keeps your app running even when Mac wants to sleep!

cd "$(dirname "$0")"

# Kill any existing instances
echo "🔄 Stopping existing instances..."
lsof -ti:8000 2>/dev/null | xargs kill -9 2>/dev/null
lsof -ti:5000 2>/dev/null | xargs kill -9 2>/dev/null
sleep 2

# Activate virtual environment
source .venv/bin/activate

# Start server with caffeinate to prevent system sleep
echo "🚀 Starting Nifty Auto-Trader (persistent mode)..."
echo "📍 Server will run at: http://localhost:8000"
echo "💡 Your Mac will NOT sleep while server is running!"
echo ""
echo "To stop: Press Ctrl+C or run: lsof -ti:8000 | xargs kill"
echo ""

# caffeinate prevents Mac from sleeping while the command runs
# -d prevents display sleep
# -i prevents idle sleep
# -m prevents disk sleep
caffeinate -dims python start.py
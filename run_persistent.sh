#!/bin/bash
# 🔋 Nifty Auto-Trader - Persistent Runner
# Keeps your app running even when Mac wants to sleep!

cd "$(dirname "$0")"

# 🐶 SMART CHECK: Only kill if needed, not blindly!
if lsof -ti:8000 >/dev/null 2>&1; then
    echo "⚠️  Port 8000 is already in use!"
    echo ""
    echo "Options:"
    echo "  1) App is already running → Just open http://localhost:8000 ✅"
    echo "  2) Stale process → Kill and restart"
    echo ""
    read -p "Do you want to KILL and restart? (y/N): " -n 1 -r
    echo
    
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "✅ Keeping existing instance running!"
        echo "📍 Open: http://localhost:8000"
        exit 0
    fi
    
    # User confirmed - kill existing instances
    echo "🔄 Stopping existing instances..."
    
    # Method 1: Kill by port (AGGRESSIVE - kill parent processes too!)
    PORT_PIDS=$(lsof -ti:8000 2>/dev/null)
    if [ ! -z "$PORT_PIDS" ]; then
        echo "🔫 Killing processes on port 8000: $PORT_PIDS"
        echo "$PORT_PIDS" | xargs kill -9 2>/dev/null
    fi
    
    PORT_PIDS_5000=$(lsof -ti:5000 2>/dev/null)
    if [ ! -z "$PORT_PIDS_5000" ]; then
        echo "🔫 Killing processes on port 5000: $PORT_PIDS_5000"
        echo "$PORT_PIDS_5000" | xargs kill -9 2>/dev/null
    fi
    
    # Method 2: Kill by process name (including caffeinate parents!)
    pkill -9 -f "uvicorn app:app" 2>/dev/null
    pkill -9 -f "python start.py" 2>/dev/null
    pkill -9 -f "caffeinate.*python start.py" 2>/dev/null
    pkill -9 -f "Python.*start.py" 2>/dev/null
    
    # Wait for processes to fully terminate
    echo "⏳ Waiting for processes to die..."
    sleep 5
    
    # Verify ports are free (with retries!)
    for i in {1..3}; do
        if lsof -ti:8000 >/dev/null 2>&1; then
            echo "⚠️  Port 8000 STILL in use (attempt $i/3)! Forcing again..."
            REMAINING=$(lsof -ti:8000 2>/dev/null)
            echo "🔫 Killing remaining: $REMAINING"
            echo "$REMAINING" | xargs kill -9 2>/dev/null
            sleep 3
        else
            break
        fi
    done
    
    # Final check
    if lsof -ti:8000 >/dev/null 2>&1; then
        echo "❌ FAILED to free port 8000! Manual intervention needed:"
        echo "   lsof -ti:8000 | xargs kill -9"
        exit 1
    fi
    
    echo "✅ All existing instances killed!"
else
    echo "✅ Port 8000 is free - ready to start!"
fi

# Activate virtual environment
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "⚠️  No virtual environment found (.venv or venv)"
    echo "   Continuing anyway..."
fi

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
caffeinate -dims .venv/bin/python start.py
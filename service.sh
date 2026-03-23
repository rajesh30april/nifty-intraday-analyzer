#!/bin/bash
# 🚀 Nifty Auto-Trader Service Manager
# Manages the persistent background service

PLIST_FILE="$HOME/Library/LaunchAgents/com.nifty.autotrader.plist"
SOURCE_PLIST="$(dirname "$0")/com.nifty.autotrader.plist"

case "$1" in
    install)
        echo "📦 Installing Nifty Auto-Trader service..."
        
        # Create LaunchAgents directory if it doesn't exist
        mkdir -p "$HOME/Library/LaunchAgents"
        
        # Copy plist file
        cp "$SOURCE_PLIST" "$PLIST_FILE"
        echo "✅ Service file installed: $PLIST_FILE"
        
        # Load the service
        launchctl load "$PLIST_FILE"
        echo "✅ Service loaded and started!"
        echo ""
        echo "🎯 Service will:"
        echo "   - Start automatically on login"
        echo "   - Keep running in background"
        echo "   - Prevent Mac from sleeping"
        echo "   - Auto-restart if it crashes"
        echo ""
        echo "📍 Server running at: http://localhost:8000"
        echo "📝 Logs: $(dirname "$0")/logs/autotrader.log"
        ;;
        
    uninstall)
        echo "🗑️  Uninstalling Nifty Auto-Trader service..."
        launchctl unload "$PLIST_FILE" 2>/dev/null
        rm -f "$PLIST_FILE"
        echo "✅ Service uninstalled!"
        ;;
        
    start)
        echo "🚀 Starting Nifty Auto-Trader service..."
        launchctl load "$PLIST_FILE" 2>/dev/null || echo "Already running!"
        sleep 2
        echo "✅ Service started!"
        echo "📍 Server at: http://localhost:8000"
        ;;
        
    stop)
        echo "⏹️  Stopping Nifty Auto-Trader service..."
        launchctl unload "$PLIST_FILE" 2>/dev/null
        # Also kill any remaining processes
        lsof -ti:8000 2>/dev/null | xargs kill -9 2>/dev/null
        lsof -ti:5000 2>/dev/null | xargs kill -9 2>/dev/null
        echo "✅ Service stopped!"
        ;;
        
    restart)
        echo "🔄 Restarting Nifty Auto-Trader service..."
        $0 stop
        sleep 2
        $0 start
        ;;
        
    status)
        echo "📊 Nifty Auto-Trader Service Status"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        
        if launchctl list | grep -q "com.nifty.autotrader"; then
            echo "✅ Service is LOADED"
        else
            echo "❌ Service is NOT loaded"
        fi
        
        if lsof -ti:8000 >/dev/null 2>&1; then
            PID=$(lsof -ti:8000)
            echo "✅ Server is RUNNING (PID: $PID)"
            echo "📍 URL: http://localhost:8000"
        else
            echo "❌ Server is NOT running"
        fi
        
        echo ""
        echo "📝 Recent logs:"
        tail -10 "$(dirname "$0")/logs/autotrader.log" 2>/dev/null || echo "No logs yet"
        ;;
        
    logs)
        echo "📝 Tailing logs (Ctrl+C to stop)..."
        tail -f "$(dirname "$0")/logs/autotrader.log"
        ;;
        
    *)
        echo "🐶 Nifty Auto-Trader Service Manager"
        echo ""
        echo "Usage: $0 {install|uninstall|start|stop|restart|status|logs}"
        echo ""
        echo "Commands:"
        echo "  install    - Install service (auto-start on login)"
        echo "  uninstall  - Remove service"
        echo "  start      - Start the service"
        echo "  stop       - Stop the service"
        echo "  restart    - Restart the service"
        echo "  status     - Check service status"
        echo "  logs       - View live logs"
        echo ""
        exit 1
        ;;
esac
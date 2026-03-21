#!/bin/bash
# Nifty Server Control Script
# Usage: ./server.sh [start|stop|restart|status|logs]

SERVICE="com.nifty.server"
PLIST="$HOME/Library/LaunchAgents/$SERVICE.plist"

case "$1" in
    start)
        echo "🚀 Starting Nifty Server..."
        launchctl load "$PLIST" 2>/dev/null
        sleep 2
        if lsof -ti:5000 > /dev/null; then
            echo "✅ Server is running on http://localhost:5000"
        else
            echo "❌ Failed to start. Check logs: tail -f /tmp/nifty_server_error.log"
        fi
        ;;
    
    stop)
        echo "⏹  Stopping Nifty Server..."
        launchctl unload "$PLIST" 2>/dev/null
        pkill -f "uvicorn app:app"
        echo "✅ Server stopped"
        ;;
    
    restart)
        echo "🔄 Restarting Nifty Server..."
        launchctl unload "$PLIST" 2>/dev/null
        pkill -f "uvicorn app:app"
        sleep 1
        launchctl load "$PLIST"
        sleep 2
        if lsof -ti:5000 > /dev/null; then
            echo "✅ Server restarted successfully"
        else
            echo "❌ Failed to restart"
        fi
        ;;
    
    status)
        if lsof -ti:5000 > /dev/null; then
            PID=$(lsof -ti:5000 | head -1)
            echo "✅ Server is RUNNING (PID: $PID)"
            echo "📊 URL: http://localhost:5000"
        else
            echo "❌ Server is NOT running"
        fi
        ;;
    
    logs)
        echo "📋 Showing server logs (Ctrl+C to exit)..."
        tail -f /tmp/nifty_server.log
        ;;
    
    errors)
        echo "❌ Showing error logs (Ctrl+C to exit)..."
        tail -f /tmp/nifty_server_error.log
        ;;
    
    *)
        echo "Nifty Server Control"
        echo "Usage: $0 {start|stop|restart|status|logs|errors}"
        echo ""
        echo "Commands:"
        echo "  start    - Start the server as a background service"
        echo "  stop     - Stop the server"
        echo "  restart  - Restart the server"
        echo "  status   - Check if server is running"
        echo "  logs     - Show server output logs"
        echo "  errors   - Show server error logs"
        exit 1
        ;;
esac

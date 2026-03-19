#!/bin/bash
# ── Nifty Trader — macOS LaunchAgent installer ───────────────────
# Installs the server as a persistent background service that:
#   ✅ Survives screen lock
#   ✅ Auto-restarts if it crashes
#   ✅ Starts automatically on login
#   ✅ Uses caffeinate to prevent Mac from sleeping
#
# Usage:
#   ./service_install.sh install    ← register + start
#   ./service_install.sh uninstall  ← stop + remove
#   ./service_install.sh status     ← check if running
#   ./service_install.sh logs       ← tail live logs

LABEL="com.rajesh.nifty-trader"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${APP_DIR}/.venv/bin/python"
LOG_OUT="/tmp/nifty-trader.log"
LOG_ERR="/tmp/nifty-trader-err.log"

install() {
    echo "📦 Installing Nifty Trader service..."

    cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <!-- caffeinate -i prevents idle sleep so Mac stays awake while trading -->
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-i</string>
        <string>${PYTHON}</string>
        <string>${APP_DIR}/start.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${APP_DIR}</string>

    <!-- Auto-restart if it crashes -->
    <key>KeepAlive</key>
    <true/>

    <!-- Start on login -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Logs -->
    <key>StandardOutPath</key>
    <string>${LOG_OUT}</string>
    <key>StandardErrorPath</key>
    <string>${LOG_ERR}</string>

    <!-- Give it 10s before force-restart after crash -->
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
EOF

    # Load it
    launchctl unload "$PLIST" 2>/dev/null
    launchctl load -w "$PLIST"
    echo "✅ Service installed and started!"
    echo "   Logs: tail -f ${LOG_OUT}"
    echo "   App:  http://localhost:8000"
}

uninstall() {
    echo "🛑 Stopping and removing Nifty Trader service..."
    launchctl unload -w "$PLIST" 2>/dev/null
    rm -f "$PLIST"
    echo "✅ Service removed."
}

status() {
    if launchctl list | grep -q "$LABEL"; then
        PID=$(launchctl list | grep "$LABEL" | awk '{print $1}')
        echo "✅ Service is RUNNING (PID: ${PID})"
        echo "   http://localhost:8000"
    else
        echo "⏹ Service is NOT running"
    fi
}

logs() {
    echo "📋 Live logs (Ctrl+C to stop):"
    tail -f "$LOG_OUT" "$LOG_ERR"
}

case "$1" in
    install)   install   ;;
    uninstall) uninstall ;;
    status)    status    ;;
    logs)      logs      ;;
    *)
        echo "Usage: $0 {install|uninstall|status|logs}"
        exit 1
        ;;
esac

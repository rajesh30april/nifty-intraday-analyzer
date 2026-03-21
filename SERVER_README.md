# 🚀 Nifty Server - Persistent Background Service

## ✅ WHAT I DID:

Created a **macOS LaunchAgent** that runs your Nifty server **24/7** as a background service.

### 🎯 Features:
- ✅ Runs automatically on boot
- ✅ Keeps running even when you close your laptop
- ✅ Auto-restarts if it crashes
- ✅ Runs in the background (no terminal needed)
- ✅ Persists through sleep/wake cycles

---

## 🎮 CONTROL THE SERVER:

```bash
cd ~/nifty-intraday-analyzer

# Check status
./server.sh status

# Start server
./server.sh start

# Stop server
./server.sh stop

# Restart server
./server.sh restart

# View logs (live)
./server.sh logs

# View errors (live)
./server.sh errors
```

---

## 📍 FILES CREATED:

1. **`/Users/r0s0iv3/Library/LaunchAgents/com.nifty.server.plist`**
   - macOS service definition
   - Loaded automatically on boot

2. **`~/nifty-intraday-analyzer/server.sh`**
   - Control script for managing the server

3. **Logs:**
   - Output: `/tmp/nifty_server.log`
   - Errors: `/tmp/nifty_server_error.log`

---

## 🔥 CURRENT STATUS:

```bash
✅ Server is RUNNING on http://localhost:5000
```

**The server will now:**
- Start automatically when you boot your Mac
- Keep running even if you close the lid
- Auto-restart if it crashes
- Survive sleep/wake cycles

---

## 🐞 TROUBLESHOOTING:

### Server not responding?
```bash
./server.sh restart
```

### Check what went wrong:
```bash
./server.sh errors
```

### Manually reload the service:
```bash
launchctl unload ~/Library/LaunchAgents/com.nifty.server.plist
launchctl load ~/Library/LaunchAgents/com.nifty.server.plist
```

### Kill everything and start fresh:
```bash
./server.sh stop
pkill -f uvicorn
./server.sh start
```

---

## 📊 ACCESS THE APP:

Open your browser:
```
http://localhost:5000
```

---

## ⚠️ REMOVING THE SERVICE:

If you ever want to disable auto-start:
```bash
launchctl unload ~/Library/LaunchAgents/com.nifty.server.plist
rm ~/Library/LaunchAgents/com.nifty.server.plist
```

---

## 🐶 Made by Jhony (Code Puppy)
Now you can close your laptop and the server keeps running! 🎉

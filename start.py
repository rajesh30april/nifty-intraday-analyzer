"""Launcher — patches Twisted signal handling before start.

KiteConnect's WebSocket uses Twisted which tries to install SIGTERM
handlers from inside uvicorn's async thread — illegal in Python.
We patch it to a no-op so the app starts cleanly.
"""
import threading
import signal as _signal

# Monkey-patch: make signal.signal() a no-op when called from non-main thread
_real_signal = _signal.signal
def _safe_signal(sig, handler):
    if threading.current_thread() is threading.main_thread():
        return _real_signal(sig, handler)
    # Non-main thread — silently skip (Twisted trying to set SIGTERM)
_signal.signal = _safe_signal

# ── IP check — warns if Kite console needs updating ──────────────────────
from check_ip import check_ip, start_ip_watcher
check_ip(auto_open=True)          # one-shot check at startup
start_ip_watcher(interval_minutes=10)  # then re-check every 10 min in bg
# ─────────────────────────────────────────────────────────────────────────

import uvicorn
uvicorn.run("app:app", host="0.0.0.0", port=8000, log_level="warning")
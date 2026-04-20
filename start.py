"""Launcher — patches Twisted signal handling before start.

KiteConnect's WebSocket uses Twisted which tries to install signal handlers
(both signal.signal AND signal.set_wakeup_fd) from inside a non-main thread
— Python raises ValueError for both calls when not on the main thread.
We patch both to silent no-ops so the reactor starts without crashing.

Without this patch you get an infinite flood of:
  ValueError: set_wakeup_fd only works in main thread
  Connection error: 1006 - connection was closed uncleanly
"""
import threading
import signal as _signal

# ── Patch 1: signal.signal() ──────────────────────────────────────────────
_real_signal = _signal.signal
def _safe_signal(sig, handler):
    if threading.current_thread() is threading.main_thread():
        return _real_signal(sig, handler)
    # Non-main thread — silently skip (Twisted trying to set SIGTERM)
_signal.signal = _safe_signal

# ── Patch 2: signal.set_wakeup_fd() ──────────────────────────────────────
# Twisted's _signals.py line 82 calls this directly — it crashes identically
_real_wakeup = _signal.set_wakeup_fd
def _safe_set_wakeup_fd(fd, *args, **kwargs):
    if threading.current_thread() is threading.main_thread():
        return _real_wakeup(fd, *args, **kwargs)
    return -1  # Non-main thread — silently skip
_signal.set_wakeup_fd = _safe_set_wakeup_fd

# ── IP check — one-shot at startup; on-demand via UI button after that ──
from check_ip import check_ip
check_ip(auto_open=True)   # still checks once at boot so startup is safe
# Periodic watcher intentionally removed — use the "Update IP" button in UI
# ─────────────────────────────────────────────────────────────────────────

import uvicorn
uvicorn.run("app:app", host="0.0.0.0", port=8000, log_level="warning")
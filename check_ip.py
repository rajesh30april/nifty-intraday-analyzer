"""
IP Change Detector for Kite Connect
------------------------------------
Runs at startup. If your public IP has changed since last time,
it warns you loudly and opens the Kite developer console so you
can paste the new IP before trading starts.

Usage (standalone):  python check_ip.py
Auto-run via:        start.py (already hooked in)
"""

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

KITE_CONSOLE_URL = "https://developers.kite.trade/apps"
IP_CACHE_FILE    = Path(__file__).parent / ".last_known_ip.json"

# Multiple fallback services — tries each until one works
IP_SERVICES = [
    "https://checkip.amazonaws.com",   # AWS — very reliable
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
]


def get_public_ip(timeout: int = 5) -> str | None:
    for url in IP_SERVICES:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read().decode().strip()
        except Exception:
            continue
    return None


def load_cached_ip() -> dict:
    if IP_CACHE_FILE.exists():
        try:
            return json.loads(IP_CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_ip(ip: str) -> None:
    from datetime import datetime
    IP_CACHE_FILE.write_text(json.dumps({
        "ip":         ip,
        "saved_at":   datetime.now().isoformat(timespec="seconds"),
    }, indent=2))


def open_browser(url: str) -> None:
    """Open URL in default browser — works on Mac & Linux."""
    try:
        subprocess.Popen(
            ["open", url] if sys.platform == "darwin" else ["xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # Non-fatal — we already printed the URL


def check_ip(auto_open: bool = True) -> bool:
    """
    Check if public IP has changed since last run.

    Returns:
        True  — IP is same as last time (or first run saved successfully)
        False — IP changed or couldn't be fetched (needs manual action)
    """
    print("🔍 Checking public IP for Kite Connect...")

    current_ip = get_public_ip()
    if not current_ip:
        print("⚠️  Could not detect public IP (no internet?). Skipping IP check.")
        print(f"   ➜ Manually verify at: {KITE_CONSOLE_URL}")
        return False  # Warn but don't hard-block startup

    cached     = load_cached_ip()
    last_ip    = cached.get("ip")
    saved_at   = cached.get("saved_at", "never")

    if last_ip == current_ip:
        print(f"✅ IP unchanged: {current_ip}  (last verified: {saved_at})")
        return True

    # ── IP has changed (or first run) ─────────────────────────────────────────
    is_first_run = last_ip is None

    print()
    print("━" * 60)
    if is_first_run:
        print("🐶 FIRST RUN — saving your IP for future comparisons")
    else:
        print("🚨 YOUR PUBLIC IP HAS CHANGED!")
        print(f"   Old IP : {last_ip}  (from {saved_at})")
    print(f"   New IP : {current_ip}")
    print()
    print("   ➜ ACTION REQUIRED:")
    print(f"     1. Go to  {KITE_CONSOLE_URL}")
    print(f"     2. Edit your app → paste IP:  {current_ip}")
    print("     3. Save — takes ~30 seconds to apply")
    print()
    print("   ⚠️  Orders WILL FAIL until you update Kite console!")
    print("━" * 60)
    print()

    if auto_open and not is_first_run:
        print("🌐 Opening Kite console in your browser...")
        open_browser(KITE_CONSOLE_URL)

    # Save the new IP so tomorrow we compare against today's
    save_ip(current_ip)
    return is_first_run  # First run = OK to proceed; changed = warn but continue


if __name__ == "__main__":
    ok = check_ip(auto_open=True)
    sys.exit(0 if ok else 1)

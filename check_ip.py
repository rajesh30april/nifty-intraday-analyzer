"""
IP Change Detector for Kite Connect
-------------------------------------
Runs at startup. Detects your public IP, compares to last known.

- First run  → shows IP, opens Kite console, waits for you to add it
- IP changed → shows old vs new, opens console, waits for you to update
- IP same    → green light, server starts immediately
"""

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

KITE_CONSOLE_URL = "https://developers.kite.trade/apps"
IP_CACHE_FILE    = Path(__file__).parent / ".last_known_ip.json"

IP_SERVICES = [
    "https://checkip.amazonaws.com",
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


def load_cached() -> dict:
    if IP_CACHE_FILE.exists():
        try:
            return json.loads(IP_CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_ip(ip: str) -> None:
    from datetime import datetime
    IP_CACHE_FILE.write_text(json.dumps({
        "ip":       ip,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }, indent=2))


def open_browser(url: str) -> None:
    try:
        cmd = ["open", url] if sys.platform == "darwin" else ["xdg-open", url]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _banner(lines: list[str]) -> None:
    width = 62
    print("\n" + "━" * width)
    for line in lines:
        print(f"  {line}")
    print("━" * width + "\n")


def check_ip(auto_open: bool = True) -> None:
    """
    Checks public IP at startup.
    Blocks until user confirms Kite console is updated (if needed).
    Always safe to call — won't hard-crash if no internet.
    """
    print("🔍 Checking public IP for Kite Connect...")

    current_ip = get_public_ip()

    if not current_ip:
        _banner([
            "⚠️  Could not detect public IP (internet issue?).",
            f"   Check manually: {KITE_CONSOLE_URL}",
            "   Continuing startup anyway...",
        ])
        return  # Don't block — maybe Kite IP hasn't changed

    cached   = load_cached()
    last_ip  = cached.get("ip")
    saved_at = cached.get("saved_at", "never")

    # ── All good — same IP ────────────────────────────────────────────────────
    if last_ip == current_ip:
        print(f"✅ IP unchanged: {current_ip}  (last verified: {saved_at})\n")
        return

    # ── IP changed OR first run — must update Kite before trading ─────────────
    is_first_run = last_ip is None

    if is_first_run:
        _banner([
            "🐶 FIRST RUN — you need to whitelist your IP on Kite console",
            "",
            f"   Your public IP  →  {current_ip}",
            "",
            "   Steps:",
            f"   1. Go to  {KITE_CONSOLE_URL}",
            "   2. Click your app → Edit",
            f"   3. Paste this IP:  {current_ip}",
            "   4. Save",
        ])
    else:
        _banner([
            "🚨 YOUR PUBLIC IP HAS CHANGED — update Kite console NOW",
            "",
            f"   Old IP  →  {last_ip}  (from {saved_at})",
            f"   New IP  →  {current_ip}   ← paste this",
            "",
            "   Steps:",
            f"   1. Go to  {KITE_CONSOLE_URL}",
            "   2. Click your app → Edit",
            f"   3. Replace IP with:  {current_ip}",
            "   4. Save",
        ])

    if auto_open:
        print("🌐 Opening Kite console in browser...")
        open_browser(KITE_CONSOLE_URL)

    # Save IP now — so it's ready for tomorrow's comparison regardless
    save_ip(current_ip)

    # ── Block until confirmed ─────────────────────────────────────────────────
    print(f"\n   📋 Your IP to paste:  {current_ip}\n")
    try:
        input("   ⏸  Press ENTER once you've saved it in Kite console... ")
    except EOFError:
        # Non-interactive mode (e.g. launched as service) — skip wait
        print("   [Non-interactive mode] Skipping wait. Update Kite console manually!")
    print("\n✅ Continuing startup...\n")


if __name__ == "__main__":
    check_ip(auto_open=True)

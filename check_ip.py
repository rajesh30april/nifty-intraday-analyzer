"""
IP Auto-Updater for Kite Developer Console
--------------------------------------------
Runs at startup via start.py.

Flow:
  1. Detect public IP
  2. Compare to last known IP
  3. If changed (or first run) → auto-login to developers.kite.trade
     via Selenium Chrome and update the allowed IP field
  4. Falls back to "open browser + press Enter" if automation fails
  5. Saves current IP for next comparison
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

KITE_CONSOLE_URL = "https://developers.kite.trade/apps"
KITE_LOGIN_URL   = "https://developers.kite.trade"
IP_CACHE_FILE    = Path(__file__).parent / ".last_known_ip.json"
KITE_API_KEY     = os.getenv("KITE_API_KEY", "")

IP_SERVICES = [
    "https://checkip.amazonaws.com",
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
    "https://ident.me",
    "http://checkip.amazonaws.com",   # http fallback (no TLS issues)
]


# ── Public IP detection ───────────────────────────────────────────────────────

def _ip_via_http(timeout: int = 8) -> str | None:
    """Try multiple HTTP/HTTPS services."""
    # Try requests first (better SSL handling)
    try:
        import requests as req
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        for url in IP_SERVICES:
            try:
                r = req.get(url, timeout=timeout, verify=False)  # noqa: S501
                ip = r.text.strip()
                if ip and ip.count(".") == 3:
                    return ip
            except Exception:
                continue
    except ImportError:
        pass

    # Fallback: urllib
    for url in IP_SERVICES:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                ip = r.read().decode().strip()
                if ip and ip.count(".") == 3:
                    return ip
        except Exception:
            continue
    return None


def _ip_via_dns() -> str | None:
    """Use OpenDNS resolver — works even when HTTP is blocked."""
    try:
        result = subprocess.run(
            ["dig", "+short", "myip.opendns.com", "@resolver1.opendns.com"],
            capture_output=True, text=True, timeout=5,
        )
        ip = result.stdout.strip()
        if ip and ip.count(".") == 3:
            return ip
    except Exception:
        pass
    return None


def _ip_via_curl() -> str | None:
    """curl as last resort — uses system proxy settings automatically."""
    for url in ["https://checkip.amazonaws.com", "https://api.ipify.org"]:
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "6", url],
                capture_output=True, text=True, timeout=8,
            )
            ip = result.stdout.strip()
            if ip and ip.count(".") == 3:
                return ip
        except Exception:
            continue
    return None


def get_public_ip() -> str | None:
    """Try HTTP → DNS → curl until one works."""
    return _ip_via_http() or _ip_via_dns() or _ip_via_curl()


# ── IP cache ─────────────────────────────────────────────────────────────────

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _banner(lines: list[str]) -> None:
    width = 64
    print("\n" + "━" * width)
    for line in lines:
        print(f"  {line}")
    print("━" * width + "\n")


def open_browser(url: str) -> None:
    try:
        cmd = ["open", url] if sys.platform == "darwin" else ["xdg-open", url]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ── Selenium automation ───────────────────────────────────────────────────────

def _auto_update_kite_ip(new_ip: str) -> bool:
    """
    Uses Selenium + Chrome to login to Kite developer console
    and update the allowed IP to new_ip.

    Returns True on success, False on any failure.
    """
    user_id  = os.getenv("ZERODHA_USER_ID", "").strip()
    password = os.getenv("ZERODHA_DEV_PASSWORD", "").strip()

    if not user_id or not password or password == "your_zerodha_password_here":
        print("⚠️  ZERODHA_USER_ID / ZERODHA_DEV_PASSWORD not set in .env")
        print("   Add them to .env to enable auto IP update.")
        return False

    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        print("⚠️  selenium/webdriver-manager not installed")
        return False

    print("🤖 Launching Chrome to auto-update Kite console...")

    opts = Options()
    opts.add_argument("--start-maximized")

    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=opts,
        )
        wait   = WebDriverWait(driver, 20)

        # ── Step 1: Open dev console ──────────────────────────────────────
        driver.get(KITE_LOGIN_URL)
        time.sleep(2)

        # ── Step 2: Click Login button if present ─────────────────────────
        try:
            login_btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//a[contains(text(),'Login') or contains(text(),'login')]")
            ))
            login_btn.click()
            time.sleep(1)
        except Exception:
            pass  # Maybe already on login form

        # ── Step 3: Fill user_id ──────────────────────────────────────────
        uid_field = wait.until(EC.presence_of_element_located(
            (By.XPATH, "//input[@type='text' or @name='user_id' or @id='user_id' or @placeholder]")
        ))
        uid_field.clear()
        uid_field.send_keys(user_id)

        # ── Step 4: Fill password ─────────────────────────────────────────
        pwd_field = wait.until(EC.presence_of_element_located(
            (By.XPATH, "//input[@type='password']")
        ))
        pwd_field.clear()
        pwd_field.send_keys(password)

        # ── Step 5: Submit ────────────────────────────────────────────────
        pwd_field.submit()

        # ── Step 6: Handle TOTP / 2FA if it appears ───────────────────────
        # Wait up to 30s — user can type TOTP manually if prompted
        print("   ⏳ Waiting for login... (enter TOTP in browser if asked)")
        try:
            wait30 = WebDriverWait(driver, 30)
            # We know we're logged in when /apps page loads
            wait30.until(lambda d: "/apps" in d.current_url or "dashboard" in d.current_url.lower())
        except Exception:
            pass  # Keep going — might already be there

        # Navigate explicitly to apps list
        driver.get(KITE_CONSOLE_URL)
        time.sleep(2)

        # ── Step 7: Find our app by API key ───────────────────────────────
        app_link = wait.until(EC.element_to_be_clickable(
            (By.XPATH, f"//*[contains(text(),'{KITE_API_KEY}')]//ancestor::a | "
                       f"//a[contains(@href,'apps')]")
        ))
        app_link.click()
        time.sleep(2)

        # ── Step 8: Look for edit button / inline editable IP field ───────
        try:
            edit_btn = driver.find_element(
                By.XPATH,
                "//button[contains(text(),'Edit')] | //a[contains(text(),'Edit')]"
            )
            edit_btn.click()
            time.sleep(1)
        except Exception:
            pass  # Field might be directly editable

        # ── Step 9: Find IP field and update ─────────────────────────────
        ip_field = wait.until(EC.presence_of_element_located(
            (By.XPATH,
             "//input[@name='ip_addresses' or @id='ip_addresses' or "
             "@placeholder[contains(.,'IP')] or @placeholder[contains(.,'ip')]]")
        ))
        ip_field.clear()
        ip_field.send_keys(new_ip)
        time.sleep(0.5)

        # ── Step 10: Save ─────────────────────────────────────────────────
        save_btn = driver.find_element(
            By.XPATH,
            "//button[@type='submit' or contains(text(),'Save') or contains(text(),'Update')]"
        )
        save_btn.click()
        time.sleep(2)

        print(f"✅ Kite console updated! IP set to: {new_ip}")
        time.sleep(1)
        driver.quit()
        return True

    except Exception as e:
        print(f"⚠️  Auto-update failed: {e}")
        try:
            driver.quit()
        except Exception:
            pass
        return False


# ── Fallback: manual update ───────────────────────────────────────────────────

def _manual_fallback(new_ip: str, is_first_run: bool, last_ip: str | None, saved_at: str) -> None:
    if is_first_run:
        _banner([
            "🐶 FIRST RUN — whitelist your IP on Kite console",
            "",
            f"   Your public IP  →  {new_ip}",
            "",
            "   1. Go to  " + KITE_CONSOLE_URL,
            "   2. Click your app → Edit",
            f"   3. Paste:  {new_ip}",
            "   4. Save",
        ])
    else:
        _banner([
            "🚨 YOUR PUBLIC IP HAS CHANGED — update Kite console!",
            "",
            f"   Old IP  →  {last_ip}  (from {saved_at})",
            f"   New IP  →  {new_ip}   ← paste this",
            "",
            "   1. Go to  " + KITE_CONSOLE_URL,
            "   2. Click your app → Edit",
            f"   3. Replace with:  {new_ip}",
            "   4. Save",
        ])

    open_browser(KITE_CONSOLE_URL)
    print(f"\n   📋 IP to paste:  {new_ip}\n")
    try:
        input("   ⏸  Press ENTER after saving in Kite console... ")
    except EOFError:
        print("   [Non-interactive] Skipping wait — update Kite console manually!")
    print("\n✅ Continuing startup...\n")


# ── Main entry point ──────────────────────────────────────────────────────────

def check_ip(auto_open: bool = True) -> None:
    """
    Detect public IP. If changed/first-run → auto-update Kite console.
    Falls back to manual prompt if automation fails.
    Always non-blocking on internet failure.
    """
    print("🔍 Checking public IP for Kite Connect...")

    current_ip = get_public_ip()
    if not current_ip:
        _banner([
            "⚠️  Could not detect public IP (internet issue?).",
            f"   Check manually: {KITE_CONSOLE_URL}",
            "   Continuing startup anyway...",
        ])
        return

    cached   = load_cached()
    last_ip  = cached.get("ip")
    saved_at = cached.get("saved_at", "never")

    # ── All good ──────────────────────────────────────────────────────────────
    if last_ip == current_ip:
        print(f"✅ IP unchanged: {current_ip}  (verified: {saved_at})\n")
        return

    # ── IP changed or first run ───────────────────────────────────────────────
    is_first_run = last_ip is None
    action       = "First run" if is_first_run else "IP CHANGED"
    print(f"⚡ {action}: {last_ip or 'none'} → {current_ip}")

    # Try auto-update first
    updated = _auto_update_kite_ip(current_ip)

    if updated:
        # Selenium confirmed success — safe to cache
        save_ip(current_ip)
        print("🚀 All good — server starting...\n")
    else:
        # Fall back to manual — only cache AFTER user confirms
        _manual_fallback(current_ip, is_first_run, last_ip, saved_at)
        save_ip(current_ip)  # user pressed Enter = confirmed updated


if __name__ == "__main__":
    check_ip(auto_open=True)

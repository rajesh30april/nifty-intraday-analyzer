"""
IP Auto-Updater for Kite Developer Console
--------------------------------------------
Runs at startup via start.py / run_persistent.sh.

Flow:
  1. Detect public IP  (HTTP → DNS → curl fallbacks)
  2. Compare to cached IP from last run
  3. Same IP  → green, server starts immediately
  4. Changed  → Selenium opens Chrome, logs into developers.kite.trade,
                 updates the IP field, saves — zero user input needed
  5. If Selenium fails → manual fallback (open browser + press Enter)
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
    "http://checkip.amazonaws.com",    # http fallback — no TLS issues
]


# ── Public IP detection ───────────────────────────────────────────────────────

def _ip_via_http(timeout: int = 8) -> str | None:
    """Try multiple HTTP/HTTPS services using requests then urllib."""
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
    """OpenDNS trick — works even when HTTP is blocked."""
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
    """curl uses system proxy settings — last resort."""
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


# ── IP cache ──────────────────────────────────────────────────────────────────

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
    Fully automated — opens Chrome, logs into developers.kite.trade,
    updates the IP field, saves. Zero user input required.
    Returns True on success, False on any failure.
    """
    user_id  = os.getenv("ZERODHA_USER_ID", "").strip()
    password = os.getenv("ZERODHA_DEV_PASSWORD", "").strip()

    if not user_id or not password or password == "your_zerodha_password_here":
        print("⚠️  ZERODHA_USER_ID / ZERODHA_DEV_PASSWORD not set in .env")
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
        print("⚠️  Run: uv pip install selenium webdriver-manager")
        return False

    print("🤖 Auto-updating Kite console via Chrome...")

    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])

    driver = None
    try:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=opts,
        )
        W = WebDriverWait(driver, 15)

        def first(selectors: list[tuple]):
            """Try selectors in order, return first element found."""
            for by, sel in selectors:
                try:
                    return W.until(EC.presence_of_element_located((by, sel)))
                except Exception:
                    continue
            raise RuntimeError(f"None found: {selectors}")

        # ── 1. Open login page ────────────────────────────────────────────
        print("   → Opening Kite developer console...")
        driver.get(KITE_LOGIN_URL)
        time.sleep(2)

        # Click Login link if visible
        try:
            driver.find_element(
                By.XPATH,
                "//a[contains(@href,'login') or contains(text(),'Login')]"
            ).click()
            time.sleep(1.5)
        except Exception:
            pass

        # ── 2. Fill user ID ───────────────────────────────────────────────
        print("   → Logging in...")
        uid = first([
            (By.ID,           "user_id"),
            (By.NAME,         "user_id"),
            (By.CSS_SELECTOR, "input[type='email']"),
            (By.CSS_SELECTOR, "input[type='text']"),
            (By.XPATH,        "//input[not(@type='password') and not(@type='hidden')]"),
        ])
        uid.clear()
        uid.send_keys(user_id)
        time.sleep(0.3)

        # ── 3. Fill password ──────────────────────────────────────────────
        pwd = first([
            (By.ID,           "password"),
            (By.NAME,         "password"),
            (By.CSS_SELECTOR, "input[type='password']"),
        ])
        pwd.clear()
        pwd.send_keys(password)
        time.sleep(0.3)

        # ── 4. Submit ─────────────────────────────────────────────────────
        try:
            driver.find_element(
                By.CSS_SELECTOR, "button[type='submit'], input[type='submit']"
            ).click()
        except Exception:
            pwd.submit()
        time.sleep(3)

        # ── 5. Go to apps list ────────────────────────────────────────────
        print("   → Finding your app...")
        driver.get(KITE_CONSOLE_URL)
        time.sleep(2)

        # Click our app — try API key match, then name, then first app
        clicked = False
        for xpath in [
            f"//a[contains(.,'{KITE_API_KEY}')]",
            "//a[contains(.,'Inevitable')]",
            "(//a[contains(@href,'/apps/')])[1]",
        ]:
            try:
                driver.find_element(By.XPATH, xpath).click()
                clicked = True
                time.sleep(2)
                break
            except Exception:
                continue
        if not clicked:
            raise RuntimeError("Could not find app link on apps page")

        # ── 6. Click Edit if needed ───────────────────────────────────────
        print("   → Updating IP...")
        for xpath in [
            "//button[contains(text(),'Edit')]",
            "//a[contains(text(),'Edit')]",
        ]:
            try:
                driver.find_element(By.XPATH, xpath).click()
                time.sleep(1)
                break
            except Exception:
                continue

        # ── 7. Find IP field (named 'ip_addresses' per Kite console) ─────
        ip_field = first([
            (By.NAME,         "ip_addresses"),
            (By.ID,           "ip_addresses"),
            (By.CSS_SELECTOR, "input[name='ip_addresses']"),
            (By.XPATH,        "//input[contains(@placeholder,'IP')]"),
            (By.XPATH,        "//label[contains(text(),'IP')]//following::input[1]"),
        ])
        ip_field.clear()
        time.sleep(0.2)
        ip_field.send_keys(new_ip)
        time.sleep(0.3)

        # ── 8. Save ───────────────────────────────────────────────────────
        save = first([
            (By.CSS_SELECTOR, "button[type='submit']"),
            (By.XPATH,        "//button[contains(text(),'Save')]"),
            (By.XPATH,        "//button[contains(text(),'Update')]"),
            (By.XPATH,        "//input[@type='submit']"),
        ])
        save.click()
        time.sleep(2)

        print(f"   ✅ IP updated to {new_ip} in Kite console!")
        driver.quit()
        return True

    except Exception as e:
        print(f"   ⚠️  Auto-update failed: {e}")
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return False


# ── Manual fallback ───────────────────────────────────────────────────────────

def _manual_fallback(new_ip: str, is_first_run: bool,
                     last_ip: str | None, saved_at: str) -> None:
    if is_first_run:
        _banner([
            "🐶 FIRST RUN — whitelist your IP on Kite console",
            "",
            f"   Your public IP  →  {new_ip}",
            "",
            f"   1. Go to  {KITE_CONSOLE_URL}",
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
            f"   1. Go to  {KITE_CONSOLE_URL}",
            "   2. Edit your app",
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
    Detect public IP. Auto-update Kite console if changed.
    Falls back to manual prompt if Selenium fails.
    Never hard-blocks startup on network failure.
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

    # ── Same IP — nothing to do ───────────────────────────────────────────────
    if last_ip == current_ip:
        print(f"✅ IP unchanged: {current_ip}  (verified: {saved_at})\n")
        return

    # ── IP changed or first run ───────────────────────────────────────────────
    is_first_run = last_ip is None
    label        = "First run" if is_first_run else "IP CHANGED"
    print(f"⚡ {label}: {last_ip or 'none'} → {current_ip}")

    updated = _auto_update_kite_ip(current_ip)

    if updated:
        save_ip(current_ip)          # cache only after confirmed success
        print("🚀 All good — server starting...\n")
    else:
        _manual_fallback(current_ip, is_first_run, last_ip, saved_at)
        save_ip(current_ip)          # user pressed Enter = confirmed


if __name__ == "__main__":
    check_ip(auto_open=True)

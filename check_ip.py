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

Periodic watcher:
  start_ip_watcher(interval_minutes=10) launches a daemon thread that
  repeats the check every N minutes while the server is live.
"""

import json
import os
import subprocess
import sys
import time
import threading
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

KITE_CONSOLE_URL   = "https://developers.kite.trade/apps"
KITE_DEV_LOGIN_URL = "https://developers.kite.trade/login"
IP_CACHE_FILE      = Path(__file__).parent / ".last_known_ip.json"
KITE_API_KEY       = os.getenv("KITE_API_KEY", "")

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

    Key improvements over v1:
    - Navigates DIRECTLY to /login (no brittle "click Login link" dance)
    - Uses element_to_be_clickable (not just presence) — elements are typeable
    - _fill() does click → select-all → type, which works on SPAs
    - Per-selector timeout is 5 s (not 15 s) so failures fail fast
    - Saves a screenshot to /tmp on failure for easy debugging
    - Masks navigator.webdriver to avoid bot-detection blocks
    """
    user_id  = os.getenv("ZERODHA_USER_ID", "").strip()
    password = os.getenv("ZERODHA_DEV_PASSWORD", "").strip()

    if not user_id or not password or password == "your_zerodha_password_here":
        print("⚠️  ZERODHA_USER_ID / ZERODHA_DEV_PASSWORD not set in .env")
        return False

    print(f"   🔑 Credentials loaded for: {user_id}")

    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError:
        print("⚠️  Run: uv pip install selenium")
        return False

    print("🤖 Auto-updating Kite console via Chrome...")

    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    opts.add_experimental_option("useAutomationExtension", False)

    # ── Locate chromedriver locally (no network download needed) ──────────────
    _THIS_DIR = Path(__file__).parent
    _CANDIDATES = [
        _THIS_DIR / ".venv" / "bin" / "chromedriver",  # project venv (preferred)
        Path("/usr/local/bin/chromedriver"),
        Path("/opt/homebrew/bin/chromedriver"),
    ]
    # Also check PATH
    _which = subprocess.run(["which", "chromedriver"], capture_output=True, text=True)
    if _which.returncode == 0 and _which.stdout.strip():
        _CANDIDATES.insert(0, Path(_which.stdout.strip()))

    _chromedriver_path = next((p for p in _CANDIDATES if p.exists()), None)
    if not _chromedriver_path:
        print("⚠️  chromedriver not found! Run the one-time install:")
        print("   curl -L --proxy http://sysproxy.wal-mart.com:8080 \\")
        print("     https://storage.googleapis.com/chrome-for-testing-public/"
              "$(google-chrome --version | grep -oE '[0-9.]+' | head -1)/"
              "mac-arm64/chromedriver-mac-arm64.zip -o /tmp/cd.zip")
        print("   unzip /tmp/cd.zip -d /tmp/cd && cp /tmp/cd/*/chromedriver .venv/bin/")
        return False

    print(f"   🔧 Using chromedriver: {_chromedriver_path}")

    driver = None
    try:
        driver = webdriver.Chrome(
            service=Service(str(_chromedriver_path)),
            options=opts,
        )
        # Hide the webdriver flag so the site doesn't block us
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        # W5  → fast fail (5 s) for individual selector probes
        # W20 → patient wait (20 s) after page navigations
        W5  = WebDriverWait(driver, 5)
        W20 = WebDriverWait(driver, 20)

        def _clickable(by, sel, wait=None):
            """Return the first *clickable* element matching (by, sel), or None."""
            try:
                return (wait or W5).until(EC.element_to_be_clickable((by, sel)))
            except Exception:
                return None

        def _fill(element, text: str) -> None:
            """Click the field, select all existing text, then type fresh value."""
            element.click()
            time.sleep(0.2)
            element.send_keys(Keys.CONTROL + "a")  # Windows/Linux select-all
            element.send_keys(Keys.COMMAND  + "a")  # macOS select-all
            time.sleep(0.1)
            element.send_keys(text)

        # ── 1. Navigate directly to the Kite developer LOGIN page ─────────
        print("   → Opening Kite developer login page...")
        driver.get(KITE_DEV_LOGIN_URL)
        time.sleep(3)   # wait for SPA to hydrate

        # ── 2. Fill email / user ID ──────────────────────────────────────
        # Kite developer console uses name="email" / id="id_email"
        # (confirmed by DOM inspection of developers.kite.trade/login)
        print("   → Filling in email...")
        uid_el = None
        for by, sel in [
            (By.ID,           "id_email"),          # Kite dev console (primary)
            (By.NAME,         "email"),              # Kite dev console (fallback)
            (By.ID,           "user_id"),            # Kite consumer login
            (By.NAME,         "user_id"),            # Kite consumer login
            (By.CSS_SELECTOR, "input[type='text']:not([type='hidden'])"),
            (By.XPATH,        "(//input[not(@type='password') and not(@type='hidden')])[1]"),
        ]:
            uid_el = _clickable(by, sel)
            if uid_el:
                print(f"      ✓ Found email field via {by}='{sel}'")
                break

        if not uid_el:
            raise RuntimeError("Could not find email/user-id input on login page")

        _fill(uid_el, user_id)
        time.sleep(0.4)

        # ── 3. Fill password ──────────────────────────────────────────────
        # Kite dev console: id="id_password" / name="password"
        print("   → Filling in password...")
        pwd_el = None
        for by, sel in [
            (By.ID,           "id_password"),       # Kite dev console (primary)
            (By.NAME,         "password"),          # both logins
            (By.CSS_SELECTOR, "input[type='password']"),
        ]:
            pwd_el = _clickable(by, sel)
            if pwd_el:
                print(f"      ✓ Found password field via {by}='{sel}'")
                break

        if not pwd_el:
            raise RuntimeError("Could not find password input on login page")

        _fill(pwd_el, password)
        time.sleep(0.4)

        # ── 4. Submit form ────────────────────────────────────────────────
        print("   → Submitting login form...")
        submit_el = (
            _clickable(By.CSS_SELECTOR, "button[type='submit']")
            or _clickable(By.XPATH, "//button[contains(text(),'Login')]")
            or _clickable(By.XPATH, "//input[@type='submit']")
        )
        if submit_el:
            submit_el.click()
        else:
            pwd_el.send_keys(Keys.RETURN)   # last resort: Enter in password field

        time.sleep(4)   # let redirect + dashboard load

        # ── 5. Navigate to apps list ──────────────────────────────────────
        print("   → Navigating to apps list...")
        driver.get(KITE_CONSOLE_URL)
        time.sleep(3)

        # Click our app — try API key match, then name, then first app
        clicked = False
        for xpath in [
            f"//a[contains(.,'{KITE_API_KEY}')]",
            "//a[contains(.,'Inevitable')]",
            "(//a[contains(@href,'/apps/')])[1]",
        ]:
            el = _clickable(By.XPATH, xpath)
            if el:
                el.click()
                clicked = True
                time.sleep(2)
                break

        if not clicked:
            raise RuntimeError("Could not find app link on apps page")

        # ── 6. Click Edit button if present ──────────────────────────────
        print("   → Updating IP address...")
        for xpath in [
            "//button[contains(text(),'Edit')]",
            "//a[contains(text(),'Edit')]",
        ]:
            el = _clickable(By.XPATH, xpath)
            if el:
                el.click()
                time.sleep(1.5)
                break

        # ── 7. Find the IP addresses input field ──────────────────────────
        ip_el = None
        for by, sel in [
            (By.NAME,         "ip_addresses"),
            (By.ID,           "ip_addresses"),
            (By.CSS_SELECTOR, "input[name='ip_addresses']"),
            (By.XPATH,        "//input[contains(@placeholder,'IP')]"),
            (By.XPATH,        "//label[contains(text(),'IP')]//following::input[1]"),
        ]:
            ip_el = _clickable(by, sel, wait=W20)
            if ip_el:
                break

        if not ip_el:
            raise RuntimeError("Could not find IP address input field")

        _fill(ip_el, new_ip)
        time.sleep(0.4)

        # ── 8. Save ───────────────────────────────────────────────────────
        save_el = (
            _clickable(By.CSS_SELECTOR, "button[type='submit']")
            or _clickable(By.XPATH, "//button[contains(text(),'Save')]")
            or _clickable(By.XPATH, "//button[contains(text(),'Update')]")
            or _clickable(By.XPATH, "//input[@type='submit']")
        )
        if not save_el:
            raise RuntimeError("Could not find Save button")

        save_el.click()
        time.sleep(2)

        print(f"   ✅ IP updated to {new_ip} in Kite console!")
        driver.quit()
        return True

    except Exception as e:
        print(f"   ⚠️  Auto-update failed: {e}")
        if driver:
            try:
                driver.save_screenshot("/tmp/kite_ip_update_error.png")
                print("   📸 Screenshot → /tmp/kite_ip_update_error.png (for debugging)")
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


# ── Main check ────────────────────────────────────────────────────────────────

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


# ── Periodic IP watcher ───────────────────────────────────────────────────────

def start_ip_watcher(interval_minutes: int = 10) -> None:
    """
    Launch a background daemon thread that re-runs check_ip() every
    `interval_minutes` minutes while the server is live.

    - IP unchanged → silent, no log noise
    - IP changed   → Selenium auto-updates; manual fallback if that fails

    Call this ONCE in start.py, right before uvicorn.run() blocks.
    """
    def _watcher() -> None:
        while True:
            time.sleep(interval_minutes * 60)
            print(f"\n🔄 [IP Watcher] Periodic check (every {interval_minutes} min)...")
            try:
                check_ip(auto_open=False)
            except Exception as exc:
                print(f"⚠️  [IP Watcher] Unexpected error: {exc}")

    t = threading.Thread(target=_watcher, daemon=True, name="ip-watcher")
    t.start()
    print(f"👀 IP Watcher started — re-checking every {interval_minutes} minutes\n")


if __name__ == "__main__":
    check_ip(auto_open=True)

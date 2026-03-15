"""Zerodha Kite Connect integration for live market data.

Handles OAuth login flow, session management, and real-time
WebSocket tick streaming for Nifty 50.
"""

import os
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from kiteconnect import KiteConnect, KiteTicker

load_dotenv()

API_KEY = os.getenv("KITE_API_KEY", "")
API_SECRET = os.getenv("KITE_API_SECRET", "")

# Zerodha API is accessible DIRECTLY (not through Walmart proxy).
# The Walmart proxy actually BLOCKS api.kite.trade.
# So we explicitly set NO proxy for Kite requests.
NO_PROXY_FOR_KITE = {"http": None, "https": None}

# Nifty 50 instrument token (NSE index)
NIFTY_INSTRUMENT_TOKEN = 256265

# File to persist session token across restarts
SESSION_FILE = Path(__file__).parent / ".kite_session.json"


class KiteManager:
    """Manages Kite Connect authentication and live data streaming."""

    def __init__(self):
        self.kite = KiteConnect(api_key=API_KEY)
        # Bypass Walmart proxy for Zerodha (proxy blocks api.kite.trade)
        self.kite.reqsession.proxies.update(NO_PROXY_FOR_KITE)
        # Also clear any env-level proxy for this session
        self.kite.reqsession.trust_env = False
        self.access_token: str | None = None
        self.ticker: KiteTicker | None = None
        self.is_streaming = False

        # Latest tick data (updated by WebSocket)
        self.latest_tick: dict | None = None
        self.tick_history: list[dict] = []  # Store ticks for building candles

        self._load_session()

    def _load_session(self):
        """Load saved session token if still valid."""
        if SESSION_FILE.exists():
            try:
                data = json.loads(SESSION_FILE.read_text())
                saved_date = data.get("date", "")
                today = datetime.now().strftime("%Y-%m-%d")

                if saved_date == today and data.get("access_token"):
                    self.access_token = data["access_token"]
                    self.kite.set_access_token(self.access_token)
                    return
            except (json.JSONDecodeError, KeyError):
                pass

    def _save_session(self):
        """Persist session token for the day."""
        SESSION_FILE.write_text(json.dumps({
            "access_token": self.access_token,
            "date": datetime.now().strftime("%Y-%m-%d"),
        }))

    @property
    def login_url(self) -> str:
        """Get the Zerodha login URL for OAuth."""
        return self.kite.login_url()

    @property
    def is_authenticated(self) -> bool:
        """Check if we have a valid session."""
        if not self.access_token:
            return False
        try:
            self.kite.profile()
            return True
        except Exception:
            self.access_token = None
            return False

    def generate_session(self, request_token: str) -> dict:
        """Exchange request_token for access_token after OAuth callback."""
        data = self.kite.generate_session(
            request_token, api_secret=API_SECRET
        )
        self.access_token = data["access_token"]
        self.kite.set_access_token(self.access_token)
        self._save_session()
        return data

    def get_margins(self) -> dict | None:
        """Get account capital/margins from Zerodha.

        Returns equity & commodity segment details including:
        - available cash, used margin, opening balance, etc.
        """
        if not self.is_authenticated:
            return None
        try:
            margins = self.kite.margins()
            return margins
        except Exception as e:
            print(f"Margins error: {e}")
            return None

    def get_live_quote(self) -> dict | None:
        """Get current Nifty 50 quote via REST API."""
        if not self.is_authenticated:
            return None
        try:
            quotes = self.kite.quote(["NSE:NIFTY 50"])
            return quotes.get("NSE:NIFTY 50")
        except Exception as e:
            print(f"Quote error: {e}")
            return None

    def get_option_ltp(self, tradingsymbol: str) -> float | None:
        """Fetch the Last Traded Price of a single NFO option via kite.ltp().

        Uses the lightweight ltp() API (returns only last price, no depth/OI).
        Returns the LTP float or None if unauthenticated / API error.
        """
        result = self.get_options_ltp_batch([tradingsymbol])
        return result.get(tradingsymbol)

    def get_options_ltp_batch(self, tradingsymbols: list[str]) -> dict[str, float]:
        """Fetch LTPs for multiple NFO options in a single kite.ltp() call.

        Far more efficient than calling get_option_ltp() in a loop.
        kite.ltp() is the lightest Kite API — returns only last_price.

        Returns a dict {tradingsymbol: ltp_float} for all symbols that
        returned a valid price. Missing symbols are omitted (not in dict).
        """
        if not self.is_authenticated or not tradingsymbols:
            return {}
        keys = [f"NFO:{sym}" for sym in tradingsymbols]
        try:
            raw = self.kite.ltp(keys)   # single HTTP round-trip for all
            result = {}
            for sym, key in zip(tradingsymbols, keys):
                ltp = raw.get(key, {}).get("last_price")
                if isinstance(ltp, (int, float)) and ltp > 0:
                    result[sym] = float(ltp)
                    print(f"📊 LTP {sym}: ₹{ltp}")
            return result
        except Exception as e:
            print(f"⚠️ Batch LTP fetch failed: {e}")
            return {}

    def get_historical_data(
        self,
        interval: str = "5minute",
        days: int = 5,
    ) -> list[dict]:
        """Fetch historical candle data from Kite.

        Args:
            interval: 'minute', '3minute', '5minute', '15minute', '30minute', '60minute', 'day'
            days: Number of days of history.
        """
        if not self.is_authenticated:
            return []

        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)

        try:
            data = self.kite.historical_data(
                instrument_token=NIFTY_INSTRUMENT_TOKEN,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
            )
            return data
        except Exception as e:
            print(f"Historical data error: {e}")
            return []

    def start_ticker(self, on_tick_callback=None):
        """Start WebSocket ticker for real-time Nifty ticks."""
        if not self.access_token or self.is_streaming:
            return

        self.ticker = KiteTicker(API_KEY, self.access_token)
        # Note: KiteTicker uses websocket-client which respects
        # http_proxy/https_proxy env vars. We set no_proxy for
        # Zerodha domains so the WebSocket connects directly.
        os.environ["no_proxy"] = os.environ.get("no_proxy", "") + ",kite.zerodha.com,api.kite.trade,ws.kite.trade"

        def on_connect(ws, response):
            ws.subscribe([NIFTY_INSTRUMENT_TOKEN])
            ws.set_mode(ws.MODE_FULL, [NIFTY_INSTRUMENT_TOKEN])
            self.is_streaming = True
            print("\u2705 Kite WebSocket connected — streaming Nifty 50 live!")

        def on_ticks(ws, ticks):
            if ticks:
                tick = ticks[0]
                self.latest_tick = {
                    "timestamp": datetime.now().isoformat(),
                    "last_price": tick.get("last_price", 0),
                    "open": tick.get("ohlc", {}).get("open", 0),
                    "high": tick.get("ohlc", {}).get("high", 0),
                    "low": tick.get("ohlc", {}).get("low", 0),
                    "close": tick.get("ohlc", {}).get("close", 0),
                    "volume": tick.get("volume_traded", 0),
                    "change": tick.get("change", 0),
                    "change_pct": round(
                        (tick.get("change", 0) / tick.get("ohlc", {}).get("close", 1)) * 100, 2
                    ) if tick.get("ohlc", {}).get("close") else 0,
                }
                self.tick_history.append(self.latest_tick)

                # Keep only last 2000 ticks to avoid memory bloat
                if len(self.tick_history) > 2000:
                    self.tick_history = self.tick_history[-1500:]

                if on_tick_callback:
                    on_tick_callback(self.latest_tick)

        def on_close(ws, code, reason):
            self.is_streaming = False
            print(f"WebSocket closed: {code} - {reason}")

        def on_error(ws, code, reason):
            print(f"WebSocket error: {code} - {reason}")

        self.ticker.on_connect = on_connect
        self.ticker.on_ticks = on_ticks
        self.ticker.on_close = on_close
        self.ticker.on_error = on_error

        # Run ticker in a background thread
        thread = threading.Thread(target=self.ticker.connect, daemon=True)
        thread.start()

    def stop_ticker(self):
        """Stop the WebSocket ticker."""
        if self.ticker:
            self.ticker.close()
            self.is_streaming = False


# Singleton instance
kite_manager = KiteManager()

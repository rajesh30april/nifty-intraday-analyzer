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

# Nifty 50 SPOT index token (NSE) — volumes are always 0 for indices!
# Used only as a fallback when futures lookup fails.
_NIFTY_SPOT_TOKEN = 256265

# File to persist session token across restarts
SESSION_FILE = Path(__file__).parent / ".kite_session.json"

# Cache for futures token so we don't hammer the instruments API
_NIFTY_FUT_TOKEN_CACHE: dict = {"token": None, "fetched_on": None}


def _get_nifty_futures_token(kite: "KiteConnect") -> int:
    """Return the nearest-expiry Nifty futures instrument token.

    Nifty futures have real volume data; the spot index does NOT.
    Result is cached for the trading day to avoid repeated API calls.
    """
    from datetime import date

    today = date.today()
    cache = _NIFTY_FUT_TOKEN_CACHE

    if cache["token"] and cache["fetched_on"] == today:
        return cache["token"]

    try:
        instruments = kite.instruments("NFO")
        nifty_futs = [
            i for i in instruments
            if i.get("name") == "NIFTY"
            and i.get("instrument_type") == "FUT"
            and i.get("segment") == "NFO-FUT"
        ]
        if not nifty_futs:
            print("⚠️  No Nifty futures found — falling back to spot index (no volume)")
            return _NIFTY_SPOT_TOKEN

        # Pick nearest upcoming expiry
        near = min(nifty_futs, key=lambda x: x["expiry"])
        token = near["instrument_token"]
        expiry = near["expiry"]
        print(f"📅 Nifty futures token: {token} (expiry {expiry}, {near['tradingsymbol']})")

        cache["token"] = token
        cache["fetched_on"] = today
        return token

    except Exception as e:
        print(f"⚠️  Futures token lookup failed ({e}) — using spot index (no volume)")
        return _NIFTY_SPOT_TOKEN


def _make_timeout_request(session, timeout: int):
    """Wrap requests.Session.request to always inject a timeout.

    The kite-connect library never sets a timeout, so API calls can hang
    indefinitely when Kite is slow or the session has expired.
    This wrapper caps every request at `timeout` seconds.
    """
    _original = session.request

    def _request(*args, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return _original(*args, **kwargs)

    return _request


class KiteManager:
    """Manages Kite Connect authentication and live data streaming."""

    # Timeout (seconds) for all Kite REST API calls.  Prevents hangs when
    # Kite is slow or the session has expired and the server stalls.
    _KITE_TIMEOUT = 8

    def __init__(self):
        self.kite = KiteConnect(api_key=API_KEY)
        # Bypass Walmart proxy for Zerodha (proxy blocks api.kite.trade)
        self.kite.reqsession.proxies.update(NO_PROXY_FOR_KITE)
        # Also clear any env-level proxy for this session
        self.kite.reqsession.trust_env = False
        # Cap ALL Kite REST calls — no more infinite hangs
        self.kite.reqsession.request = _make_timeout_request(
            self.kite.reqsession, self._KITE_TIMEOUT
        )

        self.access_token: str | None = None
        self.ticker: KiteTicker | None = None
        self.is_streaming = False
        self._crude_option_token: int | None = None          # subscribed crude option
        self._subscribe_crude_option_fn = None               # set by start_ticker()

        # Auth cache — profile() is slow; only re-verify every 60 s
        self._auth_cache: bool | None = None
        self._auth_cache_ts: float = 0.0
        self._AUTH_TTL = 60.0   # seconds before re-calling kite.profile()

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
        """Check if we have a valid Kite session.

        Caches the result of kite.profile() for _AUTH_TTL seconds so
        we don't hammer the API (and block the event loop) on every poll.
        """
        if not self.access_token:
            self._auth_cache = False
            return False

        now = time.monotonic()
        if self._auth_cache is not None and (now - self._auth_cache_ts) < self._AUTH_TTL:
            return self._auth_cache

        # TTL expired — re-verify with a real API call (capped at _KITE_TIMEOUT)
        try:
            self.kite.profile()
            self._auth_cache    = True
            self._auth_cache_ts = now
            return True
        except Exception:
            self.access_token   = None
            self._auth_cache    = False
            self._auth_cache_ts = now
            return False

    def invalidate_auth_cache(self) -> None:
        """Force re-verification on next is_authenticated check."""
        self._auth_cache    = None
        self._auth_cache_ts = 0.0

    def generate_session(self, request_token: str) -> dict:
        """Exchange request_token for access_token after OAuth callback."""
        data = self.kite.generate_session(
            request_token, api_secret=API_SECRET
        )
        self.access_token = data["access_token"]
        self.kite.set_access_token(self.access_token)
        self._save_session()
        # New token — mark auth as valid immediately, skip profile() round-trip
        self._auth_cache    = True
        self._auth_cache_ts = time.monotonic()
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

    def get_market_date(self):
        """Return today's market date. Uses system clock (reliable enough).

        Expiry is determined from the instruments list, not this date.
        """
        from datetime import date as _date
        return _date.today()

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

    def get_india_vix(self) -> float | None:
        """Fetch live India VIX via Kite ltp() — most reliable source.

        Kite lists India VIX as a tradable NSE index: symbol 'INDIA VIX'.
        Returns the VIX float (e.g. 14.23), or None if unauthenticated / error.
        This is preferred over the NSE web scrape because:
          - Uses the existing authenticated Kite session (no extra login).
          - Works behind corporate proxies (goes through Kite's servers).
          - Zero cookie handshake overhead.
        """
        if not self.is_authenticated:
            return None
        try:
            raw = self.kite.ltp(["NSE:INDIA VIX"])
            val = raw.get("NSE:INDIA VIX", {}).get("last_price")
            if isinstance(val, (int, float)) and val > 0:
                print(f"[VIX] Kite fetch OK: {val}")
                return float(val)
        except Exception as exc:
            print(f"[VIX] Kite fetch failed: {exc}")
        return None

    def get_historical_data(
        self,
        interval: str = "5minute",
        days: int = 5,
        use_futures: bool | None = None,
    ) -> list[dict]:
        """Fetch historical candle data from Kite.

        Strategy:
          - Intraday intervals (1m/3m/5m/15m/30m/60m) → Nifty FUTURES
            → Real volume, up to ~100-400 days depending on interval.
          - Daily candles → Nifty SPOT INDEX
            → Volume is 0 for the index but OHLC goes back 2000+ days.
            → Use for long-term strategy testing where volume isn't critical.

        You can override with use_futures=True/False.

        Zerodha intraday limits:
          minute:            60 days
          3min/5min:        100 days
          15min:            200 days
          30min/60min:      400 days
          day:             2000 days

        Args:
            interval:     Kite interval string.
            days:         Number of calendar days to go back.
            use_futures:  Override auto-selection of instrument.
        """
        if not self.is_authenticated:
            return []

        # Auto-select: futures for intraday (volume!), spot for daily (history!)
        _intraday = interval not in ("day", "week")
        if use_futures is None:
            use_futures = _intraday

        token = _get_nifty_futures_token(self.kite) if use_futures else _NIFTY_SPOT_TOKEN
        if not use_futures and interval == "day":
            print(f"📅 Daily candles: using Nifty SPOT index (full 2-year history, no volume)")

        to_date   = datetime.now()
        from_date = to_date - timedelta(days=days)

        try:
            data = self.kite.historical_data(
                instrument_token=token,
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

        def _subscribe_crude_option(ws):
            """Subscribe the active crude option token to the WebSocket.

            Called on connect and whenever a new crude trade is opened.
            MCX option ticks arrive ~1 s — this is what makes real-time
            exit/trail possible without waiting for the 15-s REST poll.
            """
            from crude_trader import state as ct_state
            trade = ct_state.active_trade
            if not trade:
                return
            token = getattr(trade, '_ws_token', None)
            if not token:
                # Resolve token from instrument name
                try:
                    from crude_data import get_crude_atm_option, get_crude_spot
                    sym = trade.instrument
                    # Token was stored at entry — look it up from instrument list
                    from crude_data import _get_mcx_instruments
                    instruments = _get_mcx_instruments()
                    clean = sym.replace('MCX:', '')
                    match = next((i for i in instruments
                                  if i.get('tradingsymbol') == clean), None)
                    token = match['instrument_token'] if match else None
                except Exception as e:
                    print(f"⚠️  Crude WS: token lookup failed: {e}")
            if token:
                try:
                    ws.subscribe([token])
                    ws.set_mode(ws.MODE_LTP, [token])
                    self._crude_option_token = token
                    print(f"📡 [Crude WS] Subscribed {trade.instrument} token={token} — real-time exit active")
                except Exception as e:
                    print(f"⚠️  Crude WS subscribe failed: {e}")

        # Expose so _enter_trade can call it after opening a position
        self._subscribe_crude_option_fn = _subscribe_crude_option
        self._crude_option_token = None

        def on_connect(ws, response):
            # Always subscribe Nifty spot index for live price ticks
            ws.subscribe([_NIFTY_SPOT_TOKEN])
            ws.set_mode(ws.MODE_FULL, [_NIFTY_SPOT_TOKEN])
            self.is_streaming = True
            print("✅ Kite WebSocket connected — streaming Nifty 50 live!")

            # Subscribe Nifty option if trade already open (restart recovery)
            from auto_trader import state as at_state
            if at_state.active_option_token:
                try:
                    ws.subscribe([at_state.active_option_token])
                    ws.set_mode(ws.MODE_LTP, [at_state.active_option_token])
                    print(f"📡 [on_connect] Subscribed Nifty option token {at_state.active_option_token}")
                except Exception as e:
                    print(f"⚠️  [on_connect] Nifty option subscribe failed: {e}")

            # Subscribe Crude option if trade already open (restart recovery)
            _subscribe_crude_option(ws)

        def on_ticks(ws, ticks):
            if not ticks:
                return
            from auto_trader import state as at_state
            from crude_trader import state as ct_state, _manage_trade_by_premium

            for tick in ticks:
                token = tick.get("instrument_token")
                ltp   = tick.get("last_price", 0)

                # ── Crude option tick → real-time exit / trail ────────────
                if token and token == self._crude_option_token and ltp > 0:
                    ct_state.last_option_ltp = ltp
                    if ct_state.active_trade and ct_state.is_running:
                        try:
                            _manage_trade_by_premium(ltp, source="ws_tick")
                        except Exception as e:
                            print(f"⚠️  Crude tick exit check failed: {e}")
                    continue   # handled — skip below

                # ── Nifty option tick → update LTP directly ───────────────
                if token and token == at_state.active_option_token and ltp > 0:
                    at_state.last_option_ltp = ltp
                    continue

                # ── Nifty spot tick → existing behaviour ──────────────────
                if token == _NIFTY_SPOT_TOKEN:
                    self.latest_tick = {
                        "timestamp":  datetime.now().isoformat(),
                        "last_price": ltp,
                        "open":       tick.get("ohlc", {}).get("open",  0),
                        "high":       tick.get("ohlc", {}).get("high",  0),
                        "low":        tick.get("ohlc", {}).get("low",   0),
                        "close":      tick.get("ohlc", {}).get("close", 0),
                        "volume":     tick.get("volume_traded", 0),
                        "change":     tick.get("change", 0),
                        "change_pct": round(
                            (tick.get("change", 0) /
                             tick.get("ohlc", {}).get("close", 1)) * 100, 2
                        ) if tick.get("ohlc", {}).get("close") else 0,
                    }
                    self.tick_history.append(self.latest_tick)
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

    def subscribe_crude_option(self, token: int) -> bool:
        """Subscribe a crude option instrument token to the live WebSocket.

        Called from crude_trader._enter_trade() when a new position opens.
        Returns True if subscription succeeded.
        """
        if not self.is_streaming or not self.ticker:
            return False
        try:
            self.ticker.subscribe([token])
            self.ticker.set_mode(self.ticker.MODE_LTP, [token])
            self._crude_option_token = token
            print(f"📡 [WS] Crude option token {token} subscribed — real-time exit ACTIVE")
            return True
        except Exception as e:
            print(f"⚠️  [WS] Crude subscribe failed: {e}")
            return False

    def unsubscribe_crude_option(self) -> None:
        """Unsubscribe crude option token when position is closed."""
        if self._crude_option_token and self.is_streaming and self.ticker:
            try:
                self.ticker.unsubscribe([self._crude_option_token])
            except Exception:
                pass
        self._crude_option_token = None

    def stop_ticker(self):
        """Stop the WebSocket ticker."""
        if self.ticker:
            self.ticker.close()
            self.is_streaming = False


# Singleton instance
kite_manager = KiteManager()

"""FastAPI app for Nifty 50 Intraday Probability Analyzer.

Supports both Yahoo Finance (delayed) and Zerodha Kite Connect (live) data.
"""

import dataclasses
import pandas as pd
import traceback
import json as json_lib
import time as _time
from datetime import datetime

import numpy as np
from pathlib import Path
from fastapi import FastAPI, Request, Query, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware


class NumpyEncoder(json_lib.JSONEncoder):
    """JSON encoder that handles numpy types."""
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def safe_json_response(data: dict) -> JSONResponse:
    """Create a JSONResponse with numpy-safe serialization."""
    content = json_lib.loads(json_lib.dumps(data, cls=NumpyEncoder))
    return JSONResponse(content=content)

import asyncio

from data_fetcher import fetch_intraday_data, get_todays_data
from probability import calculate_probability
from kite_integration import kite_manager
from mtf_analysis import run_mtf_analysis
from trade_signals import analyze_trade
from pattern_detector import detect_all_patterns
from trend_health import analyze_trend_health
from auto_trader import (
    get_trader_status, start_auto_trader, stop_auto_trader,
    activate_kill_switch, configure_auto_trader, sync_from_zerodha,
    set_trade_managed, discard_trade_from_app,
    refresh_active_option_ltp,
    state as trader_state, evaluate_and_act,
    _log as _at_log,
)
from pattern_scanner import scan_patterns, TIMEFRAME_META, PATTERN_EMOJIS


async def _fetch(interval: str = "5m", period: str = "5d") -> pd.DataFrame:
    """Non-blocking wrapper — runs yfinance fetch in a thread pool."""
    return await asyncio.to_thread(fetch_intraday_data, interval=interval, period=period)


# ── Background auto-trader loop ─────────────────────────────────
from contextlib import asynccontextmanager

import math as _math


def _seconds_to_next_candle_close(candle_minutes: int = 5) -> float:
    """Return seconds until the next N-minute candle close boundary.
    E.g. for 5m candles: closes at 09:20, 09:25, 09:30 ...
    We evaluate 5 seconds AFTER close to let data propagate.
    """
    now = datetime.now()
    current_minute = now.minute
    current_second = now.second
    # Next boundary minute
    next_boundary = (_math.floor(current_minute / candle_minutes) + 1) * candle_minutes
    secs_to_boundary = (next_boundary - current_minute) * 60 - current_second + 5
    return max(secs_to_boundary, 10)  # at least 10s


async def _auto_trader_loop():
    """Background loop: evaluates strategy at every 5-min candle close.

    Syncs to clock boundaries (:00, :05, :10 ...) so evaluation always
    happens on a CLOSED candle — never mid-candle garbage.
    """
    print("🤖 Auto-trader loop started — synced to 5-min candle closes")
    while True:
        wait = _seconds_to_next_candle_close(5)
        await asyncio.sleep(wait)

        now_str = datetime.now().strftime("%H:%M:%S")
        if not trader_state.is_running:
            continue   # silent — expected when stopped
        if trader_state.kill_switch:
            _at_log("🛑", "Candle skipped", "kill switch active")
            continue
        try:
            df = await asyncio.to_thread(fetch_intraday_data, interval="5m", period="5d")
            if df is not None and not df.empty:
                price     = float(df["close"].iloc[-1])
                candle_ts = df.index[-1].strftime("%H:%M")
                has_trade = trader_state.active_trade is not None
                mode_tag  = "📊 managing trade" if has_trade else "🔍 scanning signal"
                _at_log("🕯", f"Candle {candle_ts}", f"Nifty ₹{price:.0f} — {mode_tag}")
                await asyncio.to_thread(evaluate_and_act, df, price)
                print(f"🤖 [{now_str}] CANDLE {candle_ts} | ₹{price:.0f} | {mode_tag}")
            else:
                _at_log("⚠️", f"Candle {now_str}", "no data returned from feed — check VPN/proxy")
                print(f"⚠️  [{now_str}] Candle fired but no data returned")
        except Exception as e:
            import traceback as _tb
            full_trace = _tb.format_exc()
            # Show first useful line of traceback in the event log (not just message)
            trace_lines = [l.strip() for l in full_trace.splitlines() if l.strip() and 'File' in l]
            location = trace_lines[-1] if trace_lines else ''
            _at_log("❌", "Candle error", f"{str(e)[:70]} | {location[-60:]}")
            print(f"⚠️  [{now_str}] Auto-trader loop error:\n{full_trace}")


async def _crude_trader_loop():
    """Background loop for MCX Crude Oil — synced to 5-min candle closes.

    Works identically to _auto_trader_loop but fetches MCX Crude OHLCV
    and calls evaluate_and_act_crude().  Crude trades until 11:25 PM.
    """
    from crude_trader import evaluate_and_act_crude, state as crude_state
    from crude_data import fetch_crude_intraday_data, get_crude_spot
    print("🛢️  Crude auto-trader loop started")
    while True:
        wait = _seconds_to_next_candle_close(5)
        await asyncio.sleep(wait)
        if not crude_state.is_running or crude_state.kill_switch:
            continue
        try:
            df = await asyncio.to_thread(fetch_crude_intraday_data, '5minute', 5)
            price = await asyncio.to_thread(get_crude_spot)
            if df is not None and not df.empty and price:
                candle_ts = df.index[-1].strftime("%H:%M")
                await asyncio.to_thread(evaluate_and_act_crude, df, price)
                print(f"🛢️  [CANDLE {candle_ts}] ₹{price:.0f} | trades={crude_state.orders_placed}")
        except Exception as e:
            print(f"⚠️ Crude trader loop error: {e}")


async def _crude_ltp_refresh_loop():
    """Refresh Crude Oil option LTP every 15s for live P&L display."""
    from crude_trader import state as crude_state
    from crude_data import get_crude_option_ltp, get_crude_spot
    await asyncio.sleep(4)
    while True:
        try:
            if crude_state.active_trade:
                ltp = await asyncio.to_thread(
                    get_crude_option_ltp, crude_state.active_trade.instrument
                )
                if isinstance(ltp, (int, float)) and ltp > 0:
                    crude_state.last_option_ltp = ltp
            spot = await asyncio.to_thread(get_crude_spot)
            if spot:
                crude_state.last_crude_price = spot
        except Exception as e:
            print(f"⚠️ Crude LTP refresh error: {e}")
        await asyncio.sleep(15)


async def _ltp_refresh_loop():
    """Keep option LTP fresh and log a heartbeat every 60s.

    Strategy:
    - If KiteTicker WebSocket is streaming AND option is subscribed → LTP
      arrives every ~1s automatically via on_ticks. No REST call needed.
    - If WebSocket is down (disconnected / not authenticated) → fall back
      to REST poll every 15s so P&L doesn't go stale.
    - Heartbeat log every 60s regardless of source.
    """
    await asyncio.sleep(3)   # short initial delay — let server finish startup
    _hb_tick = 0
    while True:
        if trader_state.active_trade:
            # Only hit REST if WebSocket isn't delivering option ticks
            ws_delivering = (
                kite_manager.is_streaming
                and trader_state.active_option_token is not None
            )
            if not ws_delivering:
                # Fallback: REST poll every 15s
                await asyncio.to_thread(refresh_active_option_ltp)

            # Heartbeat every ~60s (4 × 15s iterations)
            _hb_tick += 1
            if _hb_tick % 4 == 0:
                t     = trader_state.active_trade
                ltp   = trader_state.last_option_ltp
                nifty = trader_state.last_nifty_price or 0
                from auto_trader import _nifty_to_option_premium
                sl_prem  = _nifty_to_option_premium(t.stop_loss, t)
                tgt_prem = _nifty_to_option_premium(t.target, t) if t.target else None
                tgt_str  = f" | Tgt ₹{tgt_prem:.1f}" if tgt_prem else ""
                ltp_str  = f"₹{ltp:.1f}" if ltp else "–"
                src      = "WS" if ws_delivering else "REST"
                _at_log("💓", "Alive",
                        f"LTP {ltp_str} | SL Prem ₹{sl_prem:.1f}{tgt_str} | Nifty ₹{nifty:.0f} [{src}]")
        else:
            _hb_tick = 0
        await asyncio.sleep(15)


async def _maybe_start_ticker():
    """Auto-start the Kite WebSocket ticker on server startup if we already
    have a valid saved session — without waiting for the user to re-login.

    Critical: tick_guard (real-time SL/target protection on every ~1s tick)
    was previously only started inside the OAuth callback, meaning any
    server restart would silently fall back to 5-min candle-only checks.
    """
    await asyncio.sleep(2)   # let server finish binding
    from auto_trader import tick_guard
    authenticated = await asyncio.to_thread(lambda: kite_manager.is_authenticated)
    if authenticated and not kite_manager.is_streaming:
        print("🔌 [Startup] Saved session found — auto-starting Kite WebSocket ticker")
        await asyncio.to_thread(kite_manager.start_ticker, tick_guard)
    elif not authenticated:
        print("⚠️  [Startup] No valid Kite session — WebSocket not started. Login via UI.")


@asynccontextmanager
async def lifespan(_app):
    """Startup: launch background tasks. Shutdown: cancel them."""
    # Auto-resume auto-trader if it was running before server restart
    if trader_state.is_running:
        import threading
        threading.Thread(
            target=start_auto_trader,
            kwargs={"strategy_id": trader_state.selected_strategy},
            daemon=True,
            name="at-auto-resume",
        ).start()
        print("🔄 Auto-trader auto-resumed from snapshot (was running before restart)")
    task_trader       = asyncio.create_task(_auto_trader_loop())
    task_ltp          = asyncio.create_task(_ltp_refresh_loop())
    task_ticker       = asyncio.create_task(_maybe_start_ticker())
    task_crude        = asyncio.create_task(_crude_trader_loop())
    task_crude_ltp    = asyncio.create_task(_crude_ltp_refresh_loop())
    yield
    for t in (task_trader, task_ltp, task_ticker, task_crude, task_crude_ltp):
        t.cancel()
    for t in (task_trader, task_ltp, task_ticker, task_crude, task_crude_ltp):
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Nifty 50 Intraday Probability Analyzer", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Paper Trading routes ──────────────────────────────────────────
from routes_paper import router as _paper_router  # noqa: E402
app.include_router(_paper_router)


class NoCacheHTMLMiddleware(BaseHTTPMiddleware):
    """Prevent browser from caching HTML pages (so JS changes apply immediately)."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if "text/html" in response.headers.get("content-type", ""):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


app.add_middleware(NoCacheHTMLMiddleware)


# ── Simple response cache (avoid hammering Yahoo Finance) ────────
_mtf_cache: dict = {"data": None, "timestamp": 0}
MTF_CACHE_TTL = 30  # seconds


# ── Pages ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main dashboard."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "is_live": kite_manager.is_authenticated,
    })


@app.get("/patterns", response_class=HTMLResponse)
async def patterns_page(request: Request):
    """Dedicated 60-day pattern history page."""
    return templates.TemplateResponse("patterns.html", {"request": request})


# ── In-process cache for pattern scan results ───────────────────────────────
# Key: YYYY-MM-DD date string. Invalidated on server restart.
# Historical dates are stable — serve from cache on repeat scans.
_day_chart_cache: dict[str, dict] = {}


@app.get("/api/day-chart")
async def day_chart(date: str = ""):
    """Return 5m candles + patterns (5m, 15m, 1h) for a single trading day."""
    try:
        from pattern_detector import detect_all_patterns

        # ── Calculate minimal lookback period ────────────────────
        # No need to fetch 60d when we only need one day's candles.
        # Add 5 buffer days to account for weekends/holidays.
        today  = pd.Timestamp.now().date()
        target = pd.Timestamp(date).date() if date else today
        cache_key = str(target)

        # ── Return cached result if available (same day, not today) ─
        # Today's data changes throughout the day — never cache it.
        # Historical dates are stable — serve from cache on repeat scans.
        if target != today and cache_key in _day_chart_cache:
            return safe_json_response(_day_chart_cache[cache_key])

        days_back = (today - target).days + 5   # +5 buffer for weekends
        period = f"{max(days_back, 2)}d"         # at least 2d so today is included

        # ── Fetch 5m data ONCE, resample to higher timeframes ───
        # Was: 3 separate Yahoo+Kite fetches = ~16s
        # Now: 1 fetch + pandas resample = ~4s  🚀
        df5 = await asyncio.to_thread(fetch_intraday_data, period=period, interval="5m")

        if df5.empty:
            return safe_json_response({"success": False, "error": "No 5m data available"})

        # Resample 5m → 15m and 1h (no extra API calls needed)
        def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
            agg = df.resample(rule, closed="left", label="left").agg({
                "open":   "first",
                "high":   "max",
                "low":    "min",
                "close":  "last",
                "volume": "sum",
            }).dropna(subset=["close"])
            return agg[agg["close"] > 0]

        df15 = _resample(df5, "15min")
        df1h = _resample(df5, "1h")

        # Slice to target day only
        day_df5  = df5 [df5 .index.date == target]
        day_df15 = df15[df15.index.date == target] if not df15.empty else pd.DataFrame()
        day_df1h = df1h[df1h.index.date == target] if not df1h.empty else pd.DataFrame()

        if day_df5.empty:
            return safe_json_response({"success": False, "error": f"No data for {target} — market may have been closed"})

        # Build 5m candle list for the chart
        candles = [
            {
                "time":  int(ts.timestamp()),
                "open":  round(float(row["open"]),  2),
                "high":  round(float(row["high"]),  2),
                "low":   round(float(row["low"]),   2),
                "close": round(float(row["close"]), 2),
            }
            for ts, row in day_df5.iterrows()
        ]

        def _serialize_patterns(pat_result, tf):
            return [
                {
                    "name":         p.name,
                    "pattern_type": p.pattern_type,
                    "bias":         p.bias,
                    "confidence":   round(p.confidence, 2),
                    "timeframe":    tf,
                    "start_time":   p.start_time,
                    "end_time":     p.end_time,
                    "description":  p.description,
                    "key_levels":   p.key_levels or {},
                    "emoji":        PATTERN_EMOJIS.get(p.name, "📊"),
                }
                for p in pat_result.get("patterns", [])
            ]

        # ── Detect patterns on all 3 timeframes IN PARALLEL ─────
        r5, r15, r1h = await asyncio.gather(
            asyncio.to_thread(detect_all_patterns, day_df5,  timeframe="5m"),
            asyncio.to_thread(detect_all_patterns, day_df15, timeframe="15m") if not day_df15.empty else asyncio.sleep(0),
            asyncio.to_thread(detect_all_patterns, day_df1h, timeframe="1h")  if not day_df1h.empty else asyncio.sleep(0),
        )

        patterns  = _serialize_patterns(r5, "5m")
        if r15 and not day_df15.empty: patterns += _serialize_patterns(r15, "15m")
        if r1h and not day_df1h.empty: patterns += _serialize_patterns(r1h, "1h")

        # Sort chronologically
        patterns.sort(key=lambda p: p["end_time"] or "")

        result = {
            "success":  True,
            "date":     str(target),
            "candles":  candles,
            "patterns": patterns,
        }
        # Cache historical dates (today changes intraday — don't cache)
        if target != today:
            _day_chart_cache[cache_key] = result

        return safe_json_response(result)
    except Exception as e:
        return safe_json_response({"success": False, "error": str(e)})



@app.get("/api/patterns-history")
async def patterns_history(
    period: str = "60d",
    timeframes: str = "5m,15m,1h",
    chart_tf: str = "1h",
):
    """Scan last N days for chart patterns + return OHLCV candles for chart."""
    try:
        tf_list = [t.strip() for t in timeframes.split(",") if t.strip()]
        result = await asyncio.to_thread(scan_patterns, timeframes=tf_list, period=period)

        if result.error:
            return safe_json_response({"success": False, "error": result.error})

        patterns_data = [
            {
                "name":             p.name,
                "pattern_type":     p.pattern_type,
                "bias":             p.bias,
                "confidence":       p.confidence,
                "timeframe":        p.timeframe,
                "start_time":       p.start_time,
                "end_time":         p.end_time,
                "description":      p.description,
                "key_levels":       p.key_levels,
                "emoji":            p.emoji,
                "duration_candles": p.duration_candles,
                "date_label":       p.date_label,
                "end_date":         p.end_date,
            }
            for p in result.patterns
        ]

        # Fetch OHLCV candles for the price chart
        candles = []
        try:
            df_raw = await asyncio.to_thread(fetch_intraday_data, period=period, interval="5m")
            if chart_tf == "15m":
                df_chart = df_raw.resample("15min").agg(
                    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
                ).dropna(subset=["open", "close"])
            elif chart_tf == "1h":
                df_chart = df_raw.resample("1h").agg(
                    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
                ).dropna(subset=["open", "close"])
            else:
                df_chart = df_raw

            import time as _time_mod
            for ts, row in df_chart.iterrows():
                try:
                    unix = int(ts.timestamp())
                    candles.append({
                        "time":  unix,
                        "open":  round(float(row["open"]),  2),
                        "high":  round(float(row["high"]),  2),
                        "low":   round(float(row["low"]),   2),
                        "close": round(float(row["close"]), 2),
                    })
                except Exception:
                    continue
        except Exception:
            pass

        return safe_json_response({
            "success":       True,
            "total":         result.total,
            "bullish_count": result.bullish_count,
            "bearish_count": result.bearish_count,
            "neutral_count": result.neutral_count,
            "by_timeframe":  result.by_timeframe,
            "by_type":       result.by_type,
            "by_name":       result.by_name,
            "patterns":      patterns_data,
            "candles":       candles,
            "chart_tf":      chart_tf,
            "meta": {
                "trading_days": result.trading_days,
                "date_range":   result.date_range,
                "period":       period,
                "timeframes":   tf_list,
                "tf_meta": {
                    k: {
                        "label":          v["label"],
                        "best_for":       v["best_for"],
                        "signal_quality": v["signal_quality"],
                        "recommended":    v["recommended"],
                    }
                    for k, v in TIMEFRAME_META.items()
                },
            },
        })
    except Exception as e:
        return safe_json_response({"success": False, "error": str(e)})


# ── Zerodha OAuth Flow ─────────────────────────────────────────────

@app.get("/login")
async def login():
    """Redirect to Zerodha login page."""
    return RedirectResponse(url=kite_manager.login_url)


@app.get("/callback")
async def callback(request: Request):
    """Handle Zerodha OAuth callback with request_token."""
    request_token = request.query_params.get("request_token")
    status_param = request.query_params.get("status", "")
    print(f"\n🔑 Kite callback received: status={status_param}, token={'yes' if request_token else 'NO'}")

    if not request_token:
        print(f"❌ No request_token! Full URL: {request.url}")
        return RedirectResponse(url="/?error=no_token")

    try:
        print(f"🔄 Exchanging request_token for access_token...")
        kite_manager.generate_session(request_token)
        print(f"✅ Zerodha authenticated! Starting WebSocket...")
        # Wire real-time tick guard for SL/target protection on every tick
        from auto_trader import tick_guard
        kite_manager.start_ticker(on_tick_callback=tick_guard)
        return RedirectResponse(url="/?live=true")
    except Exception as e:
        print(f"❌ Auth error: {e}")
        import traceback; traceback.print_exc()
        return RedirectResponse(url=f"/?error={e}")


@app.get("/api/status")
async def status():
    """Check authentication and streaming status."""
    return {
        "authenticated": kite_manager.is_authenticated,
        "streaming": kite_manager.is_streaming,
        "data_source": "zerodha_live" if kite_manager.is_authenticated else "not_connected",
    }


# ── Account Capital / Margins ──────────────────────────────────────

@app.get("/api/margins")
async def margins():
    """Get Zerodha account capital and margin details."""
    if not kite_manager.is_authenticated:
        return {"success": False, "error": "Not authenticated with Zerodha"}

    data = kite_manager.get_margins()
    if not data:
        return {"success": False, "error": "Failed to fetch margins"}

    equity = data.get("equity", {})
    return {
        "success": True,
        "equity": {
            "available_cash": equity.get("available", {}).get("cash", 0),
            "available_margin": equity.get("available", {}).get("live_balance", 0),
            "used_margin": equity.get("utilised", {}).get("debits", 0),
            "opening_balance": equity.get("available", {}).get("opening_balance", 0),
            "collateral": equity.get("available", {}).get("collateral", 0),
            "intraday_payin": equity.get("available", {}).get("intraday_payin", 0),
        },
        "raw": data,  # full response for debugging
    }


# ── Live Tick Endpoint ─────────────────────────────────────────────

@app.get("/api/live-tick")
async def live_tick():
    """Get the latest live tick from WebSocket."""
    if not kite_manager.is_authenticated:
        return {"success": False, "error": "Not authenticated with Zerodha"}

    tick = kite_manager.latest_tick
    if not tick:
        # Fall back to REST quote
        quote = kite_manager.get_live_quote()
        if quote:
            return {
                "success": True,
                "source": "rest",
                "last_price": quote.get("last_price", 0),
                "open": quote.get("ohlc", {}).get("open", 0),
                "high": quote.get("ohlc", {}).get("high", 0),
                "low": quote.get("ohlc", {}).get("low", 0),
                "close": quote.get("ohlc", {}).get("close", 0),
                "change": quote.get("change", 0),
            }
        return {"success": False, "error": "No tick data yet"}

    return {"success": True, "source": "websocket", **tick}


# ── Analysis Endpoint (supports both data sources) ─────────────────

def _kite_history_to_dataframe(data: list[dict]) -> pd.DataFrame:
    """Convert Kite historical data to our standard DataFrame."""
    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    df.columns = [c.lower().replace(" ", "_") for c in df.columns]

    required = ["open", "high", "low", "close", "volume"]
    df = df[[c for c in required if c in df.columns]]
    return df


@app.get("/api/analyze")
async def analyze(interval: str = "5m"):
    """Run probability analysis. Requires Zerodha authentication."""
    try:
        if not kite_manager.is_authenticated:
            return {"success": False, "error": "Please login with Zerodha first"}

        # Map interval to Kite format
        kite_interval_map = {
            "1m": "minute", "5m": "5minute",
            "15m": "15minute", "30m": "30minute",
        }
        kite_interval = kite_interval_map.get(interval, "5minute")
        kite_data = kite_manager.get_historical_data(
            interval=kite_interval, days=5
        )

        if kite_data:
            df = _kite_history_to_dataframe(kite_data)
        else:
            return {"success": False, "error": "Failed to fetch data from Zerodha"}

        source = "zerodha_live"

        result = await asyncio.to_thread(calculate_probability, df)

        # If live, overlay the latest tick price
        if source == "zerodha_live" and kite_manager.latest_tick:
            result.current_price = kite_manager.latest_tick["last_price"]

        today_df = get_todays_data(df)
        price_data = []
        if not today_df.empty:
            for idx, row in today_df.iterrows():
                price_data.append({
                    "time": idx.strftime("%H:%M"),
                    "close": round(row["close"], 2),
                    "volume": int(row["volume"]),
                })

        signals_data = [
            {
                "name": s.name, "value": str(s.value), "bias": s.bias,
                "strength": s.strength, "weight": s.weight,
                "description": s.description,
            }
            for s in result.signals
        ]

        return {
            "success": True,
            "data_source": source,
            "bullish_probability": result.bullish_probability,
            "bearish_probability": result.bearish_probability,
            "overall_bias": result.overall_bias,
            "confidence": result.confidence,
            "current_price": result.current_price,
            "day_change": result.day_change,
            "day_change_pct": result.day_change_pct,
            "orb_data": result.orb_data,
            "signals": signals_data,
            "price_data": price_data,
        }
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}


# ── Live Monitor ──────────────────────────────────────────────────

import asyncio
from datetime import datetime, timezone, timedelta
from fastapi.responses import StreamingResponse

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN  = (9,  15)
MARKET_CLOSE = (15, 30)


def _is_market_hours() -> bool:
    now = datetime.now(IST)
    t   = (now.hour, now.minute)
    return now.weekday() < 5 and MARKET_OPEN <= t <= MARKET_CLOSE


def _evaluate_current(strategy_id: str = "smart_router") -> dict:
    """Fetch latest candles and evaluate the strategy. Returns a state dict."""
    import strategies.loader  # noqa
    from data_fetcher import fetch_intraday_data
    from strategies.registry import get as get_strat
    from market_regime import detect_regime

    now_ist = datetime.now(IST)
    try:
        df = fetch_intraday_data(interval="5m", period="5d")
        df.index = pd.DatetimeIndex(df.index)
        if df.empty:
            return {"error": "No data"}

        latest = df.index[-1]
        price  = float(df["close"].iloc[-1])

        strat  = get_strat(strategy_id)
        sig_fires, sig_dir, confidence = False, "", 0.0
        strat_name, strat_emoji = strategy_id, "🧠"
        regime_str = "unknown"

        if strategy_id == "smart_router":
            from strategy_meta_router import evaluate_all
            from auto_trader import state as at_state
            meta = evaluate_all(df)
            sig_fires   = meta.signal.should_enter
            sig_dir     = meta.signal.direction.value if meta.signal.direction else ""
            confidence  = meta.signal.confidence
            strat_name  = meta.selected_strategy or "smart_router"
            strat_emoji = meta.selected_emoji or "🧠"
            regime_str  = meta.regime
            # Push scores to state so UI scoreboard updates immediately
            at_state.last_meta_regime = meta.regime
            at_state.last_meta_scores = [
                {
                    "id":          s["id"],
                    "name":        s["name"],
                    "emoji":       s["emoji"],
                    "category":    s["category"],
                    "confidence":  s["confidence"],
                    "win_rate":    s.get("win_rate", 50.0),
                    "regime_fit":  s["regime_fit"],
                    "time_mult":   s["time_mult"],
                    "composite":   s["composite"],
                    "should_enter": s["should_enter"],
                    "direction":   s["direction"].value if s["direction"] else None,
                    "error":       s.get("error"),
                }
                for s in meta.scores
            ]
        elif strat:
            sig = strat.evaluate(df)
            sig_fires   = sig.should_enter
            sig_dir     = sig.direction.value if sig.direction else ""
            confidence  = sig.confidence
            strat_name  = strat.name
            strat_emoji = strat.emoji
            regime_str  = detect_regime(df).value

        return {
            "ts":           now_ist.strftime("%H:%M:%S"),
            "candle_time":  latest.strftime("%H:%M"),
            "price":        price,
            "open":         float(df["open"].iloc[-1]),
            "high":         float(df["high"].iloc[-1]),
            "low":          float(df["low"].iloc[-1]),
            "strategy":     strat_name,
            "emoji":        strat_emoji,
            "signal":       sig_fires,
            "direction":    sig_dir,
            "confidence":   round(confidence, 1),
            "regime":       regime_str,
            "market_open":  _is_market_hours(),
            "meta_scores":  [
                {"id": s["id"], "name": s["name"], "emoji": s["emoji"],
                 "composite": s["composite"], "should_enter": s["should_enter"],
                 "direction": s["direction"].value if s["direction"] else None,
                 "confidence": s["confidence"], "regime_fit": s["regime_fit"],
                 "time_mult": s["time_mult"], "error": s.get("error")}
                for s in (meta.scores if strategy_id == "smart_router" else [])
            ],
            "error":        None,
        }
    except Exception as e:
        return {"ts": now_ist.strftime("%H:%M:%S"), "error": str(e)}


@app.get("/api/live-monitor/tick")
async def live_monitor_tick(strategy: str = "smart_router"):
    """Single poll — returns current strategy evaluation."""
    return _evaluate_current(strategy)


@app.get("/api/live-monitor/stream")
async def live_monitor_stream(strategy: str = "smart_router"):
    """SSE stream — pushes a new event every 30 seconds."""
    import json as _json

    async def generator():
        # Send immediate first event
        data = _evaluate_current(strategy)
        yield f"data: {_json.dumps(data)}\n\n"

        while True:
            await asyncio.sleep(30)
            data = _evaluate_current(strategy)
            yield f"data: {_json.dumps(data)}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


@app.get("/api/trade-candles")
async def trade_candles(
    date: str,
    entry_time: str,
    exit_time: str,
    entry_price: float,
    stop_loss: float,
    target: float,
    direction: str,
    sl_points: float = 30.0,
    trailing_sl: float = 15.0,
):
    """Return candle-by-candle detail for a single trade (entry → exit).
    Used by the expandable trade rows in the backtester UI.
    """
    from data_fetcher import fetch_intraday_data
    try:
        df = fetch_intraday_data(interval="5m", period="60d")
        df.index = pd.DatetimeIndex(df.index)
        target_date = pd.Timestamp(date).date()
        day_df = df[df.index.date == target_date]

        # Filter to entry_time → exit_time window (inclusive)
        entry_ts = pd.Timestamp(f"{date} {entry_time}", tz="Asia/Kolkata")
        exit_ts  = pd.Timestamp(f"{date} {exit_time}",  tz="Asia/Kolkata")
        window   = day_df[(day_df.index >= entry_ts) & (day_df.index <= exit_ts)]

        if window.empty:
            return {"success": False, "error": "No candles in window"}

        # Walk through the window and track trailing SL
        candles  = []
        sl_level = stop_loss
        highest  = float(window["high"].iloc[0])
        lowest   = float(window["low"].iloc[0])

        for i, (ts, row) in enumerate(window.iterrows()):
            c     = float(row["close"])
            h     = float(row["high"])
            lo    = float(row["low"])
            o     = float(row["open"])
            t_str = ts.strftime("%H:%M")

            # Update trailing SL
            if direction == "long":
                highest = max(highest, h)
                new_sl  = highest - trailing_sl
                if new_sl > sl_level:
                    sl_level = new_sl
                unreal = round(c - entry_price, 2)
            else:
                lowest  = min(lowest, lo)
                new_sl  = lowest + trailing_sl
                if new_sl < sl_level:
                    sl_level = new_sl
                unreal = round(entry_price - c, 2)

            # Determine state for this candle
            state = "exit" if t_str == exit_time else ("entry" if i == 0 else "in_trade")

            candles.append({
                "time":       t_str,
                "open":       round(o, 2),
                "high":       round(h, 2),
                "low":        round(lo, 2),
                "close":      round(c, 2),
                "sl":         round(sl_level, 2),
                "target":     round(target, 2),
                "unrealized": unreal,
                "state":      state,
            })

        return {"success": True, "candles": candles, "direction": direction}
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}


class _NumpySafeEncoder(json_lib.JSONEncoder):
    """JSON encoder that handles numpy scalars and pandas Timestamps.

    Plain json.dumps crashes on numpy.float64 / numpy.int64 values that
    come out of pandas calculations in BacktestResult. This encoder
    converts them to native Python types before serialisation.
    """
    def default(self, obj):
        # numpy scalar types
        try:
            import numpy as np
            if isinstance(obj, np.integer): return int(obj)
            if isinstance(obj, np.floating): return float(obj)
            if isinstance(obj, np.ndarray):  return obj.tolist()
        except ImportError:
            pass
        # pandas Timestamp
        try:
            import pandas as pd
            if isinstance(obj, pd.Timestamp): return obj.isoformat()
        except ImportError:
            pass
        return super().default(obj)


def _sse(payload: dict) -> str:
    """Format a dict as an SSE data line (numpy-safe)."""
    return "data: " + json_lib.dumps(payload, cls=_NumpySafeEncoder) + "\n\n"


@app.get("/api/backtest/replay/stream")
async def replay_stream(
    date: str = "",
    period: str = "60d",
    sl_points: float = 30.0,
    trailing_sl: float = 15.0,
    rr_ratio: float = 2.0,
    max_trades: int = 3,
    strategy: str = "smart_router",
    data_source: str = "yahoo",
    quantity: int = 750,
):
    """SSE stream: replay day candle-by-candle with live progress."""
    from backtester import replay_day
    import queue, threading

    q: queue.Queue = queue.Queue()

    def _run():
        try:
            result = replay_day(
                date_str=date, period=period, strategy_id=strategy,
                sl_points=sl_points, trailing_sl=trailing_sl, rr_ratio=rr_ratio,
                max_trades=max_trades, data_source=data_source, quantity=quantity,
                on_progress=lambda p: q.put(("progress", p)),
            )
            q.put(("done", result))
        except Exception as exc:
            q.put(("error", str(exc)))

    threading.Thread(target=_run, daemon=True).start()

    async def _generate():
        """Poll-based SSE generator — safe across Python 3.13 asyncio."""
        deadline = 120
        waited   = 0.0
        while True:
            try:
                kind, payload = q.get_nowait()
            except queue.Empty:
                if waited >= deadline:
                    yield _sse({"phase": "error", "msg": "timeout after 120s"})
                    break
                await asyncio.sleep(0.15)
                waited += 0.15
                continue

            waited = 0.0

            if kind == "progress":
                yield _sse(payload)
            elif kind == "done":
                # replay_day returns a plain dict (already serialisable)
                frames  = payload.get("frames", [])
                trades  = payload.get("trades", [])
                summary = payload.get("summary", {})
                avail   = payload.get("available_dates", [])
                yield _sse({"phase": "done", "pct": 100, "frames": frames,
                            "trades": trades, "summary": summary,
                            "available_dates": avail})
                break
            else:
                yield _sse({"phase": "error", "msg": str(payload)})
                break

    return StreamingResponse(_generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/backtest/stream")
async def backtest_stream(
    period: str = "60d",
    sl_points: float = 30.0,
    trailing_sl: float = 15.0,
    rr_ratio: float = 2.0,
    max_trades: int = 3,
    strategy: str = "smart_router",
    data_source: str = "yahoo",
    quantity: int = 750,
):
    """SSE stream: full backtest with live day-by-day progress."""
    from backtester import run_backtest
    import queue, threading

    q: queue.Queue = queue.Queue()

    def _run():
        try:
            result = run_backtest(
                period=period, interval="5m", quantity=quantity,
                sl_points=sl_points, trailing_sl=trailing_sl, rr_ratio=rr_ratio,
                max_trades_per_day=max_trades, strategy_id=strategy,
                data_source=data_source,
                on_progress=lambda p: q.put(("progress", p)),
            )
            q.put(("done", result))
        except Exception as exc:
            q.put(("error", str(exc)))

    threading.Thread(target=_run, daemon=True).start()

    async def _generate():
        """Poll the queue with async sleep — avoids blocking asyncio's thread
        pool executor across multiple yields which breaks on Python 3.13."""
        deadline = 120   # seconds before giving up
        waited   = 0.0
        while True:
            try:
                kind, payload = q.get_nowait()
            except queue.Empty:
                if waited >= deadline:
                    yield _sse({"phase": "error", "msg": "timeout after 120s"})
                    break
                await asyncio.sleep(0.15)
                waited += 0.15
                continue

            # Reset timeout on each received item
            waited = 0.0

            if kind == "progress":
                yield _sse(payload)
            elif kind == "done":
                try:
                    trades = [dataclasses.asdict(t) for t in payload.trades]
                    cumul  = 0.0
                    equity = []
                    for t in payload.trades:
                        cumul += t.pnl_points
                        equity.append({"date": t.date, "time": t.exit_time,
                                       "cumulative": round(float(cumul), 2)})
                    summary = dataclasses.asdict(payload)
                    summary.pop("trades", None)
                    yield _sse({"phase": "done", "pct": 100, "trades": trades,
                                "equity_curve": equity, "summary": summary})
                except Exception as exc:
                    yield _sse({"phase": "error", "msg": f"serialise error: {exc}"})
                break
            else:
                yield _sse({"phase": "error", "msg": str(payload)})
                break

    return StreamingResponse(_generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/api/backtest/replay")
async def replay_day_api(
    date: str = "",
    period: str = "60d",
    sl_points: float = 30.0,
    trailing_sl: float = 15.0,
    rr_ratio: float = 2.0,
    max_trades: int = 3,
    strategy: str = "smart_router",
    data_source: str = "yahoo",
):
    """Replay a single trading day candle-by-candle for the Day Replay UI."""
    from backtester import replay_day
    try:
        result = replay_day(
            date_str=date,
            period=period,
            strategy_id=strategy,
            sl_points=sl_points,
            trailing_sl=trailing_sl,
            rr_ratio=rr_ratio,
            max_trades=max_trades,
            data_source=data_source,
        )
        return {"success": True, **result}
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}


# ── Trade Report routes ────────────────────────────────────────────────────

@app.get("/report", response_class=HTMLResponse)
async def trade_report_page(request: Request):
    """Full trade analysis report page."""
    from fastapi.templating import Jinja2Templates
    templates_local = Jinja2Templates(directory="templates")
    return templates_local.TemplateResponse("trade_report.html", {"request": request})


@app.get("/api/auto-trader/zerodha-balance")
async def get_zerodha_balance():
    """Fetch live Zerodha balance + suggest best strike for available capital."""
    from kite_integration import kite_manager
    from auto_trader import LOT_SIZE
    try:
        if not kite_manager.is_authenticated:
            return {"success": False, "error": "Not authenticated"}

        margins  = kite_manager.kite.margins()
        equity   = margins.get("equity", {})
        balance  = equity.get("available", {}).get("live_balance", 0) or equity.get("net", 0)
        balance  = round(float(balance), 2)

        # Get current Nifty price for strike estimation
        nifty_ltp = None
        try:
            q = kite_manager.kite.ltp(["NSE:NIFTY 50"])
            nifty_ltp = q.get("NSE:NIFTY 50", {}).get("last_price")
        except Exception:
            pass

        # Estimate premiums for each strike offset using rough delta approximation
        # ATM premium varies; use live LTP from instruments if possible
        suggestions = []
        OFFSETS = [
            (-2, "ITM2", "deepest value, highest cost"),
            (-1, "ITM1", "safer delta, higher cost"),
            (0,  "ATM",  "most liquid, balanced"),
            (1,  "OTM1", "cheaper, needs more move"),
            (2,  "OTM2", "lottery, big move needed"),
        ]
        # Rough premium estimate per offset (% of ATM)
        ATM_PREM_APPROX = 100  # ₹ rough ATM premium
        PREM_MULT = {-2: 2.0, -1: 1.5, 0: 1.0, 1: 0.55, 2: 0.30}

        # Try to get real ATM premium
        atm_prem = ATM_PREM_APPROX
        if nifty_ltp:
            try:
                atm_strike = round(nifty_ltp / 50) * 50
                # Try to get real quote
                expiry = kite_manager._get_nearest_weekly_expiry() if hasattr(kite_manager, '_get_nearest_weekly_expiry') else None
                if expiry:
                    sym = f"NFO:NIFTY{expiry}{atm_strike}PE"
                    q2  = kite_manager.kite.ltp([sym])
                    atm_prem = q2.get(sym, {}).get("last_price") or ATM_PREM_APPROX
            except Exception:
                pass

        for offset, label, desc in OFFSETS:
            est_prem    = atm_prem * PREM_MULT.get(offset, 1.0)
            cost_per_lot = est_prem * LOT_SIZE
            max_lots    = int(balance / cost_per_lot) if cost_per_lot > 0 else 0
            max_units   = max_lots * LOT_SIZE
            affordable  = max_lots >= 1
            suggestions.append({
                "offset":       offset,
                "label":        label,
                "description":  desc,
                "est_premium":  round(est_prem, 1),
                "cost_per_lot": round(cost_per_lot, 0),
                "max_lots":     max_lots,
                "max_units":    max_units,
                "affordable":   affordable,
            })

        # Best suggestion = highest offset that allows ≥1 lot (prefer ATM or OTM1)
        affordable = [s for s in suggestions if s["affordable"]]
        recommended = next(
            (s for s in suggestions if s["offset"] == 0 and s["affordable"]),  # ATM first
            affordable[-1] if affordable else suggestions[0]                    # else best we can do
        )

        return {
            "success":     True,
            "balance":     balance,
            "nifty_ltp":  nifty_ltp,
            "atm_premium": round(atm_prem, 1),
            "lot_size":    LOT_SIZE,
            "suggestions": suggestions,
            "recommended": recommended,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/report/app-trades")
async def get_app_trades():
    """Return trades from the app's trade log with pattern analysis."""
    from trade_report import load_app_trade_log, analyse
    log_path = Path(__file__).parent / "trade_log.json"
    trades = load_app_trade_log(log_path)
    return {"success": True, "data": analyse(trades), "count": len(trades)}


@app.get("/api/report/autoload")
async def autoload_zerodha_csv():
    """Auto-load the most recent Zerodha tradebook CSV from ~/Downloads."""
    from trade_report import parse_zerodha_csv, pair_legs_into_trades, analyse
    downloads = Path.home() / "Downloads"
    candidates = sorted(
        [f for f in downloads.glob("tradebook-*.csv")],
        key=lambda f: f.stat().st_mtime, reverse=True
    )
    if not candidates:
        return {"success": False, "error": "No tradebook-*.csv found in ~/Downloads"}
    csv_file = candidates[0]
    try:
        content = csv_file.read_text(encoding="utf-8", errors="replace")
        trades  = parse_zerodha_csv(content)
        result  = analyse(trades)
        return {"success": True, "data": result, "count": len(trades),
                "filename": csv_file.name}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/report/upload-csv")
async def upload_zerodha_csv(file: UploadFile):
    """Upload Zerodha Console tradebook CSV → analyse all historical trades."""
    from trade_report import parse_zerodha_csv, pair_legs_into_trades, analyse
    try:
        content = (await file.read()).decode("utf-8", errors="replace")
        legs    = parse_zerodha_csv(content)
        trades  = pair_legs_into_trades(legs)
        result  = analyse(trades)
        return {"success": True, "data": result, "count": len(trades), "legs": len(legs)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/backtest/available-dates")
async def get_available_dates(period: str = "60d", data_source: str = "yahoo"):
    """Return all available trading dates for the date picker."""
    from data_fetcher import fetch_intraday_data
    try:
        df = fetch_intraday_data(interval="5m", period=period)
        df.index = pd.DatetimeIndex(df.index)
        dates = sorted({str(ts.date()) for ts in df.index}, reverse=True)
        return {"success": True, "dates": dates}
    except Exception as e:
        return {"success": False, "error": str(e), "dates": []}


# ── Split API Endpoints (per-section refresh) ─────────────────────

# Shared data cache so sections can reuse fetched data
_section_cache: dict = {"df_5m": None, "df_15m": None, "df_1m": None, "timestamp": 0}
SECTION_CACHE_TTL = 15  # seconds


async def _get_cached_df(interval: str, period: str = "5d") -> pd.DataFrame | None:
    """Async — returns cached df or fetches in a thread pool (never blocks event loop)."""
    cache_key = f"df_{interval}"
    now = _time.time()

    if (
        _section_cache.get(cache_key) is not None
        and (now - _section_cache["timestamp"]) < SECTION_CACHE_TTL
    ):
        return _section_cache[cache_key]

    # Fetch in thread so we never block the event loop
    if kite_manager.is_authenticated:
        kite_map = {"1m": "minute", "5m": "5minute", "15m": "15minute"}
        kite_interval = kite_map.get(interval, "5minute")
        kite_data = await asyncio.to_thread(
            kite_manager.get_historical_data, interval=kite_interval, days=5
        )
        if kite_data:
            df = _kite_history_to_dataframe(kite_data)
            _section_cache[cache_key] = df
            _section_cache["timestamp"] = now
            return df

    df = await asyncio.to_thread(fetch_intraday_data, interval=interval, period=period)
    _section_cache[cache_key] = df
    _section_cache["timestamp"] = now
    return df


@app.get("/api/section/price")
async def section_price():
    """Quick price + day change. Uses WebSocket tick or REST quote."""
    try:
        if not kite_manager.is_authenticated:
            return {"success": False, "error": "Not connected to Zerodha"}

        # Try WebSocket tick first (instant)
        if kite_manager.latest_tick:
            tick = kite_manager.latest_tick
            return {
                "success": True,
                "current_price": tick["last_price"],
                "open": tick.get("open", 0),
                "high": tick.get("high", 0),
                "low": tick.get("low", 0),
                "close": tick.get("close", 0),
                "change": tick.get("change", 0),
                "change_pct": tick.get("change_pct", 0),
            }

        # Fallback to REST quote
        quote = kite_manager.get_live_quote()
        if quote:
            ohlc = quote.get("ohlc", {})
            change = quote.get("last_price", 0) - ohlc.get("close", 0)
            change_pct = (change / ohlc.get("close", 1)) * 100 if ohlc.get("close") else 0
            return {
                "success": True,
                "current_price": quote.get("last_price", 0),
                "open": ohlc.get("open", 0),
                "high": ohlc.get("high", 0),
                "low": ohlc.get("low", 0),
                "close": ohlc.get("close", 0),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
            }

        return {"success": False, "error": "No price data available"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/section/probability")
async def section_probability():
    """MTF probability analysis (1m + 5m + 15m combined)."""
    try:
        if not kite_manager.is_authenticated:
            return {"success": False, "error": "Not connected to Zerodha"}

        mtf = await asyncio.to_thread(run_mtf_analysis)

        # Cache 5m data for other sections
        if mtf.primary_df is not None:
            _section_cache["df_5m"] = mtf.primary_df
            _section_cache["timestamp"] = _time.time()
        if mtf.df_15m is not None:
            _section_cache["df_15m"] = mtf.df_15m

        signals_data = []
        if mtf.primary_result:
            signals_data = [
                {
                    "name": s.name, "value": str(s.value), "bias": s.bias,
                    "strength": s.strength, "weight": s.weight,
                    "description": s.description,
                }
                for s in mtf.primary_result.signals
            ]

        return safe_json_response({
            "success": True,
            "bullish_probability": mtf.combined_bullish,
            "bearish_probability": mtf.combined_bearish,
            "overall_bias": mtf.combined_bias,
            "confidence": mtf.combined_confidence,
            "confluence": mtf.confluence,
            "recommendation": mtf.recommendation,
            "current_price": mtf.primary_result.current_price if mtf.primary_result else 0,
            "day_change": mtf.primary_result.day_change if mtf.primary_result else 0,
            "day_change_pct": mtf.primary_result.day_change_pct if mtf.primary_result else 0,
            "orb_data": mtf.primary_result.orb_data if mtf.primary_result else {},
            "timeframes": [
                {
                    "interval": tf.interval, "label": tf.label,
                    "weight": int(tf.weight * 100),
                    "bullish_pct": tf.bullish_pct, "bearish_pct": tf.bearish_pct,
                    "bias": tf.bias, "confidence": tf.confidence,
                    "error": tf.error,
                }
                for tf in mtf.timeframes
            ],
            "signals": signals_data,
        })
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}


@app.get("/api/section/chart")
async def section_chart(tf: str = "5m"):
    """Chart data (candles + patterns + S/R). Uses cached data if available."""
    try:
        if not kite_manager.is_authenticated:
            return {"success": False, "error": "Not connected to Zerodha"}

        df = _section_cache.get(f"df_{tf}")
        if df is None or (hasattr(df, 'empty') and df.empty):
            df = await _get_cached_df(tf)
        if df is None or df.empty:
            return {"success": False, "error": f"No {tf} data available"}

        # Build candle data
        price_data = []
        for idx, row in df.iterrows():
            price_data.append({
                "time": idx.strftime("%Y-%m-%d %H:%M"),
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": int(row["volume"]),
            })

        # Detect patterns
        patterns_data = []
        sr_data = {}
        pat_candles = {}
        try:
            pat_result = await asyncio.to_thread(detect_all_patterns, df, timeframe=tf)
            patterns_data = [
                {
                    "name": p.name, "type": p.pattern_type,
                    "bias": p.bias, "confidence": p.confidence,
                    "description": p.description, "key_levels": p.key_levels,
                    "timeframe": p.timeframe,
                    "start_time": p.start_time, "end_time": p.end_time,
                    "pivot_times": p.pivot_times,
                }
                for p in pat_result["patterns"]
            ]
            sr_data = pat_result["support_resistance"]
            pat_candles = pat_result.get("pattern_candles", {})
        except Exception:
            pass

        return safe_json_response({
            "success": True,
            "chart_timeframe": tf,
            "price_data": price_data,
            "patterns": patterns_data,
            "pattern_candles": pat_candles,
            "support_resistance": sr_data,
        })
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}


@app.get("/api/section/trade-signal")
async def section_trade_signal():
    """Trade signal analysis. Uses cached 5m data."""
    try:
        if not kite_manager.is_authenticated:
            return {"success": False, "error": "Not connected to Zerodha"}

        df = _section_cache.get("df_5m")
        if df is None or (hasattr(df, 'empty') and df.empty):
            df = await _get_cached_df("5m")
        if df is None or df.empty:
            return {"success": False, "error": "No 5m data. Refresh Probability first."}

        # Get S/R levels from patterns
        sr_data = {}
        try:
            pat_result = await asyncio.to_thread(detect_all_patterns, df, timeframe="5m")
            sr_data = pat_result["support_resistance"]
        except Exception:
            pass

        ts = analyze_trade(
            df,
            support_levels=sr_data.get("support_levels", []),
            resistance_levels=sr_data.get("resistance_levels", []),
        )

        return safe_json_response({
            "success": True,
            "action": ts.action,
            "entry_price": ts.entry_price,
            "stop_loss": ts.stop_loss,
            "target": ts.target,
            "risk_reward": ts.risk_reward,
            "current_trend": ts.current_trend,
            "trend_strength": ts.trend_strength,
            "reversal_probability": ts.reversal_probability,
            "exit_warning": ts.exit_warning,
            "reasoning": ts.reasoning,
            "reversal_signals": [
                {
                    "name": s.name, "score": round(s.score * 100),
                    "weight": s.weight, "detail": s.detail,
                }
                for s in ts.reversal_signals
            ],
        })
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}


@app.get("/api/section/trend-health")
async def section_trend_health():
    """Trend Health analysis — is the trend continuing or reversing?"""
    try:
        if not kite_manager.is_authenticated:
            return {"success": False, "error": "Not connected to Zerodha"}

        df = _section_cache.get("df_5m")
        if df is None or (hasattr(df, 'empty') and df.empty):
            df = await _get_cached_df("5m")
        if df is None or df.empty:
            return {"success": False, "error": "No 5m data. Refresh Probability first."}

        result = await asyncio.to_thread(analyze_trend_health, df)

        return safe_json_response({
            "success": True,
            "verdict": result.verdict,
            "verdict_emoji": result.verdict_emoji,
            "continuation_score": result.continuation_score,
            "reversal_score": result.reversal_score,
            "total_signals": result.total_signals,
            "confidence": result.confidence,
            "current_trend": result.current_trend,
            "summary": result.summary,
            "signals": [
                {
                    "name": s.name,
                    "emoji": s.emoji,
                    "status": s.status,
                    "detail": s.detail,
                    "value": s.value,
                }
                for s in result.signals
            ],
        })
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}


# ── Pattern Detection ───────────────────────────────────────────

@app.get("/api/patterns")
async def patterns(interval: str = "5m"):
    """Detect chart patterns in the current data."""
    try:
        df = await asyncio.to_thread(fetch_intraday_data, interval=interval, period="5d")
        result = await asyncio.to_thread(detect_all_patterns, df, timeframe=interval)

        patterns_data = [
            {
                "name": p.name,
                "type": p.pattern_type,
                "bias": p.bias,
                "confidence": p.confidence,
                "description": p.description,
                "key_levels": p.key_levels,
                "timeframe": p.timeframe,
                "start_time": p.start_time,
                "end_time": p.end_time,
                "pivot_times": p.pivot_times,
            }
            for p in result["patterns"]
        ]

        return {
            "success": True,
            "patterns": patterns_data,
            "pattern_candles": result.get("pattern_candles", {}),
            "support_resistance": result["support_resistance"],
        }
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}


# ── Multi-Timeframe Analysis ─────────────────────────────────

@app.get("/api/mtf-analyze")
async def mtf_analyze(chart_tf: str = "5m"):
    """Run multi-timeframe analysis (1m + 5m + 15m combined).

    Args:
        chart_tf: Timeframe for chart display — '5m' or '15m'.
    """
    # Return cached data if fresh (avoids hammering Yahoo Finance)
    now = _time.time()
    cache_key = f"mtf_{chart_tf}"
    if (
        _mtf_cache.get("key") == cache_key
        and _mtf_cache["data"] is not None
        and (now - _mtf_cache["timestamp"]) < MTF_CACHE_TTL
    ):
        # Still run auto-trader evaluation on cached data
        if trader_state.is_running and _mtf_cache.get("df_5m") is not None:
            df = _mtf_cache["df_5m"]
            try:
                print(f"\ud83e\udd16 [CACHE HIT] Running auto-trader eval (rows={len(df)})")
                evaluate_and_act(df, float(df["close"].iloc[-1]))
            except Exception as e:
                print(f"\u26a0\ufe0f Auto-trader cache eval error: {e}")
        elif trader_state.is_running:
            print(f"\u26a0\ufe0f [CACHE HIT] df_5m not in cache, skipping auto-trader")
        return _mtf_cache["data"]

    try:
        mtf = await asyncio.to_thread(run_mtf_analysis)

        # Reuse the 5m DataFrame from MTF (no extra API call!)
        price_data = []
        patterns_data = []
        sr_data = {}
        df_5m = mtf.primary_df

        # Reuse 15m DataFrame from MTF (already fetched, no extra API call!)
        df_15m = mtf.df_15m

        # Pick chart DataFrame based on selected timeframe
        chart_df = df_15m if chart_tf == "15m" and df_15m is not None else df_5m

        # Build price chart data from ALL available candles (multi-day history)
        if chart_df is not None and not chart_df.empty:
            for idx, row in chart_df.iterrows():
                price_data.append({
                    "time": idx.strftime("%Y-%m-%d %H:%M"),
                    "open": round(float(row["open"]), 2),
                    "high": round(float(row["high"]), 2),
                    "low": round(float(row["low"]), 2),
                    "close": round(float(row["close"]), 2),
                    "volume": int(row["volume"]),
                })

            # Detect chart patterns on BOTH 5m and 15m data
            def _extract_patterns(df, tf_label):
                """Run pattern detection on a single timeframe (sync — called from async via to_thread)."""
                try:
                    pat_result = detect_all_patterns(df, timeframe=tf_label)
                    return [
                        {
                            "name": p.name, "type": p.pattern_type,
                            "bias": p.bias, "confidence": p.confidence,
                            "description": p.description, "key_levels": p.key_levels,
                            "timeframe": p.timeframe,
                            "start_time": p.start_time,
                            "end_time": p.end_time,
                            "pivot_times": p.pivot_times,
                        }
                        for p in pat_result["patterns"]
                    ], pat_result["support_resistance"]
                except Exception:
                    return [], {}

            # Run on 5m
            if df_5m is not None and not df_5m.empty:
                pats_5m, sr_5m = await asyncio.to_thread(_extract_patterns, df_5m, "5m")
                patterns_data.extend(pats_5m)
                sr_data = sr_5m  # Use 5m S/R as primary

            # Run on 15m (more history = catches bigger patterns)
            if df_15m is not None and not df_15m.empty:
                pats_15m, sr_15m = await asyncio.to_thread(_extract_patterns, df_15m, "15m")
                patterns_data.extend(pats_15m)

                # Merge 15m S/R levels into the data
                if sr_15m:
                    for key in ["support_levels", "resistance_levels"]:
                        existing = set(sr_data.get(key, []))
                        for level in sr_15m.get(key, []):
                            # Only add if not too close to existing levels (within 0.2%)
                            if not any(abs(level - e) / e < 0.002 for e in existing if e):
                                existing.add(level)
                        sr_data[key] = sorted(existing)

        # Primary signals (from 5m)
        signals_data = []
        if mtf.primary_result:
            signals_data = [
                {
                    "name": s.name, "value": str(s.value), "bias": s.bias,
                    "strength": s.strength, "weight": s.weight,
                    "description": s.description,
                }
                for s in mtf.primary_result.signals
            ]

        # Run trade signal analysis on 5m data
        trade_signal_data = {}
        if df_5m is not None and not df_5m.empty:
            try:
                ts = analyze_trade(
                    df_5m,
                    support_levels=sr_data.get("support_levels", []),
                    resistance_levels=sr_data.get("resistance_levels", []),
                )
                trade_signal_data = {
                    "action": ts.action,
                    "entry_price": ts.entry_price,
                    "stop_loss": ts.stop_loss,
                    "target": ts.target,
                    "risk_reward": ts.risk_reward,
                    "current_trend": ts.current_trend,
                    "trend_strength": ts.trend_strength,
                    "reversal_probability": ts.reversal_probability,
                    "exit_warning": ts.exit_warning,
                    "reasoning": ts.reasoning,
                    "reversal_signals": [
                        {
                            "name": s.name,
                            "score": round(s.score * 100),
                            "weight": s.weight,
                            "detail": s.detail,
                        }
                        for s in ts.reversal_signals
                    ],
                }
            except Exception:
                trade_signal_data = {"error": "Trade signal analysis failed"}

        return safe_json_response({
            "success": True,
            "data_source": "multi_timeframe",
            # Combined MTF probability
            "bullish_probability": mtf.combined_bullish,
            "bearish_probability": mtf.combined_bearish,
            "overall_bias": mtf.combined_bias,
            "confidence": mtf.combined_confidence,
            "confluence": mtf.confluence,
            "recommendation": mtf.recommendation,
            # Per-timeframe breakdown
            "timeframes": [
                {
                    "interval": tf.interval,
                    "label": tf.label,
                    "weight": int(tf.weight * 100),
                    "bullish_pct": tf.bullish_pct,
                    "bearish_pct": tf.bearish_pct,
                    "bias": tf.bias,
                    "confidence": tf.confidence,
                    "error": tf.error,
                }
                for tf in mtf.timeframes
            ],
            # 5m data for charts/signals
            "current_price": mtf.primary_result.current_price if mtf.primary_result else 0,
            "day_change": mtf.primary_result.day_change if mtf.primary_result else 0,
            "day_change_pct": mtf.primary_result.day_change_pct if mtf.primary_result else 0,
            "orb_data": mtf.primary_result.orb_data if mtf.primary_result else {},
            "signals": signals_data,
            "price_data": price_data,
            "chart_timeframe": chart_tf,
            "patterns": patterns_data,
            "support_resistance": sr_data,
            "trade_signal": trade_signal_data,
        })

        # Cache the response
        _mtf_cache["key"] = cache_key
        _mtf_cache["data"] = response
        _mtf_cache["timestamp"] = _time.time()
        _mtf_cache["df_5m"] = df_5m  # for auto-trader on cache hits

        # ── Auto-Trader: evaluate on each refresh ─────────────────
        if trader_state.is_running and df_5m is not None and not df_5m.empty:
            current_price = float(df_5m["close"].iloc[-1])
            try:
                evaluate_and_act(df_5m, current_price)
            except Exception as eval_err:
                print(f"⚠️ Auto-trader eval error: {eval_err}")

        return response
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}


# ── Auto-Trader API ─────────────────────────────────────────────

@app.get("/api/auto-trader/preflight")
async def auto_trader_preflight():
    """Pre-market system check — run this at 9:00 AM every morning.
    Returns a checklist of everything needed for safe auto trading.
    """
    checks = []

    def chk(name, passed, detail, critical=False):
        checks.append({"name": name, "passed": passed,
                       "detail": detail, "critical": critical})
        return passed

    # 1. Kite authentication
    kite_ok = chk(
        "Zerodha session",
        kite_manager.is_authenticated,
        "Access token valid" if kite_manager.is_authenticated
            else "❌ Not logged in — open /login and complete OAuth",
        critical=True,
    )

    # 2. Margin check
    margin_ok = False
    margin_detail = "Skipped (not authenticated)"
    if kite_ok:
        try:
            margins = kite_manager.kite.margins()
            equity  = margins.get("equity", {})
            avail   = float(equity.get("available", {}).get("live_balance", 0))
            margin_ok = avail >= 10000
            margin_detail = f"₹{avail:,.0f} available {'✅' if margin_ok else '❌ (<₹10K, top up needed)'}"
        except Exception as e:
            margin_detail = f"Error: {e}"
    chk("Available margin", margin_ok, margin_detail, critical=True)

    # 3. NFO instruments available
    instruments_ok = False
    instruments_detail = "Skipped"
    if kite_ok:
        try:
            from auto_trader import _get_nfo_instruments
            instr = _get_nfo_instruments()
            instruments_ok = len(instr) > 0
            instruments_detail = f"{len(instr)} NIFTY options loaded" if instruments_ok else "❌ No instruments"
        except Exception as e:
            instruments_detail = f"Error: {e}"
    chk("NFO instruments list", instruments_ok, instruments_detail, critical=True)

    # 4. Market data (Yahoo Finance fallback)
    data_ok = False
    data_detail = "Checking..."
    try:
        df = fetch_intraday_data(interval="5m", period="2d")
        last_candle = df.index[-1] if not df.empty else None
        price       = float(df["close"].iloc[-1]) if not df.empty else 0
        data_ok     = not df.empty
        data_detail = f"Last candle: {last_candle.strftime('%Y-%m-%d %H:%M') if last_candle else 'N/A'} | Price: {price:,.2f}"
    except Exception as e:
      data_detail = f"❌ Data fetch failed: {e}"
    chk("Market data (Yahoo)", data_ok, data_detail, critical=True)

    # 5. SL/Risk config sanity
    from auto_trader import SL_POINTS, TRAILING_SL_POINTS, MAX_LOSS_PER_DAY, DEFAULT_QUANTITY
    sl_ok = SL_POINTS >= 15
    chk(
        "Stop loss config",
        sl_ok,
        f"SL={SL_POINTS}pts, Trail={TRAILING_SL_POINTS}pts, MaxLoss=₹{MAX_LOSS_PER_DAY}"
        + ("" if sl_ok else " ⚠️ SL < 15pts is too tight for Nifty!"),
        critical=False,
    )

    # 6. No existing open position (clean slate)
    from auto_trader import state as at_state
    no_orphan = at_state.active_trade is None
    chk(
        "No orphaned positions",
        no_orphan,
        "Clean slate — no active trade in system"
            if no_orphan else
            f"⚠️ Active trade detected: {at_state.active_trade.instrument if at_state.active_trade else 'unknown'}",
        critical=False,
    )
    # Also check Zerodha positions
    if kite_ok:
        try:
            positions = kite_manager.kite.positions()
            net_pos   = [p for p in positions.get("net", []) if p.get("quantity", 0) != 0]
            kite_clean = len(net_pos) == 0
            chk(
                "Zerodha open positions",
                kite_clean,
                "No open positions in Zerodha" if kite_clean
                    else f"⚠️ {len(net_pos)} open position(s) in Zerodha account!",
                critical=False,
            )
        except Exception as e:
            chk("Zerodha open positions", False, f"Could not check: {e}")

    # 7. Time check
    from datetime import timezone, timedelta
    now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    is_pre  = now_ist.hour < 9 or (now_ist.hour == 9 and now_ist.minute < 15)
    chk(
        "Market hours",
        True,
        f"IST: {now_ist.strftime('%H:%M:%S')} | "
        + ("Pre-market ✅ good time to run preflight" if is_pre else
           "Market open" if 9 <= now_ist.hour < 15 else "Market closed"),
    )

    all_critical_pass = all(c["passed"] for c in checks if c["critical"])
    return {
        "ready": all_critical_pass,
        "checks": checks,
        "summary": "✅ All systems go — safe to start auto trading!"
                   if all_critical_pass else
                   "❌ Fix critical issues before starting auto trading",
    }


@app.get("/api/auto-trader/status")
async def auto_trader_status():
    """Get current auto-trader state."""
    from auto_trader import state as at_state
    status = get_trader_status()

    if status["active_trade"]:
        trade = status["active_trade"]
        nifty  = at_state.last_nifty_price or (kite_manager.latest_tick or {}).get("last_price", 0)
        ltp    = at_state.last_option_ltp   # refreshed each candle loop
        qty    = trade["quantity"]
        from auto_trader import LOT_SIZE
        lots    = max(1, qty // LOT_SIZE)
        ep      = trade.get("entry_premium", 0) or 0
        is_long = trade["direction"] == "long"

        # Real unrealized P&L: (current option LTP − entry premium) × units
        if ltp and ltp > 0 and ep > 0:
            trade["pnl_unrealized"] = round((ltp - ep) * qty, 2)
        elif nifty and trade["entry_price"]:
            pt_move = (nifty - trade["entry_price"]) * (1 if is_long else -1)
            trade["pnl_unrealized"] = round(pt_move * 0.5 * qty, 2)

        # Premium SL / target levels (already computed in auto_trader status)
        prem_sl  = trade.get("option_sl_premium")
        prem_tgt = trade.get("option_target_premium")

        # Distance in PREMIUM terms (LTP vs SL/target) — meaningful for option buyers
        if ltp and prem_sl:
            trade["dist_to_sl"]     = round(ltp - prem_sl, 1)   # +ve = still alive, -ve = SL breached
        if ltp and prem_tgt:
            trade["dist_to_target"] = round(prem_tgt - ltp, 1)  # +ve = target not reached yet

        trade["lots"]               = lots
        trade["current_option_ltp"] = round(ltp, 2) if ltp else None
        trade["nifty_current"]      = round(nifty, 2) if nifty else None
        trade["trailing_sl"]        = trade["stop_loss"]   # Nifty spot trailing SL (internal)

    # Always expose live Nifty price at top level
    status["nifty_current"] = round(at_state.last_nifty_price, 2) if at_state.last_nifty_price else None

    return {"success": True, **status}


def _fetch_india_vix_from_nse(fallback: float = 15.0) -> float:
    """Fetch India VIX directly from NSE's official allIndices API.

    NSE requires a browser-like session (cookie handshake) before the
    JSON endpoint will respond. We do a lightweight GET on the homepage
    first to acquire the session cookie, then hit the data endpoint.

    Returns the last-traded VIX value, or `fallback` if NSE is
    unreachable (weekend / market holiday / network issue).
    """
    import requests
    NSE_HOME   = "https://www.nseindia.com"
    NSE_INDICES = "https://www.nseindia.com/api/allIndices"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/122.0.0.0 Safari/537.36",
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         NSE_HOME + "/",
    }
    # Bypass any corporate HTTP proxy for the direct NSE connection —
    # proxies like Walmart sysproxy block NSE with 407.
    NO_PROXY = {"http": "", "https": ""}
    try:
        session = requests.Session()
        session.get(NSE_HOME, headers=HEADERS, timeout=8,
                    proxies=NO_PROXY)             # acquire cookie, no proxy
        resp = session.get(NSE_INDICES, headers=HEADERS, timeout=8,
                           proxies=NO_PROXY)
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            if "VIX" in item.get("index", "").upper():
                val = float(item["last"])
                print(f"[VIX] NSE fetch OK: {val}")
                return val
        print("[VIX] NSE returned data but no VIX item found — using fallback")
    except Exception as exc:
        print(f"[VIX] NSE fetch FAILED ({type(exc).__name__}) — using fallback {fallback}")
    return fallback


@app.get("/api/premium-estimate")
async def premium_estimate(spot: float = 23500.0, offset: int = 0):
    """Return the best available premium estimate for lot-size preview.

    Priority:
      1. Live Kite LTP for the exact option strike  (when authenticated)
      2. Black-Scholes ATM approximation using India VIX  (fallback)

    offset: 0 = ATM, 1 = 1-OTM, 2 = 2-OTM  (mirrors state.strike_offset)
    """
    import math
    from datetime import date, timedelta

    # ── India VIX — priority: Kite ltp() → NSE API → last-known-good → 15.0
    # Kite is preferred (works through corporate proxy, no cookie dance).
    # NSE direct is fallback when Kite is not yet authenticated.
    # _last_vix survives across 5-min cache TTL refreshes.
    _now  = _time.time()
    cache = getattr(premium_estimate, "_cache", {})
    vix_source = cache.get("vix_source", "fallback")

    if _now - cache.get("ts", 0) > 300:   # 5-minute TTL
        vix_val = None

        # ── Path 1: Kite ltp() — most reliable, works behind proxy ──
        try:
            from kite_integration import kite_manager as _km
            vix_val = _km.get_india_vix()     # returns float or None
            if vix_val:
                vix_source = "kite"
        except Exception:
            pass

        # ── Path 2: NSE web API (no-proxy) ──────────────────────────
        if not vix_val:
            nse_val = _fetch_india_vix_from_nse(fallback=0)
            if nse_val > 0:
                vix_val    = nse_val
                vix_source = "nse"

        # ── Path 3: Sticky last-known-good ───────────────────────────
        if not vix_val:
            prev = getattr(premium_estimate, "_last_vix", None)
            if prev:
                vix_val    = prev
                vix_source = "cached"

        # ── Path 4: Hard fallback ─────────────────────────────────────
        if not vix_val:
            vix_val    = 15.0
            vix_source = "fallback"

        # Persist last good real value (not the hard fallback)
        if vix_source in ("kite", "nse"):
            premium_estimate._last_vix = vix_val

        cache = {"ts": _now, "vix": vix_val, "vix_source": vix_source}
        premium_estimate._cache = cache

    iv         = cache["vix"] / 100.0   # e.g. 14.23 VIX → 0.1423 annualised vol
    # ── Days to nearest Nifty 50 weekly expiry (Tuesday) ───────────
    # NSE moved Nifty 50 weekly expiry Thu → Tue in Oct 2024.
    # weekday(): Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    NIFTY_EXPIRY_WEEKDAY = 1   # Tuesday
    today = date.today()
    days_to_expiry = (NIFTY_EXPIRY_WEEKDAY - today.weekday()) % 7
    if days_to_expiry == 0:
        import datetime as _dt
        if _dt.datetime.now().hour >= 15:
            days_to_expiry = 7   # past 3 PM on Tue → roll to next Tuesday
    dte = max(days_to_expiry, 1)   # never 0 (div-by-zero guard)

    # ── Black-Scholes ATM approximation ──────────────────────────
    # ATM call ≈ Spot × IV × √(T) × 0.4  (Brenner-Subrahmanyam, 1988)
    T = dte / 365.0
    atm_premium = spot * iv * math.sqrt(T) * 0.4

    # ── OTM discount by delta ratio ───────────────────────────────
    # delta falls from ~0.50 (ATM) to ~0.35 (1-OTM) to ~0.22 (2-OTM)
    # Premium scales roughly proportionally with delta for small offsets.
    otm_discounts = {0: 1.0, 1: 0.65, 2: 0.40}
    discount = otm_discounts.get(offset, 0.65)
    est_premium = round(atm_premium * discount)

    strike_offset_pts = offset * 50   # 50-point Nifty step per OTM level
    atm_strike = round(spot / 50) * 50
    picked_strike = atm_strike + strike_offset_pts  # CE direction for display

    # ── Attempt live Kite LTP for CE + PE in a single batch call ──────
    # kite.ltp() returns only last_price — the lightest possible API call.
    # We fetch both CE and PE together (one HTTP round-trip) so the UI
    # can show the real price for whichever direction the user picks.
    live_ce_ltp = None
    live_pe_ltp = None
    ce_symbol   = None
    pe_symbol   = None

    if kite_manager.is_authenticated:
        try:
            from auto_trader import _get_nfo_instruments
            from datetime import date as _date, timedelta as _td
            import datetime as _dt2

            # Use Kite last_trade_time for today's date — immune to system clock drift
            _today      = kite_manager.get_market_date()
            instruments = _get_nfo_instruments()

            # Find nearest expiry from actual instrument data — no weekday assumption
            nifty_expiries = sorted({
                i["expiry"] for i in instruments
                if i["name"] == "NIFTY" and i["expiry"] >= _today
            })
            expiry_date = nifty_expiries[0] if nifty_expiries else _today + _td(days=1)

            nifty_opts  = [
                i for i in instruments
                if i["name"] == "NIFTY" and i["expiry"] == expiry_date
            ]

            def _find_sym(itype, strike):
                for i in nifty_opts:
                    if i["instrument_type"] == itype and int(i["strike"]) == int(strike):
                        return i["tradingsymbol"]
                return None

            pe_strike  = atm_strike - strike_offset_pts   # PE is below ATM
            ce_symbol  = _find_sym("CE", picked_strike)
            pe_symbol  = _find_sym("PE", pe_strike)

            symbols_to_fetch = [s for s in [ce_symbol, pe_symbol] if s]
            ltps = kite_manager.get_options_ltp_batch(symbols_to_fetch)

            if ce_symbol and ce_symbol in ltps:
                live_ce_ltp = round(ltps[ce_symbol])
            if pe_symbol and pe_symbol in ltps:
                live_pe_ltp = round(ltps[pe_symbol])

        except Exception:
            pass   # silently fall back to BS estimate

    # Use live CE price as the primary estimate (CE = LONG direction);
    # fall back to BS estimate only when Kite is not available.
    est_premium = live_ce_ltp if live_ce_ltp else est_premium
    source      = "live_kite" if (live_ce_ltp or live_pe_ltp) else "bs_estimate"

    return {
        "spot":          spot,
        "iv_pct":        round(cache["vix"], 2),
        "vix_source":    vix_source,
        "dte":           dte,
        "offset":        offset,
        "atm_strike":    int(atm_strike),
        "picked_strike": int(picked_strike),
        "atm_premium":   round(atm_premium),
        "est_premium":   est_premium,      # always use this for lot calc
        "source":        source,           # "live_kite" or "bs_estimate"
        "ce_symbol":     ce_symbol,        # e.g. NIFTY25031823550CE
        "pe_symbol":     pe_symbol,        # e.g. NIFTY25031823450PE
        "ce_ltp":        live_ce_ltp,      # real CE price (or null)
        "pe_ltp":        live_pe_ltp,      # real PE price (or null)
        "formula":       f"₹{spot}×{round(iv*100,1)}%÷100×√({dte}/365)×0.4×{discount}",
        "note":          "Live Kite LTP (ltp() API) when authenticated, B-S fallback otherwise",
    }


@app.get("/api/auto-trader/preview-symbol")
async def auto_trader_preview_symbol():
    """Resolve the exact option symbol that would be traded RIGHT NOW.

    Uses current live Nifty price + active settings (strike_offset, etc.)
    to look up the real Kite tradingsymbol and fetch its live LTP.
    Returns both LONG (CE) and SHORT (PE) symbols so the UI can show
    exactly what will be bought on the next signal.
    """
    from auto_trader import state as at_state, _get_nfo_instruments, _get_nearest_expiry_date
    from strategy import Direction

    # ── Live spot price ──────────────────────────────────────────
    tick  = kite_manager.latest_tick
    quote = kite_manager.get_live_quote() if not tick else None
    spot  = (
        tick["last_price"]           if tick and isinstance(tick, dict)
        else quote["last_price"]     if quote
        else None
    )
    if not spot:
        return {"success": False, "error": "No live Nifty price — is Kite authenticated?"}

    atm_strike = round(spot / 50) * 50
    offset_pts = at_state.strike_offset * 50
    ce_strike  = atm_strike + offset_pts
    pe_strike  = atm_strike - offset_pts
    expiry     = _get_nearest_expiry_date().date()   # always compare as date, not datetime

    # ── Instrument lookup ────────────────────────────────────────
    try:
        instruments = _get_nfo_instruments()
    except Exception as e:
        return {"success": False, "error": f"Instruments unavailable: {e}"}

    def _find(itype, strike):
        for i in instruments:
            if (i["name"] == "NIFTY"
                    and i["instrument_type"] == itype
                    and int(i["strike"]) == int(strike)
                    and i["expiry"] == expiry):
                return i["tradingsymbol"], i["instrument_token"]
        return None, None

    ce_sym, ce_tok = _find("CE", ce_strike)
    pe_sym, pe_tok = _find("PE", pe_strike)

    if not ce_sym or not pe_sym:
        return {
            "success":    False,
            "error":      f"Symbols not found for expiry {expiry} — market may be closed or instruments not loaded",
            "ce_strike":  ce_strike,
            "pe_strike":  pe_strike,
            "expiry":     str(expiry),
        }

    # ── Fetch live LTP for both in one batch call ────────────────
    ltps     = kite_manager.get_options_ltp_batch([ce_sym, pe_sym])
    ce_ltp   = ltps.get(ce_sym)
    pe_ltp   = ltps.get(pe_sym)
    lot_size = 65

    def _lots(ltp):
        if not ltp or ltp <= 0:
            return None
        if at_state.qty_mode == "capital":
            return max(1, int(at_state.capital / (ltp * lot_size)))
        return at_state.manual_qty // lot_size

    offset_label = {0: "ATM", 1: "1-OTM", 2: "2-OTM"}.get(at_state.strike_offset, "OTM")

    return {
        "success":      True,
        "spot":         spot,
        "atm_strike":   atm_strike,
        "expiry":       str(expiry),
        "offset_label": offset_label,
        "long": {
            "symbol":  ce_sym,
            "token":   ce_tok,
            "strike":  ce_strike,
            "type":    "CE",
            "ltp":     ce_ltp,
            "lots":    _lots(ce_ltp),
            "cost":    round(ce_ltp * lot_size * _lots(ce_ltp)) if ce_ltp and _lots(ce_ltp) else None,
        },
        "short": {
            "symbol":  pe_sym,
            "token":   pe_tok,
            "strike":  pe_strike,
            "type":    "PE",
            "ltp":     pe_ltp,
            "lots":    _lots(pe_ltp),
            "cost":   round(pe_ltp * lot_size * _lots(pe_ltp)) if pe_ltp and _lots(pe_ltp) else None,
        },
        "qty_mode":     at_state.qty_mode,
        "capital":      at_state.capital,
        "sl_points":    at_state.sl_points,
        "rr_ratio":     at_state.rr_ratio,
    }


# ── Crude Oil Auto-Trader endpoints ─────────────────────────────

@app.get("/api/crude/status")
async def crude_status():
    from crude_trader import get_crude_status
    return await asyncio.to_thread(get_crude_status)


@app.post("/api/crude/start")
async def crude_start():
    from crude_trader import start_crude_trader
    return await asyncio.to_thread(start_crude_trader)


@app.post("/api/crude/stop")
async def crude_stop():
    from crude_trader import stop_crude_trader
    return await asyncio.to_thread(stop_crude_trader)


@app.post("/api/crude/kill")
async def crude_kill():
    from crude_trader import kill_crude_trader
    return await asyncio.to_thread(kill_crude_trader)


@app.post("/api/crude/config")
async def crude_config(
    sl_points:      float | None = None,
    trail_points:   float | None = None,
    rr_ratio:       float | None = None,
    capital:        float | None = None,
    strike_offset:  int   | None = None,
    trail_mode:     str   | None = None,   # 'fixed' | 'atr' | 'supertrend'
    atr_multiplier: float | None = None,
):
    from crude_trader import state as cs
    if sl_points      is not None: cs.sl_points      = sl_points
    if trail_points   is not None: cs.trail_points   = trail_points
    if rr_ratio       is not None: cs.rr_ratio       = rr_ratio
    if capital        is not None: cs.capital        = capital
    if strike_offset  is not None: cs.strike_offset  = strike_offset
    if trail_mode     is not None and trail_mode in ('fixed', 'atr', 'supertrend'):
        cs.trail_mode = trail_mode
    if atr_multiplier is not None: cs.atr_multiplier = atr_multiplier
    return {'success': True, 'sl_points': cs.sl_points, 'trail_points': cs.trail_points,
            'rr_ratio': cs.rr_ratio, 'capital': cs.capital,
            'trail_mode': cs.trail_mode, 'atr_multiplier': cs.atr_multiplier}


@app.get("/api/crude/margin")
async def crude_margin():
    """Return live available margin from Zerodha for the crude trader."""
    from crude_trader import _fetch_available_margin
    try:
        avail = _fetch_available_margin()
        if avail is None:
            return {"success": False, "available": 0.0,
                    "error": "Zerodha session expired — re-login at /login"}
        return {"success": True, "available": round(avail, 2)}
    except Exception as e:
        return {"success": False, "available": 0.0, "error": str(e)}


@app.post("/api/crude/evaluate")
async def crude_evaluate():
    """Manually trigger one strategy evaluation cycle on latest candle data.

    Does NOT require the trader to be running — useful for checking
    the current signal without waiting for the next 5-min candle close.
    """
    from crude_trader import evaluate_and_act_crude, get_crude_status
    from crude_data import fetch_crude_intraday_data, get_crude_spot
    import asyncio

    try:
        loop = asyncio.get_event_loop()

        def _run():
            df    = fetch_crude_intraday_data('5minute', 5)
            price = get_crude_spot()
            if df is None or df.empty or not price:
                return None, 'No market data available'
            evaluate_and_act_crude(df, price)
            return get_crude_status(), None

        result, err = await loop.run_in_executor(None, _run)
        if err:
            return {'success': False, 'error': err}
        return {'success': True, **result}
    except Exception as e:
        return {'success': False, 'error': str(e)}


@app.get("/api/crude/history")
async def crude_history():
    """Return today's completed Crude trades from log file."""
    from crude_trader import CRUDE_LOG_FILE
    import json
    try:
        if CRUDE_LOG_FILE.exists():
            data = json.loads(CRUDE_LOG_FILE.read_text())
            return {'trades': data, 'count': len(data)}
    except Exception:
        pass
    return {'trades': [], 'count': 0}


@app.get("/api/health")
async def health_check():
    """System health check — every critical subsystem in one glance.

    Designed to give the user confidence before trusting the auto-trader
    with real money.  All checks are non-blocking (cached auth, in-memory).
    """
    import time as _time

    # ── Kite auth (uses TTL cache — no live network call here) ────
    kite_ok       = kite_manager.is_authenticated
    session_date  = None
    try:
        from kite_integration import SESSION_FILE
        import json as _json
        if SESSION_FILE.exists():
            _sess = _json.loads(SESSION_FILE.read_text())
            session_date = _sess.get("date")
    except Exception:
        pass

    # ── WebSocket / tick stream ───────────────────────────────────
    ws_streaming  = kite_manager.is_streaming
    last_tick_ts  = None
    last_tick_age = None
    if kite_manager.latest_tick:
        last_tick_ts  = kite_manager.latest_tick.get("timestamp")
        try:
            from datetime import datetime as _dt
            age = (_dt.now() - _dt.fromisoformat(last_tick_ts)).total_seconds()
            last_tick_age = round(age, 1)
        except Exception:
            pass

    # ── Auto-trader state ─────────────────────────────────────────
    at              = trader_state
    has_trade       = at.active_trade is not None
    tick_guard_live = ws_streaming  # guard is wired iff WebSocket is up
    ltp_fresh       = at.last_option_ltp > 0 if has_trade else None
    nifty_fresh     = at.last_nifty_price > 0

    # ── Snapshot integrity ────────────────────────────────────────
    from pathlib import Path
    snap_path = Path(".") / ".state_snapshot.json"
    snap_ok   = snap_path.exists() and snap_path.stat().st_size > 10

    # ── Aggregate: is the system safe to trade? ───────────────────
    # Must-have for LIVE trading:
    critical = [
        ("kite_auth",    kite_ok),
        ("ws_streaming", ws_streaming),
        ("nifty_price",  nifty_fresh),
    ]
    all_critical_ok = all(v for _, v in critical)

    return {
        "ok": all_critical_ok,
        "kite": {
            "authenticated": kite_ok,
            "session_date":  session_date,
            "ws_streaming":  ws_streaming,
            "last_tick_ts":  last_tick_ts,
            "last_tick_age_s": last_tick_age,
        },
        "trader": {
            "is_running":     at.is_running,
            "is_paper_mode":  at.is_paper_mode,
            "kill_switch":    at.kill_switch,
            "has_trade":      has_trade,
            "tick_guard_live": tick_guard_live,
            "ltp_fresh":      ltp_fresh,
            "nifty_fresh":    nifty_fresh,
            "nifty_price":    round(at.last_nifty_price, 2) if nifty_fresh else None,
        },
        "snapshot": {
            "exists": snap_ok,
            "path":   str(snap_path.resolve()),
        },
    }


@app.post("/api/auto-trader/start")
async def auto_trader_start(strategy: str = "smart_router"):
    """Start the auto-trader with a selected strategy."""
    result = start_auto_trader(strategy_id=strategy)
    return {"success": True, **result}


@app.post("/api/auto-trader/configure")
async def auto_trader_configure(
    sl_points:          float | None = Query(None),
    trailing_sl_points: float | None = Query(None),
    trail_mode:         str   | None = Query(None),
    trail_atr_mult:     float | None = Query(None),
    rr_ratio:           float | None = Query(None),
    qty_mode:           str   | None = Query(None),
    manual_qty:         int   | None = Query(None),
    capital:            float | None = Query(None),
    strike_offset:      str   | None = Query(None),
    max_trades_per_day: int   | None = Query(None),
    cooldown_minutes:   int   | None = Query(None),
):
    """Update runtime trade settings without restarting."""
    parsed_offset: int | None = None
    if strike_offset is not None:
        try:
            parsed_offset = int(strike_offset)
        except ValueError:
            pass
    result = configure_auto_trader(
        sl_points=sl_points, trailing_sl_points=trailing_sl_points,
        trail_mode=trail_mode, trail_atr_mult=trail_atr_mult,
        rr_ratio=rr_ratio, qty_mode=qty_mode,
        manual_qty=manual_qty, capital=capital,
        strike_offset=parsed_offset,
        max_trades_per_day=max_trades_per_day,
        cooldown_minutes=cooldown_minutes,
    )
    return {"success": True, **result}


@app.post("/api/auto-trader/dismiss-recovery")
async def auto_trader_dismiss_recovery():
    """User has acknowledged the recovery banner — clear it."""
    trader_state.recovery_mode    = False
    trader_state.recovery_message = ""
    return {"success": True}


@app.post("/api/auto-trader/stop")
async def auto_trader_stop():
    """Stop the auto-trader and exit positions."""
    try:
        result = stop_auto_trader()
        return {"success": True, **result}
    except Exception as e:
        # Force stop even if error
        trader_state.is_running = False
        trader_state.active_trade = None
        return {"success": True, "status": "force_stopped", "error": str(e)}


@app.post("/api/auto-trader/kill")
async def auto_trader_kill():
    """Emergency kill switch."""
    try:
        result = activate_kill_switch()
        return {"success": True, **result}
    except Exception as e:
        trader_state.is_running = False
        trader_state.kill_switch = True
        trader_state.active_trade = None
        return {"success": True, "status": "force_killed", "error": str(e)}


@app.post("/api/auto-trader/sync-zerodha")
async def auto_trader_sync_zerodha():
    """Scan Zerodha positions and import any open NFO trade into app state.

    Use this when the app was restarted with an open position already in Zerodha.
    Runs in a thread so it doesn't block the FastAPI event loop.
    """
    result = await asyncio.to_thread(sync_from_zerodha)
    return result


@app.post("/api/auto-trader/discard-trade")
async def discard_trade_api():
    """Remove active trade from app state only — no order sent to Zerodha."""
    return await asyncio.to_thread(discard_trade_from_app)


@app.post("/api/auto-trader/trade-managed")
async def set_trade_managed_api(managed: bool = True):
    """Toggle whether the app manages the active trade (SL/trail/exit).

    managed=true  → full app control (SL, trailing SL, target, time exit).
    managed=false → monitor only — app tracks P&L but never touches position.
    """
    return await asyncio.to_thread(set_trade_managed, managed)


@app.post("/api/auto-trader/evaluate")
async def auto_trader_evaluate():
    """Trigger evaluation in a thread pool — never blocks the event loop."""
    if not trader_state.is_running:
        return {"success": False, "error": "Auto-trader not running"}
    try:
        import asyncio
        loop = asyncio.get_event_loop()

        def _run():
            df = fetch_intraday_data(interval="5m", period="5d")
            if df is None or df.empty:
                return None
            evaluate_and_act(df, float(df["close"].iloc[-1]))
            return get_trader_status()

        result = await loop.run_in_executor(None, _run)
        if result is None:
            return {"success": False, "error": "No market data"}
        return {"success": True, **result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/auto-trader/force-exit")
async def auto_trader_force_exit():
    """Manually exit the active trade at market price immediately."""
    from auto_trader import state as at_state, _exit_position
    if not at_state.active_trade:
        return {"success": False, "error": "No active trade to exit"}
    try:
        import asyncio
        nifty = at_state.last_nifty_price or 0
        loop  = asyncio.get_event_loop()
        await loop.run_in_executor(None, _exit_position, "MANUAL EXIT", nifty)
        return {"success": True, "message": "Manual exit triggered"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/auto-trader/force-entry")
async def auto_trader_force_entry(direction: str = Query(...)):
    """Manually force a trade entry (LONG or SHORT) bypassing signal check."""
    from auto_trader import (
        state as at_state, Direction, _enter_trade,
        fetch_intraday_data
    )
    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        return {"success": False, "error": "direction must be LONG or SHORT"}
    if at_state.active_trade:
        return {"success": False, "error": "Already in a trade — exit first"}
    if not at_state.is_running:
        return {"success": False, "error": "Auto-trader not running — press Start first"}
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        def _run():
            df    = fetch_intraday_data(interval="5m", period="5d")
            price = float(df["close"].iloc[-1]) if df is not None and not df.empty else (at_state.last_nifty_price or 0)
            _enter_trade(Direction(direction.lower()), price)
            return get_trader_status()
        result = await loop.run_in_executor(None, _run)
        return {"success": True, **result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/auto-trader/update-trail-sl")
async def auto_trader_update_trail_sl(new_sl_points: float = Query(...)):
    """Manually update the trailing SL points on the fly."""
    from auto_trader import state as at_state, _sync_trailing_sl_to_exchange
    if new_sl_points < 5 or new_sl_points > 200:
        return {"success": False, "error": "SL must be between 5 and 200 Nifty points"}
    old = at_state.trailing_sl_points
    at_state.trailing_sl_points = new_sl_points
    at_state.sl_points = max(at_state.sl_points, new_sl_points)  # hard SL >= trail SL
    # If trade is active, recalculate SL level immediately
    if at_state.active_trade:
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _sync_trailing_sl_to_exchange)
        except Exception:
            pass
    from auto_trader import _save_state_snapshot
    _save_state_snapshot()
    return {
        "success": True,
        "old_trail_sl": old,
        "new_trail_sl": at_state.trailing_sl_points,
        "sl_points":    at_state.sl_points,
    }


@app.get("/api/auto-trader/history")
async def auto_trader_history():
    """Return completed trade history from trade log.

    Filters out ghost trades (status=filled with no exit_premium)
    so they don't pollute the history or P&L totals.
    """
    from pathlib import Path
    import json as _json
    log_file = Path(__file__).parent / "trade_log.json"
    if not log_file.exists():
        return {"success": True, "trades": [], "total_pnl": 0}
    try:
        data   = _json.loads(log_file.read_text())
        trades = data.get("trades", [])

        # Only include properly exited trades
        completed = [
            t for t in trades
            if t.get("status") == "exited" and t.get("exit_premium") is not None
        ]

        # Recompute realized P&L from completed trades only (ignore ghost fills)
        realized_pnl = round(sum(t.get("pnl", 0) or 0 for t in completed), 2)

        # Include unrealized P&L of any currently open trade
        from auto_trader import state as at_state
        unrealized_pnl = 0.0
        if at_state.active_trade and at_state.last_option_ltp > 0:
            t   = at_state.active_trade
            ltp = at_state.last_option_ltp
            unrealized_pnl = round((ltp - t.entry_premium) * t.quantity, 2)

        return {
            "success":        True,
            "trades":         completed,
            "total_pnl":      realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "day_total_pnl":  round(realized_pnl + unrealized_pnl, 2),
            "ghost_trades":   len(trades) - len(completed),  # for debugging
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Backtest API ─────────────────────────────────────────────────────

# ── Strategy Registry API ──────────────────────────────────────

@app.get("/api/strategies")
async def list_strategies():
    """Return all available strategies for the backtest dropdown."""
    import strategies.loader  # noqa: F401
    from strategies.registry import to_json
    return {"success": True, "strategies": to_json()}


@app.get("/api/truedata/status")
async def truedata_status():
    """Check if TrueData credentials are configured."""
    from truedata_fetcher import has_credentials
    return {"success": True, "configured": has_credentials()}


@app.post("/api/truedata/credentials")
async def set_truedata_credentials(username: str, password: str):
    """Save TrueData credentials for this session."""
    from truedata_fetcher import set_credentials
    if not username.strip() or not password.strip():
        return {"success": False, "error": "Username and password are required"}
    set_credentials(username.strip(), password.strip())
    return {"success": True, "message": "TrueData credentials saved for this session"}


@app.post("/api/backtest")
async def run_backtest_api(
    period: str = "60d",
    sl_points: float = 30.0,
    trailing_sl: float = 15.0,
    rr_ratio: float = 2.0,
    max_trades: int = 3,
    strategy: str = "smart_router",
    data_source: str = "yahoo",
    quantity: int = 780,
):
    """Run a backtest with given parameters, strategy, and data source."""
    from backtester import run_backtest, BacktestResult
    import dataclasses

    try:
        result = run_backtest(
            period=period,
            interval="5m",
            quantity=quantity,
            sl_points=sl_points,
            trailing_sl=trailing_sl,
            rr_ratio=rr_ratio,
            max_trades_per_day=max_trades,
            strategy_id=strategy,
            data_source=data_source,
        )

        # Build equity curve from cumulative P&L
        equity_curve = []
        cumulative = 0.0
        for t in result.trades:
            cumulative += t.pnl_points
            equity_curve.append({
                "date": t.date,
                "time": t.exit_time,
                "cumulative": round(cumulative, 2),
            })

        return {
            "success": True,
            "total_trades": result.total_trades,
            "winners": result.winners,
            "losers": result.losers,
            "win_rate": result.win_rate,
            "total_pnl_points": result.total_pnl_points,
            "total_pnl_rupees": result.total_pnl_rupees,
            "max_win": result.max_win,
            "max_loss": result.max_loss,
            "avg_win": result.avg_win,
            "avg_loss": result.avg_loss,
            "profit_factor": result.profit_factor,
            "max_drawdown": result.max_drawdown,
            "sharpe_approx": result.sharpe_approx,
            "days_tested": result.days_tested,
            "data_source": result.data_source,
            "period": result.period,
            "daily_pnl": result.daily_pnl,
            "equity_curve": equity_curve,
            "trades": [
                dataclasses.asdict(t) for t in result.trades
            ],
        }
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}

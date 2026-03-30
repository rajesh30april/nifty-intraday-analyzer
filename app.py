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
    refresh_active_option_ltp, reconcile_zerodha_position,
    state as trader_state, evaluate_and_act,
    _log as _at_log,
)
from pattern_scanner import scan_patterns, TIMEFRAME_META, PATTERN_EMOJIS
from data_manager import archive_today_trades, cleanup_old_archives


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


# Track last archived date for automatic daily archiving
_last_archived_date = None

async def _auto_trader_loop():
    """Background loop: evaluates strategy at every 5-min candle close.

    Syncs to clock boundaries (:00, :05, :10 ...) so evaluation always
    happens on a CLOSED candle — never mid-candle garbage.
    
    Also handles automatic daily archiving when date changes.
    """
    global _last_archived_date
    print("🤖 Auto-trader loop started — synced to 5-min candle closes")
    
    # Initialize last archived date to today
    _last_archived_date = datetime.now().strftime("%Y-%m-%d")
    
    while True:
        wait = _seconds_to_next_candle_close(5)
        await asyncio.sleep(wait)

        now_str = datetime.now().strftime("%H:%M:%S")
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        # Check if date changed (new trading day) - archive previous day
        if _last_archived_date != current_date:
            print(f"📅 Date changed: {_last_archived_date} → {current_date}")
            # Archive previous day's trades
            result = await asyncio.to_thread(archive_today_trades, _last_archived_date)
            print(result['message'])
            
            # Cleanup old archives (keep last 90 days)
            cleanup_result = await asyncio.to_thread(cleanup_old_archives, 90)
            print(cleanup_result['message'])
            
            # Update last archived date
            _last_archived_date = current_date
            print(f"✅ Ready for new trading day: {current_date}")
        
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
    from crude_trader import evaluate_and_act_crude, state as crude_state, _log as _crude_log
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
    """REST fallback: refresh option LTP every 5s, run exit/trail check, and log heartbeat every 60s.

    PRIMARY path: Kite WebSocket on_ticks → _manage_trade_by_premium on
    every ~1s tick (crude option subscribed after entry).

    FALLBACK path (WebSocket down / not streaming): this loop polls REST
    every 5s so worst-case exit delay is 5s, not 15s.
    
    HEARTBEAT: Logs a live heartbeat every 60s to show the trader is alive.
    """
    from crude_trader import (
        state as crude_state, 
        _manage_trade_by_premium,
        _log as _crude_log,
    )
    from crude_data import get_crude_option_ltp, get_crude_spot
    await asyncio.sleep(4)
    _hb_tick = 0  # Heartbeat counter
    
    while True:
        try:
            if crude_state.active_trade:
                ltp = await asyncio.to_thread(
                    get_crude_option_ltp, crude_state.active_trade.instrument
                )
                if isinstance(ltp, (int, float)) and ltp > 0:
                    crude_state.last_option_ltp = ltp
                    # ── Primary exit+trail check on every LTP refresh ──
                    await asyncio.to_thread(
                        _manage_trade_by_premium, ltp, "5s_poll"
                    )
                
                # 🐶 NEW: Heartbeat every ~60s (12 × 5s iterations)
                _hb_tick += 1
                if _hb_tick % 12 == 0:
                    t = crude_state.active_trade
                    ltp_val = crude_state.last_option_ltp
                    crude_price = crude_state.last_crude_price or 0
                    
                    # Calculate P&L
                    is_short = t.direction.lower() == 'short'
                    lot_size = getattr(t, 'lot_size', 10)
                    if ltp_val > 0:
                        delta = (t.entry_premium - ltp_val) if is_short else (ltp_val - t.entry_premium)
                        pnl = round(delta * t.quantity * lot_size, 2)
                        pnl_str = f"P&L ₹{pnl:+,.0f}"
                    else:
                        pnl_str = "P&L –"
                    
                    # Locked profit (distance from entry to current SL)
                    locked = abs(t.stop_loss - t.entry_price)
                    
                    # Format heartbeat message
                    ltp_str = f"₹{ltp_val:.1f}" if ltp_val else "–"
                    ws_active = kite_manager.is_streaming
                    src = "WS" if ws_active else "REST"
                    
                    _crude_log("💓", "Alive",
                               f"Crude ₹{crude_price:.0f} | Option LTP {ltp_str} ({src}) | "
                               f"SL ₹{t.stop_loss:.0f} (locked +₹{locked:.0f}) | {pnl_str}")
            
            # Always refresh spot price
            spot = await asyncio.to_thread(get_crude_spot)
            if spot:
                crude_state.last_crude_price = spot
                
        except Exception as e:
            print(f"⚠️ Crude LTP refresh error: {e}")
        
        # 5s REST fallback — WebSocket is primary (~1s tick)
        await asyncio.sleep(5)


async def _crude_heartbeat_loop():
    """🐶 Heartbeat EVERY 60 SECONDS - shows crude system is alive!
    
    Fires every minute regardless of whether there's a trade or not.
    """
    from crude_trader import state as crude_state, _log as _crude_log
    from crude_data import get_crude_spot
    
    await asyncio.sleep(10)  # Initial delay
    
    while True:
        try:
            if not crude_state.is_running or crude_state.kill_switch:
                await asyncio.sleep(60)
                continue
                
            # Get current price
            price = await asyncio.to_thread(get_crude_spot)
            if not price:
                price = crude_state.last_crude_price or 0
            
            # Count firing strategies
            firing = sum(1 for s in crude_state.meta_scores if s.get('should_enter', False))
            total = len(crude_state.meta_scores) if crude_state.meta_scores else 6
            regime = crude_state.regime or "unknown"
            
            # Log heartbeat
            if crude_state.active_trade:
                _crude_log("💓", "Heartbeat", 
                           f"TRADE ACTIVE | Crude ₹{price:.0f} | Trades: {crude_state.orders_placed}")
            else:
                _crude_log("💓", "Heartbeat", 
                           f"Monitoring | Crude ₹{price:.0f} | {firing}/{total} firing | "
                           f"{regime} | Trades: {crude_state.orders_placed}")
                           
        except Exception as e:
            print(f"⚠️ Crude heartbeat error: {e}")
        
        # Wait 60 seconds before next heartbeat
        await asyncio.sleep(60)


async def _ltp_refresh_loop():
    """Keep option LTP fresh, log a heartbeat every 60s, and reconcile
    Zerodha position state every 60s.

    Strategy:
    - If KiteTicker WebSocket is streaming AND option is subscribed → LTP
      arrives every ~1s automatically via on_ticks. No REST call needed.
    - If WebSocket is down (disconnected / not authenticated) → fall back
      to REST poll every 15s so P&L doesn’t go stale.
    - Heartbeat log every 60s regardless of source.
    - Zerodha position reconciliation every 60s: if the exchange closed the
      position (SL-M filled, user closed in Kite, expiry), detect it here
      instead of showing a ghost open trade indefinitely.
    """
    await asyncio.sleep(3)   # short initial delay — let server finish startup
    _hb_tick = 0
    while True:
        if trader_state.active_trade:
            # Only hit REST if WebSocket isn’t delivering option ticks
            ws_delivering = (
                kite_manager.is_streaming
                and trader_state.active_option_token is not None
            )
            if not ws_delivering:
                # Fallback: REST poll every 15s
                await asyncio.to_thread(refresh_active_option_ltp)

            # Heartbeat + position reconciliation every ~60s (4 × 15s)
            _hb_tick += 1
            if _hb_tick % 4 == 0:
                # ── Zerodha position reconcile ───────────────────────────────
                # Checks Zerodha’s live qty for the active instrument.
                # If qty=0 (closed externally), force-closes the app trade.
                # Runs BEFORE heartbeat log so Alive msg shows current state.
                await asyncio.to_thread(reconcile_zerodha_position)

                # Re-check: reconcile may have just cleared active_trade
                if not trader_state.active_trade:
                    _hb_tick = 0
                    await asyncio.sleep(15)
                    continue

                # ── Heartbeat log ───────────────────────────────────────
                t     = trader_state.active_trade
                ltp   = trader_state.last_option_ltp
                nifty = trader_state.last_nifty_price or 0
                from auto_trader import _nifty_to_option_premium
                sl_prem  = _nifty_to_option_premium(t.stop_loss, t)
                tgt_prem = _nifty_to_option_premium(t.target, t) if t.target else None

                is_long = t.direction == 'long'
                locked_profit = (
                    t.stop_loss - t.entry_price if is_long
                    else t.entry_price - t.stop_loss
                )
                locked_label = f"Locked:{locked_profit:+.0f}pts" if locked_profit > 0 else f"Risk:{abs(locked_profit):.0f}pts"
                tgt_str  = f" | Tgt \u20b9{tgt_prem:.1f}" if tgt_prem else ""
                ltp_str  = f"\u20b9{ltp:.1f}" if ltp else "–"
                src      = "WS" if ws_delivering else "REST"

                _at_log("💓", "Alive",
                        f"LTP {ltp_str} | SL: \u20b9{t.stop_loss:.0f} (\u20b9{sl_prem:.1f}) {locked_label}{tgt_str} | Nifty \u20b9{nifty:.0f} [{src}]")
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
    # ✅ CRITICAL: Load settings from snapshot FIRST!
    from auto_trader import _recover_state
    _recover_state()
    print("♻️  Settings restored from snapshot")
    
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
    task_crude_hb     = asyncio.create_task(_crude_heartbeat_loop())  # 🐶 NEW: Every 60s heartbeat!
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


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """Prevent browser from caching HTML, JS, and CSS so UI changes apply immediately.

    Without this, browsers happily serve stale JS from cache after a server
    restart/deploy — causing new element IDs added to the template to be
    invisible to the old cached JS, which silently fails to populate them.
    """
    _NO_CACHE_TYPES = ("text/html", "application/javascript", "text/javascript", "text/css")

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        ct = response.headers.get("content-type", "")
        if any(t in ct for t in self._NO_CACHE_TYPES):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"]        = "no-cache"
            response.headers["Expires"]       = "0"
        return response


app.add_middleware(NoCacheStaticMiddleware)


# ── Simple response cache (avoid hammering Yahoo Finance) ────────────
_mtf_cache: dict = {"data": None, "timestamp": 0}
MTF_CACHE_TTL = 30  # seconds


# ── Azure Container Apps Health Check ────────────────────────────────
@app.get("/health")
async def simple_health():
    """Lightweight health check for Docker and Azure Container Apps.
    
    Returns 200 OK if the app is running.
    Used by Docker HEALTHCHECK and Azure health probes.
    """
    import psutil
    import os
    
    return {
        "status": "healthy",
        "service": "Inevitable Algorithmic Trading Platform",
        "timestamp": datetime.now().isoformat(),
        "uptime_seconds": int(_time.time() - psutil.Process(os.getpid()).create_time()),
        "memory_mb": round(psutil.Process().memory_info().rss / 1024 / 1024, 1),
        "cpu_percent": psutil.Process().cpu_percent(interval=0.1),
    }


# ── Pages ───────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main dashboard, or redirect to login if not authenticated."""
    if not kite_manager.is_authenticated:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("index.html", {
        "request": request,
        "is_live": True,
    })


@app.get("/patterns", response_class=HTMLResponse)
async def patterns_page(request: Request):
    """Dedicated 60-day pattern history page."""
    return templates.TemplateResponse("patterns.html", {"request": request})


@app.get("/pattern-charts", response_class=HTMLResponse)
async def pattern_charts_page(request: Request):
    """Visual chart pattern viewer with candlestick charts."""
    return templates.TemplateResponse("pattern_charts.html", {"request": request})


@app.get("/patterns-guide", response_class=HTMLResponse)
async def patterns_guide_page(request: Request):
    """Visual guide showing how every chart pattern strategy works."""
    return templates.TemplateResponse("patterns_guide.html", {"request": request})


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
                    "pivot_times":  p.pivot_times or [],   # timestamps of key pivots
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
async def login(request: Request):
    """Render login page, or redirect to dashboard if already authenticated."""
    if kite_manager.is_authenticated:
        return RedirectResponse(url="/")
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "login_url": kite_manager.login_url},
    )


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


# ── All open positions (every exchange) ──────────────────────

@app.get("/api/positions/all")
async def all_positions():
    """Return every open net position across ALL exchanges (NFO, MCX, BSE, NSE…)."""
    if not kite_manager.is_authenticated:
        return {"success": False, "error": "Not authenticated"}
    try:
        pos = kite_manager.kite.positions()
        open_pos = [
            {
                "exchange":     p.get("exchange", ""),
                "symbol":       p.get("tradingsymbol", ""),
                "quantity":     p.get("quantity", 0),
                "avg_price":    round(float(p.get("average_price") or 0), 2),
                "ltp":          round(float(p.get("last_price") or 0), 2),
                "pnl":          round(float(p.get("unrealised") or 0), 2),
                "product":      p.get("product", ""),
                "instrument_token": p.get("instrument_token"),
            }
            for p in pos.get("net", [])
            if int(p.get("quantity", 0)) != 0
        ]
        return {"success": True, "positions": open_pos, "count": len(open_pos)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Account Capital / Margins ─────────────────────────────────

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
    enabled_strategies: str = "",  # 🎯 NEW!
):
    """SSE stream: replay day candle-by-candle with live progress."""
    from backtester import replay_day
    import queue, threading

    q: queue.Queue = queue.Queue()
    
    # 🎯 Parse enabled_strategies
    enabled_list = enabled_strategies.split(',') if enabled_strategies else None
    if enabled_list:
        enabled_list = [s.strip() for s in enabled_list if s.strip()]

    def _run():
        try:
            result = replay_day(
                date_str=date, period=period, strategy_id=strategy,
                sl_points=sl_points, trailing_sl=trailing_sl, rr_ratio=rr_ratio,
                max_trades=max_trades, data_source=data_source, quantity=quantity,
                enabled_strategies=enabled_list,  # 🎯 Pass it!
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
    enabled_strategies: str = "",  # 🎯 NEW! Comma-separated strategy IDs
    strike_offset: int = 0,  # ⚡ NEW! Strike offset (ITM/OTM)
    trail_mode: str = "fixed",  # 🎯 NEW! Trail SL mode
    max_daily_loss: float = 3000.0,  # 🚫 NEW! Max daily loss
    cooldown: int = 0,  # ⏳ NEW! Cooldown minutes
):
    """SSE stream: full backtest with live day-by-day progress."""
    from backtester import run_backtest
    import queue, threading

    q: queue.Queue = queue.Queue()
    
    # 🎯 Parse enabled_strategies from CSV string to list
    enabled_list = enabled_strategies.split(',') if enabled_strategies else None
    if enabled_list:
        enabled_list = [s.strip() for s in enabled_list if s.strip()]  # Remove empty strings
        print(f"🎯 [Backtest API] Testing only: {enabled_list}")
    
    # 📦 Log new parameters
    print(f"⚡ [Backtest API] Strike Offset: {strike_offset} | Trail Mode: {trail_mode}")
    print(f"🚫 [Backtest API] Max Daily Loss: ₹{max_daily_loss:,.0f} | Cooldown: {cooldown}m")

    def _run():
        try:
            result = run_backtest(
                period=period, interval="5m", quantity=quantity,
                sl_points=sl_points, trailing_sl=trailing_sl, rr_ratio=rr_ratio,
                max_trades_per_day=max_trades, strategy_id=strategy,
                data_source=data_source,
                enabled_strategies=enabled_list,  # 🎯 Pass it!
                strike_offset=strike_offset,  # ⚡ NOW PASSED!
                trail_mode=trail_mode,  # 🎯 NOW PASSED!
                max_daily_loss=max_daily_loss,  # 🚫 NOW PASSED!
                cooldown_minutes=cooldown,  # ⏳ NOW PASSED!
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
                "start_idx": p.start_idx,  # ← ADDED: needed for chart generation!
                "end_idx": p.end_idx,      # ← ADDED: needed for chart generation!
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


@app.get("/api/pattern-chart/{pattern_index}")
async def pattern_chart(pattern_index: int, interval: str = "5m", lookback: int = 50):
    """Generate a visual chart for a detected pattern.
    
    Args:
        pattern_index: Index of the pattern in the deerns list
        interval: Timeframe ('5m', '15m', etc.)
        lookback: Number of candles to show before pattern start
    
    Returns:
        JSON with base64-encoded PNG image
    """
    try:
        from pattern_chart import generate_pattern_chart, MATPLOTLIB_AVAILABLE
        
        if not MATPLOTLIB_AVAILABLE:
            return {
                "success": False,
                "error": "matplotlib not installed - pattern charts unavailable"
            }
        
        # Fetch data and detect patterns
        df = await asyncio.to_thread(fetch_intraday_data, interval=interval, period="5d")
        result = await asyncio.to_thread(detect_all_patterns, df, timeframe=interval)
        
        patterns = result["patterns"]
        
        if pattern_index < 0 or pattern_index >= len(patterns):
            return {
                "success": False,
                "error": f"Pattern index {pattern_index} out of range (0-{len(patterns)-1})"
            }
        
        pattern = patterns[pattern_index]
        
        # Generate chart
        img_base64 = await asyncio.to_thread(
            generate_pattern_chart,
            df,
            pattern,
            lookback=lookback
        )
        
        if img_base64 is None:
            return {
                "success": False,
                "error": "Failed to generate pattern chart"
            }
        
        return {
            "success": True,
            "image": img_base64,
            "pattern": {
                "name": pattern.name,
                "bias": pattern.bias,
                "confidence": pattern.confidence,
                "description": pattern.description,
            }
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
        # trailing_sl = CURRENT stop-loss (moves up/down as trail fires)
        # original_sl = SL at the moment of entry (never changes)
        # UI uses these two to know whether the trail has actually advanced.
        trade["trailing_sl"]        = round(trade["stop_loss"], 2)
        trade["original_sl"]        = round(at_state.entry_nifty_sl, 2) if at_state.entry_nifty_sl else trade["stop_loss"]
        
        # trailing_sl_premium = CURRENT SL in option premium terms (what trader sees!)
        # original_sl_premium = original SL in option premium terms
        from auto_trader import _nifty_to_option_premium
        trade["trailing_sl_premium"] = round(_nifty_to_option_premium(trade["stop_loss"], at_state.active_trade), 2) if trade["stop_loss"] else None
        trade["original_sl_premium"] = round(_nifty_to_option_premium(at_state.entry_nifty_sl, at_state.active_trade), 2) if at_state.entry_nifty_sl else None

    # Always expose live Nifty price + auto-exit time at top level
    from auto_trader import EXIT_TIME
    status["nifty_current"] = round(at_state.last_nifty_price, 2) if at_state.last_nifty_price else None
    status["exit_time"]     = EXIT_TIME.strftime("%H:%M") if EXIT_TIME else None

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


@app.post("/api/crude/reset-daily")
async def crude_reset_daily():
    """Manually reset daily counters (orders_placed, trades_today, total_pnl).

    Use this if you want to allow more entries in the same session, or if
    the automatic midnight reset didn't fire (e.g. server was restarted
    mid-session with a stale snapshot).
    """
    from crude_trader import state as cs, _reset_daily_counters_if_new_day
    # Force reset regardless of date
    cs.trade_date    = ""   # blank so the date check always fires
    _reset_daily_counters_if_new_day()
    return {
        'success':       True,
        'orders_placed': cs.orders_placed,
        'trade_date':    cs.trade_date,
        'message':       f'Daily counters reset. orders_placed={cs.orders_placed}',
    }


@app.post("/api/crude/config")
async def crude_config(
    sl_points:      float | None = None,
    trail_points:   float | None = None,
    rr_ratio:       float | None = None,
    capital:        float | None = None,
    strike_offset:  int   | None = None,
    trail_mode:     str   | None = None,   # 'off' | 'atr0.4' | 'atr0.7' | 'atr1.5' | 'atr2' | 'premium'
    atr_multiplier: float | None = None,
    max_trades:     int   | None = None,   # max entries per day (1-20)
    max_daily_loss: float | None = None,
):
    from crude_trader import state as cs, CRUDE_MAX_TRADES, save_crude_settings
    
    print(f"🔧 [API] Crude config update: sl={sl_points} trail={trail_points} rr={rr_ratio} cap={capital} mode={trail_mode} offset={strike_offset} max={max_trades} loss={max_daily_loss}")
    
    if sl_points      is not None: cs.sl_points      = sl_points
    if trail_points   is not None: cs.trail_points   = trail_points
    if rr_ratio       is not None: cs.rr_ratio       = rr_ratio
    if capital        is not None: cs.capital        = capital
    if strike_offset  is not None: cs.strike_offset  = strike_offset
    if trail_mode     is not None:
        # Parse ATR multiplier from trail mode
        valid_modes = ('off', 'atr0.4', 'atr0.7', 'atr1.5', 'atr2', 'premium', 'atr')  # 'atr' for backwards compat
        if trail_mode in valid_modes:
            # Extract multiplier from mode string
            if trail_mode == 'atr0.4':
                cs.trail_mode = 'atr'
                cs.atr_multiplier = 0.4
            elif trail_mode == 'atr0.7':
                cs.trail_mode = 'atr'
                cs.atr_multiplier = 0.7
            elif trail_mode == 'atr1.5':
                cs.trail_mode = 'atr'
                cs.atr_multiplier = 1.5
            elif trail_mode == 'atr2':
                cs.trail_mode = 'atr'
                cs.atr_multiplier = 2.0
            elif trail_mode == 'atr':
                cs.trail_mode = 'atr'
                # Keep existing multiplier or default to 1.5
                if atr_multiplier is None:
                    cs.atr_multiplier = 1.5
            else:
                cs.trail_mode = trail_mode  # 'off' or 'premium'
            print(f"✅ [API] Trail mode set to: {cs.trail_mode} (mult={cs.atr_multiplier})")
        else:
            print(f"⚠️ [API] Invalid trail mode: {trail_mode}")
    if atr_multiplier is not None: cs.atr_multiplier = atr_multiplier
    if max_trades     is not None: cs.max_trades     = max(1, min(20, max_trades))
    if max_daily_loss is not None: cs.max_daily_loss = max(100, abs(max_daily_loss))
    
    save_crude_settings()   # ← persist to disk immediately
    print(f"💾 [API] Settings saved to crude_settings.json")
    
    return {
        'success': True,
        'sl_points': cs.sl_points, 'trail_points': cs.trail_points,
        'rr_ratio': cs.rr_ratio,   'capital': cs.capital,
        'trail_mode': cs.trail_mode, 'atr_multiplier': cs.atr_multiplier,
        'strike_offset': cs.strike_offset,  # 🐶 ADDED: return this too!
        'max_trades': cs.max_trades,
        'max_daily_loss': cs.max_daily_loss,
    }


@app.post("/api/crude/strategies")
async def crude_update_strategies(request: Request):
    """🎯 Update enabled strategy selection.
    
    Body: {"enabled_strategies": ["supertrend", "divergence", ...]}
    Empty list = all strategies enabled (default)
    """
    from crude_trader import state as cs, save_crude_settings
    from crude_meta_router import CRUDE_STRATEGIES
    
    body = await request.json()
    enabled = body.get("enabled_strategies", [])
    
    # Validate strategy IDs
    valid_ids = {s["id"] for s in CRUDE_STRATEGIES}
    invalid = [sid for sid in enabled if sid not in valid_ids]
    
    if invalid:
        return {"success": False, "error": f"Invalid strategy IDs: {invalid}"}
    
    # Update state
    cs.enabled_strategies = enabled
    save_crude_settings()  # Persist to disk
    
    count = len(enabled) if enabled else len(CRUDE_STRATEGIES)
    total = len(CRUDE_STRATEGIES)
    
    print(f"🎯 [API] Strategy selection updated: {count}/{total} enabled")
    if enabled:
        print(f"  Active: {', '.join(enabled)}")
    else:
        print(f"  All strategies enabled (default)")
    
    return {
        "success": True,
        "enabled_strategies": enabled,
        "enabled_count": count,
        "total_count": total,
        "all_enabled": not bool(enabled),  # empty list = all enabled
    }


@app.get("/api/crude/strategies")
async def crude_get_strategies():
    """🎯 Get all available strategies and their enabled status."""
    from crude_trader import state as cs
    from crude_meta_router import CRUDE_STRATEGIES
    
    # If enabled_strategies is empty, all are enabled
    enabled_ids = set(cs.enabled_strategies) if cs.enabled_strategies else {s["id"] for s in CRUDE_STRATEGIES}
    
    strategies = [
        {
            "id": s["id"],
            "name": s["name"],
            "emoji": s["emoji"],
            "category": s["category"],
            "win_rate": s["win_rate"],
            "enabled": s["id"] in enabled_ids,
        }
        for s in CRUDE_STRATEGIES
    ]
    
    return {
        "success": True,
        "strategies": strategies,
        "enabled_count": len(enabled_ids),
        "total_count": len(CRUDE_STRATEGIES),
        "all_enabled": not bool(cs.enabled_strategies),
    }


@app.get("/api/crude/margin")
async def crude_margin():
    """Return live margin + per-lot cost for the current ATM option.

    Calls Zerodha order_margins() so you can see exactly what will be
    charged before committing — no more surprise 'Insufficient funds'.
    """
    from crude_trader import _fetch_available_margin, _query_zerodha_margin
    from crude_data import get_crude_spot, get_crude_atm_option, get_crude_option_ltp
    
    # Check authentication first
    if not kite_manager.is_authenticated:
        return {"success": False, "error": "Not authenticated with Zerodha. Please login to Kite."}
    
    try:
        # Fetch available margin
        margin_result = _fetch_available_margin()
        
        if not margin_result or margin_result[0] is None:
            return {"success": False, "error": "Failed to fetch margin data from Zerodha. Please ensure you're logged into Kite."}
        
        free, total, utilised = margin_result
        
        # Get crude spot price and ATM option
        spot = get_crude_spot()
        if not spot:
            return {
                "success": True,
                "free": free,
                "total": total,
                "utilised": utilised,
                "net": free - (utilised or 0) if free and utilised else free,
                "margin_1lot": None,
                "margin_2lot": None,
                "max_lots": 0,
                "shortfall": 0,
            }
        
        # Get ATM option symbol (default to LONG for margin calculation)
        from strategy import Direction
        atm_result = get_crude_atm_option(spot, Direction.LONG.value, strike_offset=0)
        if not atm_result:
            return {
                "success": True,
                "free": free,
                "total": total,
                "utilised": utilised,
                "net": free - (utilised or 0) if free and utilised else free,
                "margin_1lot": None,
                "margin_2lot": None,
                "max_lots": 0,
                "shortfall": 0,
            }
        
        atm_symbol, atm_token, lot_size = atm_result
        
        # Query Zerodha for exact margin for 1 lot and 2 lots
        margin_1lot = _query_zerodha_margin(atm_symbol, lots=1)
        margin_2lot = _query_zerodha_margin(atm_symbol, lots=2)
        
        # Calculate max affordable lots
        if margin_1lot and margin_1lot > 0:
            max_lots = int(free * 0.95 / margin_1lot)  # Use 95% of free margin for safety
        else:
            max_lots = 0
        
        # Calculate shortfall if can't afford even 1 lot
        shortfall = max(0, (margin_1lot or 0) - free) if margin_1lot else 0
        
        return {
            "success": True,
            "free": free,
            "total": total,
            "utilised": utilised,
            "net": free - (utilised or 0) if free and utilised else free,
            "margin_1lot": margin_1lot,
            "margin_2lot": margin_2lot,
            "max_lots": max_lots,
            "shortfall": shortfall,
            "spot": spot,
            "symbol": atm_symbol,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": f"Error fetching crude margin: {str(e)}"}


# ── Crude Pattern Detection ─────────────────────────────

@app.get("/api/crude/patterns")
async def crude_patterns(interval: str = "5m"):
    """Detect chart patterns in crude oil data."""
    try:
        from crude_data import fetch_crude_intraday_data
        # Convert interval format: 5m → 5minute for Kite API
        kite_interval = interval.replace('m', 'minute') if 'm' in interval else interval
        df = await asyncio.to_thread(fetch_crude_intraday_data, interval=kite_interval, days_back=5)
        
        if df is None or df.empty:
            return {"success": False, "error": "No crude data available"}
        
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
                "start_idx": p.start_idx,
                "end_idx": p.end_idx,
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


@app.get("/api/crude/pattern-chart/{pattern_index}")
async def crude_pattern_chart(pattern_index: int, interval: str = "5m", lookback: int = 50):
    """Generate a visual chart for a detected crude oil pattern.
    
    Args:
        pattern_index: Index of the pattern in the detected list
        interval: Timeframe ('5m', '15m', etc.)
        lookback: Number of candles to show before pattern start
    
    Returns:
        JSON with base64-encoded PNG image
    """
    try:
        from crude_data import fetch_crude_intraday_data
        from pattern_chart import generate_pattern_chart
        
        # Convert interval format: 5m → 5minute for Kite API
        kite_interval = interval.replace('m', 'minute') if 'm' in interval else interval
        
        # Fetch crude data
        df = await asyncio.to_thread(fetch_crude_intraday_data, interval=kite_interval, days_back=5)
        
        if df is None or df.empty:
            return {"success": False, "error": "No crude data available"}
        
        # Detect patterns
        result = await asyncio.to_thread(detect_all_patterns, df, timeframe=interval)
        patterns = result["patterns"]
        
        if pattern_index < 0 or pattern_index >= len(patterns):
            return {"success": False, "error": f"Pattern index {pattern_index} out of range (0-{len(patterns)-1})"}
        
        pattern = patterns[pattern_index]
        
        # Generate chart
        img_base64 = await asyncio.to_thread(generate_pattern_chart, df, pattern, lookback)
        
        if not img_base64:
            return {"success": False, "error": "Failed to generate pattern chart"}
        
        return {
            "success": True,
            "image": img_base64,
            "pattern": {
                "name": pattern.name,
                "bias": pattern.bias,
                "confidence": pattern.confidence,
                "description": pattern.description,
            }
        }
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}
    from crude_trader import state as cs
    try:
        free, net, used = await asyncio.to_thread(_fetch_available_margin)
        if free is None:
            return JSONResponse({"success": False, "available": 0.0,
                    "error": "Zerodha session expired — re-login at /login"})

        spot = await asyncio.to_thread(get_crude_spot)
        margin_1lot = margin_2lot = None
        symbol = None
        if spot:
            try:
                sym, _, lot_sz = get_crude_atm_option(
                    spot, 'long', cs.strike_offset, capital=free  # use FREE margin for contract selection
                )
                symbol  = sym
                ltp_val = await asyncio.to_thread(get_crude_option_ltp, sym)
                margin_1lot = await asyncio.to_thread(_query_zerodha_margin, sym, 1, ltp_val)
                margin_2lot = await asyncio.to_thread(_query_zerodha_margin, sym, 2, ltp_val)
            except Exception as ex:
                print(f"⚠️  Margin card lookup failed: {ex}")

        max_lots = 0
        if margin_1lot and margin_1lot <= free:
            max_lots = 1
            if margin_2lot and margin_2lot <= free:
                max_lots = 2

        return JSONResponse({
            "success":      True,
            "free":         round(free, 2),    # actual spendable margin
            "available":    round(free, 2),    # alias kept for JS compatibility
            "net":          round(net,  2),    # total net (includes utilised)
            "utilised":     round(used, 2),   # locked in open positions
            "symbol":       symbol,
            "margin_1lot":  round(margin_1lot, 2) if margin_1lot else None,
            "margin_2lot":  round(margin_2lot, 2) if margin_2lot else None,
            "max_lots":     max_lots,
            "can_trade":    max_lots >= 1,
            "shortfall":    round(max(0, (margin_1lot or 0) - free), 2),
        })
    except Exception as e:
        return JSONResponse({"success": False, "available": 0.0, "error": str(e)})


@app.post("/api/crude/evaluate")
async def crude_evaluate():
    """Manually trigger one strategy evaluation cycle on latest candle data.

    Returns full status PLUS per-strategy breakdown so the UI
    can show a dashboard of all strategies and their pass/fail.
    """
    from crude_trader import evaluate_and_act_crude, get_crude_status
    from crude_strategy import evaluate_crude_all
    from crude_data import fetch_crude_intraday_data, get_crude_spot

    try:
        def _run():
            df    = fetch_crude_intraday_data('5minute', 5)
            price = get_crude_spot()
            if df is None or df.empty or not price:
                return None, None, 'No market data available'
            strategies = evaluate_crude_all(df)
            evaluate_and_act_crude(df, price)
            return get_crude_status(), strategies, None

        result, strategies, err = await asyncio.to_thread(_run)
        if err:
            return JSONResponse({'success': False, 'error': err})
        return JSONResponse({'success': True, 'strategies': strategies, **result})
    except Exception as e:
        return JSONResponse({'success': False, 'error': str(e)})


@app.post("/api/crude/force-entry")
async def crude_force_entry(direction: str = Query(...)):
    """Manually force a Crude trade entry (LONG or SHORT) bypassing signal check."""
    from crude_trader import state as crude_state, _enter_trade, get_crude_status
    from crude_data import fetch_crude_intraday_data, get_crude_spot
    from strategy import Direction
    
    direction = direction.upper()
    if direction not in ("LONG", "SHORT"):
        return JSONResponse({"success": False, "error": "direction must be LONG or SHORT"})
    if crude_state.active_trade:
        return JSONResponse({"success": False, "error": "Already in a trade — exit first"})
    if not crude_state.is_running:
        return JSONResponse({"success": False, "error": "Crude trader not running — press Start first"})
    try:
        def _run():
            price = get_crude_spot() or crude_state.last_crude_price or 9000.0
            _enter_trade(Direction(direction.lower()), price)
            return get_crude_status()
        result = await asyncio.to_thread(_run)
        return JSONResponse({"success": True, **result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/api/crude/force-exit")
async def crude_force_exit():
    """Manually exit the active Crude trade at market price immediately."""
    from crude_trader import state as crude_state, _exit_position, get_crude_status
    from crude_data import get_crude_spot
    
    if not crude_state.active_trade:
        return JSONResponse({"success": False, "error": "No active trade to exit"})
    try:
        def _run():
            price = get_crude_spot() or crude_state.last_crude_price or 9000.0
            _exit_position("MANUAL EXIT", price)
            return get_crude_status()
        result = await asyncio.to_thread(_run)
        return JSONResponse({"success": True, **result})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/api/crude/sync-positions")
async def crude_sync_positions():
    """Sync existing crude option positions from Zerodha into the trader.
    
    This pulls any open MCX crude oil option positions from Zerodha and
    loads them into the auto-trader so it can manage them.
    """
    from crude_trader import state as crude_state, CrudeTrade, CrudeOrderStatus, _save_snapshot, _log  # 🐶 ADD _log!
    from crude_data import get_crude_spot
    import traceback as _tb
    
    if not kite_manager.is_authenticated:
        _log("❌", "Sync failed", "Not authenticated with Zerodha")
        return JSONResponse({"success": False, "error": "Not authenticated with Zerodha"})
    
    try:
        _log("🔍", "Fetching positions", "Querying Zerodha API...")
        # Get positions from Zerodha
        positions = kite_manager.kite.positions().get('net', [])
        
        # Filter for crude oil options (MCX)
        crude_positions = [
            p for p in positions 
            if p.get('exchange') == 'MCX' 
            and 'CRUDEOIL' in (p.get('tradingsymbol') or '')
            and (p.get('tradingsymbol') or '').endswith(('CE', 'PE'))
            and p.get('quantity', 0) != 0
        ]
        
        print(f"🔍 [Sync] Found {len(crude_positions)} crude option positions")
        _log("🔍", "Sync initiated", f"Found {len(crude_positions)} crude positions")
        
        if not crude_positions:
            _log("ℹ️", "Sync complete", "No crude options found in Zerodha")
            return JSONResponse({"success": True, "found": False, "message": "No crude options found"})
        
        # Take the first position (if multiple, warn user)
        pos = crude_positions[0]
        qty = pos.get('quantity', 0)
        symbol = pos.get('tradingsymbol', '')
        avg_price = pos.get('average_price', 0)
        
        print(f"🔄 [Sync] Syncing position: {symbol} qty={qty} avg={avg_price}")
        _log("🔄", "Syncing position", f"{symbol} qty={qty} @ ₹{avg_price}")
        
        # Determine direction
        direction = 'long' if qty > 0 else 'short'
        qty = abs(qty)
        
        # Crude options on Zerodha are traded in LOTS already!
        # qty=3 means 3 lots, NOT 3 barrels
        # Each lot = 10 barrels for MINI, 100 for full
        lot_size = 10 if 'M' in symbol or 'MINI' in symbol.upper() else 100
        lots = qty  # Zerodha already gives us lots!
        
        print(f"🔄 [Sync] Position: {lots} lots ({lots * lot_size} barrels) of {symbol}")
        crude_spot = get_crude_spot() or 9000.0
        
        # Calculate SL and Target based on current settings
        sl_points = crude_state.sl_points
        target_points = sl_points * crude_state.rr_ratio
        
        if direction == 'long':
            stop_loss = crude_spot - sl_points
            target = crude_spot + target_points
        else:
            stop_loss = crude_spot + sl_points
            target = crude_spot - target_points
        
        # Calculate premium-based SL
        sl_prem = round(avg_price - sl_points, 1) if direction == 'long' else round(avg_price + sl_points, 1)
        tgt_prem = round(avg_price + target_points, 1) if direction == 'long' else round(avg_price - target_points, 1)
        
        # Create CrudeTrade object
        trade = CrudeTrade(
            id=f"CRUDE-SYNC-{int(_time.time())}",
            timestamp=datetime.now().isoformat(),
            direction=direction,
            instrument=f"MCX:{symbol}",
            entry_price=crude_spot,
            entry_premium=avg_price,
            quantity=lots,
            lot_size=lot_size,
            stop_loss=stop_loss,
            target=target,
            sl_premium=sl_prem,
            tgt_premium=tgt_prem,
            peak_ltp=avg_price,
            status=CrudeOrderStatus.FILLED,
            order_id="ZERODHA-SYNC",
            paper=False,
        )
        
        # Update state
        crude_state.active_trade = trade
        crude_state.last_option_ltp = avg_price
        crude_state.entry_crude_sl = stop_loss
        
        # Save snapshot
        _save_snapshot()
        
        print(f"✅ [Sync] Position synced: {direction.upper()} {lots} lots @ ₹{avg_price}")
        _log("✅", "Sync successful", f"{direction.upper()} {lots} lots @ ₹{avg_price} | SL ₹{stop_loss:.0f} | Tgt ₹{target:.0f}")
        
        return JSONResponse({
            "success": True,
            "found": True,
            "direction": direction,
            "instrument": symbol,
            "lots": lots,
            "entry_premium": avg_price,
            "entry_price": crude_spot,
            "stop_loss": stop_loss,
            "target": target,
        })
        
    except Exception as e:
        _tb.print_exc()
        _log("❌", "Sync error", str(e)[:100])
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/api/crude/add-lots")
async def crude_add_lots(request: Request):
    """Add extra lots to the current active crude trade (scale-in).

    Body: { "lots": 2 }  — defaults to 1 if not provided.
    """
    from crude_trader import add_lots_to_trade
    import traceback as _tb
    try:
        body  = await request.json()
        extra = int(body.get("lots", 1))
    except Exception:
        extra = 1
    try:
        result = await asyncio.to_thread(add_lots_to_trade, extra)
        return JSONResponse(result)
    except Exception as exc:
        _tb.print_exc()
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


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


@app.get("/crude/chart", response_class=HTMLResponse)
async def crude_chart_page(request: Request):
    """Standalone live chart page for MCX Crude Oil."""
    return templates.TemplateResponse("crude_chart.html", {"request": request})


@app.get("/api/crude/chart-data")
async def crude_chart_data(days: int = 3):
    """OHLCV + indicators for the Lightweight Charts frontend.

    Returns:
      candles   — list of {time, open, high, low, close, volume}
      st_long   — SuperTrend line values when direction=LONG  (green)
      st_short  — SuperTrend line values when direction=SHORT (red)
      vwap      — session VWAP line
      ema9      — EMA 9
      ema21     — EMA 21
      signals   — entry/exit markers from trade log
      meta      — {symbol, price, days_to_expiry}
    Time values are Unix seconds (UTC) for Lightweight Charts.
    """
    import indicators as ind
    from crude_data import fetch_crude_intraday_data, get_crude_spot
    from crude_data import _last_futures_symbol, _last_days_to_expiry
    from crude_trader import CRUDE_LOG_FILE
    from crude_strategy import (
        CRUDE_ST_PERIOD, CRUDE_ST_MULTIPLIER,
        CRUDE_EMA_FAST, CRUDE_EMA_SLOW,
    )
    import json as _json

    def _run():
        df = fetch_crude_intraday_data('5minute', max(days, 1))
        price = get_crude_spot()
        return df, price

    df, price = await asyncio.to_thread(_run)
    if df is None or df.empty:
        return JSONResponse({'error': 'No data — check Kite auth'}, status_code=503)

    # Convert index to UTC Unix seconds
    def _ts(idx):
        return int(idx.tz_convert('UTC').timestamp())

    # ── OHLCV candles ────────────────────────────────────────────
    candles = [
        {'time': _ts(r.Index), 'open': r.open, 'high': r.high,
         'low': r.low, 'close': r.close, 'volume': int(r.volume)}
        for r in df.itertuples()
    ]

    # ── SuperTrend ───────────────────────────────────────────────
    st_df = ind.supertrend(
        df['high'], df['low'], df['close'],
        CRUDE_ST_PERIOD, CRUDE_ST_MULTIPLIER
    )
    st_long, st_short = [], []
    for idx, row in st_df.iterrows():
        val = row['supertrend']
        if pd.isna(val):
            continue
        pt = {'time': _ts(idx), 'value': round(float(val), 2)}
        (st_long if float(row['direction']) == 1 else st_short).append(pt)

    # ── Session VWAP (today only) ─────────────────────────────────
    import pytz
    today_ist = pd.Timestamp.now(tz='Asia/Kolkata').date()
    today_df  = df[df.index.tz_convert('Asia/Kolkata').map(lambda x: x.date()) == today_ist]
    vwap_pts  = []
    if not today_df.empty:
        tp       = (today_df['high'] + today_df['low'] + today_df['close']) / 3
        cum_tp   = (tp * today_df['volume']).cumsum()
        cum_vol  = today_df['volume'].cumsum()
        vwap_ser = cum_tp / cum_vol.replace(0, float('nan'))
        vwap_pts = [
            {'time': _ts(idx), 'value': round(float(v), 2)}
            for idx, v in vwap_ser.items() if not pd.isna(v)
        ]

    # ── EMA 9 / EMA 21 ───────────────────────────────────────────
    ema9_ser  = ind.ema(df['close'], CRUDE_EMA_FAST)
    ema21_ser = ind.ema(df['close'], CRUDE_EMA_SLOW)
    ema9  = [{'time': _ts(i), 'value': round(float(v), 2)}
             for i, v in ema9_ser.items()  if not pd.isna(v)]
    ema21 = [{'time': _ts(i), 'value': round(float(v), 2)}
             for i, v in ema21_ser.items() if not pd.isna(v)]

    # ── Trade signals from log ────────────────────────────────────
    signals = []
    try:
        raw_log = _json.loads(CRUDE_LOG_FILE.read_text())
        for t in raw_log:
            if t.get('entry_time'):
                try:
                    ts = int(pd.Timestamp(t['entry_time'])
                             .tz_localize('Asia/Kolkata')
                             .tz_convert('UTC').timestamp())
                    signals.append({
                        'time': ts,
                        'position': 'belowBar' if t.get('direction') == 'long' else 'aboveBar',
                        'color': '#22c55e' if t.get('direction') == 'long' else '#ef4444',                  'shape': 'arrowUp' if t.get('direction') == 'long' else 'arrowDown',
                        'text': f"{t.get('direction','?').upper()} @{t.get('entry_price','?')}",
                    })
                except Exception:
                    pass
            if t.get('exit_time'):
                try:
                    ts = int(pd.Timestamp(t['exit_time'])
                             .tz_localize('Asia/Kolkata')
                             .tz_convert('UTC').timestamp())
                    pnl = t.get('pnl', 0)
                    signals.append({
                        'time': ts,
                        'position': 'aboveBar' if t.get('direction') == 'long' else 'belowBar',
                        'color': '#facc15',
                        'shape': 'circle',
                        'text': f"EXIT {'▲' if pnl>0 else '▼'}{abs(pnl):.0f}",
                    })
                except Exception:
                    pass
    except Exception:
        pass

    return JSONResponse({
        'candles':  candles,
        'st_long':  st_long,
        'st_short': st_short,
        'vwap':     vwap_pts,
        'ema9':     ema9,
        'ema21':    ema21,
        'signals':  signals,
        'meta': {
            'symbol':         _last_futures_symbol or 'CRUDEOIL',
            'price':          price,
            'days_to_expiry': _last_days_to_expiry,
            'candle_count':   len(candles),
        },
    })


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
    max_daily_loss:     float | None = Query(None),  # ← NEW: Configurable max loss
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
        max_daily_loss=max_daily_loss,  # ← NEW: Pass max loss to configure
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


@app.post("/api/auto-trader/reconcile")
async def auto_trader_reconcile():
    """Manually trigger a Zerodha position reconcile check.

    Queries Zerodha for the active instrument's live qty.  If qty=0 (position
    closed externally by exchange SL-M, manual close in Kite, or expiry),
    force-closes the app trade and returns a summary.

    This also runs automatically every ~60s via the LTP heartbeat loop.
    Use this endpoint to trigger it immediately without waiting.
    """
    trade = trader_state.active_trade
    if not trade:
        return {"message": "No active trade to reconcile", "action": "none"}
    instrument = trade.instrument
    await asyncio.to_thread(reconcile_zerodha_position)
    if not trader_state.active_trade:
        return {
            "message": f"{instrument} confirmed closed in Zerodha — app state cleared",
            "action": "force_closed",
        }
    return {
        "message": f"{instrument} still open in Zerodha — no change",
        "action": "none",
    }


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
    from auto_trader import state as at_state, Direction, _enter_trade
    from data_fetcher import fetch_intraday_data  # ← FIX: correct import
    
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


@app.post("/api/auto-trader/update-sl-premium")
async def auto_trader_update_sl_premium(premium_sl: float = Query(...)):
    """Directly update SL based on premium value (for synced trades).
    
    This bypasses the Nifty-to-premium conversion and sets the SL
    to trigger when option premium falls to the specified level.
    Useful for synced trades where the normal calculation is wrong.
    """
    from auto_trader import state as at_state, _save_state_snapshot, ASSUMED_DELTA
    
    if not at_state.active_trade:
        return {"success": False, "error": "No active trade"}
    
    if premium_sl < 5 or premium_sl > 500:
        return {"success": False, "error": "Premium SL must be between ₹5 and ₹500"}
    
    t = at_state.active_trade
    old_sl_nifty = t.stop_loss
    from auto_trader import _nifty_to_option_premium
    old_sl_premium = _nifty_to_option_premium(old_sl_nifty, t)
    
    # Reverse calculate: what Nifty SL gives this premium?
    # premium_sl = entry_premium + (nifty_sl - entry_nifty) * sign * delta
    # Solve for nifty_sl:
    sign = 1.0 if t.direction == "long" else -1.0
    nifty_sl = t.entry_price + ((premium_sl - t.entry_premium) / (sign * ASSUMED_DELTA))
    
    # Update the SL
    t.stop_loss = round(nifty_sl, 2)
    
    # Save state
    _save_state_snapshot()
    
    # Verify the new premium SL
    new_sl_premium = _nifty_to_option_premium(t.stop_loss, t)
    
    return {
        "success": True,
        "old_sl_nifty": old_sl_nifty,
        "new_sl_nifty": t.stop_loss,
        "old_sl_premium": round(old_sl_premium, 2),
        "new_sl_premium": round(new_sl_premium, 2),
        "requested_premium": premium_sl,
        "direction": t.direction,
        "entry_premium": t.entry_premium,
    }


@app.get("/api/auto-trader/history")
async def auto_trader_history():
    """Return TODAY's completed trade history from trade log.

    Filters by today's date so yesterday's trades never pollute
    the day P&L or trade history after a server restart.
    Filters out ghost trades (status=filled with no exit_premium)
    so they don't pollute the history or P&L totals.
    """
    from pathlib import Path
    import json as _json
    from datetime import datetime as _dt
    today = _dt.now().strftime("%Y-%m-%d")
    log_file = Path(__file__).parent / "trade_log.json"
    if not log_file.exists():
        return {"success": True, "trades": [], "total_pnl": 0}
    try:
        data   = _json.loads(log_file.read_text())
        trades = data.get("trades", [])

        # Dedup by trade ID (last occurrence wins)
        seen: dict = {}
        for t in trades:
            seen[t["id"]] = t
        trades = list(seen.values())

        # Only today's properly exited trades
        completed = [
            t for t in trades
            if t.get("status") == "exited"
            and t.get("exit_premium") is not None
            and (t.get("timestamp") or "")[:10] == today
        ]

        realized_pnl = round(sum(t.get("pnl", 0) or 0 for t in completed), 2)

        # Include unrealized P&L of any currently open trade
        from auto_trader import state as at_state
        unrealized_pnl = 0.0
        if at_state.active_trade and at_state.last_option_ltp > 0:
            t   = at_state.active_trade
            ltp = at_state.last_option_ltp
            unrealized_pnl = round((ltp - t.entry_premium) * t.quantity, 2)

        # Ghost trades for today only
        today_trades = [t for t in trades if (t.get("timestamp") or "")[:10] == today]
        ghost_count  = len([t for t in today_trades if t.get("status") != "exited" or t.get("exit_premium") is None])

        return {
            "success":        True,
            "trades":         completed,
            "total_pnl":      realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "day_total_pnl":  round(realized_pnl + unrealized_pnl, 2),
            "ghost_trades":   ghost_count,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Archive & Historical Data API ─────────────────────────────────────

@app.get("/api/archive/trades/{date}")
async def get_archived_trades(date: str):
    """
    Get all trades for a specific date.
    
    Args:
        date: YYYY-MM-DD format
        
    Returns:
        Trade data for that date or error
    """
    try:
        from data_manager import get_trades_for_date
        trades_data = await asyncio.to_thread(get_trades_for_date, date)
        
        if trades_data is None:
            return {
                "success": False,
                "error": f"No trades found for {date}"
            }
        
        return {
            "success": True,
            "date": date,
            "data": trades_data
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/archive/stats/last-n-days/{days}")
async def get_last_n_days_stats(days: int = 7):
    """
    Get statistics for last N days.
    
    Args:
        days: Number of days to look back (default: 7)
        
    Returns:
        Aggregated statistics
    """
    try:
        from data_manager import get_last_n_days_stats
        stats = await asyncio.to_thread(get_last_n_days_stats, days)
        
        return {
            "success": True,
            "stats": stats
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/archive/stats/monthly/{year}/{month}")
async def get_monthly_stats(year: int, month: int):
    """
    Get statistics for a specific month.
    
    Args:
        year: 2026
        month: 1-12
        
    Returns:
        Monthly statistics
    """
    try:
        from data_manager import get_monthly_stats
        stats = await asyncio.to_thread(get_monthly_stats, year, month)
        
        return {
            "success": True,
            "stats": stats
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/archive/manual")
async def manual_archive(date: str = None):
    """
    Manually trigger archive of trade_log.json.
    
    Args:
        date: Optional date (YYYY-MM-DD), defaults to today
        
    Returns:
        Archive result
    """
    try:
        from data_manager import archive_today_trades
        result = await asyncio.to_thread(archive_today_trades, date)
        
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/archive/list")
async def list_archives():
    """
    List all available archived dates.
    
    Returns:
        List of dates that have archives
    """
    try:
        from pathlib import Path
        archives_dir = Path(__file__).parent / "archives"
        
        if not archives_dir.exists():
            return {"success": True, "dates": []}
        
        # Find all trade_log_*.json files
        archive_files = sorted(archives_dir.glob("trade_log_*.json"))
        
        dates = []
        for f in archive_files:
            # Extract date from filename: trade_log_2026-03-23.json
            date_str = f.stem.replace('trade_log_', '')
            dates.append(date_str)
        
        return {
            "success": True,
            "dates": sorted(dates, reverse=True)  # Most recent first
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


# 🎯 NEW: Nifty strategy selection endpoints (like Crude Oil)
@app.get("/api/nifty/strategies")
async def get_nifty_strategies():
    """Return all Nifty strategies with enabled status."""
    import strategies.loader  # noqa: F401
    from strategies.registry import all_strategies
    from auto_trader import state
    from calibrator import win_rate_for
    
    all_strats = all_strategies()
    enabled_set = set(state.enabled_strategies) if state.enabled_strategies else set()
    all_enabled = len(enabled_set) == 0  # empty = all enabled
    
    # Map categories for display
    category_map = {
        "trend": "trend",
        "reversal": "reversal",
        "breakout": "breakout",
        "momentum": "momentum",
        "scalping": "scalping",
        "adaptive": "adaptive",
        "pattern": "pattern",
    }
    
    strategies_list = [
        {
            "id": s.id,
            "name": s.name,
            "emoji": s.emoji,
            "category": category_map.get(s.category, s.category),
            "win_rate": round(win_rate_for(s.id), 1),
            "enabled": all_enabled or s.id in enabled_set,
        }
        for s in all_strats
        if s.id not in ("smart_router", "meta_router")  # Skip meta strategies
    ]
    
    enabled_count = len([s for s in strategies_list if s["enabled"]])
    
    return {
        "success": True,
        "strategies": strategies_list,
        "enabled_count": enabled_count,
        "total_count": len(strategies_list),
        "all_enabled": all_enabled,
    }


@app.post("/api/nifty/strategies")
async def update_nifty_strategies(request: Request):
    """Update which strategies are enabled for Nifty trading."""
    from auto_trader import state, _save_state_snapshot
    
    try:
        body = await request.json()
        enabled_list = body.get("enabled_strategies", [])
        
        # Validate strategy IDs
        import strategies.loader  # noqa: F401
        from strategies.registry import ids as get_all_ids
        valid_ids = set(get_all_ids())
        valid_ids.discard("smart_router")
        valid_ids.discard("meta_router")
        
        # Filter to valid IDs only
        enabled_strategies = [sid for sid in enabled_list if sid in valid_ids]
        
        # Update state
        state.enabled_strategies = enabled_strategies
        
        # Persist to disk
        _save_state_snapshot()
        
        all_enabled = len(enabled_strategies) == 0
        
        return {
            "success": True,
            "enabled_strategies": enabled_strategies,
            "enabled_count": len(enabled_strategies) if enabled_strategies else len(valid_ids),
            "total_count": len(valid_ids),
            "all_enabled": all_enabled,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


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

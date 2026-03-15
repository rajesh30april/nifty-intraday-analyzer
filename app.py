"""FastAPI app for Nifty 50 Intraday Probability Analyzer.

Supports both Yahoo Finance (delayed) and Zerodha Kite Connect (live) data.
"""

import pandas as pd
import traceback
import json as json_lib
import time as _time

import numpy as np
from fastapi import FastAPI, Request
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
    activate_kill_switch, state as trader_state, evaluate_and_act,
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

        if not trader_state.is_running or trader_state.kill_switch:
            continue
        try:
            df = await asyncio.to_thread(fetch_intraday_data, interval="5m", period="5d")
            if df is not None and not df.empty:
                price = float(df["close"].iloc[-1])
                candle_ts = df.index[-1].strftime("%H:%M")
                await asyncio.to_thread(evaluate_and_act, df, price)
                print(f"🤖 [CANDLE {candle_ts}] ₹{price:.0f} | trades={trader_state.orders_placed}")
        except Exception as e:
            print(f"⚠️ Auto-trader loop error: {e}")


@asynccontextmanager
async def lifespan(_app):
    """Startup: launch background task. Shutdown: cancel it."""
    task = asyncio.create_task(_auto_trader_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Nifty 50 Intraday Probability Analyzer", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


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


@app.get("/api/day-chart")
async def day_chart(date: str = ""):
    """Return 5m candles + patterns (5m, 15m, 1h) for a single trading day."""
    try:
        from pattern_detector import detect_all_patterns

        # Fetch 5m data (source for candles + 5m patterns)
        df5 = await asyncio.to_thread(fetch_intraday_data, period="60d", interval="5m")
        if df5.empty:
            return safe_json_response({"success": False, "error": "No data"})

        target = pd.Timestamp(date).date() if date else df5.index[-1].date()
        day_df5 = df5[df5.index.date == target]

        if day_df5.empty:
            return safe_json_response({"success": False, "error": f"No data for {target}"})

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

        # ── Detect 5m patterns ───────────────────────────────────
        r5 = await asyncio.to_thread(detect_all_patterns, day_df5, timeframe="5m")
        patterns = _serialize_patterns(r5, "5m")

        # ── Detect 15m patterns ──────────────────────────────────
        try:
            df15  = await asyncio.to_thread(fetch_intraday_data, period="60d", interval="15m")
            day15 = df15[df15.index.date == target]
            if not day15.empty:
                r15 = await asyncio.to_thread(detect_all_patterns, day15, timeframe="15m")
                patterns += _serialize_patterns(r15, "15m")
        except Exception:
            pass   # 15m optional — don't fail the whole request

        # ── Detect 1h patterns ───────────────────────────────────
        try:
            df1h  = await asyncio.to_thread(fetch_intraday_data, period="60d", interval="1h")
            day1h = df1h[df1h.index.date == target]
            if not day1h.empty:
                r1h = await asyncio.to_thread(detect_all_patterns, day1h, timeframe="1h")
                patterns += _serialize_patterns(r1h, "1h")
        except Exception:
            pass   # 1h optional

        # Sort by end_time so cards appear chronologically
        patterns.sort(key=lambda p: p["end_time"] or "")

        return safe_json_response({
            "success":  True,
            "date":     str(target),
            "candles":  candles,
            "patterns": patterns,
        })
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
        # Start live WebSocket streaming after login
        kite_manager.start_ticker()
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
            meta = evaluate_all(df)
            sig_fires   = meta.signal.should_enter
            sig_dir     = meta.signal.direction.value if meta.signal.direction else ""
            confidence  = meta.signal.confidence
            strat_name  = meta.selected_strategy or "smart_router"
            strat_emoji = meta.selected_emoji or "🧠"
            regime_str  = meta.regime
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
    status = get_trader_status()
    # Add unrealized P&L if there's an active trade
    if status["active_trade"] and kite_manager.latest_tick:
        price = kite_manager.latest_tick["last_price"]
        trade = status["active_trade"]
        if trade["direction"] == "long":
            trade["pnl_unrealized"] = round(
                (price - trade["entry_price"]) * trade["quantity"], 2
            )
        else:
            trade["pnl_unrealized"] = round(
                (trade["entry_price"] - price) * trade["quantity"], 2
            )
    return {"success": True, **status}


@app.post("/api/auto-trader/start")
async def auto_trader_start(strategy: str = "smart_router"):
    """Start the auto-trader with a selected strategy."""
    result = start_auto_trader(strategy_id=strategy)
    return {"success": True, **result}


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
        # Force kill even if error
        trader_state.is_running = False
        trader_state.kill_switch = True
        trader_state.active_trade = None
        return {"success": True, "status": "force_killed", "error": str(e)}


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


@app.get("/api/auto-trader/history")
async def auto_trader_history():
    """Return trade history from trade log."""
    from pathlib import Path
    import json as _json
    log_file = Path(__file__).parent / "trade_log.json"
    if not log_file.exists():
        return {"success": True, "trades": [], "total_pnl": 0}
    try:
        data = _json.loads(log_file.read_text())
        return {"success": True, **data}
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
):
    """Run a backtest with given parameters, strategy, and data source."""
    from backtester import run_backtest, BacktestResult
    import dataclasses

    try:
        result = run_backtest(
            period=period,
            interval="5m",
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

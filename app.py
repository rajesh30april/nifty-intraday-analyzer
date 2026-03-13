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

app = FastAPI(title="Nifty 50 Intraday Probability Analyzer")
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


# ── Simple response cache (avoid hammering Yahoo Finance) ─────────
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

        result = calculate_probability(df)

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


# ── Split API Endpoints (per-section refresh) ─────────────────────

# Shared data cache so sections can reuse fetched data
_section_cache: dict = {"df_5m": None, "df_15m": None, "df_1m": None, "timestamp": 0}
SECTION_CACHE_TTL = 15  # seconds — reuse data within 15s


def _get_cached_df(interval: str, period: str = "5d") -> pd.DataFrame | None:
    """Get DataFrame from cache or fetch fresh from Zerodha."""
    cache_key = f"df_{interval.replace('minute', 'm').replace('5m', '5m')}"
    now = _time.time()

    # Return cached if fresh
    if (
        _section_cache.get(cache_key) is not None
        and (now - _section_cache["timestamp"]) < SECTION_CACHE_TTL
    ):
        return _section_cache[cache_key]

    # Fetch fresh
    if kite_manager.is_authenticated:
        kite_map = {"1m": "minute", "5m": "5minute", "15m": "15minute"}
        kite_data = kite_manager.get_historical_data(
            interval=kite_map.get(interval, "5minute"), days=5
        )
        if kite_data:
            df = _kite_history_to_dataframe(kite_data)
            _section_cache[cache_key] = df
            _section_cache["timestamp"] = now
            return df

    # Fallback to Yahoo (for backtester)
    df = fetch_intraday_data(interval=interval, period=period)
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

        mtf = run_mtf_analysis()

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
            df = _get_cached_df(tf)
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
            pat_result = detect_all_patterns(df, timeframe=tf)
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
            df = _get_cached_df("5m")
        if df is None or df.empty:
            return {"success": False, "error": "No 5m data. Refresh Probability first."}

        # Get S/R levels from patterns
        sr_data = {}
        try:
            pat_result = detect_all_patterns(df, timeframe="5m")
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
            df = _get_cached_df("5m")
        if df is None or df.empty:
            return {"success": False, "error": "No 5m data. Refresh Probability first."}

        result = analyze_trend_health(df)

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
        df = fetch_intraday_data(interval=interval, period="5d")
        result = detect_all_patterns(df, timeframe=interval)

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
        mtf = run_mtf_analysis()

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
                """Run pattern detection on a single timeframe."""
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
                pats_5m, sr_5m = _extract_patterns(df_5m, "5m")
                patterns_data.extend(pats_5m)
                sr_data = sr_5m  # Use 5m S/R as primary

            # Run on 15m (more history = catches bigger patterns)
            if df_15m is not None and not df_15m.empty:
                pats_15m, sr_15m = _extract_patterns(df_15m, "15m")
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
    result = stop_auto_trader()
    return {"success": True, **result}


@app.post("/api/auto-trader/kill")
async def auto_trader_kill():
    """Emergency kill switch."""
    result = activate_kill_switch()
    return {"success": True, **result}


@app.post("/api/auto-trader/evaluate")
async def auto_trader_evaluate():
    """Manually trigger strategy evaluation with fresh data."""
    if not trader_state.is_running:
        return {"success": False, "error": "Auto-trader not running"}
    try:
        df = fetch_intraday_data(interval="5m", period="5d")
        if df is None or df.empty:
            return {"success": False, "error": "No data available"}
        current_price = float(df["close"].iloc[-1])
        evaluate_and_act(df, current_price)
        return {"success": True, **get_trader_status()}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Backtest API ────────────────────────────────────────────────

# ── Strategy Registry API ──────────────────────────────────────

@app.get("/api/strategies")
async def list_strategies():
    """Return all available strategies for the backtest dropdown."""
    import strategies.loader  # noqa: F401
    from strategies.registry import to_json
    return {"success": True, "strategies": to_json()}


@app.post("/api/backtest")
async def run_backtest_api(
    period: str = "60d",
    sl_points: float = 30.0,
    trailing_sl: float = 15.0,
    rr_ratio: float = 2.0,
    max_trades: int = 3,
    strategy: str = "smart_router",
):
    """Run a backtest with given parameters and strategy."""
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

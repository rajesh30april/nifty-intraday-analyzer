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
        "data_source": "zerodha_live" if kite_manager.is_authenticated else "yahoo_delayed",
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
    """Run probability analysis. Uses Kite if authenticated, else Yahoo."""
    try:
        source = "yahoo_delayed"

        if kite_manager.is_authenticated:
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
                source = "zerodha_live"
            else:
                df = fetch_intraday_data(interval=interval, period="5d")
        else:
            df = fetch_intraday_data(interval=interval, period="5d")

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

        return response
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}

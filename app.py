"""FastAPI app for Nifty 50 Intraday Probability Analyzer.

Supports both Yahoo Finance (delayed) and Zerodha Kite Connect (live) data.
"""

import pandas as pd
import traceback
import json as json_lib

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
async def mtf_analyze():
    """Run multi-timeframe analysis (1m + 5m + 15m combined)."""
    try:
        mtf = run_mtf_analysis()

        # Reuse the 5m DataFrame from MTF (no extra API call!)
        price_data = []
        patterns_data = []
        sr_data = {}
        df_5m = mtf.primary_df


        # Build price chart data from today's 5m candles
        if df_5m is not None and not df_5m.empty:
            today_df = get_todays_data(df_5m)
            if not today_df.empty:
                for idx, row in today_df.iterrows():
                    price_data.append({
                        "time": idx.strftime("%H:%M"),
                        "open": round(float(row["open"]), 2),
                        "high": round(float(row["high"]), 2),
                        "low": round(float(row["low"]), 2),
                        "close": round(float(row["close"]), 2),
                        "volume": int(row["volume"]),
                    })

            # Detect chart patterns on full 5m data
            try:
                pat_result = detect_all_patterns(df_5m, timeframe="5m")
                patterns_data = [
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
                ]
                sr_data = pat_result["support_resistance"]
            except Exception:
                pass

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
            "patterns": patterns_data,
            "support_resistance": sr_data,
        })
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}

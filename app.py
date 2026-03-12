"""FastAPI app for Nifty 50 Intraday Probability Analyzer.

Supports both Yahoo Finance (delayed) and Zerodha Kite Connect (live) data.
"""

import pandas as pd
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from data_fetcher import fetch_intraday_data, get_todays_data
from probability import calculate_probability
from kite_integration import kite_manager

app = FastAPI(title="Nifty 50 Intraday Probability Analyzer")
templates = Jinja2Templates(directory="templates")


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
    if not request_token:
        return RedirectResponse(url="/?error=no_token")

    try:
        kite_manager.generate_session(request_token)
        # Start live WebSocket streaming after login
        kite_manager.start_ticker()
        return RedirectResponse(url="/?live=true")
    except Exception as e:
        print(f"Auth error: {e}")
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

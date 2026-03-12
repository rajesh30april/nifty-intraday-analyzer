"""FastAPI app for Nifty 50 Intraday Probability Analyzer."""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import json
import traceback

from data_fetcher import fetch_intraday_data, get_todays_data
from probability import calculate_probability

app = FastAPI(title="Nifty 50 Intraday Probability Analyzer")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the main dashboard."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/analyze")
async def analyze(interval: str = "5m"):
    """Run probability analysis and return results as JSON."""
    try:
        df = fetch_intraday_data(interval=interval, period="5d")
        result = calculate_probability(df)

        today_df = get_todays_data(df)
        price_data = []
        if not today_df.empty:
            for idx, row in today_df.iterrows():
                price_data.append({
                    "time": idx.strftime("%H:%M"),
                    "close": round(row["close"], 2),
                    "volume": int(row["volume"]),
                })

        signals_data = []
        for s in result.signals:
            signals_data.append({
                "name": s.name,
                "value": str(s.value),
                "bias": s.bias,
                "strength": s.strength,
                "weight": s.weight,
                "description": s.description,
            })

        return {
            "success": True,
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


@app.get("/api/signals-table", response_class=HTMLResponse)
async def signals_table(interval: str = "5m"):
    """Return just the signals table HTML fragment (for HTMX polling)."""
    try:
        df = fetch_intraday_data(interval=interval, period="5d")
        result = calculate_probability(df)

        rows = ""
        for s in result.signals:
            bias_color = {
                "bullish": "text-green-600",
                "bearish": "text-red-600",
                "neutral": "text-gray-500",
            }.get(s.bias, "text-gray-500")

            bias_bg = {
                "bullish": "bg-green-50",
                "bearish": "bg-red-50",
                "neutral": "bg-gray-50",
            }.get(s.bias, "bg-gray-50")

            strength_bar_color = {
                "bullish": "bg-green-500",
                "bearish": "bg-red-500",
                "neutral": "bg-gray-400",
            }.get(s.bias, "bg-gray-400")

            strength_pct = int(s.strength * 100)

            rows += f"""
            <tr class="{bias_bg} border-b border-gray-200">
                <td class="px-4 py-3 font-semibold text-gray-800">{s.name}</td>
                <td class="px-4 py-3 {bias_color} font-bold">{s.value}</td>
                <td class="px-4 py-3">
                    <span class="inline-block px-2 py-1 rounded-full text-xs font-bold
                        {bias_color} {'bg-green-100' if s.bias == 'bullish' else 'bg-red-100' if s.bias == 'bearish' else 'bg-gray-200'}">
                        {s.bias.upper()}
                    </span>
                </td>
                <td class="px-4 py-3">
                    <div class="w-full bg-gray-200 rounded-full h-2">
                        <div class="{strength_bar_color} h-2 rounded-full" style="width: {strength_pct}%"></div>
                    </div>
                    <span class="text-xs text-gray-500">{strength_pct}%</span>
                </td>
                <td class="px-4 py-3 text-sm text-gray-600">{s.description}</td>
            </tr>"""

        return rows
    except Exception as e:
        return f'<tr><td colspan="5" class="text-red-500 p-4">Error: {e}</td></tr>'

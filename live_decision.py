"""Live Nifty Decision Panel — a one-glance trade decision aid.

Refreshes every candle and answers the only 3 questions that matter:
  1. What KIND of market is it now?      (trend / range / volatile)
  2. Continue, reverse, or no-edge?       (the scoring engine)
  3. So what do I DO right now?           (one clear call + NO-TRADE flag)

Data source (auto):
  - Zerodha Kite NIFTY futures  -> REAL-TIME (if logged in)
  - Yahoo Finance ^NSEI         -> ~15 min DELAYED fallback (clearly flagged)

Run:
  .venv/bin/python3 -m uvicorn live_decision:app --host 0.0.0.0 --port 8001
  open http://localhost:8001
"""

from __future__ import annotations

import time
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

import indicators as ind
from market_regime import detect_regime
# Reuse the engines we already built (DRY)
from generate_nifty_report import (
    analyze_timeframe,
    compute_levels,
    compute_rev_cont,
)

# Mountable router — included by the main app.py (served on port 8000 under /live)
live_router = APIRouter()
# Standalone app — only used when run directly (uvicorn live_decision:app)
app = FastAPI(title="Nifty Live Decision Panel")

# Refresh cadence — Yahoo rate-limits hard, so never fetch faster than this.
_REFRESH_SECONDS = 60
_CACHE: dict = {"df": None, "source": "", "ts": 0.0}


# ─────────────────────────── Data (cached) ───────────────────────────

def _get_data() -> tuple[pd.DataFrame, str]:
    """Fetch 5-min data, cached for _REFRESH_SECONDS to dodge rate limits."""
    now = time.time()
    if _CACHE["df"] is not None and (now - _CACHE["ts"]) < _REFRESH_SECONDS:
        return _CACHE["df"], _CACHE["source"]

    df, source = None, ""
    # Kite first (real-time), Yahoo fallback (delayed) — with retries
    try:
        from kite_fetcher import fetch_data
        df, source = fetch_data(interval="5m", period="5d", prefer_kite=True)
    except Exception:  # noqa: BLE001
        df = None

    if df is None or df.empty:
        # last-resort: cached CSV so the panel never goes blank
        from pathlib import Path
        csv = Path(__file__).parent / "data" / "nifty_5min_2yr.csv"
        if csv.exists():
            df = (pd.read_csv(csv, parse_dates=["date"]).set_index("date")
                  [["open", "high", "low", "close", "volume"]].sort_index())
            source = "Local cache (OFFLINE — stale!)"
        else:
            raise RuntimeError("No data source available")

    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    _CACHE.update(df=df, source=source, ts=now)
    return df, source


# ─────────────────────────── Decision logic ───────────────────────────

def _freshness(df: pd.DataFrame, source: str) -> dict:
    """How stale is the last candle? Flag delayed/old data loudly."""
    last = df.index[-1].to_pydatetime()
    age_min = (datetime.now() - last).total_seconds() / 60.0
    is_yahoo = "Yahoo" in source
    is_live = ("Kite" in source) or ("FUT" in source)
    if "OFFLINE" in source:
        level, note = "stale", "OFFLINE cached data — do NOT trade off this."
    elif is_yahoo:
        level, note = "delayed", "Yahoo feed is ~15 min DELAYED. Use Kite for live trading."
    elif is_live and age_min <= 7:
        level, note = "live", "Real-time Kite data."
    else:
        level, note = "old", f"Last candle is {age_min:.0f} min old — market may be closed."
    return {"last_candle": last.strftime("%Y-%m-%d %H:%M"),
            "age_min": round(age_min, 1), "level": level, "note": note,
            "is_live": is_live}


def _build_call(rc: dict, levels: dict) -> dict:
    """Turn the verdict + levels into ONE actionable instruction."""
    price = levels["price"]
    nr = levels["nearest_res"]
    ns = levels["nearest_sup"]
    cam = levels["camarilla"]
    pivot = levels["cpr"]["pivot"]

    # NO-TRADE wins over everything — discipline first.
    if not rc["trade_ok"]:
        return {
            "call": "NO TRADE — STAND ASIDE",
            "color": "#6b7280",
            "detail": rc["action"],
            "trigger": (f"Re-engage only if price breaks {nr['level']:,.0f} "
                        f"(up) or {ns['level']:,.0f} (down) with momentum."
                        if nr and ns else "Wait for a level break."),
        }

    rec = rc["recommendation"]
    above_pivot = price > pivot

    if rec == "CONTINUATION":
        if above_pivot:
            return {
                "call": "BIAS: LONG — ride the uptrend",
                "color": "#16a34a",
                "detail": ("Trend is continuing and price is above the pivot. "
                           "Buy dips toward support; do NOT short / fade."),
                "trigger": (f"Enter longs on a pullback near {ns['level']:,.0f} "
                            f"({ns['name']}). Stop just below it. "
                            f"Target {nr['level']:,.0f}." if ns and nr else ""),
            }
        return {
            "call": "BIAS: SHORT — ride the downtrend",
            "color": "#dc2626",
            "detail": ("Trend is continuing and price is below the pivot. "
                       "Sell rallies toward resistance; do NOT buy dips."),
            "trigger": (f"Enter shorts on a bounce near {nr['level']:,.0f} "
                        f"({nr['name']}). Stop just above it. "
                        f"Target {ns['level']:,.0f}." if ns and nr else ""),
        }

    # REVERSAL (possibly with the 'strong trend, be careful' caveat)
    near_top = nr and abs(price - nr["level"]) <= max(levels["atr"], 10)
    near_bot = ns and abs(price - ns["level"]) <= max(levels["atr"], 10)
    if rc["is_trending"]:
        return {
            "call": "REVERSAL FLAGGED — wait for confirmation",
            "color": "#ea580c",
            "detail": rc["action"],
            "trigger": (f"Only fade after a 5-min close back through "
                        f"{nr['level']:,.0f} / {ns['level']:,.0f}. Half size."
                        if nr and ns else ""),
        }
    if near_top:
        return {
            "call": f"FADE SHORT near {nr['level']:,.0f}",
            "color": "#dc2626",
            "detail": ("Range day + reversal evidence at resistance. "
                       "Short the rejection, target the pivot/support."),
            "trigger": (f"Stop just above {cam['H4']:,.0f} (Cam H4). "
                        f"Target {pivot:,.0f} then {ns['level']:,.0f}." if ns else ""),
        }
    if near_bot:
        return {
            "call": f"FADE LONG near {ns['level']:,.0f}",
            "color": "#16a34a",
            "detail": ("Range day + reversal evidence at support. "
                       "Buy the bounce, target the pivot/resistance."),
            "trigger": (f"Stop just below {cam['L4']:,.0f} (Cam L4). "
                        f"Target {pivot:,.0f} then {nr['level']:,.0f}." if nr else ""),
        }
    return {
        "call": "REVERSAL setup — wait for price to reach a level",
        "color": "#ca8a04",
        "detail": ("Reversal evidence present but price is mid-range. "
                   "Let it travel to support/resistance before acting."),
        "trigger": (f"Watch {nr['level']:,.0f} (short) and {ns['level']:,.0f} (long)."
                    if nr and ns else ""),
    }


def get_decision() -> dict:
    df, source = _get_data()
    intraday = analyze_timeframe("5-Min", df.tail(500))
    levels = compute_levels(df)
    rc = compute_rev_cont(df.tail(500), intraday["regime"], levels["adx"])
    fresh = _freshness(df, source)
    call = _build_call(rc, levels)
    return {
        "generated_at": datetime.now().strftime("%H:%M:%S"),
        "source": source,
        "freshness": fresh,
        "price": round(levels["price"], 1),
        "regime": intraday["regime"],
        "adx": round(levels["adx"]),
        "rsi": round(levels["rsi"]),
        "atr": round(levels["atr"]),
        "rev_cont": rc,
        "call": call,
        "nearest_res": levels["nearest_res"],
        "nearest_sup": levels["nearest_sup"],
        "pivot": round(levels["cpr"]["pivot"], 1),
        "camarilla": {k: round(v) for k, v in levels["camarilla"].items()},
        "refresh_seconds": _REFRESH_SECONDS,
    }


# ─────────────────────────── Routes ───────────────────────────

@live_router.get("/live/api/decision")
def api_decision() -> JSONResponse:
    try:
        return JSONResponse(get_decision())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=503)


@live_router.get("/live", response_class=HTMLResponse)
def live_index() -> str:
    return _PAGE


# Wire the router into the standalone app too, plus a root redirect for convenience.
app.include_router(live_router)


@app.get("/", response_class=HTMLResponse)
def root_redirect() -> str:
    return '<meta http-equiv="refresh" content="0; url=/live">'


_PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Nifty Live Decision</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body{background:#0b1020;color:#e5e7eb;font-family:ui-sans-serif,system-ui,sans-serif}
  .card{background:#141a2e;border:1px solid #1f2940;border-radius:14px}
  .pill{border-radius:999px;padding:2px 12px;font-size:12px;font-weight:700}
  .blink{animation:b 1.4s ease-in-out infinite}@keyframes b{50%{opacity:.45}}
</style></head>
<body class="px-4 py-5 md:px-8">
<div class="max-w-3xl mx-auto">
  <div class="flex items-center justify-between mb-4">
    <h1 class="text-2xl font-bold">Nifty Live Decision</h1>
    <div class="text-right text-xs text-gray-400">
      <div id="clock">--:--:--</div>
      <div>next refresh in <span id="countdown">--</span>s</div>
    </div>
  </div>

  <div id="freshness" class="mb-3 text-sm"></div>

  <!-- THE CALL -->
  <div id="callCard" class="card p-6 mb-4" style="border-width:2px">
    <div class="text-xs uppercase tracking-wider text-gray-400">Your move right now</div>
    <div id="call" class="text-3xl font-bold my-1">loading...</div>
    <div id="callDetail" class="text-sm text-gray-300"></div>
    <div id="callTrigger" class="text-sm mt-2 text-gray-400"></div>
  </div>

  <!-- Snapshot row -->
  <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
    <div class="card p-3"><div class="text-xs text-gray-400">Price</div><div id="price" class="text-xl font-bold">--</div></div>
    <div class="card p-3"><div class="text-xs text-gray-400">Regime</div><div id="regime" class="text-sm font-semibold mt-1">--</div></div>
    <div class="card p-3"><div class="text-xs text-gray-400">Continue/Reverse</div><div id="rec" class="text-sm font-semibold mt-1">--</div></div>
    <div class="card p-3"><div class="text-xs text-gray-400">RSI / ADX / ATR</div><div id="rai" class="text-sm font-semibold mt-1">--</div></div>
  </div>

  <!-- Scores + levels -->
  <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
    <div class="card p-4">
      <div class="text-xs text-gray-400 mb-2">Evidence scores</div>
      <div class="text-sm">Reversal: <b style="color:#f87171"><span id="revS">--</span>/100</b></div>
      <div class="text-sm">Continuation: <b style="color:#4ade80"><span id="contS">--</span>/100</b></div>
      <div class="text-sm">Confidence: <b><span id="conf">--</span>%</b></div>
    </div>
    <div class="card p-4">
      <div class="text-xs text-gray-400 mb-2">Levels to act at</div>
      <div class="text-sm">Resistance: <b style="color:#f87171"><span id="res">--</span></b></div>
      <div class="text-sm">Pivot: <b style="color:#fbbf24"><span id="piv">--</span></b></div>
      <div class="text-sm">Support: <b style="color:#4ade80"><span id="sup">--</span></b></div>
    </div>
  </div>

  <details class="card p-4 mb-6">
    <summary class="cursor-pointer text-sm text-gray-400">Why (signals found)</summary>
    <ul id="signals" class="mt-2 space-y-1 text-sm list-disc list-inside text-gray-300"></ul>
  </details>

  <p class="text-center text-xs text-gray-600">Rule-based decision aid — not financial advice. Always use a stop-loss.</p>
</div>

<script>
const REFRESH = 60;
let left = REFRESH;

function fmt(n){return n==null?'--':Number(n).toLocaleString('en-IN',{maximumFractionDigits:0});}

async function load(){
  try{
    const r = await fetch('/live/api/decision'); const d = await r.json();
    if(d.error){document.getElementById('call').textContent='Data error: '+d.error;return;}
    const c = d.call;
    document.getElementById('call').textContent = c.call;
    document.getElementById('call').style.color = c.color;
    document.getElementById('callCard').style.borderColor = c.color;
    document.getElementById('callDetail').textContent = c.detail;
    document.getElementById('callTrigger').textContent = c.trigger || '';

    document.getElementById('price').textContent = '\u20b9'+fmt(d.price);
    document.getElementById('regime').textContent = d.regime+' (ADX '+d.adx+')';
    document.getElementById('rec').textContent = d.rev_cont.recommendation;
    document.getElementById('rai').textContent = d.rsi+' / '+d.adx+' / '+d.atr;

    document.getElementById('revS').textContent = d.rev_cont.reversal_score;
    document.getElementById('contS').textContent = d.rev_cont.continuation_score;
    document.getElementById('conf').textContent = d.rev_cont.confidence;

    document.getElementById('res').textContent = d.nearest_res?fmt(d.nearest_res.level)+' ('+d.nearest_res.name+')':'--';
    document.getElementById('sup').textContent = d.nearest_sup?fmt(d.nearest_sup.level)+' ('+d.nearest_sup.name+')':'--';
    document.getElementById('piv').textContent = fmt(d.pivot);

    const f = d.freshness;
    const colors={live:'#16a34a',delayed:'#ea580c',old:'#ca8a04',stale:'#dc2626'};
    const fr = document.getElementById('freshness');
    fr.innerHTML = '<span class="pill '+(f.level==='live'?'':'blink')+'" style="background:'+colors[f.level]+'22;color:'+colors[f.level]+';border:1px solid '+colors[f.level]+'">'+f.level.toUpperCase()+'</span> '+
      '<span class="text-gray-400">'+d.source+' \u00b7 last candle '+f.last_candle+' \u00b7 '+f.note+'</span>';

    const ul = document.getElementById('signals'); ul.innerHTML='';
    (d.rev_cont.signals||[]).forEach(s=>{const li=document.createElement('li');li.textContent=s;ul.appendChild(li);});
    if(!(d.rev_cont.signals||[]).length){const li=document.createElement('li');li.textContent='No strong signals — that absence IS the message: no edge.';ul.appendChild(li);}
  }catch(e){document.getElementById('call').textContent='Connection lost — retrying...';}
  left = REFRESH;
}

setInterval(()=>{document.getElementById('clock').textContent=new Date().toLocaleTimeString();
  left--; if(left<=0){load();} document.getElementById('countdown').textContent=Math.max(left,0);},1000);
load();
</script>
</body></html>"""

"""Nifty Daily Outlook Report Generator.

Composition over duplication — reuses the platform's own brains:
  - data_fetcher.fetch_intraday_data / fetch_daily_data   (live data)
  - market_regime.detect_regime                           (trend/regime)
  - trend_health.analyze_trend_health                     (continue vs reverse)
  - indicators.{camarilla_pivots, central_pivot_range,
                rsi, adx, atr, ema}                        (S/R + indicators)

Produces a flat HTML report (Tailwind CDN + Chart.js) answering:
  "How is Nifty going to behave today — uptrend/downtrend, S/R levels?"

Usage:  .venv/bin/python3 generate_nifty_report.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

import indicators as ind
from market_regime import MarketRegime, detect_regime
from trend_health import analyze_trend_health
from reversal_continuation_detector import ReversalContinuationDetector


# ─────────────────────────── Data loading ───────────────────────────

def _resample(df_5m: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = df_5m.resample(rule).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["close"])
    return out


def load_data() -> dict:
    """Live first (Yahoo), with graceful messages. Returns dict of frames."""
    from data_fetcher import fetch_intraday_data, fetch_daily_data

    source = "Yahoo Finance ^NSEI (live)"
    # Intraday 5-min — try a few windows; Yahoo rate-limits aggressive pulls
    import time as _t
    df_5m = None
    last_err = None
    for period in ("1mo", "5d", "7d"):
        try:
            df_5m = fetch_intraday_data(interval="5m", period=period, enrich_volume=False)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            _t.sleep(3)
    if df_5m is None:
        # Fallback: cached 2yr CSV (offline, stale but lets the report build)
        csv_path = Path(__file__).parent / "data" / "nifty_5min_2yr.csv"
        if csv_path.exists():
            df_5m = (pd.read_csv(csv_path, parse_dates=["date"])
                       .set_index("date")[["open", "high", "low", "close", "volume"]]
                       .sort_index())
            source = f"Local cache {csv_path.name} (OFFLINE — Yahoo rate-limited)"
        else:
            raise RuntimeError(f"Yahoo unavailable and no cache. Last error: {last_err}")
    if df_5m.index.tz is not None:
        df_5m.index = df_5m.index.tz_localize(None)

    # Daily — 6mo for a solid macro regime read
    try:
        df_1d = fetch_daily_data(period="6mo")
        if df_1d.index.tz is not None:
            df_1d.index = df_1d.index.tz_localize(None)
    except Exception:
        df_1d = _resample(df_5m, "1D")

    df_1h = _resample(df_5m, "1h")

    return {"5m": df_5m, "1h": df_1h, "1d": df_1d, "source": source}


# ─────────────────────────── Analysis ───────────────────────────

_REGIME_LABEL = {
    MarketRegime.TRENDING_UP:   ("UPTREND", "", "up"),
    MarketRegime.TRENDING_DOWN: ("DOWNTREND", "", "down"),
    MarketRegime.SIDEWAYS:      ("SIDEWAYS", "↔", "flat"),
}


def _regime_label(regime: MarketRegime, trend_dir: str) -> tuple[str, str, str]:
    if regime == MarketRegime.VOLATILE:
        suffix = {"up": "UP", "down": "DOWN"}.get(trend_dir, "CHOPPY")
        return (f"VOLATILE-{suffix}", "", trend_dir)
    return _REGIME_LABEL.get(regime, ("UNKNOWN", "", "flat"))


def analyze_timeframe(label: str, df: pd.DataFrame) -> dict:
    reg = detect_regime(df)
    health = analyze_trend_health(df)
    name, emoji, bias = _regime_label(reg.regime, reg.trend_direction)
    return {
        "timeframe": label,
        "regime": name,
        "emoji": emoji,
        "bias": bias,
        "adx": reg.adx,
        "atr_pct": reg.atr_pct,
        "confidence": reg.confidence,
        "detail": reg.detail,
        "health_verdict": health.verdict,
        "health_emoji": health.verdict_emoji,
        "cont_score": health.continuation_score,
        "rev_score": health.reversal_score,
        "health_summary": health.summary,
        "signals": [
            {"name": s.name, "emoji": s.emoji, "status": s.status,
             "value": s.value, "detail": s.detail}
            for s in health.signals
        ],
    }


def consensus(verdicts: list[dict]) -> dict:
    weights = {"Daily": 3, "1-Hour": 2, "5-Min": 1}
    up = down = flat = 0
    for v in verdicts:
        w = weights.get(v["timeframe"], 1)
        if "UPTREND" in v["regime"] or "VOLATILE-UP" in v["regime"]:
            up += w
        elif "DOWNTREND" in v["regime"] or "VOLATILE-DOWN" in v["regime"]:
            down += w
        else:
            flat += w
    total = up + down + flat
    if up > down and up > flat:
        verdict, emoji, tone = "UPTREND", "", "bull"
        detail = f"{up}/{total} weighted votes bullish"
    elif down > up and down > flat:
        verdict, emoji, tone = "DOWNTREND", "", "bear"
        detail = f"{down}/{total} weighted votes bearish"
    else:
        verdict, emoji, tone = "SIDEWAYS / MIXED", "↔", "neutral"
        detail = f"bull={up} · bear={down} · flat={flat}"
    return {"verdict": verdict, "emoji": emoji, "tone": tone,
            "detail": detail, "up": up, "down": down, "flat": flat}


def compute_rev_cont(df_5m: pd.DataFrame, regime_label: str, adx: float) -> dict:
    """Run the reversal/continuation engine and synthesize an actionable verdict.

    Combines TWO independent reads so no single pattern is trusted alone:
      1. detect_regime  -> what KIND of day (trend/range/volatile)
      2. ReversalContinuationDetector -> 5 weighted signals scoring
         reversal vs continuation (RSI div, volume div, candles, S/R, momentum)
    """
    try:
        result = ReversalContinuationDetector(df_5m, lookback=30).analyze()
        rev = round(result.reversal_score)
        cont = round(result.continuation_score)
        rec = result.recommendation
        conf = round(result.confidence)
        signals = list(result.signals)
    except Exception as exc:  # noqa: BLE001
        rev = cont = conf = 0
        rec = "NEUTRAL"
        signals = [f"detector error: {exc}"]

    is_trending = adx >= 25

    # ── Synthesize the headline call + a plain-English action ─────────
    if rec == "CONTINUATION":
        headline = "TREND CONTINUES"
        tone = "cont"
        action = ("Ride the move, do NOT fade it. Reversal patterns (double top, "
                  "star, engulfing) are LOW-reliability here and likely to fail.")
        trade_ok = True
    elif rec == "REVERSAL":
        if is_trending:
            headline = "REVERSAL SIGNAL — but trend is strong (be careful)"
            tone = "warn"
            action = ("A reversal is flagged, yet ADX says the trend is strong. "
                      "Wait for PRICE confirmation (a close back through the level) "
                      "before fading. Half-size at most.")
            trade_ok = True
        else:
            headline = "REVERSAL LIKELY"
            tone = "rev"
            action = ("Range/weak-trend day + reversal evidence = your fade patterns "
                      "(double top/bottom, star, engulfing) are NOW backed by confluence. "
                      "Fade the extreme, tight stop beyond it.")
            trade_ok = True
    else:  # NEUTRAL
        headline = "NO CLEAR EDGE — RANGE / SIT OUT"
        tone = "neutral"
        action = ("The evidence does NOT agree. This is the honest 'I can't tell' state "
                  "— and the correct response is NO TRADE. Wait for a level to break or "
                  "for signals to stack up. Forcing a trade here is how accounts bleed.")
        trade_ok = False

    return {
        "recommendation": rec,
        "headline": headline,
        "tone": tone,
        "action": action,
        "trade_ok": trade_ok,
        "reversal_score": rev,
        "continuation_score": cont,
        "confidence": conf,
        "signals": signals,
        "adx": round(adx),
        "is_trending": is_trending,
    }


def compute_levels(df_5m: pd.DataFrame) -> dict:
    """Support/Resistance: Camarilla, CPR, prev-day, today, EMAs."""
    dates = sorted(set(df_5m.index.date))
    today = dates[-1]
    prev = dates[-2] if len(dates) >= 2 else today

    prev_df = df_5m[df_5m.index.date == prev]
    today_df = df_5m[df_5m.index.date == today]

    prev_h = float(prev_df["high"].max())
    prev_l = float(prev_df["low"].min())
    prev_c = float(prev_df["close"].iloc[-1])
    prev_o = float(prev_df["open"].iloc[0])

    cam = ind.camarilla_pivots(prev_h, prev_l, prev_c)
    cpr = ind.central_pivot_range(prev_h, prev_l, prev_c)

    price = float(df_5m["close"].iloc[-1])
    ema20 = float(ind.ema(df_5m["close"], 20).iloc[-1])
    ema50 = float(ind.ema(df_5m["close"], 50).iloc[-1])
    rsi = float(ind.rsi(df_5m["close"], 14).iloc[-1])
    adx_df = ind.adx(df_5m["high"], df_5m["low"], df_5m["close"], 14)
    adx = float(adx_df["adx"].iloc[-1])
    atr = float(ind.atr(df_5m["high"], df_5m["low"], df_5m["close"], 14).iloc[-1])

    cpr_width_pct = cpr["width"] / price * 100
    cpr_type = ("Narrow → range/reversal day likely" if cpr_width_pct < 0.2
                else "Wide → trending day likely" if cpr_width_pct > 0.5
                else "Medium → balanced day")

    # Build a sorted S/R ladder of the key levels relative to current price
    raw = [
        ("Camarilla H4 (breakout)", cam["H4"], "res"),
        ("Camarilla H3", cam["H3"], "res"),
        ("CPR R2", cpr["r2"], "res"),
        ("CPR R1", cpr["r1"], "res"),
        ("Prev Day High", prev_h, "res"),
        ("CPR Top (TC)", cpr["tc"], "piv"),
        ("CPR Pivot", cpr["pivot"], "piv"),
        ("CPR Bottom (BC)", cpr["bc"], "piv"),
        ("Prev Day Close", prev_c, "piv"),
        ("Prev Day Low", prev_l, "sup"),
        ("CPR S1", cpr["s1"], "sup"),
        ("CPR S2", cpr["s2"], "sup"),
        ("Camarilla L3", cam["L3"], "sup"),
        ("Camarilla L4 (breakdown)", cam["L4"], "sup"),
    ]
    ladder = sorted(raw, key=lambda x: x[1], reverse=True)

    # Nearest support (highest level below price) & resistance (lowest above)
    above = [x for x in ladder if x[1] > price]
    below = [x for x in ladder if x[1] <= price]
    nearest_res = above[-1] if above else None
    nearest_sup = below[0] if below else None

    return {
        "price": price,
        "prev": {"high": prev_h, "low": prev_l, "close": prev_c, "open": prev_o},
        "today": {
            "high": float(today_df["high"].max()),
            "low": float(today_df["low"].min()),
            "open": float(today_df["open"].iloc[0]),
        },
        "camarilla": cam,
        "cpr": cpr,
        "cpr_width_pct": round(cpr_width_pct, 3),
        "cpr_type": cpr_type,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi,
        "adx": adx,
        "atr": atr,
        "ladder": [{"name": n, "level": round(l, 1), "kind": k} for n, l, k in ladder],
        "nearest_res": {"name": nearest_res[0], "level": round(nearest_res[1], 1)} if nearest_res else None,
        "nearest_sup": {"name": nearest_sup[0], "level": round(nearest_sup[1], 1)} if nearest_sup else None,
        "today_date": today.strftime("%Y-%m-%d"),
        "prev_date": prev.strftime("%Y-%m-%d"),
    }


def build_insights(con: dict, lv: dict, verdicts: list[dict]) -> list[str]:
    out = []
    price = lv["price"]
    nr, ns = lv["nearest_res"], lv["nearest_sup"]

    if con["tone"] == "bull":
        out.append(
            f" Bias is BULLISH ({con['detail']}). Favour longs on dips toward support; "
            f"the higher timeframes are aligned upward.")
    elif con["tone"] == "bear":
        out.append(
            f" Bias is BEARISH ({con['detail']}). Favour shorts on rallies toward resistance; "
            f"higher timeframes are aligned downward.")
    else:
        out.append(
            f"↔ Bias is MIXED ({con['detail']}). Treat it as a range day — fade the edges, "
            f"don't chase the middle.")

    if ns and nr:
        out.append(
            f" Key intraday range to watch: support ~{ns['level']:,.0f} ({ns['name']}) "
            f"and resistance ~{nr['level']:,.0f} ({nr['name']}). "
            f"Price is currently ₹{price:,.0f}.")

    out.append(f" {lv['cpr_type']} — CPR width is {lv['cpr_width_pct']:.2f}% of price.")

    rsi = lv["rsi"]
    if rsi >= 70:
        out.append(f" RSI is {rsi:.0f} (overbought) — momentum is stretched, watch for pullbacks.")
    elif rsi <= 30:
        out.append(f" RSI is {rsi:.0f} (oversold) — bounce risk for shorts.")
    else:
        out.append(f" RSI is {rsi:.0f} (neutral zone) — no extreme momentum reading.")

    daily = next((v for v in verdicts if v["timeframe"] == "Daily"), None)
    if daily:
        out.append(
            f" Daily picture: {daily['emoji']} {daily['regime']} "
            f"(ADX {daily['adx']:.0f}) — {daily['health_emoji']} {daily['health_verdict']}.")

    out.append(
        " Risk note: this is rule-based technical analysis, NOT a prediction. "
        "Levels guide decisions; always use a stop-loss.")
    return out


# ─────────────────────────── HTML rendering ───────────────────────────

def render_html(ctx: dict) -> str:
    payload = json.dumps(ctx)
    tone_color = {"bull": "#16a34a", "bear": "#dc2626", "neutral": "#ca8a04"}[ctx["consensus"]["tone"]]
    rc = ctx["rev_cont"]
    rc_color = {"cont": "#16a34a", "rev": "#dc2626", "warn": "#ea580c",
                "neutral": "#6b7280"}[rc["tone"]]
    rc_badge = ("TRADEABLE SETUP" if rc["trade_ok"] else "NO TRADE — SIT OUT")
    rc_badge_color = "#16a34a" if rc["trade_ok"] else "#dc2626"
    rc_signals_html = (
        "".join(f'<li class="text-sm text-gray-300 leading-relaxed">{s}</li>'
                for s in rc["signals"])
        if rc["signals"]
        else '<li class="text-sm text-gray-400">No strong signals detected '
             '— that absence IS the message: no edge, stay flat.</li>'
    )
    rc_panel = f"""
  <!-- Continue / Reverse / Range verdict -->
  <div class="card p-6 mb-6" style="border-color:{rc_color}; border-width:2px">
    <div class="flex flex-wrap items-center justify-between gap-3 mb-2">
      <div>
        <div class="text-xs uppercase tracking-wider text-gray-400">Continue / Reverse / Range engine</div>
        <div class="text-2xl font-bold" style="color:{rc_color}">{rc['headline']}</div>
      </div>
      <div class="pill" style="background:{rc_badge_color}22; color:{rc_badge_color}; border:1px solid {rc_badge_color}">
        {rc_badge}
      </div>
    </div>
    <div class="flex flex-wrap gap-4 my-3 text-sm">
      <div>Reversal score: <b style="color:#f87171">{rc['reversal_score']}/100</b></div>
      <div>Continuation score: <b style="color:#4ade80">{rc['continuation_score']}/100</b></div>
      <div>Confidence: <b>{rc['confidence']}%</b></div>
      <div>ADX: <b>{rc['adx']}</b> ({'trending' if rc['is_trending'] else 'weak/range'})</div>
    </div>
    <div class="text-sm rounded p-3" style="background:{rc_color}18">
      <b>What to do:</b> {rc['action']}
    </div>
    <details class="mt-3">
      <summary class="cursor-pointer text-sm text-gray-400">Why (signals the engine found)</summary>
      <ul class="mt-2 space-y-1 list-disc list-inside">{rc_signals_html}</ul>
    </details>
  </div>
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Nifty 50 Outlook — {ctx['today_date']}</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  body {{ background:#0b1020; color:#e5e7eb; font-family:ui-sans-serif,system-ui,-apple-system,sans-serif; }}
  .card {{ background:#141a2e; border:1px solid #1f2940; border-radius:14px; }}
  .pill {{ border-radius:999px; padding:2px 10px; font-size:12px; font-weight:600; }}
  .res {{ color:#f87171; }} .sup {{ color:#4ade80; }} .piv {{ color:#fbbf24; }}
  th,td {{ padding:8px 10px; }}
</style>
</head>
<body class="px-4 py-6 md:px-10">
<div class="max-w-6xl mx-auto">

  <!-- Header -->
  <header class="flex flex-wrap items-end justify-between gap-3 mb-6">
    <div>
      <h1 class="text-3xl font-bold"> Nifty 50 — Daily Outlook</h1>
      <p class="text-sm text-gray-400 mt-1">Generated {ctx['generated_at']} · Source: {ctx['source']}</p>
    </div>
    <div class="text-right">
      <div class="text-xs text-gray-400">Spot (last candle {ctx['last_candle']})</div>
      <div class="text-3xl font-bold">₹{ctx['levels']['price']:,.0f}</div>
    </div>
  </header>

  <!-- Executive verdict -->
  <div class="card p-6 mb-6" style="border-color:{tone_color}">
    <div class="flex items-center gap-4">
      <div class="text-5xl">{ctx['consensus']['emoji']}</div>
      <div>
        <div class="text-xs uppercase tracking-wider text-gray-400">Overall multi-timeframe verdict</div>
        <div class="text-3xl font-bold" style="color:{tone_color}">{ctx['consensus']['verdict']}</div>
        <div class="text-sm text-gray-400">{ctx['consensus']['detail']}</div>
      </div>
    </div>
  </div>
{rc_panel}
  <!-- Top executive insights -->
  <section class="card p-6 mb-6">
    <h2 class="text-lg font-semibold mb-3"> Executive Insights</h2>
    <ul class="space-y-2 text-sm">
      {''.join(f'<li class="leading-relaxed">{i}</li>' for i in ctx['insights'])}
    </ul>
  </section>

  <!-- Nearest S/R quick cards -->
  <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
    <div class="card p-4">
      <div class="text-xs text-gray-400">Nearest Resistance</div>
      <div class="text-2xl font-bold res">{('₹{:,.0f}'.format(ctx['levels']['nearest_res']['level'])) if ctx['levels']['nearest_res'] else '—'}</div>
      <div class="text-xs text-gray-500">{ctx['levels']['nearest_res']['name'] if ctx['levels']['nearest_res'] else ''}</div>
    </div>
    <div class="card p-4">
      <div class="text-xs text-gray-400">Current Price</div>
      <div class="text-2xl font-bold">₹{ctx['levels']['price']:,.0f}</div>
      <div class="text-xs text-gray-500">RSI {ctx['levels']['rsi']:.0f} · ADX {ctx['levels']['adx']:.0f} · ATR {ctx['levels']['atr']:.0f}</div>
    </div>
    <div class="card p-4">
      <div class="text-xs text-gray-400">Nearest Support</div>
      <div class="text-2xl font-bold sup">{('₹{:,.0f}'.format(ctx['levels']['nearest_sup']['level'])) if ctx['levels']['nearest_sup'] else '—'}</div>
      <div class="text-xs text-gray-500">{ctx['levels']['nearest_sup']['name'] if ctx['levels']['nearest_sup'] else ''}</div>
    </div>
  </div>

  <!-- Multi-timeframe table -->
  <section class="card p-6 mb-6 overflow-x-auto">
    <h2 class="text-lg font-semibold mb-3">⏱ Multi-Timeframe Trend</h2>
    <table class="w-full text-sm">
      <thead class="text-gray-400 text-left border-b border-gray-700">
        <tr><th>Timeframe</th><th>Regime</th><th>ADX</th><th>ATR%</th><th>Conf</th><th>Trend Health</th><th>Cont/Rev</th></tr>
      </thead>
      <tbody>
        {''.join(
          f'<tr class="border-b border-gray-800">'
          f'<td class="font-semibold">{v["timeframe"]}</td>'
          f'<td>{v["emoji"]} {v["regime"]}</td>'
          f'<td>{v["adx"]:.0f}</td><td>{v["atr_pct"]:.2f}</td><td>{v["confidence"]:.0f}%</td>'
          f'<td>{v["health_emoji"]} {v["health_verdict"]}</td>'
          f'<td><span class="sup">{v["cont_score"]}↑</span> / <span class="res">{v["rev_score"]}↓</span></td>'
          f'</tr>'
          for v in ctx['verdicts'])}
      </tbody>
    </table>
    <div class="mt-3 text-xs text-gray-500 space-y-1">
      {''.join(f'<div>{v["timeframe"]}: {v["detail"]}</div>' for v in ctx['verdicts'])}
    </div>
  </section>

  <!-- Chart -->
  <section class="card p-6 mb-6">
    <h2 class="text-lg font-semibold mb-3"> Price vs Key Levels (last 2 sessions, 5-min)</h2>
    <div style="position:relative; height:380px;">
      <canvas id="priceChart"></canvas>
    </div>
  </section>

  <!-- S/R ladder + pivots -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
    <section class="card p-6">
      <h2 class="text-lg font-semibold mb-3"> Support / Resistance Ladder</h2>
      <table class="w-full text-sm">
        <tbody>
          {''.join(
            f'<tr class="border-b border-gray-800">'
            f'<td class="{x["kind"]}">{"" if x["kind"]=="res" else "" if x["kind"]=="sup" else ""} {x["name"]}</td>'
            f'<td class="text-right font-mono {x["kind"]}">{x["level"]:,.0f}</td></tr>'
            for x in ctx['levels']['ladder'])}
        </tbody>
      </table>
    </section>
    <section class="card p-6">
      <h2 class="text-lg font-semibold mb-3"> Pivots & Context</h2>
      <table class="w-full text-sm">
        <tbody>
          <tr class="border-b border-gray-800"><td>CPR Pivot</td><td class="text-right font-mono piv">{ctx['levels']['cpr']['pivot']:,.0f}</td></tr>
          <tr class="border-b border-gray-800"><td>CPR Top / Bottom</td><td class="text-right font-mono piv">{ctx['levels']['cpr']['tc']:,.0f} / {ctx['levels']['cpr']['bc']:,.0f}</td></tr>
          <tr class="border-b border-gray-800"><td>CPR Width</td><td class="text-right font-mono">{ctx['levels']['cpr']['width']:,.0f} ({ctx['levels']['cpr_width_pct']:.2f}%)</td></tr>
          <tr class="border-b border-gray-800"><td>Prev Day H / L / C</td><td class="text-right font-mono">{ctx['levels']['prev']['high']:,.0f} / {ctx['levels']['prev']['low']:,.0f} / {ctx['levels']['prev']['close']:,.0f}</td></tr>
          <tr class="border-b border-gray-800"><td>Today O / H / L</td><td class="text-right font-mono">{ctx['levels']['today']['open']:,.0f} / {ctx['levels']['today']['high']:,.0f} / {ctx['levels']['today']['low']:,.0f}</td></tr>
          <tr class="border-b border-gray-800"><td>EMA20 / EMA50 (5m)</td><td class="text-right font-mono">{ctx['levels']['ema20']:,.0f} / {ctx['levels']['ema50']:,.0f}</td></tr>
          <tr><td>RSI / ADX / ATR (5m)</td><td class="text-right font-mono">{ctx['levels']['rsi']:.0f} / {ctx['levels']['adx']:.0f} / {ctx['levels']['atr']:.0f}</td></tr>
        </tbody>
      </table>
    </section>
  </div>

  <!-- Trend health signals (5-min) -->
  <section class="card p-6 mb-6">
    <h2 class="text-lg font-semibold mb-3"> Intraday Trend Health Signals (5-min)</h2>
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
      {''.join(
        f'<div class="flex items-start gap-2 text-sm p-2 rounded" '
        f'style="background:{("#15331f" if s["status"]=="continuation" else "#3a1717" if s["status"]=="reversal" else "#23262e")}">'
        f'<span>{s["emoji"]}</span><div><b>{s["name"]}</b> — {s["value"]}<br>'
        f'<span class="text-gray-400 text-xs">{s["detail"]}</span></div></div>'
        for s in ctx['intraday_signals'])}
    </div>
  </section>

  <!-- Bottom insights -->
  <section class="card p-6 mb-6">
    <h2 class="text-lg font-semibold mb-3"> Bottom Line</h2>
    <ul class="space-y-2 text-sm">
      {''.join(f'<li>{i}</li>' for i in ctx['insights'])}
    </ul>
  </section>

  <footer class="text-center text-xs text-gray-600 py-6">
     Inevitable · Rule-based technical analysis — not financial advice · Generated {ctx['generated_at']}
  </footer>
</div>

<script>
const CTX = {payload};
(function() {{
  const c = CTX.chart;
  const ctxEl = document.getElementById('priceChart').getContext('2d');
  const levelLines = CTX.chart.level_lines.map(L => ({{
    label: L.name, data: c.labels.map(() => L.level),
    borderColor: L.color, borderWidth: 1, borderDash: [6,4],
    pointRadius: 0, fill: false, tension: 0
  }}));
  new Chart(ctxEl, {{
    type: 'line',
    data: {{
      labels: c.labels,
      datasets: [{{
        label: 'Nifty', data: c.close, borderColor: '#60a5fa',
        backgroundColor: 'rgba(96,165,250,0.1)', borderWidth: 2,
        pointRadius: 0, fill: true, tension: 0.2
      }}, ...levelLines]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{ legend: {{ labels: {{ color: '#9ca3af', boxWidth: 12, font: {{ size: 10 }} }} }} }},
      scales: {{
        x: {{ ticks: {{ color: '#6b7280', maxTicksLimit: 12 }}, grid: {{ color: '#1f2940' }} }},
        y: {{ ticks: {{ color: '#9ca3af' }}, grid: {{ color: '#1f2940' }} }}
      }}
    }}
  }});
}})();
</script>
</body>
</html>"""


# ─────────────────────────── Orchestration ───────────────────────────

def main() -> int:
    print(" Fetching live Nifty data...")
    data = load_data()
    df_5m = data["5m"]

    verdicts = [
        analyze_timeframe("Daily", data["1d"]),
        analyze_timeframe("1-Hour", data["1h"]),
        analyze_timeframe("5-Min", df_5m.tail(500)),
    ]
    con = consensus(verdicts)
    levels = compute_levels(df_5m)
    intraday = next(v for v in verdicts if v["timeframe"] == "5-Min")
    rev_cont = compute_rev_cont(df_5m.tail(500), intraday["regime"], levels["adx"])
    insights = build_insights(con, levels, verdicts)

    # Chart: last 2 sessions of 5-min closes + key level lines
    dates = sorted(set(df_5m.index.date))
    chart_dates = dates[-2:] if len(dates) >= 2 else dates
    chart_df = df_5m[df_5m.index.isin(df_5m[df_5m.index.map(lambda x: x.date() in chart_dates)].index)]
    chart_df = df_5m[[d in chart_dates for d in df_5m.index.date]]
    labels = [ts.strftime("%m-%d %H:%M") for ts in chart_df.index]
    closes = [round(float(c), 1) for c in chart_df["close"]]
    cam = levels["camarilla"]
    cpr = levels["cpr"]
    level_lines = [
        {"name": "Cam H4", "level": cam["H4"], "color": "#f87171"},
        {"name": "Cam H3", "level": cam["H3"], "color": "#fb923c"},
        {"name": "CPR Pivot", "level": cpr["pivot"], "color": "#fbbf24"},
        {"name": "Cam L3", "level": cam["L3"], "color": "#34d399"},
        {"name": "Cam L4", "level": cam["L4"], "color": "#4ade80"},
    ]

    ctx = {
        "today_date": levels["today_date"],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_candle": df_5m.index[-1].strftime("%Y-%m-%d %H:%M"),
        "source": data["source"],
        "consensus": con,
        "rev_cont": rev_cont,
        "verdicts": verdicts,
        "levels": levels,
        "insights": insights,
        "intraday_signals": intraday["signals"],
        "chart": {"labels": labels, "close": closes, "level_lines": level_lines},
    }

    html = render_html(ctx)
    out_dir = Path(__file__).parent / "reports"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"nifty_outlook_{levels['today_date']}.html"
    out_path.write_text(html)
    print(f" Report written: {out_path}")
    print(f"   Verdict: {con['emoji']} {con['verdict']} ({con['detail']})")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

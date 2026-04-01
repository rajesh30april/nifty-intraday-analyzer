/* crude-chart.js — MCX Crude Oil live chart
 * Works in two contexts:
 *   1. Standalone: loaded by crude_chart.html (iframe), auto-starts on DOMContentLoaded
 *   2. Inline:     loaded by index.html (deferred), started by initCrudeChart()
 * All DOM IDs are prefixed cc- to avoid collisions.
 */

'use strict';

// ── Module state ───────────────────────────────────────────────
let _cc_main      = null;
let _cc_vol       = null;
let _cc_priceInt  = null;
let _cc_cdInt     = null;
let _cc_countdown = 60;
let _cc_ready     = false;

// ── Colour palette ────────────────────────────────────────────
const _CC = {
  bg:    '#ffffff', grid:  '#f1f5f9', border: '#e2e8f0',
  text:  '#64748b', bull:  '#22c55e', bear:   '#ef4444',
  vwap:  '#a78bfa', ema9:  '#fb923c', ema21:  '#38bdf8',
  xhair: '#94a3b8', blue:  '#0053e2',
};

// ── Build chart options lazily so we don't blow up at parse time
//    if LightweightCharts hasn't loaded yet (e.g. in index.html context).
function _ccOpts() {
  const crosshairMode = (typeof LightweightCharts !== 'undefined' &&
    LightweightCharts.CrosshairMode?.Normal) ?? 1;
  return {
    layout:    { background: { color: _CC.bg }, textColor: _CC.text },
    grid:      { vertLines: { color: _CC.grid }, horzLines: { color: _CC.grid } },
    crosshair: {
      mode: crosshairMode,
      vertLine: { color: _CC.xhair, labelBackgroundColor: _CC.blue },
      horzLine: { color: _CC.xhair, labelBackgroundColor: _CC.blue },
    },
    rightPriceScale: { borderColor: _CC.border },
    timeScale: { borderColor: _CC.border, timeVisible: true, secondsVisible: false },
    handleScroll: { mouseWheel: true, pressedMouseMove: true },
    handleScale:  { mouseWheel: true, pinch: true },
  };
}

// ── Helpers ──────────────────────────────────────────────────
function _$cc(id) { return document.getElementById(id); }
function _setcc(id, v) { const e = _$cc(id); if (e) e.textContent = v; }
function _hideOverlay(el) { if (el) el.style.display = 'none'; }

// ── Create a LightweightCharts instance inside an element ─────
function _makeCC(elId, height, extra = {}) {
  const el = _$cc(elId);
  if (!el) {
    console.error(`[CrudeChart] element #${elId} not found`);
    return null;
  }
  // Use explicit clientWidth, fall back to a sensible default so chart is
  // never created at zero-width (which makes it completely invisible).
  const w = el.clientWidth || el.parentElement?.clientWidth || 800;
  el.style.height = height + 'px';
  try {
    return LightweightCharts.createChart(el, { ..._ccOpts(), width: w, height, ...extra });
  } catch (err) {
    console.error(`[CrudeChart] createChart(#${elId}) failed:`, err);
    return null;
  }
}

// ── Resize observer: keep charts filling their container ─────
function _bindCCResize() {
  const wrap = _$cc('cc-chart')?.parentElement;
  if (!wrap) return;
  new ResizeObserver(entries => {
    const w = entries[0]?.contentRect.width || wrap.clientWidth;
    if (w < 10) return;           // ignore spurious zero-width callbacks
    if (_cc_main) _cc_main.resize(w, 560);
    if (_cc_vol)  _cc_vol.resize(w, 140);
  }).observe(wrap);
}

// ── Show error inside the overlay ────────────────────────────
function _showCCError(loading, msg, isAuth) {
  if (!loading) return;
  loading.style.display = 'flex';
  loading.innerHTML = `
    <div class="flex flex-col items-center gap-4 p-8 text-center">
      <div class="text-5xl">${isAuth ? '⚠️' : '❌'}</div>
      <div class="font-black text-xl ${isAuth ? 'text-orange-600' : 'text-red-600'}">
        ${isAuth ? 'Kite Connection Required' : 'Chart Load Failed'}
      </div>
      <div class="text-gray-700 text-sm">${msg}</div>
      <div class="text-gray-500 text-xs">
        ${isAuth
          ? 'Go to <b>Crude Oil Auto-Trader</b> tab and click <b>Start Trading</b>'
          : 'Open browser console (F12 → Console) for details'}
      </div>
      <button onclick="loadChart()"
        class="mt-2 px-6 py-2.5 bg-[#0053e2] text-white rounded-lg font-bold shadow hover:bg-blue-700 transition">
        ⟳ Retry
      </button>
    </div>`;
}

// ── Core chart builder ────────────────────────────────────────
async function _buildCCChart() {
  // Guard: library must be present
  if (typeof LightweightCharts === 'undefined') {
    console.error('[CrudeChart] LightweightCharts not loaded!');
    _showCCError(_$cc('cc-loading'), 'LightweightCharts library failed to load — please refresh.', false);
    return;
  }

  const loading = _$cc('cc-loading');
  if (loading) loading.style.display = 'flex';

  // Destroy any previous chart instances
  if (_cc_main) { try { _cc_main.remove(); } catch (_) {} _cc_main = null; }
  if (_cc_vol)  { try { _cc_vol.remove();  } catch (_) {} _cc_vol  = null; }

  let data;
  let _success = false;
  try {
    // ── Fetch OHLCV + indicator data ──────────────────────────
    const r = await fetch('/api/crude/chart-data?days=3');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    data = await r.json();
    if (data.error) throw new Error(data.error);

    // ── Update header meta ────────────────────────────────────
    const m = data.meta || {};
    _setcc('cc-symbol', m.symbol || '--');
    if (m.price) _setcc('cc-price', '₹' + Number(m.price).toLocaleString('en-IN'));

    // ── Main price chart ──────────────────────────────────────
    _cc_main = _makeCC('cc-chart', 560);
    if (!_cc_main) throw new Error('Failed to create chart — check browser console');

    // Candlestick series (strip extra fields LightweightCharts doesn't want)
    const cs = _cc_main.addCandlestickSeries({
      upColor: _CC.bull, downColor: _CC.bear,
      borderUpColor: _CC.bull, borderDownColor: _CC.bear,
      wickUpColor:   _CC.bull, wickDownColor:   _CC.bear,
    });
    const candles = data.candles.map(({ time, open, high, low, close }) =>
      ({ time, open, high, low, close }));
    cs.setData(candles);

    // SuperTrend Long (green solid)
    if (data.st_long?.length) {
      const s = _cc_main.addLineSeries({
        color: _CC.bull, lineWidth: 2,
        lineStyle: LightweightCharts.LineStyle.Solid,
        priceLineVisible: false, lastValueVisible: false, title: 'ST↑',
      });
      s.setData(data.st_long);
    }

    // SuperTrend Short (red solid)
    if (data.st_short?.length) {
      const s = _cc_main.addLineSeries({
        color: _CC.bear, lineWidth: 2,
        lineStyle: LightweightCharts.LineStyle.Solid,
        priceLineVisible: false, lastValueVisible: false, title: 'ST↓',
      });
      s.setData(data.st_short);
    }

    // VWAP (purple dashed)
    if (data.vwap?.length) {
      const s = _cc_main.addLineSeries({
        color: _CC.vwap, lineWidth: 2,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        priceLineVisible: false, lastValueVisible: true, title: 'VWAP',
      });
      s.setData(data.vwap);
    }

    // EMA 9 (orange dotted)
    if (data.ema9?.length) {
      const s = _cc_main.addLineSeries({
        color: _CC.ema9, lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        priceLineVisible: false, lastValueVisible: false, title: 'EMA9',
      });
      s.setData(data.ema9);
    }

    // EMA 21 (sky blue dotted)
    if (data.ema21?.length) {
      const s = _cc_main.addLineSeries({
        color: _CC.ema21, lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        priceLineVisible: false, lastValueVisible: false, title: 'EMA21',
      });
      s.setData(data.ema21);
    }

    // Trade markers
    if (data.signals?.length) {
      cs.setMarkers([...data.signals].sort((a, b) => a.time - b.time));
    }

    _cc_main.timeScale().fitContent();

    // ── Volume histogram ──────────────────────────────────────
    _cc_vol = _makeCC('cc-vol', 140, {
      rightPriceScale: { visible: false },
      leftPriceScale:  { visible: false },
      timeScale:       { visible: false },
    });
    if (_cc_vol) {
      const vs = _cc_vol.addHistogramSeries({ priceScaleId: '' });
      vs.priceScale().applyOptions({ scaleMargins: { top: 0.1, bottom: 0 } });
      vs.setData(data.candles.map(c => ({
        time: c.time, value: c.volume,
        color: c.close >= c.open ? '#22c55e44' : '#ef444444',
      })));
      _cc_vol.timeScale().fitContent();

      // Sync crosshair: main → volume
      _cc_main.subscribeCrosshairMove(p => {
        if (p?.time) _cc_vol.setCrosshairPosition(0, p.time, vs);
        else         _cc_vol.clearCrosshairPosition();
      });
    }

    _setcc('cc-updated', new Date().toLocaleTimeString('en-IN'));
    _bindCCResize();
    // Force one resize to fix any zero-width initialisation race
    setTimeout(() => {
      const wrap = _$cc('cc-chart')?.parentElement;
      if (!wrap) return;
      const w = wrap.clientWidth;
      if (w > 10) {
        if (_cc_main) _cc_main.resize(w, 560);
        if (_cc_vol)  _cc_vol.resize(w, 140);
      }
    }, 100);

    _success = true;
    console.log('[CrudeChart] ✅ built —', data.candles?.length, 'candles');

  } catch (err) {
    console.error('[CrudeChart] build error:', err);
    _showCCError(loading, err.message || 'Unknown error',
      err.message?.includes('503') || err.message?.includes('auth'));

  } finally {
    // Hide overlay on success; on error _showCCError keeps it visible.
    if (_success) _hideOverlay(loading);
  }
}

// ── 60-second auto-refresh countdown ─────────────────────────
function _startCCCountdown() {
  clearInterval(_cc_cdInt);
  _cc_cdInt = setInterval(() => {
    const autoEl = _$cc('cc-auto');
    const nextEl = _$cc('cc-next');
    if (!autoEl?.checked) { if (nextEl) nextEl.textContent = 'off'; return; }
    if (--_cc_countdown <= 0) {
      _cc_countdown = 60;
      _buildCCChart();
    }
    if (nextEl) nextEl.textContent = _cc_countdown + 's';
  }, 1000);
}

// ── Live spot price poll every 5 s ───────────────────────────
function _startCCPricePoll() {
  clearInterval(_cc_priceInt);
  _cc_priceInt = setInterval(async () => {
    try {
      const d = await fetch('/api/crude/status').then(r => r.json());
      if (d.crude_price) _setcc('cc-price', '₹' + Number(d.crude_price).toLocaleString('en-IN'));
    } catch (_) {}
  }, 5000);
}

// ── Public API ────────────────────────────────────────────────

/** Called by DOMContentLoaded (standalone page) or switchCrudeTab (inline). */
function initCrudeChart() {
  if (!_cc_ready) {
    _cc_ready = true;
    _startCCPricePoll();
    _startCCCountdown();
  }
  _buildCCChart();
}

/** Refresh button handler. */
function reloadCrudeChart() {
  _cc_countdown = 60;
  _buildCCChart();
}

/** Alias used by the Refresh button onclick in crude_chart.html. */
function loadChart() { reloadCrudeChart(); }

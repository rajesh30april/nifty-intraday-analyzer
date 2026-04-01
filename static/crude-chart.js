/* crude-chart.js — Embedded live chart for MCX Crude Oil (in-page, not new tab)
 * Initialised lazily when the user clicks "📈 Crude Live Chart" in the sidebar.
 * All DOM IDs are prefixed cc- to avoid collisions with other pages.
 */

'use strict';

// ── Module state ───────────────────────────────────────────────
let _cc_main      = null;   // LightweightCharts main chart instance
let _cc_vol       = null;   // volume histogram chart
let _cc_priceInt  = null;   // setInterval for live spot price
let _cc_cdInt     = null;   // setInterval for countdown
let _cc_countdown = 60;
let _cc_ready     = false;  // has initCrudeChart() run at least once?

// ── Walmart light-mode palette ───────────────────────────────────
const _CC = {
  bg:      '#ffffff',
  grid:    '#f1f5f9',
  border:  '#e2e8f0',
  text:    '#64748b',
  bull:    '#22c55e',
  bear:    '#ef4444',
  vwap:    '#a78bfa',
  ema9:    '#fb923c',
  ema21:   '#38bdf8',
  xhair:   '#94a3b8',
  blue:    '#0053e2',
  spark:   '#ffc220',
};

const _CC_OPTS = {
  layout:    { background: { color: _CC.bg }, textColor: _CC.text },
  grid:      { vertLines: { color: _CC.grid }, horzLines: { color: _CC.grid } },
  crosshair: {
    mode: LightweightCharts.CrosshairMode.Normal,
    vertLine: { color: _CC.xhair, labelBackgroundColor: _CC.blue },
    horzLine: { color: _CC.xhair, labelBackgroundColor: _CC.blue },
  },
  rightPriceScale: { borderColor: _CC.border },
  timeScale: { borderColor: _CC.border, timeVisible: true, secondsVisible: false },
  handleScroll: { mouseWheel: true, pressedMouseMove: true },
  handleScale:  { mouseWheel: true, pinch: true },
};

// ── Helpers ──────────────────────────────────────────────────
function _$cc(id) { return document.getElementById(id); }
function _setcc(id, v) { const e = _$cc(id); if (e) e.textContent = v; }

function _makeCC(elId, height, extra = {}) {
  console.log(`[Crude Chart] Creating chart for element: ${elId}`);
  const el = _$cc(elId);
  if (!el) {
    console.error(`[Crude Chart] Element not found: ${elId}`);
    return null;
  }
  console.log(`[Crude Chart] Element found, creating chart with height: ${height}px`);
  el.style.height = height + 'px';
  try {
    const chart = LightweightCharts.createChart(el, { ..._CC_OPTS, height, ...extra });
    console.log(`[Crude Chart] Chart created successfully for ${elId}`);
    return chart;
  } catch (e) {
    console.error(`[Crude Chart] Error creating chart for ${elId}:`, e);
    return null;
  }
}

// ── Resize: keep charts filling their container ───────────────────
function _bindCCResize() {
  const wrap = _$cc('cc-chart')?.parentElement;
  if (!wrap) return;
  new ResizeObserver(() => {
    const w = wrap.clientWidth;
    if (_cc_main) _cc_main.resize(w, 560);
    if (_cc_vol)  _cc_vol.resize(w, 140);
  }).observe(wrap);
}

// ── Crosshair OHLCV tooltip (removed - simplified chart) ───────────
function _bindCCCrosshair(candleSeries) {
  // Crosshair tooltip removed for cleaner UI
  // Users can see OHLC data in the chart's default tooltip
}


// ── Core chart builder ─────────────────────────────────────────────
async function _buildCCChart() {
  console.log('[Crude Chart] Building chart...');
  
  // Check if LightweightCharts is available
  if (typeof LightweightCharts === 'undefined') {
    console.error('[Crude Chart] LightweightCharts library not loaded!');
    const loading = _$cc('cc-loading');
    if (loading) {
      loading.style.display = 'flex';
      loading.innerHTML = `<div class="text-center text-red-600">
        ❌ LightweightCharts library not loaded<br>
        <span class="text-sm text-gray-500">Please refresh the page</span>
      </div>`;
    }
    return;
  }
  
  const loading = _$cc('cc-loading');
  if (loading) {
    console.log('[Crude Chart] Showing loading overlay');
    loading.style.display = 'flex';
  } else {
    console.warn('[Crude Chart] Loading element not found!');
  }

  if (_cc_main) { _cc_main.remove(); _cc_main = null; }
  if (_cc_vol)  { _cc_vol.remove();  _cc_vol  = null; }

  let data;
  try {
    const r = await fetch('/api/crude/chart-data?days=3');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    data = await r.json();
    if (data.error) throw new Error(data.error);
  } catch (e) {
    console.error('Crude chart load error:', e);
    if (loading) {
      loading.style.display = 'flex';
      const isAuthError = e.message?.includes('503') || e.message?.includes('auth');
      const errorTitle = isAuthError ? 'Kite Connection Required' : 'Chart Load Failed';
      const errorDetail = isAuthError 
        ? 'Please start the Crude Trader first to authenticate with Kite' 
        : (e.message || 'Unknown error');
      
      loading.innerHTML = `<div class="flex flex-col items-center gap-4 p-8">
        <svg class="w-20 h-20 ${isAuthError ? 'text-orange-500' : 'text-red-600'}" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
        </svg>
        <div class="text-center">
          <div class="${isAuthError ? 'text-orange-600' : 'text-red-600'} font-black text-xl mb-2">${isAuthError ? '⚠️' : '❌'} ${errorTitle}</div>
          <div class="text-gray-700 text-sm font-medium mb-1">${errorDetail}</div>
          <div class="text-gray-500 text-xs mt-3">
            ${isAuthError 
              ? 'Go to <b>Crude Oil Auto-Trader</b> tab and click <b>Start Trading</b>' 
              : 'Check browser console (F12) for details'}
          </div>
          <button onclick="reloadCrudeChart()" class="mt-5 px-6 py-2.5 bg-[#0053e2] text-white rounded-lg hover:bg-blue-700 transition font-bold shadow-md">
            ⟳ Retry
          </button>
        </div>
      </div>`;
    }
    return;
  }

  console.log('[Crude Chart] Data fetched successfully:', {
    candles: data.candles?.length,
    st_long: data.st_long?.length,
    st_short: data.st_short?.length,
    vwap: data.vwap?.length,
    ema9: data.ema9?.length,
    ema21: data.ema21?.length,
    meta: data.meta
  });

  // Header meta
  const m = data.meta || {};
  _setcc('cc-symbol', m.symbol || '--');
  if (m.price) _setcc('cc-price', '₹' + Number(m.price).toLocaleString('en-IN'));

  console.log('[Crude Chart] Creating main chart...');
  // ─ Main chart
  _cc_main = _makeCC('cc-chart', 560);
  if (!_cc_main) {
    console.error('[Crude Chart] Failed to create main chart!');
    return;
  }
  console.log('[Crude Chart] Main chart created successfully');

  // Candlestick
  const cs = _cc_main.addCandlestickSeries({
    upColor: _CC.bull, downColor: _CC.bear,
    borderUpColor: _CC.bull, borderDownColor: _CC.bear,
    wickUpColor: _CC.bull, wickDownColor: _CC.bear,
  });
  cs.setData(data.candles.map(c => ({ ...c, customValues: { volume: c.volume } })));

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

  _bindCCCrosshair(cs);
  _cc_main.timeScale().scrollToRealTime();

  // ─ Volume chart
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
    _cc_vol.timeScale().scrollToRealTime();

    // Sync crosshair main → vol
    _cc_main.subscribeCrosshairMove(p => {
      if (p?.time) _cc_vol.setCrosshairPosition(0, p.time, vs);
      else         _cc_vol.clearCrosshairPosition();
    });
  }

  if (loading) loading.style.display = 'none';
  _setcc('cc-updated', new Date().toLocaleTimeString('en-IN'));

  _bindCCResize();
  // Remove strategy bar loading - we don't show it anymore
  
  console.log('[Crude Chart] Chart built successfully!');
}


function _startCCCountdown() {
  clearInterval(_cc_cdInt);
  _cc_cdInt = setInterval(() => {
    const autoEl = _$cc('cc-auto');
    const nextEl = _$cc('cc-next');
    if (!autoEl?.checked) { if (nextEl) nextEl.textContent = 'off'; return; }
    _cc_countdown--;
    if (nextEl) nextEl.textContent = _cc_countdown + 's';
    if (_cc_countdown <= 0) {
      _cc_countdown = 60;
      _buildCCChart();
    }
  }, 1000);
}

// ── Live spot price every 5 s ─────────────────────────────────────
function _startCCPricePoll() {
  clearInterval(_cc_priceInt);
  _cc_priceInt = setInterval(async () => {
    try {
      const r = await fetch('/api/crude/status');
      const d = await r.json();
      if (d.crude_price) {
        _setcc('cc-price', '₹' + Number(d.crude_price).toLocaleString('en-IN'));
      }
    } catch (_) {}
  }, 5000);
}

// ── Public API ────────────────────────────────────────────────────
// Called by switchPage('crude-chart') in the sidebar
function initCrudeChart() {
  console.log('[Crude Chart] Initializing...');
  if (!_cc_ready) {
    console.log('[Crude Chart] First time init - starting polls');
    _cc_ready = true;
    _startCCPricePoll();
    _startCCCountdown();
  }
  console.log('[Crude Chart] Building chart...');
  _buildCCChart();
}

// Called by the Refresh button
function reloadCrudeChart() {
  _cc_countdown = 60;
  _buildCCChart();
}

// Alias for the Refresh button onclick in crude_chart.html
function loadChart() {
  reloadCrudeChart();
}

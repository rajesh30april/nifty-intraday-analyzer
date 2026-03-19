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

// ── Walmart dark-mode palette ─────────────────────────────────
const _CC = {
  bg:      '#111827',
  grid:    '#1f2937',
  border:  '#374151',
  text:    '#9ca3af',
  bull:    '#22c55e',
  bear:    '#ef4444',
  vwap:    '#a78bfa',
  ema9:    '#fb923c',
  ema21:   '#38bdf8',
  xhair:   '#4b5563',
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
  const el = _$cc(elId);
  if (!el) return null;
  el.style.height = height + 'px';
  return LightweightCharts.createChart(el, { ..._CC_OPTS, height, ...extra });
}

// ── Resize: keep charts filling their container ───────────────────
function _bindCCResize() {
  const wrap = _$cc('cc-chart')?.parentElement;
  if (!wrap) return;
  new ResizeObserver(() => {
    const w = wrap.clientWidth;
    if (_cc_main) _cc_main.resize(w, 520);
    if (_cc_vol)  _cc_vol.resize(w, 120);
  }).observe(wrap);
}

// ── Crosshair OHLCV tooltip ──────────────────────────────────
function _bindCCCrosshair(candleSeries) {
  _cc_main.subscribeCrosshairMove(p => {
    const bar = _$cc('cc-ohlc');
    if (!bar) return;
    if (!p?.seriesData?.has(candleSeries)) { bar.classList.add('hidden'); return; }
    const d = p.seriesData.get(candleSeries);
    if (!d) { bar.classList.add('hidden'); return; }
    bar.classList.remove('hidden');
    _setcc('cc-o', d.open);
    _setcc('cc-h', d.high);
    _setcc('cc-l', d.low);
    _setcc('cc-c', d.close);
    _setcc('cc-v', d.customValues?.volume?.toLocaleString('en-IN') ?? '--');
  });
}

// ── Strategy pill bar ─────────────────────────────────────────
async function _ccStrategyBar() {
  try {
    const r = await fetch('/api/crude/evaluate', { method: 'POST' });
    const d = await r.json();
    const bar = _$cc('cc-strategy-bar');
    if (!bar) return;
    bar.innerHTML = (d.strategies || []).map(s => {
      const ok  = s.should_enter;
      const cls = ok ? 'bg-green-900 text-green-300' : 'bg-gray-800 text-gray-500';
      const dir = s.direction ? ' ' + s.direction.toUpperCase() : '';
      return `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${cls}"
                    title="${s.reason}">
                ${ok ? '✅' : '⛔'} ${s.name}(${s.weight})${dir}
              </span>`;
    }).join('');
  } catch (_) {}
}

// ── Core chart builder ─────────────────────────────────────────
async function _buildCCChart() {
  const loading = _$cc('cc-loading');
  if (loading) loading.style.display = 'flex';

  if (_cc_main) { _cc_main.remove(); _cc_main = null; }
  if (_cc_vol)  { _cc_vol.remove();  _cc_vol  = null; }

  let data;
  try {
    const r = await fetch('/api/crude/chart-data?days=3');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    data = await r.json();
    if (data.error) throw new Error(data.error);
  } catch (e) {
    if (loading) loading.textContent = '❌ ' + (e.message || 'Load failed');
    return;
  }

  // Header meta
  const m = data.meta || {};
  _setcc('cc-symbol', m.symbol || '--');
  _setcc('cc-expiry', m.days_to_expiry != null ? `Expiry in ${m.days_to_expiry}d` : '--');
  if (m.price) _setcc('cc-price', '₹' + Number(m.price).toLocaleString('en-IN'));

  // ─ Main chart
  _cc_main = _makeCC('cc-chart', 520);
  if (!_cc_main) return;

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
  _cc_vol = _makeCC('cc-vol', 120, {
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
  _ccStrategyBar();   // non-blocking
}

// ── Public: called by switchPage('crude-chart') ──────────────────
function initCrudeChart() {
  if (!_cc_ready) {
    _cc_ready = true;
    _startCCPricePoll();
    _startCCCountdown();
  }
  _buildCCChart();
}

// Public: Refresh button
function reloadCrudeChart() {
  _cc_countdown = 60;
  _buildCCChart();
}

// ── 60-second auto-refresh countdown ────────────────────────────
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
      // Only refresh if this page is visible
      const page = _$cc('page-crude-chart');
      if (page?.classList.contains('active')) _buildCCChart();
    }
  }, 1000);
}

// ── Live spot price every 5 s ─────────────────────────────────
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

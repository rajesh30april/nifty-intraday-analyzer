/* crude-chart.js — Live Lightweight Charts page for MCX Crude Oil
 * Chart series: Candlestick | ST Long | ST Short | VWAP | EMA9 | EMA21
 * Bottom panel: Volume histogram
 * Markers: trade entries (↑↓) and exits (●)
 */

'use strict';

// ── Chart instances (module-level so loadChart() can recreate) ────
let _mainChart   = null;
let _volChart    = null;
let _autoTimer   = null;
let _countdown   = 60;
let _cdInterval  = null;

// ── Walmart colours ───────────────────────────────────────────────
const C = {
  bg:         '#0f172a',
  grid:        '#1e293b',
  border:      '#334155',
  text:        '#94a3b8',
  bull:        '#22c55e',   // green candle / ST long
  bear:        '#ef4444',   // red candle  / ST short
  vwap:        '#a78bfa',   // purple
  ema9:        '#fb923c',   // orange
  ema21:       '#38bdf8',   // sky blue
  volUp:       '#22c55e44',
  volDown:     '#ef444444',
  crosshair:   '#475569',
  walmart:     '#0053e2',
  spark:       '#ffc220',
};

const CHART_OPTS = {
  layout:     { background: { color: C.bg }, textColor: C.text },
  grid:       { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
  crosshair:  { mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: C.crosshair, labelBackgroundColor: C.walmart },
                horzLine: { color: C.crosshair, labelBackgroundColor: C.walmart } },
  rightPriceScale: { borderColor: C.border },
  timeScale:  { borderColor: C.border, timeVisible: true, secondsVisible: false,
                fixLeftEdge: true },
  handleScroll:   { mouseWheel: true, pressedMouseMove: true },
  handleScale:    { mouseWheel: true, pinch: true },
};

function _makeChart(elId, height, opts = {}) {
  const el = document.getElementById(elId);
  el.style.height = height + 'px';
  return LightweightCharts.createChart(el, { ...CHART_OPTS, height, ...opts });
}

// ── Resize observer — keeps charts fluid ─────────────────────────
function _bindResize() {
  const mainEl = document.getElementById('chart');
  const volEl  = document.getElementById('vol-chart');
  new ResizeObserver(entries => {
    for (const e of entries) {
      const w = e.contentRect.width;
      if (_mainChart) _mainChart.resize(w, 520);
      if (_volChart)  _volChart.resize(w, 120);
    }
  }).observe(mainEl.parentElement);
}

// ── OHLCV crosshair tooltip ───────────────────────────────────────
function _bindCrosshair(candleSeries) {
  const bar = document.getElementById('ohlc-bar');
  _mainChart.subscribeCrosshairMove(p => {
    if (!p || !p.seriesData || !p.seriesData.has(candleSeries)) {
      bar.classList.add('hidden'); return;
    }
    const d = p.seriesData.get(candleSeries);
    if (!d) { bar.classList.add('hidden'); return; }
    bar.classList.remove('hidden');
    document.getElementById('cv-o').textContent = d.open;
    document.getElementById('cv-h').textContent = d.high;
    document.getElementById('cv-l').textContent = d.low;
    document.getElementById('cv-c').textContent = d.close;
    document.getElementById('cv-v').textContent =
      d.customValues?.volume?.toLocaleString('en-IN') ?? '--';
  });
}

// ── Strategy status bar ───────────────────────────────────────────
async function _loadStrategyBar() {
  try {
    const r = await fetch('/api/crude/evaluate', { method: 'POST' });
    const d = await r.json();
    const bar = document.getElementById('strategy-bar');
    bar.innerHTML = (d.strategies || []).map(s => {
      const ok  = s.should_enter;
      const cls = ok ? 'bg-green-900 text-green-300' : 'bg-gray-800 text-gray-500';
      const ico = ok ? '✅' : '⛔';
      const dir = s.direction ? ` ${s.direction.toUpperCase()}` : '';
      return `<span class="pill ${cls}" title="${s.reason}">${ico} ${s.name}(${s.weight})${dir}</span>`;
    }).join('');
  } catch (_) {}
}

// ── Main chart loader ─────────────────────────────────────────────
async function loadChart() {
  const loading = document.getElementById('loading-overlay');
  loading.style.display = 'flex';

  // Destroy previous instances
  if (_mainChart) { _mainChart.remove(); _mainChart = null; }
  if (_volChart)  { _volChart.remove();  _volChart  = null; }

  let data;
  try {
    const r = await fetch('/api/crude/chart-data?days=3');
    if (!r.ok) throw new Error(await r.text());
    data = await r.json();
  } catch (e) {
    loading.textContent = '❌ ' + (e.message || 'Failed to load');
    return;
  }

  // ── Header meta ────────────────────────────────────────────────
  const m = data.meta || {};
  document.getElementById('hdr-symbol').textContent  = m.symbol || '--';
  document.getElementById('hdr-expiry').textContent  =
    m.days_to_expiry != null ? `Expiry in ${m.days_to_expiry}d` : '--';
  if (m.price) {
    document.getElementById('hdr-price').textContent = '₹' + m.price.toLocaleString('en-IN');
  }

  // ── Build main chart ───────────────────────────────────────────
  _mainChart = _makeChart('chart', 520);

  // Candlestick series
  const candleSeries = _mainChart.addCandlestickSeries({
    upColor: C.bull, downColor: C.bear,
    borderUpColor: C.bull, borderDownColor: C.bear,
    wickUpColor: C.bull, wickDownColor: C.bear,
  });
  // Attach volume as custom value for crosshair tooltip
  const candleData = data.candles.map(c => ({
    ...c, customValues: { volume: c.volume }
  }));
  candleSeries.setData(candleData);

  // SuperTrend — LONG side (green)
  if (data.st_long?.length) {
    const stL = _mainChart.addLineSeries({
      color: C.bull, lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      priceLineVisible: false, lastValueVisible: false,
      title: 'ST↑',
    });
    stL.setData(data.st_long);
  }

  // SuperTrend — SHORT side (red)
  if (data.st_short?.length) {
    const stS = _mainChart.addLineSeries({
      color: C.bear, lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Solid,
      priceLineVisible: false, lastValueVisible: false,
      title: 'ST↓',
    });
    stS.setData(data.st_short);
  }

  // VWAP (session only — purple dashed)
  if (data.vwap?.length) {
    const vwapSeries = _mainChart.addLineSeries({
      color: C.vwap, lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      priceLineVisible: false, lastValueVisible: true,
      title: 'VWAP',
    });
    vwapSeries.setData(data.vwap);
  }

  // EMA 9 (orange)
  if (data.ema9?.length) {
    const e9 = _mainChart.addLineSeries({
      color: C.ema9, lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dotted,
      priceLineVisible: false, lastValueVisible: false,
      title: 'EMA9',
    });
    e9.setData(data.ema9);
  }

  // EMA 21 (sky blue)
  if (data.ema21?.length) {
    const e21 = _mainChart.addLineSeries({
      color: C.ema21, lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dotted,
      priceLineVisible: false, lastValueVisible: false,
      title: 'EMA21',
    });
    e21.setData(data.ema21);
  }

  // Trade markers
  if (data.signals?.length) {
    candleSeries.setMarkers(
      [...data.signals].sort((a, b) => a.time - b.time)
    );
  }

  // Crosshair OHLCV tooltip
  _bindCrosshair(candleSeries);

  // Scroll to latest candle
  _mainChart.timeScale().scrollToRealTime();

  // ── Volume chart (bottom panel) ────────────────────────────────
  _volChart = _makeChart('vol-chart', 120, {
    rightPriceScale: { visible: false },
    leftPriceScale:  { visible: false },
    timeScale: { visible: false },
  });
  const volSeries = _volChart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: '',
  });
  volSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.1, bottom: 0 },
  });
  volSeries.setData(data.candles.map(c => ({
    time: c.time, value: c.volume,
    color: c.close >= c.open ? C.volUp : C.volDown,
  })));
  _volChart.timeScale().scrollToRealTime();

  // ── Sync crosshair between main and vol ───────────────────────
  _mainChart.subscribeCrosshairMove(p => {
    if (p?.time) _volChart.setCrosshairPosition(0, p.time, volSeries);
    else         _volChart.clearCrosshairPosition();
  });

  loading.style.display = 'none';
  document.getElementById('last-updated').textContent =
    new Date().toLocaleTimeString('en-IN');

  // Strategy bar (non-blocking)
  _loadStrategyBar();
  _bindResize();
}

// ── Auto-refresh (60s countdown) ─────────────────────────────────
function _startAutoRefresh() {
  clearInterval(_cdInterval);
  clearInterval(_autoTimer);
  _countdown = 60;
  const cdEl = document.getElementById('next-refresh');
  cdEl.textContent = _countdown + 's';

  _cdInterval = setInterval(() => {
    if (!document.getElementById('auto-refresh').checked) {
      cdEl.textContent = 'off';
      return;
    }
    _countdown--;
    cdEl.textContent = _countdown + 's';
    if (_countdown <= 0) {
      _countdown = 60;
      loadChart();
    }
  }, 1000);
}

// ── Live spot price (every 5 s) ───────────────────────────────────
function _startPricePoll() {
  setInterval(async () => {
    try {
      const r = await fetch('/api/crude/status');
      const d = await r.json();
      if (d.crude_price) {
        document.getElementById('hdr-price').textContent =
          '₹' + Number(d.crude_price).toLocaleString('en-IN');
      }
    } catch (_) {}
  }, 5000);
}

// ── Boot ──────────────────────────────────────────────────────────
loadChart();
_startAutoRefresh();
_startPricePoll();

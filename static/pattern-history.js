// ── Pattern Finder (on-demand, single-day) ───────────────────────
// Flow:
//   1. User picks date + filters → clicks "Find Patterns"
//   2. /api/day-chart?date=YYYY-MM-DD fetched → 5m candles + patterns
//   3. Pattern cards rendered (clickable)
//   4. Click card → detail view with zoomed candlestick chart

let _phCandles    = [];   // 5m candles for the loaded day
let _phPatterns   = [];   // detected patterns for the loaded day
let _phLoadedDate = null; // YYYY-MM-DD that is currently loaded
let _phDetailChart = null;
let _phDetailSeries = null;

// ── Tab open ─────────────────────────────────────────────────────
function onPatternsTabOpen() {
    // Set date input to today if not already set
    const inp = document.getElementById('ph-date-input');
    if (inp && !inp.value) {
        const today = new Date().toISOString().slice(0, 10);
        inp.value = today;
    }
}

// ── Scan a single day ────────────────────────────────────────────
async function phScanDay() {
    const dateInp = document.getElementById('ph-date-input');
    const date    = dateInp?.value || new Date().toISOString().slice(0, 10);

    _phShowLoading(`Scanning ${date} for patterns…`);

    try {
        const r = await fetch(`/api/day-chart?date=${date}`);
        const d = await r.json();
        if (!d.success) throw new Error(d.error || 'Scan failed');

        _phCandles    = d.candles    || [];
        _phPatterns   = d.patterns   || [];
        _phLoadedDate = d.date       || date;

        _phRenderList();
    } catch (e) {
        _phShowError(e.message);
    }
}

// ── List view ─────────────────────────────────────────────────────
function _phRenderList() {
    _phShowView('list');

    const filtered = _phApplyFilters(_phPatterns);
    const bull     = filtered.filter(p => p.bias === 'bullish').length;
    const bear     = filtered.filter(p => p.bias === 'bearish').length;
    const fmtDate  = new Date(_phLoadedDate + 'T00:00:00')
        .toLocaleDateString('en-IN', { weekday:'short', day:'2-digit', month:'short', year:'numeric' });

    _setEl('ph-total',       filtered.length);
    _setEl('ph-bull',        bull);
    _setEl('ph-bear',        bear);
    _setEl('ph-result-date', `📅 ${fmtDate}`);
    _setEl('ph-vis-count',   `${filtered.length} of ${_phPatterns.length} patterns`);

    document.getElementById('ph-results').classList.remove('hidden');

    const grid  = document.getElementById('ph-cards');
    const empty = document.getElementById('ph-cards-empty');

    if (!filtered.length) {
        grid.innerHTML = '';
        grid.classList.add('hidden');
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');
    grid.classList.remove('hidden');
    grid.innerHTML = filtered.map((p, i) => _phCard(p, i)).join('');
}

function _phCard(p, i) {
    const isBull = p.bias === 'bullish';
    const isBear = p.bias === 'bearish';
    const border  = isBull ? '#bbf7d0' : isBear ? '#fecaca' : '#e5e7eb';
    const biasChip = isBull
        ? '<span class="chip bg-green-100 text-green-700">🟢 Bullish</span>'
        : isBear
        ? '<span class="chip bg-red-100 text-red-700">🔴 Bearish</span>'
        : '<span class="chip bg-gray-100 text-gray-500">⚪ Neutral</span>';
    const typeChip = p.pattern_type === 'reversal'
        ? '<span class="chip bg-orange-100 text-orange-700">↩ Reversal</span>'
        : p.pattern_type === 'continuation'
        ? '<span class="chip bg-blue-100 text-blue-700">→ Cont.</span>'
        : '<span class="chip bg-purple-100 text-purple-700">⟳ Structure</span>';
    const conf = Math.round(p.confidence * 100);
    const confColor = conf >= 75 ? '#2a8703' : conf >= 50 ? '#0053e2' : '#f59e0b';
    const timeStr = p.end_time
        ? new Date(p.end_time).toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit' }) : '';

    return `
    <div class="bg-white rounded-xl p-3 shadow-sm border-2 cursor-pointer hover:shadow-md hover:-translate-y-0.5 transition-all"
         style="border-color:${border};animation:fadeUp .2s ${i*30}ms both"
         onclick="phOpenDetail(${i})" role="button" tabindex="0"
         onkeydown="if(event.key==='Enter')phOpenDetail(${i})">
        <div class="flex items-start justify-between mb-2">
            <div class="flex items-center gap-2">
                <span class="text-xl">${p.emoji || '📊'}</span>
                <div>
                    <div class="font-black text-gray-800 text-sm leading-tight">${p.name}</div>
                    <div class="text-[10px] text-gray-400">${timeStr} · ${p.timeframe}</div>
                </div>
            </div>
            <svg class="w-4 h-4 text-gray-300 mt-1 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
        </div>
        <div class="flex gap-1 flex-wrap mb-2">${biasChip} ${typeChip}</div>
        <div>
            <div class="flex justify-between text-[10px] text-gray-400 mb-1">
                <span>Confidence</span>
                <span class="font-black" style="color:${confColor}">${conf}%</span>
            </div>
            <div class="conf-bar"><div class="conf-fill" style="width:${conf}%;background:${confColor}"></div></div>
        </div>
        <p class="text-[11px] text-gray-500 mt-2 line-clamp-2">${p.description || ''}</p>
    </div>`;
}

// ── Detail view ───────────────────────────────────────────────────
function phOpenDetail(idx) {
    const filtered = _phApplyFilters(_phPatterns);
    const p = filtered[idx];
    if (!p) return;

    _phShowView('detail');

    const isBull = p.bias === 'bullish';
    const isBear = p.bias === 'bearish';
    const conf   = Math.round(p.confidence * 100);
    const confColor = conf >= 75 ? '#2a8703' : conf >= 50 ? '#0053e2' : '#f59e0b';
    const timeStr = p.end_time
        ? new Date(p.end_time).toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit' }) : 'N/A';
    const fmtDate = new Date(_phLoadedDate + 'T00:00:00')
        .toLocaleDateString('en-IN', { weekday:'short', day:'2-digit', month:'short' });

    // Breadcrumb
    _setEl('ph-detail-breadcrumb', `${fmtDate} · ${p.name}`);

    // Header card
    const biasStyle = isBull ? 'bg-green-50 border-green-300 text-green-800'
                    : isBear ? 'bg-red-50 border-red-300 text-red-800'
                    : 'bg-gray-50 border-gray-300 text-gray-700';
    const biasLabel = isBull ? '🟢 Bullish' : isBear ? '🔴 Bearish' : '⚪ Neutral';
    document.getElementById('ph-detail-header').innerHTML = `
        <div class="flex flex-wrap items-center gap-4">
            <span class="text-4xl">${p.emoji || '📊'}</span>
            <div class="flex-1 min-w-0">
                <h2 class="text-xl font-black text-gray-900">${p.name}</h2>
                <div class="flex flex-wrap gap-2 mt-1 text-xs">
                    <span class="font-semibold text-gray-500">⏱ ${timeStr}</span>
                    <span class="font-semibold text-gray-500">· TF: ${p.timeframe}</span>
                    <span class="font-semibold text-gray-500">· Type: ${p.pattern_type || '—'}</span>
                </div>
            </div>
            <div class="flex flex-col items-end gap-2">
                <span class="px-3 py-1 rounded-full font-bold text-sm border ${biasStyle}">${biasLabel}</span>
                <div class="text-right">
                    <div class="text-[10px] text-gray-400">Confidence</div>
                    <div class="text-xl font-black" style="color:${confColor}">${conf}%</div>
                </div>
            </div>
        </div>`;

    // Chart title
    _setEl('ph-detail-chart-title', `📈 ${p.name} — ${timeStr} (${fmtDate})`);

    // Key levels
    const levelsEl = document.getElementById('ph-detail-levels');
    const levels   = Object.entries(p.key_levels || {});
    if (levels.length) {
        levelsEl.innerHTML = levels.map(([k, v]) => {
            const isSupport  = k.toLowerCase().includes('support');
            const isResist   = k.toLowerCase().includes('resist');
            const isBreakout = k.toLowerCase().includes('break');
            const color = isSupport  ? '#2a8703'
                        : isResist   ? '#ea1100'
                        : isBreakout ? '#0053e2' : '#6b7280';
            const icon  = isSupport ? '🟢' : isResist ? '🔴' : isBreakout ? '⚡' : '📍';
            return `<div class="flex items-center justify-between py-1.5 border-b border-gray-100 last:border-0">
                <span class="text-xs text-gray-500 capitalize">${icon} ${k.replace(/_/g,' ')}</span>
                <span class="font-black text-sm" style="color:${color}">₹${Number(v).toLocaleString('en-IN',{maximumFractionDigits:0})}</span>
            </div>`;
        }).join('');
    } else {
        levelsEl.innerHTML = '<p class="text-xs text-gray-400">No key levels detected</p>';
    }

    // Trade idea
    const tradeEl = document.getElementById('ph-detail-trade-body');
    if (isBull) {
        const sl  = levels.find(([k]) => k.toLowerCase().includes('support'));
        const tgt = levels.find(([k]) => k.toLowerCase().includes('resist') || k.toLowerCase().includes('target'));
        tradeEl.innerHTML = `
            <div class="space-y-2 text-sm">
                <div class="flex items-center gap-2 text-green-700 font-bold">📈 Look for LONG entry</div>
                <div class="text-xs text-gray-600">Wait for price to break out above the pattern high on volume.</div>
                ${sl  ? `<div class="text-xs"><span class="text-gray-400">🛡 Stop Loss:</span> <b>₹${Number(sl[1]).toLocaleString('en-IN',{maximumFractionDigits:0})}</b></div>` : ''}
                ${tgt ? `<div class="text-xs"><span class="text-gray-400">🎯 Target:</span> <b>₹${Number(tgt[1]).toLocaleString('en-IN',{maximumFractionDigits:0})}</b></div>` : ''}
                <div class="text-[10px] text-gray-400 pt-1">⚠️ Always confirm with volume &amp; market context</div>
            </div>`;
    } else if (isBear) {
        const sl  = levels.find(([k]) => k.toLowerCase().includes('resist'));
        const tgt = levels.find(([k]) => k.toLowerCase().includes('support') || k.toLowerCase().includes('target'));
        tradeEl.innerHTML = `
            <div class="space-y-2 text-sm">
                <div class="flex items-center gap-2 text-red-700 font-bold">📉 Look for SHORT entry</div>
                <div class="text-xs text-gray-600">Wait for price to break below the pattern low on volume.</div>
                ${sl  ? `<div class="text-xs"><span class="text-gray-400">🛡 Stop Loss:</span> <b>₹${Number(sl[1]).toLocaleString('en-IN',{maximumFractionDigits:0})}</b></div>` : ''}
                ${tgt ? `<div class="text-xs"><span class="text-gray-400">🎯 Target:</span> <b>₹${Number(tgt[1]).toLocaleString('en-IN',{maximumFractionDigits:0})}</b></div>` : ''}
                <div class="text-[10px] text-gray-400 pt-1">⚠️ Always confirm with volume &amp; market context</div>
            </div>`;
    } else {
        tradeEl.innerHTML = '<p class="text-xs text-gray-400">No directional bias — wait for confirmation.</p>';
    }

    // Full description
    _setEl('ph-detail-desc', p.description || 'No description available.');

    // Build zoomed chart
    _phBuildDetailChart(p);
}

function phBackToList() {
    _phShowView('list');
    if (_phDetailChart) {
        try { _phDetailChart.remove(); } catch(e) {}
        _phDetailChart  = null;
        _phDetailSeries = null;
    }
}

// ── Zoomed chart around pattern ──────────────────────────────────
function _phBuildDetailChart(p) {
    const container = document.getElementById('ph-detail-chart');
    if (!container) return;
    container.innerHTML = '';

    if (!_phCandles.length) {
        container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#9ca3af">No candle data</div>';
        return;
    }

    const PADDING  = 10;
    const patStart = p.start_time ? Math.floor(new Date(p.start_time).getTime() / 1000) : null;
    const patEnd   = p.end_time   ? Math.floor(new Date(p.end_time).getTime()   / 1000) : null;

    let startIdx = 0;
    let endIdx   = _phCandles.length - 1;

    if (patStart) {
        const si = _phCandles.findIndex(c => c.time >= patStart);
        startIdx = Math.max(0, (si === -1 ? 0 : si) - PADDING);
    }
    if (patEnd) {
        const ei = _phCandles.findIndex(c => c.time >= patEnd);
        endIdx   = Math.min(_phCandles.length - 1, (ei === -1 ? _phCandles.length - 1 : ei) + PADDING);
    }

    const win = _phCandles.slice(startIdx, endIdx + 1);
    if (!win.length) {
        container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#9ca3af">Could not locate pattern window</div>';
        return;
    }

    if (_phDetailChart) {
        try { _phDetailChart.remove(); } catch(e) {}
        _phDetailChart = null;
    }

    _phDetailChart = LightweightCharts.createChart(container, {
        layout:  { background: { color: '#ffffff' }, textColor: '#374151' },
        grid:    { vertLines: { color: '#f1f5f9' }, horzLines: { color: '#f1f5f9' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: '#e2e8f0' },
        timeScale: { borderColor: '#e2e8f0', timeVisible: true, secondsVisible: false },
        width:  container.clientWidth,
        height: 400,
    });

    // Base candles (greyed context before/after)
    _phDetailSeries = _phDetailChart.addCandlestickSeries({
        upColor:       '#d1fae5', downColor:       '#fee2e2',
        borderUpColor: '#6ee7b7', borderDownColor: '#fca5a5',
        wickUpColor:   '#6ee7b7', wickDownColor:   '#fca5a5',
    });
    _phDetailSeries.setData(win);

    // Pattern candles highlighted (yellow/orange = Spark!)
    if (patStart && patEnd) {
        const patCandles = win.filter(c => c.time >= patStart && c.time <= patEnd);
        if (patCandles.length) {
            _phDetailChart.addCandlestickSeries({
                upColor:        '#2a8703', downColor:        '#ea1100',
                borderUpColor:  '#2a8703', borderDownColor:  '#ea1100',
                wickUpColor:    '#2a8703', wickDownColor:    '#ea1100',
            }).setData(patCandles);
        }
    }

    // Arrow marker at pattern end
    if (patEnd) {
        const snapTime = _phSnapCandle(win, patEnd);
        if (snapTime) {
            _phDetailSeries.setMarkers([{
                time:     snapTime,
                position: p.bias === 'bullish' ? 'belowBar' : 'aboveBar',
                color:    p.bias === 'bullish' ? '#2a8703'  : '#ea1100',
                shape:    p.bias === 'bullish' ? 'arrowUp'  : 'arrowDown',
                text:     `${p.emoji || ''} ${p.name}`,
                size:     2,
            }]);
        }
    }

    // Horizontal key-level lines
    const levels = Object.entries(p.key_levels || {});
    levels.forEach(([key, val]) => {
        const price = parseFloat(val);
        if (isNaN(price)) return;
        const isSupport = key.toLowerCase().includes('support');
        const isResist  = key.toLowerCase().includes('resist');
        const color = isSupport ? '#2a8703' : isResist ? '#ea1100' : '#0053e2';
        _phDetailChart.addLineSeries({
            color,
            lineWidth:        1,
            lineStyle:        2,
            priceLineVisible: false,
            lastValueVisible: true,
            title: key.replace(/_/g, ' '),
        }).setData(win.map(c => ({ time: c.time, value: price })));
    });

    _phDetailChart.timeScale().fitContent();

    new ResizeObserver(() => {
        if (_phDetailChart) _phDetailChart.resize(container.clientWidth, 400);
    }).observe(container);
}

// ── Filters (re-render without re-fetching) ───────────────────────
function _phApplyFilters(list) {
    const tf   = document.getElementById('ph-tf-select')?.value   || 'all';
    const bias = document.getElementById('ph-bias-select')?.value || 'all';
    return list.filter(p => {
        if (tf   !== 'all' && p.timeframe !== tf)   return false;
        if (bias !== 'all' && p.bias      !== bias) return false;
        return true;
    });
}

// ── UI helpers ────────────────────────────────────────────────────
function _phShowView(view) {
    const listEl   = document.getElementById('ph-list-view');
    const detailEl = document.getElementById('ph-detail-view');
    const loadEl   = document.getElementById('ph-loading');
    const promptEl = document.getElementById('ph-prompt');

    if (view === 'detail') {
        if (listEl)   listEl.classList.add('hidden');
        if (detailEl) detailEl.classList.remove('hidden');
    } else {
        if (detailEl) detailEl.classList.add('hidden');
        if (listEl)   listEl.classList.remove('hidden');
        if (loadEl)   loadEl.classList.add('hidden');
        if (promptEl) promptEl.classList.add('hidden');
    }
}

function _phShowLoading(msg) {
    const loadEl    = document.getElementById('ph-loading');
    const promptEl  = document.getElementById('ph-prompt');
    const resultsEl = document.getElementById('ph-results');
    if (promptEl)  promptEl.classList.add('hidden');
    if (resultsEl) resultsEl.classList.add('hidden');
    if (loadEl) {
        loadEl.classList.remove('hidden');
        const msgEl = document.getElementById('ph-loading-msg');
        if (msgEl) msgEl.textContent = msg || 'Loading…';
    }
}

function _phShowError(msg) {
    const loadEl = document.getElementById('ph-loading');
    if (loadEl) loadEl.innerHTML = `
        <p class="text-red-500 font-bold">❌ ${msg}</p>
        <button onclick="_phShowLoading(); document.getElementById('ph-prompt').classList.remove('hidden'); document.getElementById('ph-loading').classList.add('hidden')" class="mt-2 text-xs text-blue-500 underline">Go back</button>`;
}

function _setEl(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

function _phSnapCandle(candles, unix) {
    if (!candles.length) return null;
    return candles.reduce((best, c) =>
        Math.abs(c.time - unix) < Math.abs(best.time - unix) ? c : best
    ).time;
}

// ── Backwards compat stubs ────────────────────────────────────────
function phLoadAll()   { phScanDay(); }
function phSelectDay() {}
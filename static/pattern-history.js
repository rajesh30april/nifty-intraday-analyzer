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

    _phShowLoading(`Scanning ${date} for patterns… (first scan ~10s, cached after)`);

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

    // Key levels — skip internal zone-helper keys (drawn on chart, not listed)
    const levelsEl   = document.getElementById('ph-detail-levels');
    const _hideKeys  = new Set(['resistance_high', 'support_low', 'resistance_zone', 'support_zone']);
    const levels     = Object.entries(p.key_levels || {}).filter(([k]) => !_hideKeys.has(k));

    // Friendly label map
    const _labelMap = {
        peak1: '⛰ Peak 1 (P1)',  peak2: '⛰ Peak 2 (P2)',  peak3: '⛰ Peak 3 (P3)',
        trough1: '🪣 Trough 1 (T1)', trough2: '🪣 Trough 2 (T2)', trough3: '🪣 Trough 3 (T3)',
        neckline: '📏 Neckline',  measured_target: '🎯 Target (Measured Move)',
        peak: '⛰ Peak',  trough: '🪣 Trough',
        head: '👑 Head',  left_shoulder: '◀ Left Shoulder', right_shoulder: '▶ Right Shoulder',
        flag_high: '📌 Flag High', flag_low: '📌 Flag Low',
        upper_trend: '📐 Upper Trend', lower_trend: '📐 Lower Trend',
        // 🐶 NEW: Trend structure labels
        latest_lh: '🔴 Latest LH (SELL ZONE)',
        latest_ll: '📍 Latest LL',
        latest_hh: '📍 Latest HH',
        latest_hl: '🟢 Latest HL (BUY ZONE)',
    };

    if (levels.length) {
        levelsEl.innerHTML = levels.map(([k, v]) => {
            const kl = k.toLowerCase();
            const isNeckline = kl.includes('neckline');
            const isTarget   = kl.includes('target');
            const isResist   = kl.includes('resist') || kl.includes('peak');
            const isSupport  = kl.includes('support') || kl.includes('trough');
            
            // 🐶 NEW: Special styling for trend structure LATEST levels
            const isLatestLH = kl.includes('latest_lh');
            const isLatestLL = kl.includes('latest_ll');
            const isLatestHH = kl.includes('latest_hh');
            const isLatestHL = kl.includes('latest_hl');
            const isLatest = isLatestLH || isLatestLL || isLatestHH || isLatestHL;
            
            let color, bg, border;
            
            if (isLatestLH) {
                // LATEST LH - bright red, this is the SELL ZONE
                color = '#dc2626';
                bg = 'bg-red-50';
                border = 'border-l-4 border-red-500';
            } else if (isLatestHL) {
                // LATEST HL - bright green, this is the BUY ZONE
                color = '#16a34a';
                bg = 'bg-green-50';
                border = 'border-l-4 border-green-500';
            } else if (isLatestLL || isLatestHH) {
                // Other latest levels
                color = isLatestLL ? '#ea1100' : '#2a8703';
                bg = isLatestLL ? 'bg-red-50' : 'bg-green-50';
                border = '';
            } else if (isNeckline) {
                color = '#0053e2';
                bg = 'bg-blue-50';
                border = '';
            } else if (isTarget) {
                color = '#995213';
                bg = 'bg-amber-50';
                border = '';
            } else if (isResist) {
                color = '#ea1100';
                bg = '';
                border = '';
            } else if (isSupport) {
                color = '#2a8703';
                bg = '';
                border = '';
            } else {
                color = '#6b7280';
                bg = '';
                border = '';
            }
            
            const label = _labelMap[k] || `📍 ${k.replace(/_/g, ' ')}`;
            const fontWeight = isLatest ? 'font-black' : 'font-bold';
            const textSize = isLatest ? 'text-base' : 'text-sm';
            
            return `<div class="flex items-center justify-between py-2 border-b border-gray-100 last:border-0 rounded px-2 ${bg} ${border}">
      <span class="text-xs text-gray-700 font-medium">${label}</span>
                <span class="${fontWeight} ${textSize}" style="color:${color}">₹${Number(v).toLocaleString('en-IN',{maximumFractionDigits:0})}</span>
            </div>`;
        }).join('');
    } else {
        levelsEl.innerHTML = '<p class="text-xs text-gray-400">No key levels detected</p>';
    }

    // Trade idea — use specific keys from key_levels where available
    const tradeEl = document.getElementById('ph-detail-trade-body');
    const kl      = p.key_levels || {};
    const fmt     = v => `₹${Number(v).toLocaleString('en-IN', {maximumFractionDigits: 0})}`;

    // 🐶 IMPROVED: Different trade ideas based on pattern type
    const isStructure = p.pattern_type === 'structure' || p.name?.toLowerCase().includes('structure');

    if (isBull) {
        if (isStructure) {
            // TREND STRUCTURE patterns (HH/HL) - NO neckline!
            const latest_hl = kl['latest_hl'] || kl['latest_low'];
            const sl = p.stop_loss || kl['stop_loss'];
            tradeEl.innerHTML = `
                <div class="space-y-2 text-sm">
                    <div class="flex items-center gap-2 text-green-700 font-bold">📈 Trend Following Strategy</div>
                    <div class="text-xs text-gray-600">The trend is UP. Wait for price to dip toward the latest Higher Low ${latest_hl ? fmt(latest_hl) : ''}, then LONG on bullish confirmation.</div>
                    ${sl  ? `<div class="flex justify-between text-xs"><span class="text-gray-400">🛡 Stop Loss (below HL):</span> <b class="text-red-600">${fmt(sl)}</b></div>` : ''}
                    <div class="text-[10px] text-gray-400 pt-1">🐶 This is NOT a breakout setup! It's a trend — buy dips, don't chase highs.</div>
                </div>`;
        } else {
            // REVERSAL/BREAKOUT patterns (Double Bottom, Ascending Triangle, etc.)
            const neckline = kl['neckline'];
            const sl       = kl['support_zone'] || kl['trough_avg'] || kl['trough1'];
            const tgt      = kl['measured_target'] || p.measured_target;
            tradeEl.innerHTML = `
                <div class="space-y-2 text-sm">
                    <div class="flex items-center gap-2 text-green-700 font-bold">📈 Look for LONG entry</div>
                    <div class="text-xs text-gray-600">Enter on confirmed breakout above ${neckline ? 'neckline ' + fmt(neckline) : 'resistance'} with strong volume.</div>
                    ${sl  ? `<div class="flex justify-between text-xs"><span class="text-gray-400">🛡 Stop Loss (below support):</span> <b class="text-red-600">${fmt(sl)}</b></div>` : ''}
                    ${tgt ? `<div class="flex justify-between text-xs"><span class="text-gray-400">🎯 Measured Target:</span> <b class="text-green-700">${fmt(tgt)}</b></div>` : ''}
                    <div class="text-[10px] text-gray-400 pt-1">⚠️ Always confirm with volume &amp; market context</div>
                </div>`;
        }
    } else if (isBear) {
        if (isStructure) {
            // TREND STRUCTURE patterns (LH/LL) - NO neckline!
            const latest_lh = kl['latest_lh'] || kl['latest_high'];
            const sl = p.stop_loss || kl['stop_loss'];
            tradeEl.innerHTML = `
                <div class="space-y-2 text-sm">
                    <div class="flex items-center gap-2 text-red-700 font-bold">📉 Trend Following Strategy</div>
                    <div class="text-xs text-gray-600">The trend is DOWN. Wait for price to rally toward the latest Lower High ${latest_lh ? fmt(latest_lh) : ''}, then SHORT on bearish confirmation.</div>
                    ${sl  ? `<div class="flex justify-between text-xs"><span class="text-gray-400">🛡 Stop Loss (above LH):</span> <b class="text-red-600">${fmt(sl)}</b></div>` : ''}
                    <div class="text-[10px] text-gray-400 pt-1">🐶 This is NOT a breakout setup! It's a trend — sell rallies, don't chase lows.</div>
                </div>`;
        } else {
            // REVERSAL/BREAKOUT patterns (Double Top, Descending Triangle, etc.)
            const neckline = kl['neckline'];
            const sl       = kl['resistance_zone'] || kl['peak_avg'] || kl['peak3'];
            const tgt      = kl['measured_target'] || p.measured_target;
            tradeEl.innerHTML = `
                <div class="space-y-2 text-sm">
                    <div class="flex items-center gap-2 text-red-700 font-bold">📉 Look for SHORT entry</div>
                    <div class="text-xs text-gray-600">Enter on confirmed breakdown below ${neckline ? 'neckline ' + fmt(neckline) : 'support'} with strong volume.</div>
                    ${sl  ? `<div class="flex justify-between text-xs"><span class="text-gray-400">🛡 Stop Loss (above resistance):</span> <b class="text-red-600">${fmt(sl)}</b></div>` : ''}
                    ${tgt ? `<div class="flex justify-between text-xs"><span class="text-gray-400">🎯 Measured Target:</span> <b class="text-green-700">${fmt(tgt)}</b></div>` : ''}
                    <div class="text-[10px] text-gray-400 pt-1">⚠️ Always confirm with volume &amp; market context</div>
                </div>`;
        }
    } else {
        tradeEl.innerHTML = '<p class="text-xs text-gray-400">No directional bias — wait for confirmation.</p>';
    }

    // Full description
    const descEl = document.getElementById('ph-detail-desc');
    let descHTML = p.description || 'No description available.';
    
    // 🐶 ADD VISUAL DIAGRAM for trend structure patterns
    if (isStructure) {
        const kl = p.key_levels || {};
        if (isBear && kl.latest_lh && kl.latest_ll) {
            // DOWNTREND diagram
            const lh = `₹${Number(kl.latest_lh).toLocaleString('en-IN', {maximumFractionDigits:0})}`;
            const ll = `₹${Number(kl.latest_ll).toLocaleString('en-IN', {maximumFractionDigits:0})}`;
            descHTML += `
                <div class="mt-4 p-3 bg-red-50 border-l-4 border-red-500 rounded">
                    <div class="text-xs font-bold text-red-700 mb-2">📉 DOWNTREND STRUCTURE:</div>
                    <div class="font-mono text-xs text-gray-700 leading-relaxed">
                        <div class="flex items-center gap-2">
                            <span class="text-red-600">●</span>
                            <span>LH3 → LH2 → <strong class="text-red-600 bg-red-100 px-1 rounded">LH1 ${lh} ⬅ SELL HERE</strong></span>
                        </div>
                        <div class="ml-2 text-gray-400">│</div>
                        <div class="flex items-center gap-2">
                            <span class="text-red-400">●</span>
                            <span>LL3 → LL2 → <strong>LL1 ${ll}</strong></span>
                        </div>
                    </div>
                    <div class="text-[10px] text-red-600 mt-2 font-semibold">
                        ⚠️ Price is making LOWER highs and LOWER lows. Wait for rally to ${lh}, then SHORT!
                    </div>
                </div>`;
        } else if (!isBear && kl.latest_hh && kl.latest_hl) {
            // UPTREND diagram
            const hh = `₹${Number(kl.latest_hh).toLocaleString('en-IN', {maximumFractionDigits:0})}`;
            const hl = `₹${Number(kl.latest_hl).toLocaleString('en-IN', {maximumFractionDigits:0})}`;
            descHTML += `
                <div class="mt-4 p-3 bg-green-50 border-l-4 border-green-500 rounded">
                    <div class="text-xs font-bold text-green-700 mb-2">📈 UPTREND STRUCTURE:</div>
                    <div class="font-mono text-xs text-gray-700 leading-relaxed">
                        <div class="flex items-center gap-2">
                            <span class="text-green-600">●</span>
                            <span>HH1 → HH2 → <strong>HH3 ${hh}</strong></span>
                        </div>
                        <div class="ml-2 text-gray-400">│</div>
                        <div class="flex items-center gap-2">
                            <span class="text-green-400">●</span>
                            <span>HL1 → HL2 → <strong class="text-green-600 bg-green-100 px-1 rounded">HL3 ${hl} ⬅ BUY HERE</strong></span>
                        </div>
                    </div>
                    <div class="text-[10px] text-green-600 mt-2 font-semibold">
                        ⚠️ Price is making HIGHER highs and HIGHER lows. Wait for dip to ${hl}, then LONG!
                    </div>
                </div>`;
        }
    }
    
    descEl.innerHTML = descHTML;

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

    // ── Pattern-specific markers (P1/P2/P3 or T1/T2/T3) ────────────
    const isTripleTop    = p.name === 'Triple Top';
    const isTripleBottom = p.name === 'Triple Bottom';
    const markers = [];

    if ((isTripleTop || isTripleBottom) && p.pivot_times && p.pivot_times.length === 5) {
        // pivots: [peak1_ts, trough1_ts, peak2_ts, trough2_ts, peak3_ts] for triple top
        //         [trough1_ts, peak1_ts, trough2_ts, peak2_ts, trough3_ts] for triple bottom
        const pivotLabels = isTripleTop
            ? ['P1', 'T1', 'P2', 'T2', 'P3']
            : ['T1', 'P1', 'T2', 'P2', 'T3'];
        const pivotPositions = isTripleTop
            ? ['aboveBar', 'belowBar', 'aboveBar', 'belowBar', 'aboveBar']
            : ['belowBar', 'aboveBar', 'belowBar', 'aboveBar', 'belowBar'];
        const pivotShapes = isTripleTop
            ? ['arrowDown', 'arrowUp', 'arrowDown', 'arrowUp', 'arrowDown']
            : ['arrowUp', 'arrowDown', 'arrowUp', 'arrowDown', 'arrowUp'];
        const pivotColors = isTripleTop
            ? ['#ea1100', '#6b7280', '#ea1100', '#6b7280', '#ea1100']
            : ['#2a8703', '#6b7280', '#2a8703', '#6b7280', '#2a8703'];

        p.pivot_times.forEach((ts, i) => {
            if (!ts) return;
            const unix = Math.floor(new Date(ts).getTime() / 1000);
            const snapTime = _phSnapCandle(win, unix);
            if (!snapTime) return;
            markers.push({
                time:     snapTime,
                position: pivotPositions[i],
                color:    pivotColors[i],
                shape:    pivotShapes[i],
                text:     pivotLabels[i],
                size:     1,
            });
        });
    } else {
        // Generic single arrow at pattern end
        if (patEnd) {
            const snapTime = _phSnapCandle(win, patEnd);
            if (snapTime) {
                markers.push({
                    time:     snapTime,
                    position: p.bias === 'bullish' ? 'belowBar' : 'aboveBar',
                    color:    p.bias === 'bullish' ? '#2a8703'  : '#ea1100',
                    shape:    p.bias === 'bullish' ? 'arrowUp'  : 'arrowDown',
                    text:     `${p.emoji || ''} ${p.name}`,
                    size:     2,
                });
            }
        }
    }

    if (markers.length) {
        markers.sort((a, b) => a.time - b.time);
        _phDetailSeries.setMarkers(markers);
    }

    // ── Key-level lines ─────────────────────────────────────────────
    const levels = Object.entries(p.key_levels || {});
    const skippedZoneKeys = new Set(['resistance_high', 'support_low']); // drawn as zone, not line

    levels.forEach(([key, val]) => {
        if (skippedZoneKeys.has(key)) return;   // part of zone band
        const price = parseFloat(val);
        if (isNaN(price)) return;

        const kl = key.toLowerCase();
        const isNeckline = kl.includes('neckline');
        const isTarget   = kl.includes('target');
        const isResist   = kl.includes('resist') || kl.includes('peak');
        const isSupport  = kl.includes('support') || kl.includes('trough');

        const color     = isNeckline ? '#0053e2'
                        : isTarget   ? '#ffc220'
                        : isResist   ? '#ea1100'
                        : isSupport  ? '#2a8703'
                        : '#6b7280';
        const lineWidth = isNeckline ? 2 : 1;
        const lineStyle = isTarget   ? 3   // dotted
                        : isNeckline ? 0   // solid
                        : 2;              // dashed
        const label     = key.replace(/_/g, ' ');

        _phDetailChart.addLineSeries({
            color,
            lineWidth,
            lineStyle,
            priceLineVisible: false,
            lastValueVisible: true,
            title: label,
        }).setData(win.map(c => ({ time: c.time, value: price })));
    });

    // ── Resistance / Support zone band (Triple Top / Triple Bottom) ─
    if (isTripleTop) {
        const zoneMid  = parseFloat(p.key_levels['resistance_zone'] || 0);
        const zoneTop  = parseFloat(p.key_levels['resistance_high'] || 0);
        if (zoneMid && zoneTop) {
            // Upper edge of zone (solid thin)
            _phDetailChart.addLineSeries({
                color: 'rgba(234,17,0,0.4)', lineWidth: 1, lineStyle: 2,
                priceLineVisible: false, lastValueVisible: false, title: '',
            }).setData(win.map(c => ({ time: c.time, value: zoneTop })));
        }
    }
    if (isTripleBottom) {
        const zoneMid = parseFloat(p.key_levels['support_zone'] || 0);
        const zoneBot = parseFloat(p.key_levels['support_low']  || 0);
        if (zoneMid && zoneBot) {
            _phDetailChart.addLineSeries({
                color: 'rgba(42,135,3,0.4)', lineWidth: 1, lineStyle: 2,
                priceLineVisible: false, lastValueVisible: false, title: '',
            }).setData(win.map(c => ({ time: c.time, value: zoneBot })));
        }
    }

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
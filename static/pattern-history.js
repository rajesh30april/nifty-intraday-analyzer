// ── Pattern History Tab (60-day sliding window scan) ─────────────

let _phPatterns  = [];
let _phDays      = [];
let _phSelectedDay = null;
let _phFilters   = { tf: 'all', bias: 'all' };
let _phChart     = null;
let _phSeries    = null;
let _phLoaded    = false;

// Called when user switches to the patterns tab
function onPatternsTabOpen() {
    if (!_phLoaded) phLoadAll();
}

async function phLoadAll() {
    const wrap    = document.getElementById('ph-day-wrap');
    const loading = document.getElementById('ph-loading');
    if (!wrap || !loading) return;

    wrap.classList.add('hidden');
    loading.classList.remove('hidden');

    try {
        const r = await fetch('/api/patterns-history?period=60d&timeframes=5m,15m,1h');
        const d = await r.json();
        if (!d.success) {
            loading.innerHTML = `<p class="text-red-500 font-bold">❌ ${d.error}</p>`;
            return;
        }

        _phPatterns = d.patterns || [];
        _phLoaded   = true;

        // Stats
        const el = id => document.getElementById(id);
        el('ph-total').textContent = d.total;
        el('ph-bull').textContent  = d.bullish_count;
        el('ph-bear').textContent  = d.bearish_count;

        // Unique trading days newest-first
        const daySet = new Set(_phPatterns.map(p => p.end_date).filter(Boolean));
        _phDays = [...daySet].sort().reverse();

        _phBuildDayStrip();
        _phRenderTable();

        loading.classList.add('hidden');
        wrap.classList.remove('hidden');

        // Auto-select newest day
        if (_phDays.length) phSelectDay(_phDays[0]);

    } catch (e) {
        loading.innerHTML = `<p class="text-red-500 font-bold">❌ ${e.message}</p>`;
    }
}

// ── Day strip ────────────────────────────────────────────────────
function _phBuildDayStrip() {
    const strip = document.getElementById('ph-day-strip');
    if (!strip) return;
    strip.innerHTML = _phDays.map(day => {
        const cnt   = _phPatterns.filter(p => p.end_date === day).length;
        const label = _phFmtDay(day);
        return `<button class="ph-day-btn" data-day="${day}" onclick="phSelectDay('${day}')">
            <div>${label}</div>
            <div style="font-size:9px;opacity:.65;text-align:center">${cnt} pat${cnt !== 1 ? 's' : ''}</div>
        </button>`;
    }).join('');
}

async function phSelectDay(day) {
    _phSelectedDay = day;

    // Highlight button
    document.querySelectorAll('.ph-day-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.day === day));
    const active = document.querySelector(`.ph-day-btn[data-day="${day}"]`);
    if (active) active.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });

    // Update day info label
    const cnt = _phPatterns.filter(p => p.end_date === day).length;
    const info = document.getElementById('ph-day-info');
    if (info) info.textContent = `${cnt} pattern${cnt !== 1 ? 's' : ''} detected`;

    // Chart title
    const title = document.getElementById('ph-chart-title');
    if (title) title.textContent = `📈 5-Min Chart — ${_phFmtDay(day)}`;

    // Fetch candles + patterns for this day
    const chartLoad = document.getElementById('ph-chart-loading');
    if (chartLoad) chartLoad.classList.remove('hidden');

    try {
        const r = await fetch(`/api/day-chart?date=${day}`);
        const d = await r.json();
        _phBuildChart(d.success ? (d.candles || []) : [], d.success ? (d.patterns || []) : []);
    } catch (e) {
        _phBuildChart([], []);
    } finally {
        if (chartLoad) chartLoad.classList.add('hidden');
    }

    _phRenderDayCards();
}

// ── Chart ────────────────────────────────────────────────────────
function _phBuildChart(candles, patterns) {
    const container = document.getElementById('ph-price-chart');
    if (!container) return;
    container.innerHTML = '';

    if (!candles.length) {
        container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#9ca3af;font-size:13px">No candle data for this day</div>';
        return;
    }

    if (_phChart) { try { _phChart.remove(); } catch(e) {} _phChart = null; }

    _phChart = LightweightCharts.createChart(container, {
        layout:  { background: { color: '#ffffff' }, textColor: '#374151' },
        grid:    { vertLines: { color: '#f8fafc' },  horzLines: { color: '#f8fafc' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: '#e2e8f0' },
        timeScale: { borderColor: '#e2e8f0', timeVisible: true, secondsVisible: false },
        width:  container.clientWidth,
        height: 340,
    });

    _phSeries = _phChart.addCandlestickSeries({
        upColor: '#2a8703', downColor: '#ea1100',
        borderUpColor: '#2a8703', borderDownColor: '#ea1100',
        wickUpColor:   '#2a8703', wickDownColor:   '#ea1100',
    });
    _phSeries.setData(candles);

    // Pattern markers
    const markers = [];
    patterns.forEach(p => {
        const ts = p.end_time || p.start_time;
        if (!ts) return;
        const unix = Math.floor(new Date(ts).getTime() / 1000);
        const snap = _phSnapCandle(candles, unix);
        if (!snap) return;
        markers.push({
            time:     snap,
            position: p.bias === 'bullish' ? 'belowBar' : 'aboveBar',
            color:    p.bias === 'bullish' ? '#2a8703'  : '#ea1100',
            shape:    p.bias === 'bullish' ? 'arrowUp'  : 'arrowDown',
            text:     `${p.emoji || ''} ${p.name}`,
            size:     Math.max(1, Math.round(p.confidence * 2)),
        });
    });
    markers.sort((a, b) => a.time - b.time);
    _phSeries.setMarkers(markers);
    _phChart.timeScale().fitContent();

    new ResizeObserver(() => {
        if (_phChart) _phChart.resize(container.clientWidth, 340);
    }).observe(container);
}

function _phSnapCandle(candles, unix) {
    if (!candles.length) return null;
    return candles.reduce((best, c) =>
        Math.abs(c.time - unix) < Math.abs(best.time - unix) ? c : best
    ).time;
}

// ── Filters ──────────────────────────────────────────────────────
function phFilter(group, val, btn) {
    _phFilters[group] = val;
    document.querySelectorAll(`[data-g="${group}"]`).forEach(b => {
        b.classList.remove('active');
        b.style.cssText = (b.dataset.v === '15m' && group === 'tf')
            ? 'border-color:#16a34a;color:#15803d;background:#f0fdf4' : '';
    });
    btn.classList.add('active');
    btn.style.cssText = '';
    _phRenderDayCards();
    _phRenderTable();
}

function _phApplyFilters(list) {
    return list.filter(p => {
        if (_phFilters.tf   !== 'all' && p.timeframe !== _phFilters.tf)   return false;
        if (_phFilters.bias !== 'all' && p.bias      !== _phFilters.bias) return false;
        return true;
    });
}

// ── Cards for selected day ────────────────────────────────────────
function _phRenderDayCards() {
    const grid  = document.getElementById('ph-cards');
    const empty = document.getElementById('ph-cards-empty');
    const countEl = document.getElementById('ph-vis-count');
    if (!grid) return;

    const dayPats  = _phPatterns.filter(p => p.end_date === _phSelectedDay);
    const filtered = _phApplyFilters(dayPats);

    if (countEl) countEl.textContent = `${filtered.length} pattern${filtered.length !== 1 ? 's' : ''} on this day`;

    if (!filtered.length) {
        grid.innerHTML = '';
        grid.classList.add('hidden');
        if (empty) empty.classList.remove('hidden');
        return;
    }
    if (empty) empty.classList.add('hidden');
    grid.classList.remove('hidden');
    grid.innerHTML = filtered.map((p, i) => _phCard(p, i)).join('');
}

function _phCard(p, i) {
    const isBull = p.bias === 'bullish', isBear = p.bias === 'bearish';
    const border = isBull ? 'border-green-200' : isBear ? 'border-red-200' : 'border-gray-200';
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
    const tfStyle = p.timeframe === '15m'
        ? 'background:#dcfce7;color:#15803d;font-weight:900'
        : p.timeframe === '5m' ? 'background:#dbeafe;color:#1d4ed8' : 'background:#f3e8ff;color:#7e22ce';
    const conf = Math.round(p.confidence * 100);
    const confColor = conf >= 75 ? '#2a8703' : conf >= 50 ? '#0053e2' : '#f59e0b';
    const timeStr = p.end_time
        ? new Date(p.end_time).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }) : '';
    const levels = Object.entries(p.key_levels || {}).slice(0, 3)
        .map(([k, v]) => `<span class="text-gray-400">${k}:</span> <b>${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</b>`)
        .join(' · ');

    return `
    <div class="bg-white border ${border} rounded-xl p-3 shadow-sm" style="animation:fadeUp .25s ${i * 30}ms both">
        <div class="flex items-start justify-between mb-2">
            <div class="flex items-center gap-2">
                <span class="text-xl">${p.emoji || '📊'}</span>
                <div>
                    <div class="font-black text-gray-800 text-sm leading-tight">${p.name}</div>
                    <div class="text-[10px] text-gray-400">${timeStr}</div>
                </div>
            </div>
            <span class="chip" style="${tfStyle}">${p.timeframe}</span>
        </div>
        <div class="flex gap-1 flex-wrap mb-2">${biasChip} ${typeChip}</div>
        <div class="mb-2">
            <div class="flex justify-between text-[10px] text-gray-400 mb-1">
                <span>Confidence</span><span class="font-black" style="color:${confColor}">${conf}%</span>
            </div>
            <div class="conf-bar"><div class="conf-fill" style="width:${conf}%;background:${confColor}"></div></div>
        </div>
        <p class="text-[11px] text-gray-500 leading-relaxed line-clamp-2 mb-1">${p.description}</p>
        ${levels ? `<div class="text-[10px] border-t border-gray-100 pt-1">${levels}</div>` : ''}
    </div>`;
}

// ── Full 60-day table ─────────────────────────────────────────────
function _phRenderTable() {
    const tbody  = document.getElementById('ph-table');
    const countEl = document.getElementById('ph-tbl-count');
    if (!tbody) return;

    const filtered = _phApplyFilters(_phPatterns);
    if (countEl) countEl.textContent = `${filtered.length} of ${_phPatterns.length}`;

    tbody.innerHTML = filtered.map(p => {
        const biasColor = p.bias === 'bullish' ? 'color:#2a8703' : p.bias === 'bearish' ? 'color:#ea1100' : 'color:#6b7280';
        const timeStr   = p.end_time
            ? new Date(p.end_time).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }) : '—';
        const conf = Math.round(p.confidence * 100);
        const confColor = conf >= 75 ? '#2a8703' : conf >= 50 ? '#0053e2' : '#f59e0b';
        const tfStyle   = p.timeframe === '15m'
            ? 'background:#dcfce7;color:#15803d'
            : p.timeframe === '5m' ? 'background:#dbeafe;color:#1d4ed8' : 'background:#f3e8ff;color:#7e22ce';
        return `<tr class="hover:bg-gray-50 cursor-pointer" onclick="phSelectDay('${p.end_date}')">
            <td class="px-3 py-1.5 font-bold text-gray-800">${p.emoji || ''} ${p.name}</td>
            <td class="px-3 py-1.5 text-gray-500">${p.date_label || '—'}</td>
            <td class="px-3 py-1.5 text-gray-500">${timeStr}</td>
            <td class="px-3 py-1.5"><span class="chip" style="${tfStyle}">${p.timeframe}</span></td>
            <td class="px-3 py-1.5 font-bold" style="${biasColor}">${p.bias}</td>
            <td class="px-3 py-1.5">
                <div style="display:flex;align-items:center;gap:6px">
                    <div class="conf-bar" style="width:48px"><div class="conf-fill" style="width:${conf}%;background:${confColor}"></div></div>
                    <span class="font-bold" style="color:${confColor};font-size:10px">${conf}%</span>
                </div>
            </td>
        </tr>`;
    }).join('');
}

// ── Helpers ───────────────────────────────────────────────────────
function _phFmtDay(d) {
    const dt = new Date(d + 'T00:00:00');
    return dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', weekday: 'short' });
}
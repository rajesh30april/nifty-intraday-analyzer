/* ── Live Monitor + Expandable Trade Rows ──────────────────────
 * 1. SSE-based live candle log in the terminal at the bottom
 * 2. Each backtest trade row expands to show candle-by-candle detail
 */

// ───────────────────────────────────────────────────────────────────
// PART 1 — SSE LIVE MONITOR
// ───────────────────────────────────────────────────────────────────

let _lmSource    = null;
let _lmConnected = false;
let _lmLastPrice = null;
let _tradeCharts = {};    // trade index → Chart instance

/** Connect to the SSE stream. */
function lmConnect() {
    if (_lmConnected) { lmDisconnect(); return; }

    const strategy = document.getElementById('lm-strategy')?.value || 'smart_router';
    const url       = `/api/live-monitor/stream?strategy=${strategy}`;

    _lmSource = new EventSource(url);
    _lmConnected = true;

    _setStatus('connecting', 'Connecting...');

    _lmSource.onopen = () => {
        _setStatus('connected', 'Live');
        document.getElementById('lm-connect-btn').textContent = 'Disconnect';
        document.getElementById('lm-connect-btn').className =
            'text-xs px-3 py-1 rounded font-bold bg-red-700 hover:bg-red-600 text-white transition';
        _appendLog({ type: 'system', msg: '── Stream connected ──' });
    };

    _lmSource.onmessage = (e) => {
        try {
            const d = JSON.parse(e.data);
            _handleEvent(d);
        } catch {}
    };

    _lmSource.onerror = () => {
        _setStatus('error', 'Reconnecting...');
        _appendLog({ type: 'error', msg: 'Connection lost — retrying...' });
    };
}

function lmDisconnect() {
    if (_lmSource) { _lmSource.close(); _lmSource = null; }
    _lmConnected = false;
    _setStatus('disconnected', 'Disconnected');
    document.getElementById('lm-connect-btn').textContent = 'Connect';
    document.getElementById('lm-connect-btn').className =
        'text-xs px-3 py-1 rounded font-bold bg-green-700 hover:bg-green-600 text-white transition';
    _appendLog({ type: 'system', msg: '── Disconnected ──' });
}

function lmClear() {
    const log = document.getElementById('lm-log');
    if (log) log.innerHTML = '<div class="text-gray-600">── Log cleared ──</div>';
}

// Handle one SSE event
function _handleEvent(d) {
    if (d.error) {
        _appendLog({ type: 'error', msg: d.ts + '  ERROR: ' + d.error });
        return;
    }

    // Update meta strip
    document.getElementById('lm-meta-strip')?.classList.remove('hidden');
    document.getElementById('lm-signal-bar')?.classList.remove('hidden');

    const priceEl = document.getElementById('lm-price');
    if (priceEl) {
        const dir = _lmLastPrice !== null
            ? (d.price > _lmLastPrice ? ' ▲' : d.price < _lmLastPrice ? ' ▼' : '')
            : '';
        priceEl.textContent = d.price.toLocaleString() + dir;
        priceEl.className = 'font-black ' + (dir.includes('▲') ? 'text-green-400'
            : dir.includes('▼') ? 'text-red-400' : 'text-white');
    }
    _lmLastPrice = d.price;

    const regimeMap = {
        trending_up:   '📈 UP', trending_down: '📉 DOWN',
        sideways:      '↔ FLAT',    volatile:       '⚡ VOLATILE',
    };
    _setText('lm-regime', regimeMap[d.regime] || d.regime);
    _setText('lm-strat',  d.emoji + ' ' + d.strategy);
    _setText('lm-conf',   d.confidence + '%');
    _setText('lm-candle-time', 'candle ' + d.candle_time);

    // ── SL / R:R / Qty from backtest settings (shared inputs) ────
    const slVal  = document.getElementById('bt-sl')?.value  || '30';
    const rrVal  = document.getElementById('bt-rr')?.value  || '2';
    const qtyVal = document.getElementById('bt-qty')?.value || '780';
    const lots   = Math.round(parseInt(qtyVal) / 75);   // approx lots (lot size 75)
    _setText('lm-sl-val',  slVal + ' pts');
    _setText('lm-rr-val',  '1:' + rrVal);
    _setText('lm-qty-val', qtyVal + ' (~' + lots + 'L)');

    // Signal banner
    const sigBar  = document.getElementById('lm-signal-bar');
    const sigText = document.getElementById('lm-signal-text');
    if (sigBar && sigText) {
        if (d.signal) {
            const arrow = d.direction === 'long' ? '▲ LONG' : '▼ SHORT';
            sigText.textContent = `🚀 SIGNAL FIRES — ${arrow}  (${d.confidence}% conf)`;
            sigBar.className = 'px-3 py-2 border-b text-xs font-bold flex items-center justify-between '
                + (d.direction === 'long' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300');
        } else {
            const mktMsg = d.market_open ? '⏸ No signal this candle' : '🌙 Market closed';
            sigText.textContent = `${d.emoji} ${d.strategy}  —  ${mktMsg}`;
            sigBar.className = 'px-3 py-2 border-b text-xs font-bold flex items-center justify-between bg-gray-800 text-gray-400';
        }
    }

    // Append to log
    _appendLog({
        type:  d.signal ? 'signal' : (d.market_open ? 'candle' : 'closed'),
        ts:    d.ts,
        candle: d.candle_time,
        price: d.price,
        strategy: d.emoji + ' ' + d.strategy,
        signal: d.signal,
        dir:   d.direction,
        conf:  d.confidence,
        regime: d.regime,
        market_open: d.market_open,
    });
}

function _appendLog(entry) {
    const log = document.getElementById('lm-log');
    if (!log) return;

    const el = document.createElement('div');
    el.className = 'flex gap-2 items-start py-0.5 border-b border-gray-900';

    if (entry.type === 'system') {
        el.innerHTML = `<span class="text-gray-600 w-full text-center">${entry.msg}</span>`;
    } else if (entry.type === 'error') {
        el.innerHTML = `<span class="text-red-500">⚠ ${entry.msg}</span>`;
    } else if (entry.type === 'closed') {
        el.innerHTML = `
            <span class="text-gray-600 w-14 shrink-0">${entry.ts}</span>
            <span class="text-gray-600">🌙 Market closed — ${entry.strategy}</span>`;
    } else if (entry.type === 'signal') {
        const arrow = entry.dir === 'long' ? '▲' : '▼';
        const col   = entry.dir === 'long' ? 'text-green-400' : 'text-red-400';
        el.innerHTML = `
            <span class="text-gray-500 w-14 shrink-0">${entry.ts}</span>
            <span class="text-yellow-400 w-12 shrink-0">${entry.candle}</span>
            <span class="text-white font-bold w-20 shrink-0">${entry.price.toLocaleString()}</span>
            <span class="${col} font-bold">🚀 ${arrow}${entry.dir.toUpperCase()} ${entry.conf}%</span>
            <span class="text-gray-500 ml-auto">${entry.strategy}</span>`;
    } else {
        // normal candle — show strategy verdict clearly
        const regFmt = { trending_up: '▲UP', trending_down: '▼DN', sideways: '↔ flat', volatile: '⚡vola' };
        // If signal fires but we're just monitoring (no auto-trader), make it obvious
        const verdictHtml = entry.signal
            ? `<span class="text-yellow-500 font-bold">👁 SIGNAL(${entry.dir?.toUpperCase()}) ${entry.conf}% — monitoring only</span>`
            : `<span class="text-gray-600">⏸ waiting  ${entry.strategy}</span>`;
        el.innerHTML = `
            <span class="text-gray-600 w-14 shrink-0">${entry.ts}</span>
            <span class="text-gray-500 w-12 shrink-0">${entry.candle}</span>
            <span class="text-gray-300 w-20 shrink-0">${entry.price.toLocaleString()}</span>
            <span class="text-gray-600 w-16 shrink-0 text-[10px]">${regFmt[entry.regime] || entry.regime}</span>
            ${verdictHtml}`;
    }

    log.appendChild(el);
    log.scrollTop = log.scrollHeight;

    // Keep max 200 entries
    while (log.children.length > 200) log.removeChild(log.firstChild);
}

function _setStatus(state, label) {
    const dot    = document.getElementById('lm-dot');
    const status = document.getElementById('lm-status');
    if (!dot || !status) return;
    status.textContent = label;
    const colors = { connected: 'bg-green-400 animate-pulse', connecting: 'bg-yellow-400 animate-pulse',
                     error: 'bg-red-400 animate-pulse', disconnected: 'bg-gray-500' };
    dot.className = 'w-2 h-2 rounded-full ' + (colors[state] || colors.disconnected);
    // sidebar dot
    const sdot = document.getElementById('lm-sidebar-dot');
    if (sdot) sdot.className = 'ml-auto w-2 h-2 rounded-full ' +
        (state === 'connected' ? 'bg-green-400' : 'bg-gray-300');
}

function _setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}


// ───────────────────────────────────────────────────────────────────
// PART 2 — EXPANDABLE TRADE ROWS
// ───────────────────────────────────────────────────────────────────

/** Called from renderTradeLog() in backtest.js — re-render with expand buttons. */
function renderTradeLogWithExpand(trades) {
    const tbody = document.getElementById('bt-trades-body');
    if (!tbody) return;

    if (!trades.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="text-center py-4 text-gray-400">No trades</td></tr>';
        return;
    }

    tbody.innerHTML = trades.map((t, idx) => {
        const isWin   = t.pnl_points >= 0;
        const rowBg   = isWin ? 'bg-green-50' : 'bg-red-50';
        const pnlCol  = isWin ? 'text-green-700' : 'text-red-700';
        const dirBadge = t.direction === 'long'
            ? '<span class="bg-green-100 text-green-800 px-1.5 py-0.5 rounded text-[10px] font-bold">LONG</span>'
            : '<span class="bg-red-100 text-red-800 px-1.5 py-0.5 rounded text-[10px] font-bold">SHORT</span>';
        const winIcon = isWin ? '✅' : '❌';

        return `
        <tr class="${rowBg} border-b border-gray-200 hover:brightness-95 transition cursor-pointer"
            onclick="toggleTradeDetail(${idx})">
            <td class="px-2 py-2 text-gray-400 text-xs" id="td-arrow-${idx}">▶</td>
            <td class="px-2 py-2 text-xs">${t.date}</td>
            <td class="px-2 py-2 text-xs font-mono">${t.entry_time}–${t.exit_time}</td>
            <td class="px-2 py-2">${dirBadge}</td>
            <td class="px-2 py-2 text-xs font-mono">${t.entry_price.toLocaleString()}</td>
            <td class="px-2 py-2 text-xs font-mono">${t.exit_price.toLocaleString()}</td>
            <td class="px-2 py-2 text-xs">
                <span class="bg-gray-200 px-1.5 py-0.5 rounded text-[10px]">${t.exit_reason}</span>
            </td>
            <td class="px-2 py-2 text-xs font-bold ${pnlCol}">${isWin?'+':''}${t.pnl_points}pts</td>
            <td class="px-2 py-2 text-xs font-bold ${pnlCol}">${winIcon} ₹${isWin?'+':''}${t.pnl_rupees.toLocaleString()}</td>
        </tr>
        <tr id="td-detail-${idx}" class="hidden">
            <td colspan="9" class="p-0">
                <div class="bg-gray-950 p-4 border-b-2 border-indigo-500">
                    <div id="td-loading-${idx}" class="text-gray-400 text-xs text-center py-4">Loading candles...</div>
                    <div id="td-content-${idx}" class="hidden space-y-3">
                        <div style="height:160px"><canvas id="td-chart-${idx}"></canvas></div>
                        <div class="overflow-x-auto">
                            <table class="w-full text-[11px] text-left" aria-label="Trade detail">
                                <thead>
                                    <tr class="text-gray-500 border-b border-gray-800">
                                        <th class="px-2 py-1">Time</th>
                                        <th class="px-2 py-1">Open</th>
                                        <th class="px-2 py-1">High</th>
                                        <th class="px-2 py-1">Low</th>
                                        <th class="px-2 py-1">Close</th>
                                        <th class="px-2 py-1">Trail SL</th>
                                        <th class="px-2 py-1">Target</th>
                                        <th class="px-2 py-1">Unreal.</th>
                                        <th class="px-2 py-1">State</th>
                                    </tr>
                                </thead>
                                <tbody id="td-tbody-${idx}" class="font-mono"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </td>
        </tr>`;
    }).join('');

    // Store trades for later fetch
    window._btTrades = trades;
}

/** Toggle a trade detail panel open/closed. */
async function toggleTradeDetail(idx) {
    const detailRow = document.getElementById(`td-detail-${idx}`);
    const arrow     = document.getElementById(`td-arrow-${idx}`);
    if (!detailRow) return;

    const isOpen = !detailRow.classList.contains('hidden');
    if (isOpen) {
        detailRow.classList.add('hidden');
        arrow.textContent = '▶';
        return;
    }

    detailRow.classList.remove('hidden');
    arrow.textContent = '▼';

    // Already loaded?
    if (!document.getElementById(`td-loading-${idx}`).classList.contains('hidden')) return;

    await _loadTradeDetail(idx);
}

async function _loadTradeDetail(idx) {
    const t = window._btTrades?.[idx];
    if (!t) return;

    const loadEl    = document.getElementById(`td-loading-${idx}`);
    const contentEl = document.getElementById(`td-content-${idx}`);
    loadEl.classList.remove('hidden');
    contentEl.classList.add('hidden');

    // Collect backtest settings for correct SL tracking
    const sl      = parseFloat(document.getElementById('bt-sl')?.value    || '30');
    const trail   = parseFloat(document.getElementById('bt-trail')?.value || '15');

    const params = new URLSearchParams({
        date:         t.date,
        entry_time:   t.entry_time,
        exit_time:    t.exit_time,
        entry_price:  t.entry_price,
        stop_loss:    t.stop_loss,
        target:       t.target,
        direction:    t.direction,
        sl_points:    sl,
        trailing_sl:  trail,
    });

    try {
        const resp = await fetch(`/api/trade-candles?${params}`);
        const data = await resp.json();

        if (!data.success) {
            loadEl.textContent = '⚠ ' + (data.error || 'Failed to load');
            return;
        }

        _renderTradeDetail(idx, data.candles, data.direction, t);
        loadEl.classList.add('hidden');
        contentEl.classList.remove('hidden');
    } catch (e) {
        loadEl.textContent = '⚠ Error: ' + e.message;
    }
}

function _renderTradeDetail(idx, candles, direction, trade) {
    // ─ Mini chart ─
    const ctx = document.getElementById(`td-chart-${idx}`)?.getContext('2d');
    if (ctx) {
        if (_tradeCharts[idx]) _tradeCharts[idx].destroy();

        const labels  = candles.map(c => c.time);
        const closes  = candles.map(c => c.close);
        const slLine  = candles.map(c => c.sl);
        const tgtLine = candles.map(c => c.target);

        const entryPt = [{ x: 0, y: candles[0].close }];
        const exitPt  = [{ x: candles.length - 1, y: candles[candles.length - 1].close }];
        const isWin   = trade.pnl_points >= 0;

        _tradeCharts[idx] = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [
                    { label: 'Price', data: closes,  borderColor: '#a5b4fc', borderWidth: 2,
                      backgroundColor: 'rgba(165,180,252,0.1)', fill: true, tension: 0.2,
                      pointRadius: 0, order: 4 },
                    { label: 'Trail SL', data: slLine, borderColor: '#ef4444', borderWidth: 1.5,
                      borderDash: [4, 3], pointRadius: 0, fill: false, order: 3 },
                    { label: 'Target',   data: tgtLine, borderColor: '#22c55e', borderWidth: 1.5,
                      borderDash: [4, 3], pointRadius: 0, fill: false, order: 3 },
                    { label: 'Entry', data: entryPt, type: 'scatter',
                      pointStyle: 'triangle', rotation: direction === 'short' ? 180 : 0,
                      pointRadius: 10, backgroundColor: '#818cf8', borderColor: '#fff', borderWidth: 2, order: 1 },
                    { label: 'Exit', data: exitPt, type: 'scatter',
                      pointStyle: 'crossRot', pointRadius: 10,
                      backgroundColor: isWin ? '#22c55e' : '#ef4444',
                      borderColor: '#fff', borderWidth: 2, order: 1 },
                ],
            },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                plugins: {
                    legend: { labels: { color: '#9ca3af', font: { size: 9 }, boxWidth: 10 } },
                    tooltip: { mode: 'index', intersect: false },
                },
                scales: {
                    x: { ticks: { color: '#6b7280', font: { size: 9 } }, grid: { color: '#1f2937' } },
                    y: { ticks: { color: '#6b7280', font: { size: 9 } }, grid: { color: '#1f2937' } },
                },
            },
        });
    }

    // ─ Table rows ─
    const tbody = document.getElementById(`td-tbody-${idx}`);
    if (!tbody) return;

    tbody.innerHTML = candles.map(c => {
        const stateMap = {
            entry:    '<span class="text-indigo-400 font-bold">ENTRY</span>',
            in_trade: '<span class="text-blue-400">TRADE</span>',
            exit:     `<span class="font-bold ${c.unrealized >= 0 ? 'text-green-400' : 'text-red-400'}">EXIT</span>`,
        };
        const urCol  = c.unrealized >= 0 ? 'text-green-400' : 'text-red-400';
        const rowBg  = c.state === 'entry' ? 'bg-indigo-950'
                     : c.state === 'exit'  ? (c.unrealized >= 0 ? 'bg-green-950' : 'bg-red-950')
                     : '';
        // Highlight trailing SL moves
        const slPrev = candles[candles.indexOf(c) - 1]?.sl ?? c.sl;
        const slMoved = c.sl !== slPrev;
        const slColor = slMoved ? 'text-yellow-400 font-bold' : 'text-red-400';

        return `
        <tr class="border-b border-gray-900 ${rowBg}">
            <td class="px-2 py-1 text-gray-300">${c.time}</td>
            <td class="px-2 py-1 text-gray-400">${c.open.toLocaleString()}</td>
            <td class="px-2 py-1 text-green-500">${c.high.toLocaleString()}</td>
            <td class="px-2 py-1 text-red-500">${c.low.toLocaleString()}</td>
            <td class="px-2 py-1 text-white font-bold">${c.close.toLocaleString()}</td>
            <td class="px-2 py-1 ${slColor}">${c.sl.toLocaleString()}${slMoved ? ' ↑' : ''}</td>
            <td class="px-2 py-1 text-green-500">${c.target.toLocaleString()}</td>
            <td class="px-2 py-1 font-bold ${urCol}">${c.unrealized >= 0 ? '+' : ''}${c.unrealized}</td>
            <td class="px-2 py-1">${stateMap[c.state] || ''}</td>
        </tr>`;
    }).join('');
}
/* ── Day Replay Module ───────────────────────────────────────────
 * Candle-by-candle day replay for the Nifty backtester UI.
 * Depends on: Chart.js (already loaded globally)
 */

let _replayFrames   = [];
let _replayIdx      = -1;
let _replayTimer    = null;
let _replayPaused   = false;
let _replayChart    = null;
let _replayDates    = [];

// ── Public: called after a backtest completes ──────────────────
/**
 * Populate the date picker from a completed backtest result.
 * Call this from renderBacktestResults() in backtest.js
 */
function replayPopulateDates(dailyPnl) {
    const sel = document.getElementById('replay-date');
    if (!sel) return;
    const dates = Object.keys(dailyPnl).sort().reverse();
    _replayDates = dates;
    sel.innerHTML = dates.map((d, i) => {
        const pnl  = dailyPnl[d];
        const sign = pnl >= 0 ? '+' : '';
        const flag = pnl >= 0 ? '✅' : '❌';
        return `<option value="${d}">${flag} ${d}  (${sign}${pnl.toFixed(1)} pts)</option>`;
    }).join('');
}

// ── Public: auto-select date (called from daily P&L chart click)
function replaySelectDate(dateStr) {
    const sel = document.getElementById('replay-date');
    if (!sel) return;
    sel.value = dateStr;
    document.getElementById('replay-btn').classList.add('ring-2', 'ring-yellow-400');
    setTimeout(() => document.getElementById('replay-btn').classList.remove('ring-2', 'ring-yellow-400'), 1500);
}

// ── Run Day Replay (animated) ──────────────────────────────────
async function runDayReplay() {
    const date = document.getElementById('replay-date').value;
    if (!date) { alert('Please run a backtest first to load dates!'); return; }
    await _fetchAndStartReplay(date, false);
}

// Run instantly (no animation) ─────────────────────────────────
async function replayInstantFull() {
    const date = document.getElementById('replay-date').value;
    if (!date) { alert('Please run a backtest first to load dates!'); return; }
    await _fetchAndStartReplay(date, true);
}

async function _fetchAndStartReplay(date, instant) {
    _stopTimer();

    const params = new URLSearchParams({
        date,
        period:      document.getElementById('bt-period')?.value     || '60d',
        sl_points:   document.getElementById('bt-sl')?.value         || '30',
        trailing_sl: document.getElementById('bt-trail')?.value      || '15',
        rr_ratio:    document.getElementById('bt-rr')?.value         || '2',
        max_trades:  document.getElementById('bt-max-trades')?.value || '3',
        strategy:    document.getElementById('bt-strategy')?.value   || 'smart_router',
        quantity:    document.getElementById('bt-qty')?.value        || '750',
        data_source: window._currentDataSource || 'yahoo',
    });

    const loadingEl = document.getElementById('replay-loading');
    const resultsEl = document.getElementById('replay-results');
    loadingEl.classList.remove('hidden');
    resultsEl.classList.add('hidden');
    _setReplayProgress(0, 'Connecting…');

    return new Promise((resolve) => {
        const es = new EventSource(`/api/backtest/replay/stream?${params}`);

        es.onmessage = (e) => {
            const msg = JSON.parse(e.data);

            if (msg.phase === 'fetching') {
                _setReplayProgress(msg.pct, '📡 ' + (msg.msg || 'Fetching data…'));
            } else if (msg.phase === 'processing') {
                const label = msg.total
                    ? `🕯 Candle ${msg.candle} / ${msg.total}`
                    : '🕯 Processing…';
                _setReplayProgress(msg.pct, label);
            } else if (msg.phase === 'done') {
                es.close();
                _setReplayProgress(100, '✅ Done!');

                _replayFrames = msg.frames || [];
                _replayIdx    = -1;
                _replayPaused = false;

                // Always hide loading first — never leave user stuck
                loadingEl.classList.add('hidden');

                try {
                    document.getElementById('replay-date-label').textContent = date;
                    resultsEl.classList.remove('hidden');
                    document.getElementById('replay-candle-card')?.classList.remove('hidden');

                    _renderSummary(msg.summary);
                    _initPriceChart(msg.frames, date);
                    _buildTable(msg.frames);

                    if (instant) {
                        _replayIdx = _replayFrames.length - 1;
                        _renderFrame(_replayIdx);
                        _updateProgress();
                    } else {
                        document.getElementById('replay-pause-btn').textContent = '⏸';
                        _advanceReplay();
                    }
                } catch (err) {
                    console.error('Replay render failed:', err, msg);
                    _setReplayProgress(0, '❌ Render error — check console');
                }
                resolve();
            } else if (msg.phase === 'error') {
                es.close();
                loadingEl.classList.add('hidden');
                _setReplayProgress(0, '❌ Error: ' + (msg.msg || 'unknown'));
                resolve();
            }
        };

        es.onerror = () => {
            es.close();
            loadingEl.classList.add('hidden');
            _setReplayProgress(0, '❌ Connection error');
            resolve();
        };
    });
}

function _setReplayProgress(pct, label) {
    const bar   = document.getElementById('replay-progress-bar');
    const txt   = document.getElementById('replay-progress-label');
    const wrap  = document.getElementById('replay-progress-wrap');
    if (!wrap) return;
    wrap.classList.remove('hidden');
    if (bar)  { bar.style.width = pct + '%'; bar.textContent = pct + '%'; }
    if (txt)  txt.textContent = label || '';
    if (pct >= 100) setTimeout(() => wrap?.classList.add('hidden'), 1500);
}

// ── Timer control ─────────────────────────────────────────────
function _advanceReplay() {
    if (_replayPaused) return;
    const speed = parseInt(document.getElementById('replay-speed')?.value || '300');

    _replayIdx++;
    if (_replayIdx >= _replayFrames.length) { _replayIdx = _replayFrames.length - 1; return; }

    _renderFrame(_replayIdx);
    _updateProgress();
    _highlightTableRow(_replayIdx);

    if (_replayIdx < _replayFrames.length - 1 && speed > 0) {
        _replayTimer = setTimeout(_advanceReplay, speed);
    }
}

function _stopTimer() {
    if (_replayTimer) { clearTimeout(_replayTimer); _replayTimer = null; }
}

function replayPauseResume() {
    _replayPaused = !_replayPaused;
    document.getElementById('replay-pause-btn').textContent = _replayPaused ? '▶' : '⏸';
    if (!_replayPaused) _advanceReplay();
}

function replayNext() {
    _stopTimer();
    _replayPaused = true;
    if (_replayIdx < _replayFrames.length - 1) {
        _replayIdx++;
        _renderFrame(_replayIdx);
        _updateProgress();
        _highlightTableRow(_replayIdx);
    }
}

function replayPrev() {
    _stopTimer();
    _replayPaused = true;
    if (_replayIdx > 0) {
        _replayIdx--;
        _renderFrame(_replayIdx);
        _updateProgress();
        _highlightTableRow(_replayIdx);
    }
}

// ── Render a single frame ─────────────────────────────────────
let _lastIdleReason = '';

function _renderFrame(idx) {
    const f = _replayFrames[idx];
    if (!f) return;

    // Announce state changes in the candle card subtitle
    if (f.trade_state === 'idle' && f.idle_reason !== _lastIdleReason) {
        _lastIdleReason = f.idle_reason;
        const msgs = {
            max_trades:  '🚫 Daily trade limit reached — signals ignored for rest of day',
            time_filter: '⏰ Outside trading hours',
            no_signal:   '⏸ Waiting for signal',
        };
        const hint = document.getElementById('replay-hint');
        if (hint && msgs[f.idle_reason]) {
            hint.textContent = msgs[f.idle_reason];
            hint.className = f.idle_reason === 'max_trades'
                ? 'text-xs mt-2 text-orange-600 font-bold'
                : 'text-xs mt-2 text-gray-500';
        }
    }

    // Time
    document.getElementById('rc-time').textContent = f.time;

    // Candle values
    document.getElementById('rc-close').textContent = f.close.toLocaleString();
    document.getElementById('rc-hl').textContent    = `${f.high.toLocaleString()} / ${f.low.toLocaleString()}`;
    document.getElementById('rc-strategy').textContent  = `${f.strategy_emoji} ${f.strategy_name}`;
    document.getElementById('rc-confidence').textContent = `${f.confidence}%`;

    // Regime
    document.getElementById('rc-regime').textContent = _fmtRegime(f.regime);

    // Signal
    const sigEl = document.getElementById('rc-signal');
    if (f.signal_fires) {
        const dir = f.direction === 'long' ? '▲ LONG' : '▼ SHORT';
        sigEl.textContent = `🚀 ${dir}`;
        sigEl.className   = f.direction === 'long'
            ? 'text-sm font-bold text-green-600'
            : 'text-sm font-bold text-red-600';
    } else {
        sigEl.textContent = '⏸ No signal';
        sigEl.className   = 'text-sm font-bold text-gray-400';
    }

    // State badge
    const badge = document.getElementById('rc-state-badge');
    const idleLabelMap = {
        max_trades:  '🚫 MAX TRADES',
        time_filter: '⏰ OFF HOURS',
        no_signal:   '⏸ NO SIGNAL',
        in_trade:    '📊 IN TRADE',
        '':          '⏸ IDLE',
    };
    const stateConfig = {
        idle:     { label: idleLabelMap[f.idle_reason] || '⏸ IDLE', cls:
                    f.idle_reason === 'max_trades'  ? 'bg-orange-100 text-orange-700'
                  : f.idle_reason === 'time_filter' ? 'bg-gray-100 text-gray-500'
                  : 'bg-gray-100 text-gray-600' },
        entry:    { label: '🟢 ENTRY',    cls: 'bg-green-100 text-green-700' },
        in_trade: { label: '📊 IN TRADE', cls: 'bg-blue-100 text-blue-700' },
        exit:     { label: '🔴 EXIT',     cls: 'bg-red-100 text-red-700' },
    };
    const sc = stateConfig[f.trade_state] || stateConfig.idle;
    badge.textContent = sc.label;
    badge.className   = `px-2 py-0.5 rounded-full text-xs font-bold ${sc.cls}`;

    // Card border color
    const card = document.getElementById('replay-candle-card');
    const borderMap = {
        idle:     'border-gray-200',
        entry:    'border-green-400',
        in_trade: 'border-blue-400',
        exit:     f.unrealized_pts >= 0 ? 'border-green-500' : 'border-red-500',
    };
    card.className = card.className.replace(/border-\w+-\d+/g, '');
    card.classList.add(borderMap[f.trade_state] || 'border-gray-200');

    // Trade info
    const tradeInfo = document.getElementById('rc-trade-info');
    if (f.trade_state !== 'idle') {
        tradeInfo.classList.remove('hidden');
        document.getElementById('rc-entry').textContent  = f.entry_price.toLocaleString();
        document.getElementById('rc-sl').textContent     = f.stop_loss.toLocaleString();
        document.getElementById('rc-target').textContent = f.target.toLocaleString();
    } else {
        tradeInfo.classList.add('hidden');
    }

    // P&L
    const unreal   = f.unrealized_pts;
    const cumul    = f.cumul_pts;
    const urEl     = document.getElementById('rc-unrealized');
    const cumEl    = document.getElementById('rc-cumul');
    urEl.textContent  = `${unreal >= 0 ? '+' : ''}${unreal} pts`;
    urEl.className    = `font-bold ${unreal >= 0 ? 'text-green-600' : 'text-red-600'}`;
    cumEl.textContent = `${cumul >= 0 ? '+' : ''}${cumul} pts`;
    cumEl.className   = `font-bold ${cumul >= 0 ? 'text-green-600' : 'text-red-600'}`;

    // Update chart vertical line
    _updateChartCursor(idx);
}

// ── Summary Cards ─────────────────────────────────────────────
function _renderSummary(summary) {
    const el = document.getElementById('replay-summary');
    const pnlColor = summary.total_pnl_pts >= 0 ? 'text-green-600' : 'text-red-600';
    el.innerHTML = `
        <div class="bg-gray-50 rounded-lg p-3 text-center border">
            <div class="text-[10px] text-gray-500">Trades</div>
            <div class="text-2xl font-black">${summary.total_trades}</div>
            <div class="text-[10px]"><span class="text-green-600 font-bold">${summary.winners}W</span> / <span class="text-red-600 font-bold">${summary.losers}L</span></div>
        </div>
        <div class="bg-gray-50 rounded-lg p-3 text-center border">
            <div class="text-[10px] text-gray-500">Day P&L</div>
            <div class="text-2xl font-black ${pnlColor}">${summary.total_pnl_pts >= 0 ? '+' : ''}${summary.total_pnl_pts} pts</div>
        </div>
        <div class="bg-gray-50 rounded-lg p-3 text-center border">
            <div class="text-[10px] text-gray-500">In Rupees</div>
            <div class="text-xl font-black ${pnlColor}">₹${summary.total_pnl_rupees >= 0 ? '+' : ''}${summary.total_pnl_rupees.toLocaleString()}</div>
        </div>
        <div class="bg-gray-50 rounded-lg p-3 text-center border">
            <div class="text-[10px] text-gray-500">Win Rate</div>
            <div class="text-2xl font-black ${summary.winners > summary.losers ? 'text-green-600' : 'text-red-600'}">
                ${summary.total_trades > 0 ? Math.round(summary.winners / summary.total_trades * 100) : 0}%
            </div>
        </div>
    `;
}

// ── Price Chart ───────────────────────────────────────────────
function _initPriceChart(frames, date) {
    const ctx = document.getElementById('replay-price-chart').getContext('2d');
    if (_replayChart) { _replayChart.destroy(); _replayChart = null; }

    const labels  = frames.map(f => f.time);
    const closes  = frames.map(f => f.close);

    // Entry / exit scatter points
    const entryLong  = frames.map((f, i) => f.trade_state === 'entry' && f.direction === 'long'  ? { x: i, y: f.close } : null).filter(Boolean);
    const entryShort = frames.map((f, i) => f.trade_state === 'entry' && f.direction === 'short' ? { x: i, y: f.close } : null).filter(Boolean);
    const exits      = frames.map((f, i) => f.trade_state === 'exit'  ? { x: i, y: f.close } : null).filter(Boolean);

    // SL / Target reference lines from first in-trade frame
    let slLine = null; let tgtLine = null;
    const inTradeFrames = frames.filter(f => f.trade_state !== 'idle' && f.stop_loss > 0);
    if (inTradeFrames.length) {
        const ref = inTradeFrames[0];
        slLine  = Array(frames.length).fill(ref.stop_loss);
        tgtLine = Array(frames.length).fill(ref.target);
    }

    const datasets = [
        {
            label: 'Close',
            data: closes,
            borderColor: '#0053e2',
            backgroundColor: 'rgba(0,83,226,0.07)',
            borderWidth: 2,
            fill: true,
            tension: 0.2,
            pointRadius: 0,
            pointHitRadius: 6,
            order: 4,
        },
        {
            label: 'Long Entry',
            data: entryLong,
            type: 'scatter',
            pointStyle: 'triangle',
            pointRadius: 12,
            backgroundColor: '#2a8703',
            borderColor: '#fff',
            borderWidth: 2,
            order: 1,
        },
        {
            label: 'Short Entry',
            data: entryShort,
            type: 'scatter',
            pointStyle: 'triangle',
            rotation: 180,
            pointRadius: 12,
            backgroundColor: '#ea1100',
            borderColor: '#fff',
            borderWidth: 2,
            order: 1,
        },
        {
            label: 'Exit',
            data: exits,
            type: 'scatter',
            pointStyle: 'crossRot',
            pointRadius: 10,
            backgroundColor: '#ffc220',
            borderColor: '#995213',
            borderWidth: 2,
            order: 1,
        },
    ];

    if (slLine) {
        datasets.push({
            label: 'Stop Loss',
            data: slLine,
            borderColor: '#ea1100',
            borderWidth: 1,
            borderDash: [4, 4],
            pointRadius: 0,
            fill: false,
            order: 3,
        });
        datasets.push({
            label: 'Target',
            data: tgtLine,
            borderColor: '#2a8703',
            borderWidth: 1,
            borderDash: [4, 4],
            pointRadius: 0,
            fill: false,
            order: 3,
        });
    }

    // Cursor line plugin
    const cursorPlugin = {
        id: 'cursorLine',
        afterDraw(chart) {
            if (chart._cursorIdx == null) return;
            const { ctx: c, chartArea, scales } = chart;
            const x = scales.x.getPixelForValue(chart._cursorIdx);
            c.save();
            c.beginPath();
            c.moveTo(x, chartArea.top);
            c.lineTo(x, chartArea.bottom);
            c.strokeStyle = 'rgba(99,102,241,0.6)';
            c.lineWidth   = 2;
            c.setLineDash([4, 3]);
            c.stroke();
            c.restore();
        },
    };

    _replayChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: { display: true, labels: { font: { size: 10 }, boxWidth: 12 } },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    callbacks: {
                        label: item => `${item.dataset.label}: ${item.raw?.y ?? item.raw}`,
                    },
                },
            },
            scales: {
                x: { ticks: { maxTicksLimit: 12, font: { size: 9 } }, grid: { display: false } },
                y: { ticks: { font: { size: 9 } }, grid: { color: '#f0f0f0' } },
            },
            onClick(event, elements) {
                if (!elements.length) return;
                const idx = elements[0].index;
                _stopTimer();
                _replayPaused = true;
                _replayIdx = idx;
                _renderFrame(idx);
                _updateProgress();
                _highlightTableRow(idx);
            },
        },
        plugins: [cursorPlugin],
    });
}

function _updateChartCursor(idx) {
    if (!_replayChart) return;
    _replayChart._cursorIdx = idx;
    _replayChart.update('none');
}

// ── Table ─────────────────────────────────────────────────────
function _buildTable(frames) {
    const tbody = document.getElementById('replay-table-body');
    tbody.innerHTML = frames.map((f, i) => {
        const idleLabel = {
            max_trades:  '<span class="bg-orange-100 text-orange-700 px-1.5 py-0.5 rounded font-bold text-[10px]">🚫 MAX</span>',
            time_filter: '<span class="text-gray-300 text-[10px]">⏰ off-hrs</span>',
            no_signal:   '<span class="text-gray-400 text-[10px]">⏸ wait</span>',
            '':          '<span class="text-gray-400 text-[10px]">idle</span>',
        };
        const stateMap = {
            idle:     (r) => idleLabel[r.idle_reason] || idleLabel[''],
            entry:    () => '<span class="bg-green-100 text-green-700 px-1.5 py-0.5 rounded font-bold">ENTRY</span>',
            in_trade: () => '<span class="bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded font-bold">TRADE</span>',
            exit:     () => '<span class="bg-red-100 text-red-700 px-1.5 py-0.5 rounded font-bold">EXIT</span>',
        };
        const sigHtml = f.signal_fires
            ? `<span class="text-${f.direction === 'long' ? 'green' : 'red'}-600 font-bold">${f.direction === 'long' ? '▲' : '▼'} ${f.confidence}%</span>`
            : '<span class="text-gray-300">—</span>';
        const urColor = f.unrealized_pts >= 0 ? 'text-green-600' : 'text-red-600';
        const cumColor = f.cumul_pts >= 0 ? 'text-green-600' : 'text-red-600';
        const rowBg = f.trade_state === 'entry' ? 'bg-green-50' :
                      f.trade_state === 'exit'  ? 'bg-red-50'   : '';
        return `
            <tr id="rrow-${i}" class="border-b border-gray-100 hover:bg-indigo-50 cursor-pointer transition ${rowBg}"
                onclick="_replayJumpTo(${i})">
                <td class="px-2 py-1.5 font-mono font-semibold">${f.time}</td>
                <td class="px-2 py-1.5 font-mono">${f.close.toLocaleString()}</td>
                <td class="px-2 py-1.5">${f.strategy_emoji} <span class="text-gray-600">${f.strategy_name.split(' ')[0]}</span></td>
                <td class="px-2 py-1.5">${sigHtml}</td>
                <td class="px-2 py-1.5">${(stateMap[f.trade_state] || stateMap.idle)(f)}</td>
                <td class="px-2 py-1.5 ${urColor} font-semibold">${f.unrealized_pts !== 0 ? (f.unrealized_pts > 0 ? '+' : '') + f.unrealized_pts : '—'}</td>
                <td class="px-2 py-1.5 ${cumColor} font-bold">${f.cumul_pts >= 0 ? '+' : ''}${f.cumul_pts}</td>
            </tr>
        `;
    }).join('');
}

function _highlightTableRow(idx) {
    document.querySelectorAll('[id^="rrow-"]').forEach(r => r.classList.remove('ring-2', 'ring-indigo-400'));
    const row = document.getElementById(`rrow-${idx}`);
    if (!row) return;
    row.classList.add('ring-2', 'ring-indigo-400');
    row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function _replayJumpTo(idx) {
    _stopTimer();
    _replayPaused = true;
    _replayIdx = idx;
    _renderFrame(idx);
    _updateProgress();
    _highlightTableRow(idx);
}

// ── Progress bar ──────────────────────────────────────────────
function _updateProgress() {
    const total = _replayFrames.length;
    const pct   = total ? Math.round((_replayIdx + 1) / total * 100) : 0;
    document.getElementById('replay-progress-bar').style.width = pct + '%';
    document.getElementById('replay-progress-label').textContent =
        `Candle ${_replayIdx + 1} / ${total}`;
}

// ── Helpers ───────────────────────────────────────────────────
function _fmtRegime(r) {
    const map = {
        trending_up:   '📈 Trending Up',
        trending_down: '📉 Trending Down',
        sideways:      '↔️ Sideways',
        volatile:      '⚡ Volatile',
    };
    return map[r] || r;
}

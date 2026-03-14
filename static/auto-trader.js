// ── Auto-Trader Controls & Renderer ──────────────────────────────

// Single source of truth — never read the DOM to decide state
let _atIsRunning   = false;
let _atKillSwitch  = false;
let _atPollTimer   = null;
const AT_POLL_MS   = 6000;   // poll every 6 s when tab is open

// ── Strategy dropdown ────────────────────────────────────────────
async function populateAutoTraderStrategies() {
    try {
        const resp = await fetch('/api/strategies');
        const data = await resp.json();
        if (!data.success) return;
        const select = document.getElementById('at-strategy');
        if (!select) return;
        select.innerHTML = data.strategies.map(s =>
            `<option value="${s.id}">${s.emoji} ${s.name}</option>`
        ).join('');
        select.value = 'orb';
    } catch (e) { console.error('Strategies load failed:', e); }
}
document.addEventListener('DOMContentLoaded', populateAutoTraderStrategies);

// ── Tab open / close hooks (called by dashboard.js switchPage) ───
function onAutoTraderTabOpen() {
    pollAutoTraderStatus();          // immediate refresh
    loadTradeHistory();
    _startAtPolling();
}
function onAutoTraderTabClose() {
    _stopAtPolling();
}

function _startAtPolling() {
    _stopAtPolling();
    _atPollTimer = setInterval(async () => {
        await pollAutoTraderStatus();
        await loadTradeHistory();
    }, AT_POLL_MS);
}
function _stopAtPolling() {
    if (_atPollTimer) { clearInterval(_atPollTimer); _atPollTimer = null; }
}

// ── Start ────────────────────────────────────────────────────────
async function startAutoTrader() {
    const strategyId = document.getElementById('at-strategy')?.value || 'orb';
    _setAtStatus('starting');
    try {
        const resp = await fetch(`/api/auto-trader/start?strategy=${strategyId}`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            _setAtStatus('evaluating');
            await fetch('/api/auto-trader/evaluate', { method: 'POST' });
            await pollAutoTraderStatus();
        } else {
            _setAtStatus('error', data.error || 'Failed to start');
        }
    } catch (e) {
        _setAtStatus('error', 'Connection error');
    }
}

// ── Stop ─────────────────────────────────────────────────────────
async function stopAutoTrader() {
    _setAtStatus('stopping');
    try {
        await fetch('/api/auto-trader/stop', { method: 'POST' });
        await pollAutoTraderStatus();
    } catch (e) {
        _setAtStatus('error', 'Stop failed');
    }
}

// ── Kill switch ──────────────────────────────────────────────────
async function killAutoTrader() {
    if (!confirm('🚨 KILL SWITCH: Exit all positions and halt trading?')) return;
    try {
        await fetch('/api/auto-trader/kill', { method: 'POST' });
        await pollAutoTraderStatus();
    } catch (e) { console.error('Kill failed:', e); }
}

// ── Status helper (sets transient UI states) ─────────────────────
function _setAtStatus(state, msg = '') {
    const statusEl  = document.getElementById('at-status');
    const startBtn  = document.getElementById('at-start-btn');
    const stopBtn   = document.getElementById('at-stop-btn');
    if (!statusEl || !startBtn || !stopBtn) return;

    const BTN = 'px-3 py-1.5 rounded-lg font-bold text-xs transition';

    const states = {
        starting:   { text: '⏳ Starting…',  cls: 'text-sm font-bold text-yellow-400 animate-pulse', startDis: true,  stopDis: true  },
        stopping:   { text: '⏳ Stopping…',  cls: 'text-sm font-bold text-yellow-400 animate-pulse', startDis: true,  stopDis: true  },
        evaluating: { text: '⚙️ Evaluating…', cls: 'text-sm font-bold text-blue-400 animate-pulse',  startDis: true,  stopDis: false },
        running:    { text: '▶ Running',      cls: 'text-sm font-bold text-green-400',                startDis: true,  stopDis: false },
        idle:       { text: '⏸ Idle',         cls: 'text-sm font-bold text-yellow-400',               startDis: false, stopDis: true  },
        killed:     { text: '🚨 KILLED',      cls: 'text-sm font-bold text-red-500 animate-pulse',   startDis: false, stopDis: true  },
        error:      { text: `❌ ${msg}`,      cls: 'text-sm font-bold text-red-400',                  startDis: false, stopDis: true  },
    };

    const s = states[state] || states.idle;
    statusEl.textContent = s.text;
    statusEl.className   = s.cls;

    startBtn.disabled  = s.startDis;
    stopBtn.disabled   = s.stopDis;
    startBtn.textContent = s.startDis && state === 'running' ? '▶ Running' : '▶ Start';
    startBtn.className = `${BTN} ${s.startDis ? 'bg-green-800 opacity-50 cursor-not-allowed' : 'bg-green-600 hover:bg-green-700'}`;
    stopBtn.className  = `${BTN} ${s.stopDis  ? 'bg-gray-700 opacity-50 cursor-not-allowed'  : 'bg-gray-500 hover:bg-gray-400'}`;
}

// ── Poll status from server (source of truth) ────────────────────
async function pollAutoTraderStatus() {
    try {
        const resp = await fetch('/api/auto-trader/status');
        const data = await resp.json();
        if (!data.success) return;

        // Update internal state
        _atIsRunning  = !!data.is_running;
        _atKillSwitch = !!data.kill_switch;

        // If running, get a fresh evaluate snapshot for signal details
        if (_atIsRunning) {
            try {
                const evResp = await fetch('/api/auto-trader/evaluate', { method: 'POST' });
                const evData = await evResp.json();
                if (evData.success) { renderAutoTrader(evData); return; }
            } catch (_) {}
        }
        renderAutoTrader(data);
    } catch (e) { /* network blip — stay silent */ }
}

// ── Full render (called with latest server data) ─────────────────
function renderAutoTrader(data) {
    _atIsRunning  = !!data.is_running;
    _atKillSwitch = !!data.kill_switch;

    // Mode badge
    const modeEl = document.getElementById('at-mode');
    if (modeEl) {
        if (data.is_paper_mode) {
            modeEl.textContent = '📝 PAPER';
            modeEl.className   = 'bg-yellow-600 text-xs px-2 py-0.5 rounded font-bold';
        } else {
            modeEl.textContent = '🟢 LIVE';
            modeEl.className   = 'bg-green-600 text-xs px-2 py-0.5 rounded font-bold';
        }
    }

    // Strategy select
    const stratSelect = document.getElementById('at-strategy');
    const stratLabel  = document.getElementById('at-strat-label');
    if (stratSelect) {
        stratSelect.disabled = _atIsRunning;
        if (_atIsRunning && data.selected_strategy) stratSelect.value = data.selected_strategy;
    }
    if (stratLabel && stratSelect) {
        const opt = stratSelect.options[stratSelect.selectedIndex];
        if (opt) stratLabel.textContent = opt.textContent;
    }

    // Status buttons — driven from server truth
    if (_atKillSwitch)    _setAtStatus('killed');
    else if (_atIsRunning) _setAtStatus('running');
    else                   _setAtStatus('idle');

    // P&L
    const pnlEl = document.getElementById('at-pnl');
    if (pnlEl) {
        pnlEl.textContent = `₹${data.total_pnl >= 0 ? '+' : ''}${data.total_pnl}`;
        pnlEl.className   = `text-sm font-bold ${data.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`;
    }

    const ordEl = document.getElementById('at-orders');
    if (ordEl) ordEl.textContent = `${data.orders_placed}/${data.max_orders}`;

    const exitEl = document.getElementById('at-exit-time');
    if (exitEl) exitEl.textContent = data.exit_time;

    // Last eval time
    if (data.last_evaluation) {
        const t = new Date(data.last_evaluation);
        const evalEl = document.getElementById('at-last-eval');
        if (evalEl) evalEl.textContent =
            `Last eval: ${t.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
    }

    // Signal / conditions
    const sigEl = document.getElementById('at-signal');
    if (sigEl) {
        let sigHTML = data.last_signal || '';
        if (data.conditions?.length) {
            sigHTML += '<div class="mt-2 space-y-1">';
            data.conditions.forEach(c => {
                const icon  = c.met ? '✅' : '❌';
                const color = c.met ? 'text-green-400' : 'text-red-400';
                sigHTML += `<div class="${color} text-xs">${icon} <strong>${c.name}</strong>: ${c.detail}</div>`;
            });
            sigHTML += '</div>';
        }
        sigEl.innerHTML = sigHTML;
    }

    // Active trade panel
    const tradeDetail = document.getElementById('at-trade-detail');
    const activeLabel = document.getElementById('at-active');
    if (data.active_trade) {
        if (tradeDetail) tradeDetail.classList.remove('hidden');
        const t = data.active_trade;
        if (activeLabel) {
            activeLabel.textContent = `${t.direction.toUpperCase()} ₹${t.entry_price}`;
            activeLabel.className   = `text-sm font-bold ${t.direction === 'long' ? 'text-green-400' : 'text-red-400'}`;
        }
        const dirEl = document.getElementById('at-dir');
        if (dirEl) { dirEl.textContent = t.direction === 'long' ? '⬆ LONG' : '⬇ SHORT'; dirEl.className = t.direction === 'long' ? 'text-green-400' : 'text-red-400'; }
        const setT = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
        setT('at-entry', `₹${t.entry_price}`);
        setT('at-sl',    `₹${t.stop_loss}`);
        setT('at-tgt',   `₹${t.target || '--'}`);
        const upnlEl = document.getElementById('at-upnl');
        if (upnlEl) {
            upnlEl.textContent = `₹${t.pnl_unrealized >= 0 ? '+' : ''}${t.pnl_unrealized}`;
            upnlEl.className   = t.pnl_unrealized >= 0 ? 'text-green-400' : 'text-red-400';
        }
    } else {
        if (tradeDetail) tradeDetail.classList.add('hidden');
        if (activeLabel) { activeLabel.textContent = 'None'; activeLabel.className = 'text-sm font-bold text-gray-500'; }
    }
}

// ── Trade history ────────────────────────────────────────────────
async function loadTradeHistory() {
    try {
        const resp = await fetch('/api/auto-trader/history');
        const data = await resp.json();
        if (data.success) renderTradeHistory(data);
    } catch (e) { /* silent */ }
}

function renderTradeHistory(data) {
    const trades   = data.trades || [];
    const totalPnl = data.total_pnl || 0;
    const wins     = trades.filter(t => t.pnl > 0).length;
    const losses   = trades.filter(t => t.pnl < 0).length;

    const setT = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    setT('ath-count',  trades.length);
    setT('ath-wins',   wins);
    setT('ath-losses', losses);

    const pnlEl = document.getElementById('ath-pnl');
    if (pnlEl) {
        pnlEl.textContent = `₹${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(0)}`;
        pnlEl.className   = `text-xs font-bold ${totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}`;
    }

    const container = document.getElementById('at-history-table');
    if (!container) return;
    if (!trades.length) {
        container.innerHTML = '<p class="text-gray-600 text-center py-3">No trades yet</p>';
        return;
    }

    const rows = [...trades].reverse().map(t => {
        const pnlColor = t.pnl > 0 ? 'text-green-400' : t.pnl < 0 ? 'text-red-400' : 'text-gray-400';
        const dirIcon  = t.direction === 'long' ? '⬆' : '⬇';
        const dirColor = t.direction === 'long' ? 'text-green-400' : 'text-red-400';
        const time     = t.timestamp ? (t.timestamp.split('T')[1]?.substring(0, 8) || t.timestamp) : '--';
        const exitPx   = t.exit_price ? `₹${Number(t.exit_price).toFixed(0)}` : '--';
        const reason   = (t.exit_reason || '').substring(0, 25);
        const emoji    = t.pnl > 0 ? '🟢' : t.pnl < 0 ? '🔴' : '⚪';
        return `<tr class="border-t border-gray-800 hover:bg-gray-800/50">
            <td class="py-1.5 text-gray-400">${time}</td>
            <td class="text-center ${dirColor} font-bold">${dirIcon}</td>
            <td class="text-center">₹${Number(t.entry_price).toFixed(0)}</td>
            <td class="text-center">${exitPx}</td>
            <td class="text-center font-bold ${pnlColor}">${emoji} ₹${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(0)}</td>
            <td class="text-gray-500 truncate max-w-[120px]" title="${t.exit_reason || ''}">${reason}</td>
        </tr>`;
    }).join('');

    container.innerHTML = `<table class="w-full" role="table">
        <thead class="text-[10px] text-gray-500 uppercase">
            <tr><th class="text-left py-1">Time</th><th>Dir</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Reason</th></tr>
        </thead><tbody>${rows}</tbody></table>`;
}

// ── Init ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        pollAutoTraderStatus();
        loadTradeHistory();
    }, 1500);
});
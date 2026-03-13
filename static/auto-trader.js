// ── Auto-Trader Controls & Renderer ─────────────────────────────

// Populate auto-trader strategy dropdown on load
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
        // Default to ORB for scalping
        select.value = 'orb';
    } catch (e) { console.error('Failed to load strategies for AT:', e); }
}
document.addEventListener('DOMContentLoaded', populateAutoTraderStrategies);

async function startAutoTrader() {
    const btn = document.getElementById('at-start-btn');
    const statusEl = document.getElementById('at-status');
    const strategyId = document.getElementById('at-strategy')?.value || 'smart_router';
    btn.disabled = true;
    btn.textContent = '⏳ Starting...';
    btn.className = 'bg-yellow-600 px-4 py-2 rounded-lg font-bold text-sm animate-pulse';
    statusEl.textContent = '⏳ Starting...';
    statusEl.className = 'text-sm font-bold text-yellow-400 animate-pulse';
    try {
        const resp = await fetch(`/api/auto-trader/start?strategy=${strategyId}`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            statusEl.textContent = '⚙️ Evaluating...';
            await fetch('/api/auto-trader/evaluate', { method: 'POST' });
            await pollAutoTraderStatus();
        }
    } catch (e) {
        console.error('Start failed:', e);
        statusEl.textContent = '❌ Failed';
        statusEl.className = 'text-sm font-bold text-red-400';
    }
    _updateAutoTraderButtons();
}

async function stopAutoTrader() {
    const btn = document.getElementById('at-stop-btn');
    btn.disabled = true;
    btn.textContent = '⏳ Stopping...';
    try {
        await fetch('/api/auto-trader/stop', { method: 'POST' });
        await pollAutoTraderStatus();
    } catch (e) { console.error('Stop failed:', e); }
    _updateAutoTraderButtons();
}

async function killAutoTrader() {
    if (!confirm('🚨 KILL SWITCH: Exit all positions and halt trading?')) return;
    try {
        await fetch('/api/auto-trader/kill', { method: 'POST' });
        await pollAutoTraderStatus();
    } catch (e) { console.error('Kill failed:', e); }
}

function _updateAutoTraderButtons() {
    const startBtn = document.getElementById('at-start-btn');
    const stopBtn = document.getElementById('at-stop-btn');
    const isRunning = document.getElementById('at-status')?.textContent?.includes('Running');
    startBtn.disabled = isRunning;
    stopBtn.disabled = !isRunning;
    startBtn.textContent = isRunning ? '▶ Running' : '▶ Start';
    stopBtn.textContent = isRunning ? '⏹ Stop' : '⏹ Stop';
    startBtn.className = isRunning
        ? 'bg-green-800 px-4 py-2 rounded-lg font-bold text-sm opacity-60 cursor-not-allowed'
        : 'bg-green-600 hover:bg-green-700 px-4 py-2 rounded-lg font-bold text-sm transition';
    stopBtn.className = !isRunning
        ? 'bg-gray-700 px-4 py-2 rounded-lg font-bold text-sm opacity-60 cursor-not-allowed'
        : 'bg-gray-600 hover:bg-gray-500 px-4 py-2 rounded-lg font-bold text-sm transition';
}

async function pollAutoTraderStatus() {
    try {
        // If running, trigger evaluation with fresh data first
        const statusResp = await fetch('/api/auto-trader/status');
        const statusData = await statusResp.json();
        if (statusData.success && statusData.is_running) {
            const evalResp = await fetch('/api/auto-trader/evaluate', { method: 'POST' });
            const evalData = await evalResp.json();
            if (evalData.success) { renderAutoTrader(evalData); return; }
        }
        if (statusData.success) renderAutoTrader(statusData);
    } catch (e) { /* silent */ }
}

function renderAutoTrader(data) {
    // Mode badge
    const modeEl = document.getElementById('at-mode');
    if (data.is_paper_mode) {
        modeEl.textContent = '📝 PAPER';
        modeEl.className = 'bg-yellow-600 text-xs px-2 py-0.5 rounded font-bold';
    } else {
        modeEl.textContent = '🟢 LIVE';
        modeEl.className = 'bg-green-600 text-xs px-2 py-0.5 rounded font-bold';
    }

    // Strategy badge
    if (data.selected_strategy) {
        const stratSelect = document.getElementById('at-strategy');
        const stratLabel = document.getElementById('at-strat-label');
        if (stratSelect && !data.is_running) {
            stratSelect.disabled = false;
        } else if (stratSelect && data.is_running) {
            stratSelect.value = data.selected_strategy;
            stratSelect.disabled = true;
        }
        // Update the status grid label
        if (stratLabel && stratSelect) {
            const opt = stratSelect.options[stratSelect.selectedIndex];
            stratLabel.textContent = opt ? opt.textContent : data.selected_strategy;
        }
    }

    // Status
    const statusEl = document.getElementById('at-status');
    if (data.kill_switch) {
        statusEl.textContent = '🚨 KILLED';
        statusEl.className = 'text-sm font-bold text-red-500 animate-pulse';
    } else if (data.is_running) {
        statusEl.textContent = '▶ Running';
        statusEl.className = 'text-sm font-bold text-green-400';
    } else {
        statusEl.textContent = '⏸ Idle';
        statusEl.className = 'text-sm font-bold text-yellow-400';
    }

    // P&L
    const pnlEl = document.getElementById('at-pnl');
    pnlEl.textContent = `₹${data.total_pnl >= 0 ? '+' : ''}${data.total_pnl}`;
    pnlEl.className = `text-sm font-bold ${data.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`;

    // Orders
    document.getElementById('at-orders').textContent = `${data.orders_placed}/${data.max_orders}`;
    document.getElementById('at-exit-time').textContent = data.exit_time;

    // Last eval time
    if (data.last_evaluation) {
        const t = new Date(data.last_evaluation);
        document.getElementById('at-last-eval').textContent =
            `Last eval: ${t.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
    }

    // Signal reason + conditions
    const sigEl = document.getElementById('at-signal');
    let sigHTML = data.last_signal || '';
    if (data.conditions && data.conditions.length) {
        sigHTML += '<div class="mt-2 space-y-1">';
        data.conditions.forEach(c => {
            const icon = c.met ? '\u2705' : '\u274c';
            const color = c.met ? 'text-green-400' : 'text-red-400';
            sigHTML += `<div class="${color} text-xs">${icon} <strong>${c.name}</strong>: ${c.detail}</div>`;
        });
        sigHTML += '</div>';
    }
    sigEl.innerHTML = sigHTML;

    // Active trade
    const tradeDetail = document.getElementById('at-trade-detail');
    const activeLabel = document.getElementById('at-active');
    if (data.active_trade) {
        tradeDetail.classList.remove('hidden');
        const t = data.active_trade;
        activeLabel.textContent = `${t.direction.toUpperCase()} ₹${t.entry_price}`;
        activeLabel.className = `text-sm font-bold ${t.direction === 'long' ? 'text-green-400' : 'text-red-400'}`;

        document.getElementById('at-dir').textContent = t.direction === 'long' ? '⬆ LONG' : '⬇ SHORT';
        document.getElementById('at-dir').className = t.direction === 'long' ? 'text-green-400' : 'text-red-400';
        document.getElementById('at-entry').textContent = `₹${t.entry_price}`;
        document.getElementById('at-sl').textContent = `₹${t.stop_loss}`;
        document.getElementById('at-tgt').textContent = `₹${t.target || '--'}`;

        const upnlEl = document.getElementById('at-upnl');
        upnlEl.textContent = `₹${t.pnl_unrealized >= 0 ? '+' : ''}${t.pnl_unrealized}`;
        upnlEl.className = t.pnl_unrealized >= 0 ? 'text-green-400' : 'text-red-400';
    } else {
        tradeDetail.classList.add('hidden');
        activeLabel.textContent = 'None';
        activeLabel.className = 'text-sm font-bold text-gray-500';
    }

    // Update button states
    _updateAutoTraderButtons();
}

// ── Trade History ─────────────────────────────────────────

async function loadTradeHistory() {
    try {
        const resp = await fetch('/api/auto-trader/history');
        const data = await resp.json();
        if (!data.success) return;
        renderTradeHistory(data);
    } catch (e) { console.error('History load failed:', e); }
}

function renderTradeHistory(data) {
    const trades = data.trades || [];
    const totalPnl = data.total_pnl || 0;

    // Summary
    const wins = trades.filter(t => t.pnl > 0).length;
    const losses = trades.filter(t => t.pnl < 0).length;

    document.getElementById('ath-count').textContent = trades.length;

    const pnlEl = document.getElementById('ath-pnl');
    pnlEl.textContent = `\u20b9${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(0)}`;
    pnlEl.className = `text-xs font-bold ${totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}`;

    document.getElementById('ath-wins').textContent = wins;
    document.getElementById('ath-losses').textContent = losses;

    // Table
    const container = document.getElementById('at-history-table');
    if (!trades.length) {
        container.innerHTML = '<p class="text-gray-600 text-center py-3">No trades yet</p>';
        return;
    }

    let html = `<table class="w-full" role="table">
        <thead class="text-[10px] text-gray-500 uppercase">
            <tr><th class="text-left py-1">Time</th><th>Dir</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Reason</th></tr>
        </thead><tbody>`;

    // Show most recent first
    const reversed = [...trades].reverse();
    for (const t of reversed) {
        const pnlColor = t.pnl > 0 ? 'text-green-400' : t.pnl < 0 ? 'text-red-400' : 'text-gray-400';
        const dirIcon = t.direction === 'long' ? '\u2b06' : '\u2b07';
        const dirColor = t.direction === 'long' ? 'text-green-400' : 'text-red-400';
        const time = t.timestamp ? t.timestamp.split('T')[1]?.substring(0, 8) || t.timestamp : '--';
        const exitPrice = t.exit_price ? `\u20b9${Number(t.exit_price).toFixed(0)}` : '--';
        const reason = (t.exit_reason || '').replace(/[\u2014—]/g, '-').substring(0, 25);
        const emoji = t.pnl > 0 ? '\ud83d\udfe2' : t.pnl < 0 ? '\ud83d\udd34' : '\u26aa';

        html += `<tr class="border-t border-gray-800 hover:bg-gray-800/50">
            <td class="py-1.5 text-gray-400">${time}</td>
            <td class="text-center ${dirColor} font-bold">${dirIcon}</td>
            <td class="text-center">\u20b9${Number(t.entry_price).toFixed(0)}</td>
            <td class="text-center">${exitPrice}</td>
            <td class="text-center font-bold ${pnlColor}">${emoji} \u20b9${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(0)}</td>
            <td class="text-gray-500 truncate max-w-[120px]" title="${t.exit_reason || ''}">${reason}</td>
        </tr>`;
    }

    html += '</tbody></table>';
    container.innerHTML = html;
}

// Load history when auto-trader page is shown
const _origSwitchPage = typeof switchPage === 'function' ? switchPage : null;
if (_origSwitchPage) {
    const _wrappedSwitch = _origSwitchPage;
    // We'll call loadTradeHistory from pollAutoTraderStatus instead
}

// Also load history after polling status
const _origPoll = pollAutoTraderStatus;
pollAutoTraderStatus = async function() {
    await _origPoll();
    await loadTradeHistory();
};

// Load on page init
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(loadTradeHistory, 2000);
});

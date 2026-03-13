// ── Auto-Trader Controls & Renderer ─────────────────────────────

async function startAutoTrader() {
    const btn = document.getElementById('at-start-btn');
    const statusEl = document.getElementById('at-status');
    btn.disabled = true;
    btn.textContent = '⏳ Starting...';
    btn.className = 'bg-yellow-600 px-4 py-2 rounded-lg font-bold text-sm animate-pulse';
    statusEl.textContent = '⏳ Starting...';
    statusEl.className = 'text-sm font-bold text-yellow-400 animate-pulse';
    try {
        const resp = await fetch('/api/auto-trader/start', { method: 'POST' });
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

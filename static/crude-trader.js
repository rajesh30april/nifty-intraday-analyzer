/**
 * crude-trader.js — UI logic for the MCX Crude Oil Auto-Trader tab.
 *
 * Polls /api/crude/status every 5s when the tab is visible.
 * All DOM IDs are prefixed 'crude-' to avoid collisions with Nifty AT.
 */

'use strict';

// ── State ─────────────────────────────────────────────────────────
let _crudePoller      = null;
let _crudeRunning     = false;
let _crudeLastSignal  = null;   // dedup signal logs
let _crudeLastBlock   = null;   // dedup block_reason logs
let _crudeLastTrade   = null;   // dedup active trade logs

// ── Toast ─────────────────────────────────────────────────────────
function _crudeToast(msg, type = 'info') {
    const container = document.getElementById('crude-toasts');
    if (!container) return;
    const colors = {
        info:  'bg-blue-800 border-blue-400',
        error: 'bg-red-900 border-red-500',
        warn:  'bg-yellow-700 border-yellow-400',
        ok:    'bg-green-700 border-green-400',
    };
    const cls = colors[type] || colors.info;
    const el  = document.createElement('div');
    el.className = `pointer-events-auto ${cls} border text-white rounded-xl px-4 py-3 shadow-2xl text-sm`;
    el.innerHTML = `<div class="flex justify-between gap-3">
        <span>${msg}</span>
        <button onclick="this.closest('div').parentElement.remove()" class="text-white/60 hover:text-white">&times;</button>
    </div>`;
    container.appendChild(el);
    setTimeout(() => el.remove(), 6000);
}

// ── Event log ────────────────────────────────────────────────────
function _crudeLog(msg, type = 'info') {
    const log = document.getElementById('crude-event-log');
    if (!log) return;
    const colors = {
        info:  'text-blue-400',
        ok:    'text-green-400',
        warn:  'text-yellow-400',
        error: 'text-red-400',
        trade: 'text-spark-100',
    };
    const now = new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const row = document.createElement('div');
    row.className = 'flex gap-2 items-start';
    row.innerHTML = `<span class="text-gray-600 shrink-0">${now}</span>
        <span class="${colors[type] ?? colors.info} break-all">${msg}</span>`;
    log.prepend(row);
    // cap at 50 entries
    while (log.children.length > 50) log.lastChild.remove();
}

// ── Button state management (mirrors auto-trader.js _setAtStatus) ──
function _setCrudeStatus(s) {
    const startBtn  = document.getElementById('crude-start-btn');
    const stopBtn   = document.getElementById('crude-stop-btn');
    const killBtn   = document.getElementById('crude-kill-btn');
    const statusEl  = document.getElementById('crude-status');
    if (!startBtn || !stopBtn) return;

    // Exact same pattern as Nifty AT _setAtStatus
    const BTN = 'px-3 py-1.5 rounded-lg font-bold text-xs transition';
    const STATES = {
        idle:     { startDis: false, stopDis: true,  killDis: true,  txt: '⏸ Idle',        cls: 'text-yellow-400' },
        loading:  { startDis: true,  stopDis: true,  killDis: true,  txt: '⏳ Loading…',    cls: 'text-yellow-400 animate-pulse' },
        running:  { startDis: true,  stopDis: false, killDis: false, txt: '▶ Running',      cls: 'text-green-400' },
        stopping: { startDis: true,  stopDis: true,  killDis: true,  txt: '⏳ Stopping…',   cls: 'text-yellow-400 animate-pulse' },
        killed:   { startDis: false, stopDis: true,  killDis: true,  txt: '🚨 KILLED',      cls: 'text-red-500 animate-pulse' },
        error:    { startDis: false, stopDis: true,  killDis: true,  txt: '❌ Error',        cls: 'text-red-400' },
    };
    const st = STATES[s] ?? STATES.idle;

    startBtn.disabled    = st.startDis;
    startBtn.textContent = st.startDis && s === 'running' ? '▶ Running' : '▶ Start';
    startBtn.className   = `${BTN} ${st.startDis ? 'bg-green-800 opacity-50 cursor-not-allowed' : 'bg-green-600 hover:bg-green-700'}`;

    stopBtn.disabled     = st.stopDis;
    stopBtn.textContent  = '⏹ Stop';
    stopBtn.className    = `${BTN} ${st.stopDis ? 'bg-gray-700 opacity-50 cursor-not-allowed' : 'bg-gray-500 hover:bg-gray-400'}`;

    if (killBtn) {
        killBtn.disabled   = st.killDis;
        killBtn.textContent = '🚨 Kill';
        killBtn.className  = `${BTN} ${st.killDis ? 'bg-gray-700 opacity-50 cursor-not-allowed' : 'bg-red-700 hover:bg-red-600'}`;
    }

    if (statusEl) {
        statusEl.textContent = st.txt;
        statusEl.className   = `text-xs font-bold ${st.cls}`;
    }
}

// ── Controls ──────────────────────────────────────────────────────
async function crudeTrade(action) {
    const label = { start: '▶ Start', stop: '⏹ Stop', kill: '🚨 Kill' };
    // show transitional state immediately — same UX as Nifty AT
    _setCrudeStatus(action === 'stop' || action === 'kill' ? 'stopping' : 'loading');
    _crudeToast(`${label[action] ?? action}ing Crude trader…`, 'info');
    _crudeLog(`🖱 ${label[action] ?? action} clicked`, 'info');
    try {
        const resp = await fetch(`/api/crude/${action}`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            const mode = data.mode ? ` [${data.mode}]` : '';
            _crudeToast(`✅ ${action.charAt(0).toUpperCase() + action.slice(1)} OK${mode}`, 'ok');
            _crudeLog(`✅ ${action.toUpperCase()} confirmed${mode}`, 'ok');
            await pollCrudeStatus();
        } else {
            const err = data.error ?? 'Failed';
            _crudeToast(`❌ ${err}`, 'error');
            _crudeLog(`❌ ${action.toUpperCase()} failed: ${err}`, 'error');
            _setCrudeStatus('error');
            await pollCrudeStatus();
        }
    } catch (e) {
        _crudeToast(`❌ ${e.message}`, 'error');
        _crudeLog(`❌ Network error: ${e.message}`, 'error');
        _setCrudeStatus('error');
        await pollCrudeStatus();
    }
}

async function saveCrudeConfig() {
    const sl      = parseFloat(document.getElementById('crude-sl')?.value);
    const trail   = parseFloat(document.getElementById('crude-trail')?.value);
    const rr      = parseFloat(document.getElementById('crude-rr')?.value);
    const capital = parseFloat(document.getElementById('crude-capital')?.value);

    if ([sl, trail, rr, capital].some(isNaN)) {
        _crudeToast('⚠️ Invalid settings — check all fields', 'warn');
        return;
    }
    if (trail >= sl) {
        _crudeToast('⚠️ Trail must be smaller than SL', 'warn');
        return;
    }
    const params = new URLSearchParams({ sl_points: sl, trail_points: trail, rr_ratio: rr, capital });
    try {
        const resp = await fetch(`/api/crude/config?${params}`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            _crudeToast(`✅ Saved — SL:₹${sl} Trail:₹${trail} R:R 1:${rr}`, 'ok');
        } else {
            _crudeToast('❌ Save failed', 'error');
        }
    } catch (e) {
        _crudeToast(`❌ ${e.message}`, 'error');
    }
}

// ── Status poll ───────────────────────────────────────────────────
function _fmt(v, prefix = '₹') {
    if (v == null || v === '' || v === 0 && prefix === '') return '--';
    if (v == null) return '--';
    return `${prefix}${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 1 })}`;
}

function _pnlClass(v) {
    if (v == null) return 'text-gray-400';
    return v > 0 ? 'text-green-400' : v < 0 ? 'text-red-400' : 'text-gray-400';
}

function renderCrudeStatus(d) {
    // ── Button + status label — exact same logic as Nifty AT ─────
    if (d.kill_switch)     _setCrudeStatus('killed');
    else if (d.is_running) _setCrudeStatus('running');
    else                   _setCrudeStatus('idle');

    // ── State-change event log entries ────────────────────────────
    const wasRunning = _crudeRunning;
    if (d.is_running !== wasRunning) {
        if (d.is_running)  _crudeLog('▶ Crude trader STARTED', 'ok');
        else               _crudeLog('⏹ Crude trader STOPPED', 'warn');
    }
    if (d.kill_switch && wasRunning) _crudeLog('🚨 Kill switch activated — position exited', 'error');

    // ── Signal / block reason changes ────────────────────────────
    const sig = d.last_signal || '';
    const blk = d.block_reason || '';
    if (sig && sig !== _crudeLastSignal) {
        _crudeLog(`📡 Signal: ${sig}`, sig.startsWith('[ST]') || sig.startsWith('[ORB]') ? 'trade' : 'info');
        _crudeLastSignal = sig;
    }
    if (blk && blk !== _crudeLastBlock) {
        _crudeLog(`⛔ Blocked: ${blk}`, 'warn');
        _crudeLastBlock = blk;
    }

    // ── Active trade change ───────────────────────────────────────
    const tradeId = d.active_trade?.id ?? null;
    if (tradeId !== _crudeLastTrade) {
        if (tradeId) {
            const at = d.active_trade;
            _crudeLog(`🛢️ Trade OPEN: ${at.direction?.toUpperCase()} @ ₹${at.entry_price} | SL ₹${at.stop_loss} | Tgt ₹${at.target}`, 'trade');
        } else if (_crudeLastTrade) {
            _crudeLog('🏁 Trade CLOSED', 'ok');
        }
        _crudeLastTrade = tradeId;
    }

    // ── Mode badge ────────────────────────────────────────────────
    const badge = document.getElementById('crude-mode-badge');
    if (badge) {
        badge.textContent  = d.is_paper_mode ? '📝 PAPER' : '🟢 LIVE';
        badge.className    = `text-xs px-2 py-0.5 rounded font-bold ${d.is_paper_mode ? 'bg-yellow-600' : 'bg-green-700'}`;
    }

    // ── Status dot ────────────────────────────────────────────────
    const dot = document.getElementById('crude-status-dot');
    if (dot) {
        dot.className = `w-2.5 h-2.5 rounded-full ${
            d.kill_switch ? 'bg-red-500' :
            d.is_running  ? 'bg-green-400 animate-pulse' : 'bg-gray-400'
        }`;
    }

    // ── Sidebar badge ─────────────────────────────────────────────
    const sb = document.getElementById('sidebar-crude-badge');
    if (sb) {
        sb.textContent = d.is_running ? '🟢' : '⏸';
        sb.className   = `badge ${d.is_running ? 'bg-green-600 text-white' : 'bg-gray-200 text-gray-500'}`;
    }

    // ── Live price strip ──────────────────────────────────────────
    _setText('crude-spot',       d.crude_price ? `₹${d.crude_price}` : '--');
    _setText('crude-option-ltp', _fmt(d.last_option_ltp));
    _setText('crude-signal',     d.block_reason || d.last_signal || '--');

    // ── P&L ───────────────────────────────────────────────────────
    const pnlEl = document.getElementById('crude-pnl');
    const at    = d.active_trade;
    if (pnlEl) {
        const pnl = at?.pnl_unrealized;
        pnlEl.textContent = pnl != null ? `₹${pnl > 0 ? '+' : ''}${pnl.toFixed(0)}` : '--';
        pnlEl.className   = _pnlClass(pnl);
    }
    const tpnlEl = document.getElementById('crude-total-pnl');
    if (tpnlEl) {
        const tp = d.total_pnl;
        tpnlEl.textContent = tp != null ? `₹${tp > 0 ? '+' : ''}${tp.toFixed(0)}` : '--';
        tpnlEl.className   = _pnlClass(tp);
    }

    // ── Active trade card ─────────────────────────────────────────
    const card = document.getElementById('crude-trade-card');
    if (card) {
        if (at) {
            card.classList.remove('hidden');
            const dir = at.direction?.toUpperCase();
            _setText('ct-dir',   dir, dir === 'LONG' ? 'text-green-400 font-bold' : 'text-red-400 font-bold');
            const instrEl = document.getElementById('ct-instr');
            if (instrEl) { instrEl.textContent = at.instrument?.replace('MCX:', ''); instrEl.title = at.instrument; }
            _setText('ct-entry', _fmt(at.entry_price));
            _setText('ct-sl',    _fmt(at.stop_loss));
            _setText('ct-tgt',   _fmt(at.target));
            _setText('ct-qty',   at.quantity ?? '--');
        } else {
            card.classList.add('hidden');
        }
    }

    _crudeRunning = d.is_running;
}

function _setText(id, val, cls = '') {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = val;
    if (cls) el.className = cls;
}

async function pollCrudeStatus() {
    try {
        const resp = await fetch('/api/crude/status');
        if (!resp.ok) return;
        const data = await resp.json();
        renderCrudeStatus(data);
    } catch (_) { /* ignore network blips */ }
}

// ── Trade history ─────────────────────────────────────────────────
async function loadCrudeHistory() {
    const el = document.getElementById('crude-history');
    if (!el) return;
    try {
        const resp = await fetch('/api/crude/history');
        const data = await resp.json();
        if (!data.trades?.length) {
            el.innerHTML = '<span class="text-gray-500">No trades yet today.</span>';
            return;
        }
        const rows = data.trades.map(t => {
            const pnl    = t.pnl ?? 0;
            const pnlCls = pnl > 0 ? 'text-green-400' : pnl < 0 ? 'text-red-400' : 'text-gray-400';
            return `<div class="flex gap-3 items-center border-b border-gray-800 py-1.5">
                <span class="${t.direction === 'long' ? 'text-green-400' : 'text-red-400'} font-bold w-12">
                    ${t.direction?.toUpperCase()}</span>
                <span class="flex-1 truncate text-gray-300">${t.instrument?.replace('MCX:','')}</span>
                <span class="text-gray-400">${t.exit_reason ?? '--'}</span>
                <span class="${pnlCls} font-bold w-20 text-right">
                    ₹${pnl > 0 ? '+' : ''}${pnl.toFixed(0)}</span>
            </div>`;
        }).join('');
        el.innerHTML = rows;
    } catch (e) {
        el.innerHTML = `<span class="text-red-400">Error: ${e.message}</span>`;
    }
}

// ── Lifecycle (called by dashboard.js switchPage) ─────────────────
function onCrudeTraderTabOpen() {
    _crudeLog('👁 Crude trader tab opened', 'info');
    pollCrudeStatus();
    loadCrudeHistory();
    _crudePoller = setInterval(pollCrudeStatus, 5000);
}

function onCrudeTraderTabClose() {
    clearInterval(_crudePoller);
    _crudePoller = null;
}

// Register with switchPage
document.addEventListener('DOMContentLoaded', () => {
    // Patch dashboard switchPage to call crude lifecycle hooks
    const _origSwitch = window.switchPage;
    if (typeof _origSwitch === 'function') {
        window.switchPage = function(pageId) {
            if (pageId !== 'crude-trader') onCrudeTraderTabClose();
            _origSwitch(pageId);
            if (pageId === 'crude-trader') onCrudeTraderTabOpen();
        };
    }
});
/**
 * crude-trader.js — UI logic for the MCX Crude Oil Auto-Trader tab.
 *
 * Polls /api/crude/status every 5s when the tab is visible.
 * All DOM IDs are prefixed 'crude-' to avoid collisions with Nifty AT.
 */

'use strict';

// ── State ─────────────────────────────────────────────────────────
let _crudePoller  = null;
let _crudeRunning = false;

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

// ── Controls ──────────────────────────────────────────────────────
async function crudeTrade(action) {
    const label = { start: '▶ Start', stop: '⏹ Stop', kill: '🚨 Kill' };
    _crudeToast(`${label[action] ?? action}ing Crude trader…`, 'info');
    try {
        const resp = await fetch(`/api/crude/${action}`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            _crudeToast(`✅ ${action.charAt(0).toUpperCase() + action.slice(1)} OK`, 'ok');
            await pollCrudeStatus();
        } else {
            _crudeToast(`❌ ${data.error ?? 'Failed'}`, 'error');
        }
    } catch (e) {
        _crudeToast(`❌ ${e.message}`, 'error');
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
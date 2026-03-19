// ── Auto-Trader Controls & Renderer ──────────────────────────────

// Single source of truth
let _atIsRunning       = false;
let _atKillSwitch      = false;
let _atPollTimer       = null;
let _atHadTrade        = false;
let _atQtyMode         = 'manual';   // 'manual' | 'capital'
const AT_POLL_MS       = 3000;  // poll every 3s — fast enough without hammering server
const LOT_SIZE         = 65;         // Nifty F&O lot size (65 units/lot, Apr 2025)

// ── Event log dedup trackers ──────────────────────────────────────
let _atLastCondKey     = null;   // JSON key of which conditions were met last render
let _atLastSignalText  = null;   // last signal summary logged
let _atLastEvalTime    = null;   // last evaluation timestamp logged
const AT_LOG_THROTTLE  = 60;     // minimum seconds between signal-summary logs

// ── Toast system ─────────────────────────────────────────────────
function showAtToast(type, title, body) {
    const container = document.getElementById('at-toasts');
    if (!container) return;

    const colors = {
        entry:   { bg:'bg-green-700',  border:'border-green-400',  icon:'🟢' },
        exit_win:{ bg:'bg-green-800',  border:'border-green-300',  icon:'💰' },
        exit_loss:{bg:'bg-red-800',    border:'border-red-400',    icon:'🔴' },
        exit_be: { bg:'bg-gray-700',   border:'border-gray-400',   icon:'⚪' },
        info:    { bg:'bg-blue-800',   border:'border-blue-400',   icon:'ℹ️' },
        warn:    { bg:'bg-yellow-700', border:'border-yellow-400', icon:'⚠️' },
        error:   { bg:'bg-red-900',    border:'border-red-500',    icon:'❌' },
    };
    const c = colors[type] || colors.info;
    const now = new Date().toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit', second:'2-digit' });

    const el = document.createElement('div');
    el.className = `pointer-events-auto ${c.bg} border ${c.border} text-white rounded-xl px-4 py-3 shadow-2xl`;
    el.style.cssText = 'animation:slideInRight .3s ease both';
    el.innerHTML = `
        <div class="flex items-start gap-3">
            <span class="text-xl mt-0.5">${c.icon}</span>
            <div class="flex-1 min-w-0">
                <div class="font-black text-sm">${title}</div>
                <div class="text-xs opacity-80 mt-0.5">${body}</div>
                <div class="text-[10px] opacity-50 mt-1">${now}</div>
            </div>
            <button onclick="this.closest('div[class]').remove()" class="text-white/60 hover:text-white text-lg leading-none">&times;</button>
        </div>`;

    container.appendChild(el);

    // Auto-dismiss after 6s
    setTimeout(() => {
        el.style.animation = 'slideOutRight .3s ease both';
        setTimeout(() => el.remove(), 300);
    }, 6000);

    // Also log to event log
    _atLogEvent(c.icon, title, body);
}

/** Convenience wrapper — _atShowToast(message, type)
 *  Adapts the simple 2-arg call used across at-settings.js and
 *  syncFromZerodha() to the full showAtToast(type, title, body) API.
 */
function _atShowToast(msg, type = 'info') {
    showAtToast(type, msg, '');
}

// ── Event log ────────────────────────────────────────────────────
// ts: optional ISO timestamp from server — use it so log shows actual
//     candle-close time, not the random poll-detection time.
function _atLogEvent(icon, title, detail, ts) {
    const log = document.getElementById('at-event-log');
    if (!log) return;
    const timeStr = ts
        ? new Date(ts).toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit', second:'2-digit' })
        : new Date().toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit', second:'2-digit' });
    const row = document.createElement('div');
    row.className = 'flex items-start gap-2 py-1 border-b border-gray-800';
    row.innerHTML = `<span class="text-gray-500 shrink-0">${timeStr}</span>
                     <span class="shrink-0">${icon}</span>
                     <span class="text-gray-300"><b>${title}</b> ${detail}</span>`;
    log.prepend(row);
    // Keep last 30 events
    while (log.children.length > 30) log.lastChild.remove();
}

// ── Smart event log: only writes on meaningful state changes ──────
function _atMaybeLog(data) {
    const conds    = data.conditions || [];
    const signal   = (data.last_signal || '').replace(/<[^>]*>/g, '').trim(); // strip html
    const evalTime = data.last_evaluation || null;

    // Build a key from met/not-met states of each condition
    const condKey = conds.map(c => `${c.name}:${c.met ? '1' : '0'}`).join('|');

    // ── 1. Log individual condition flips ───────────────────────
    if (_atLastCondKey !== null && condKey !== _atLastCondKey) {
        const prev = Object.fromEntries(
            (_atLastCondKey || '').split('|').map(s => {
                const [n, v] = s.split(':');
                return [n, v === '1'];
            })
        );
        conds.forEach(c => {
            const wasMet = prev[c.name];
            if (wasMet === undefined) return; // new condition, skip
            if (c.met && !wasMet) {
                _atLogEvent('✅', c.name, c.detail || 'condition met');
            } else if (!c.met && wasMet) {
                _atLogEvent('❌', c.name, c.detail || 'condition lost');
            }
        });
    }

    // ── 2. Signal summary: now owned by server event log — skip client dupe ──
    // Server writes 🚦/🚫/⏸/✅ outcome rows; _renderServerEventLog shows them.
    const signalChanged = signal !== _atLastSignalText;
    if (signalChanged) _atLastSignalText = signal;  // still track for condition-flip context

    // ── 3. Log kill-switch / safety blocks ──────────────────────
    if (data.kill_switch && !_atKillSwitchLogged) {
        _atLogEvent('🔴', 'Kill Switch ACTIVE', 'No new orders will be placed');
        _atKillSwitchLogged = true;
    } else if (!data.kill_switch) {
        _atKillSwitchLogged = false;
    }

    // ── 4. Log runner start/stop ─────────────────────────────────
    if (!data.is_running && _atIsRunning) {
        _atLogEvent('⏹', 'Auto-Trader stopped', '');
    }

    // Save state for next comparison
    _atLastCondKey   = condKey || _atLastCondKey;
    _atLastEvalTime  = evalTime || _atLastEvalTime;
}

// init these after the trackers block
let _atLastSignalTime   = 0;
let _atKillSwitchLogged = false;

function _atClearLog() {
    const log = document.getElementById('at-event-log');
    if (log) log.innerHTML = '';
    // Also reset trackers so next poll re-logs current state
    _atLastCondKey    = null;
    _atLastSignalText = null;
    _atLastSignalTime = 0;
}

// ── Beep on trade events ─────────────────────────────────────────
function _atBeep(freq = 880, dur = 150, vol = 0.3) {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.frequency.value = freq;
        gain.gain.setValueAtTime(vol, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + dur / 1000);
        osc.start(); osc.stop(ctx.currentTime + dur / 1000);
    } catch (_) {}
}

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
    const strategyId = document.getElementById('at-strategy')?.value || 'smart_router';
    _setAtStatus('starting');
    try {
        const resp = await fetch(`/api/auto-trader/start?strategy=${strategyId}`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            _setAtStatus('running');
            _atIsRunning = true;
            _atLastCondKey    = null;
            _atLastSignalText = null;
            _atLastSignalTime = 0;
            _atLogEvent('🟢', 'Auto-Trader started', `strategy: ${strategyId}`);
            await pollAutoTraderStatus();
        } else {
            _setAtStatus('error', data.error || 'Failed to start');
        }
    } catch (e) {
        _setAtStatus('error', 'Connection error');
    }
}

// ── Render recovery banner based on recovery_type ──────────────
function _renderRecoveryBanner(data) {
    const banner  = document.getElementById('recovery-banner');
    if (!banner) return;

    if (!data.recovery_mode) {
        banner.classList.add('hidden');
        return;
    }

    const type    = data.recovery_type || 'closed';
    const msg     = data.recovery_message || '';
    const titleEl = document.getElementById('recovery-title');
    const msgEl   = document.getElementById('recovery-msg');
    const iconEl  = document.getElementById('recovery-icon');
    const actEl   = document.getElementById('recovery-actions');

    banner.classList.remove('hidden');

    if (type === 'open') {
        // Trade still live in Zerodha — user needs to resume managing it
        banner.className = 'mb-3 border-2 border-red-400 bg-red-50 rounded-xl p-4';
        if (iconEl)  iconEl.textContent  = '🚨';
        if (titleEl) { titleEl.textContent = 'App Restarted — Open Trade Detected!';
                       titleEl.className  = 'font-bold text-sm text-red-800'; }
        if (msgEl)   { msgEl.textContent  = msg;
                       msgEl.className    = 'text-red-700 text-xs mt-1'; }
        if (actEl) actEl.innerHTML = `
            <button onclick="resumeFromRecovery()"
                class="bg-red-600 hover:bg-red-700 text-white text-xs px-3 py-1.5 rounded-lg font-bold">
                ▶ Resume Managing Trade
            </button>
            <a href="https://kite.zerodha.com/" target="_blank"
                class="bg-white border border-red-400 text-red-700 text-xs px-3 py-1.5 rounded-lg font-bold">
                Open Zerodha ↗
            </a>`;

    } else if (type === 'closed') {
        // Trade was already closed in Zerodha — nothing to resume
        banner.className = 'mb-3 border-2 border-yellow-400 bg-yellow-50 rounded-xl p-4';
        if (iconEl)  iconEl.textContent  = '⚠️';
        if (titleEl) { titleEl.textContent = 'Position Closed While App Was Offline';
                       titleEl.className  = 'font-bold text-sm text-yellow-800'; }
        if (msgEl)   { msgEl.textContent  = msg + ' Nothing to resume — the trade is already done.';
                       msgEl.className    = 'text-yellow-700 text-xs mt-1'; }
        if (actEl) actEl.innerHTML = `
            <button onclick="dismissRecovery()"
                class="bg-yellow-500 hover:bg-yellow-600 text-white text-xs px-3 py-1.5 rounded-lg font-bold">
                ✅ Got It — Dismiss
            </button>
            <a href="https://kite.zerodha.com/" target="_blank"
                class="bg-white border border-yellow-400 text-yellow-700 text-xs px-3 py-1.5 rounded-lg font-bold">
                Check P&amp;L in Zerodha ↗
            </a>`;

    } else {
        // type === 'clean' — just a routine restore, no alert needed
        banner.classList.add('hidden');
        return;
    }

    // Beep once per session
    if (!banner._beeped && type !== 'clean') {
        _atBeep(type === 'open' ? 440 : 330, 200);
        setTimeout(() => _atBeep(type === 'open' ? 330 : 440, 300), 220);
        banner._beeped = true;
    }
}

// ── Resume from crash recovery (dismiss banner + start trader) ──
async function resumeFromRecovery() {
    await dismissRecovery();
    await startAutoTrader();
}

// ── Dismiss recovery banner without starting ──────────────────────
async function dismissRecovery() {
    try {
        await fetch('/api/auto-trader/dismiss-recovery', { method: 'POST' });
    } catch (_) { /* silent */ }
    const banner = document.getElementById('recovery-banner');
    if (banner) banner.classList.add('hidden');
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
        if (data.success) renderAutoTrader(data);
    } catch (e) { /* network blip — stay silent */ }
}

// ── Full render (called with latest server data) ──────────────────
function renderAutoTrader(data) {
    _atIsRunning  = !!data.is_running;
    _atKillSwitch = !!data.kill_switch;
    const hasTrade = !!data.active_trade;

    // ── Detect entry / exit events ─────────────────────────────
    if (hasTrade && !_atHadTrade) {
        const t = data.active_trade;
        const dir = t.direction === 'long' ? '⬆ LONG' : '⬇ SHORT';
        _atBeep(660, 120); setTimeout(() => _atBeep(880, 120), 130);
        showAtToast('entry', `🟢 POSITION ENTERED`, `${dir} at ₹${t.entry_price} | SL ₹${t.stop_loss} | Tgt ₹${t.target || '--'}`);
    } else if (!hasTrade && _atHadTrade) {
        // Just closed — fetch history to get the last P&L
        fetch('/api/auto-trader/history').then(r => r.json()).then(h => {
            const last = (h.trades || []).slice(-1)[0];
            if (!last) return;
            const pnl = last.pnl;
            if (pnl > 0) {
                _atBeep(880, 100); setTimeout(() => _atBeep(1100, 150), 120);
                showAtToast('exit_win', `💰 POSITION CLOSED — WIN`, `P&L ₹+${pnl.toFixed(0)} | ${last.exit_reason || ''}`);
            } else if (pnl < 0) {
                _atBeep(300, 200);
                showAtToast('exit_loss', `🔴 POSITION CLOSED — LOSS`, `P&L ₹${pnl.toFixed(0)} | ${last.exit_reason || ''}`);
            } else {
                _atBeep(550, 100);
                showAtToast('exit_be', `⚪ POSITION CLOSED — B/E`, `P&L ₹0 | ${last.exit_reason || ''}`);
            }
            renderTradeHistory(h);
        }).catch(() => {});
    }
    _atHadTrade = hasTrade;

    // ── Mode badge ───────────────────────────────────────────────
    const modeEl = document.getElementById('at-mode');
    if (modeEl) {
        modeEl.textContent = data.is_paper_mode ? '📝 PAPER' : '🟢 LIVE';
        modeEl.className   = `${data.is_paper_mode ? 'bg-yellow-600' : 'bg-green-600'} text-xs px-2 py-0.5 rounded font-bold`;
    }

    // Strategy
    const stratSelect = document.getElementById('at-strategy');
    const stratLabel  = document.getElementById('at-strat-label');
    if (stratSelect) {
        stratSelect.disabled = _atIsRunning;
        // Always sync dropdown from server — not just when running
        if (data.selected_strategy) stratSelect.value = data.selected_strategy;
    }
    if (stratLabel && stratSelect) {
        const opt = stratSelect.options[stratSelect.selectedIndex];
        if (opt) stratLabel.textContent = opt.textContent;
    }

    // Status buttons
    if (_atKillSwitch)     _setAtStatus('killed');
    else if (_atIsRunning) _setAtStatus('running');
    else                   _setAtStatus('idle');

    // P&L + counters
    const setT = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    const pnlEl = document.getElementById('at-pnl');
    if (pnlEl) {
        // Day P&L = realized (closed trades) + unrealized (open trade if any)
        const realized   = data.total_pnl || 0;
        const unrealized = hasTrade ? (data.active_trade?.pnl_unrealized ?? 0) : 0;
        const dayTotal   = realized + unrealized;
        const sign       = dayTotal >= 0 ? '+' : '';
        pnlEl.textContent = `₹${sign}${dayTotal.toLocaleString('en-IN', {minimumFractionDigits: 0, maximumFractionDigits: 0})}`;
        pnlEl.className   = `text-xs font-bold ${dayTotal >= 0 ? 'text-green-400' : 'text-red-400'}`;
        pnlEl.title       = unrealized !== 0
            ? `Realized: ₹${realized >= 0 ? '+' : ''}${realized}  |  Unrealized: ₹${unrealized >= 0 ? '+' : ''}${unrealized}`
            : `Realized P&L from ${data.trades_today ?? 0} trade(s) today`;
    }
    setT('at-orders',    `${data.orders_placed}/${data.max_orders}`);
    setT('at-exit-time', data.exit_time);

    // Last eval
    if (data.last_evaluation) {
        const t = new Date(data.last_evaluation);
        setT('at-last-eval', `Last eval: ${t.toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit', second:'2-digit' })}`);
    }

    // Signal / conditions
    const sigEl = document.getElementById('at-signal');
    if (sigEl) {
        let sigHTML = data.last_signal || '';
        if (data.conditions?.length) {
            sigHTML += '<div class="mt-2 space-y-1">';
            data.conditions.forEach(c => {
                sigHTML += `<div class="${c.met ? 'text-green-400' : 'text-red-400'} text-xs">
                    ${c.met ? '✅' : '❌'} <strong>${c.name}</strong>: ${c.detail}</div>`;
            });
            sigHTML += '</div>';
        }
        sigEl.innerHTML = sigHTML;
    }

    // ── Strategy Scoreboard (smart_router only) ─────────────────
    _renderMetaScoreboard(data);

    // ── Event log — write meaningful entries on state changes ────
    _atMaybeLog(data);

    // ── LIVE POSITION BANNER ───────────────────────────────────
    const banner = document.getElementById('at-pos-banner');
    const noPos  = document.getElementById('at-no-pos');

    if (hasTrade) {
        if (banner) banner.classList.remove('hidden');
        if (noPos)  noPos.classList.add('hidden');

        const t      = data.active_trade;
        const isLong = t.direction === 'long';

        // Direction badge
        const dirBadge = document.getElementById('at-pos-dir-badge');
        if (dirBadge) {
            dirBadge.textContent  = isLong ? '⬆ LONG' : '⬇ SHORT';
            dirBadge.className    = `px-3 py-1 rounded-full font-black text-sm ${
                isLong ? 'bg-green-500 text-white' : 'bg-red-500 text-white'}`;
        }
        // Managed toggle — sync from server state
        _refreshManagedToggle(t.app_managed !== false);
        // Active label in grid
        const activeEl = document.getElementById('at-active');
        if (activeEl) {
            activeEl.textContent = isLong ? '⬆ LONG' : '⬇ SHORT';
            activeEl.className   = `text-xs font-bold ${isLong ? 'text-green-400' : 'text-red-400'}`;
        }

        // Border color flips with direction
        if (banner) {
            banner.style.borderColor = isLong ? '#22c55e' : '#ef4444';
            banner.style.background  = isLong ? 'rgba(21,128,61,.15)' : 'rgba(153,27,27,.15)';
        }

        // ── All prices in OPTION PREMIUM terms ───────────────────
        const ltp    = t.current_option_ltp;
        const slPrem = t.option_sl_premium;
        const tgtPrem = t.option_target_premium;

        // Entry — show entry premium paid
        setT('at-entry', t.entry_premium ? `₹${t.entry_premium.toFixed(2)}` : '--');

        // SL — option premium SL level, Nifty spot as tooltip
        const slEl = document.getElementById('at-sl');
        if (slEl) {
            slEl.textContent = slPrem ? `₹${slPrem.toFixed(1)}` : '--';
            slEl.title       = `Nifty spot SL: ₹${t.stop_loss}`;
        }

        // Target — option premium target level, Nifty spot as tooltip
        const tgtEl = document.getElementById('at-tgt');
        if (tgtEl) {
            tgtEl.textContent = tgtPrem ? `₹${tgtPrem.toFixed(1)}` : '--';
            tgtEl.title       = `Nifty spot target: ₹${t.target}`;
        }

        // Distance — how far option LTP is from SL / target in premium terms
        const distSl  = document.getElementById('at-dist-sl');
        const distTgt = document.getElementById('at-dist-tgt');
        if (distSl && ltp && slPrem) {
            const d = (ltp - slPrem).toFixed(1);
            distSl.textContent = `₹${d} away`;
            distSl.className   = parseFloat(d) < 5 ? 'text-[9px] text-red-400 font-bold' : 'text-[9px] text-gray-500';
        }
        if (distTgt && ltp && tgtPrem) {
            const d = (tgtPrem - ltp).toFixed(1);
            distTgt.textContent = `₹${d} away`;
        }

        // Row 2 — live prices & quantity
        const niftyCur = data.nifty_current || t.nifty_current;
        setT('at-nifty-live',  niftyCur ? `₹${niftyCur}` : '⏳ loading…');
        setT('at-opt-ltp',     ltp ? `₹${ltp}` : '⏳ loading…');
        setT('at-entry-prem',  t.entry_premium ? `₹${t.entry_premium.toFixed(2)}` : '⏳ loading…');
        setT('at-qty-lots',    t.lots != null  ? `${t.quantity} / ${t.lots}L` : '--');

        // ── Exchange SL-M sync badge ──────────────────────────────
        const slBadge = document.getElementById('at-sl-sync-badge');
        if (slBadge) {
            if (data.exchange_sl_pending) {
                slBadge.classList.remove('hidden');
                slBadge.title = 'Trailing SL updated in-app — Zerodha will sync on next 5-min candle';
            } else {
                slBadge.classList.add('hidden');
            }
        }

        // Unrealized P&L — live LTP preferred, delta estimate fallback
        const upnlEl = document.getElementById('at-upnl');
        if (upnlEl) {
            const hasLtp = t.current_option_ltp && t.current_option_ltp > 0;
            const hasEp  = t.entry_premium      && t.entry_premium > 0;
            const pnl    = t.pnl_unrealized ?? 0;

            if (hasLtp && hasEp) {
                // Best case: real option LTP available
                upnlEl.textContent = `₹${pnl >= 0 ? '+' : ''}${pnl.toLocaleString('en-IN')}`;
                upnlEl.className   = `text-sm font-black ${pnl > 0 ? 'text-green-400' : pnl < 0 ? 'text-red-400' : 'text-gray-400'}`;
                upnlEl.title       = `LTP ₹${t.current_option_ltp} | Entry ₹${t.entry_premium} | ${t.quantity}u`;
            } else if (pnl !== 0) {
                // Delta estimate from server (nifty move × 0.5 × qty)
                upnlEl.textContent = `~₹${pnl >= 0 ? '+' : ''}${pnl.toLocaleString('en-IN')}`;
                upnlEl.className   = `text-sm font-black ${pnl > 0 ? 'text-green-400' : pnl < 0 ? 'text-red-400' : 'text-gray-400'}`;
                upnlEl.title       = 'Estimated (delta × Nifty move) — LTP refreshing…';
            } else {
                // Nothing yet — show 0 with note, not a spinner
                upnlEl.textContent = '₹0';
                upnlEl.className   = 'text-sm font-black text-gray-500';
                upnlEl.title       = 'Waiting for first LTP tick (updates every 15s)';
            }
        }
    } else {
        if (banner) banner.classList.add('hidden');
        if (noPos)  noPos.classList.remove('hidden');
        const activeEl = document.getElementById('at-active');
        if (activeEl) { activeEl.textContent = 'None'; activeEl.className = 'text-xs font-bold text-gray-500'; }

        // ── Show WHY there's no position (not just a static message) ──
        const noPosMsg = document.getElementById('at-no-pos-msg');
        if (noPosMsg) {
            const block  = data.block_reason || null;
            const allMet = data.conditions?.length > 0 && data.conditions.every(c => c.met);

            if (block && block.toLowerCase().includes('past exit time')) {
                noPosMsg.textContent  = '⏰ Past exit time (15:15) — no new entries today';
                noPosMsg.className    = 'text-[10px] text-yellow-500';
            } else if (block && block.toLowerCase().includes('kill switch')) {
                noPosMsg.textContent  = '🚨 Kill switch active — trading paused';
                noPosMsg.className    = 'text-[10px] text-red-400';
            } else if (block && block.toLowerCase().includes('loss')) {
                noPosMsg.textContent  = '🛑 Daily loss limit hit — no new entries';
                noPosMsg.className    = 'text-[10px] text-red-400';
            } else if (block && block.toLowerCase().includes('max orders')) {
                noPosMsg.textContent  = '🛑 Max orders reached for today';
                noPosMsg.className    = 'text-[10px] text-red-400';
            } else if (block) {
                noPosMsg.textContent  = `⚠️ Blocked: ${block}`;
                noPosMsg.className    = 'text-[10px] text-yellow-500';
            } else if (allMet) {
                noPosMsg.textContent  = '✅ Signal ready — entry pending next candle';
                noPosMsg.className    = 'text-[10px] text-green-400 animate-pulse';
            } else if (!data.is_running) {
                noPosMsg.textContent  = '⏸ Auto-trader not running';
                noPosMsg.className    = 'text-[10px] text-gray-500';
            } else {
                noPosMsg.textContent  = 'Waiting for entry signal…';
                noPosMsg.className    = 'text-[10px] text-gray-600';
            }
        }
    }

    // ── SERVER-SIDE EVENT LOG ────────────────────────────────────
    _renderServerEventLog(data.event_log || []);

    // ── CRASH RECOVERY BANNER ──────────────────────────────────
    _renderRecoveryBanner(data);

    // ── Sync settings panel inputs from server state ─────────────
    if (typeof syncAtSettingsFromStatus === 'function') syncAtSettingsFromStatus(data);

    // ── Refresh manual controls visibility ────────────────────────
    if (typeof _atRefreshManualControls === 'function') {
        _atRefreshManualControls(!!data.active_trade);
    }
}

// Colour + style per icon so outcomes are instantly readable
const _LOG_ICON_STYLE = {
    '✅': { row: 'bg-green-950/40 border-green-900',  label: 'text-green-400', detail: 'text-green-300' },
    '🚫': { row: 'bg-red-950/40 border-red-900',      label: 'text-red-400',   detail: 'text-red-300'   },
    '⏸':  { row: 'bg-yellow-950/30 border-yellow-900',label: 'text-yellow-400',detail: 'text-yellow-300'},
    '🚦': { row: 'border-gray-800',                   label: 'text-blue-300',  detail: 'text-gray-400'  },
    '🔍': { row: 'border-gray-800',                   label: 'text-gray-400',  detail: 'text-gray-500'  },
    '🟢': { row: 'bg-green-950/30 border-green-900',  label: 'text-green-300', detail: 'text-green-400' },
    '🔴': { row: 'bg-red-950/30 border-red-900',      label: 'text-red-300',   detail: 'text-red-400'   },
};

// ── Strategy Scoreboard ─────────────────────────────────────────
function _renderMetaScoreboard(data) {
    const board = document.getElementById('at-meta-scoreboard');
    const list  = document.getElementById('at-meta-scores-list');
    const regEl = document.getElementById('at-meta-regime');
    if (!board || !list) return;

    const scores = data.meta_scores || [];
    const isMeta = data.selected_strategy === 'smart_router';

    if (!isMeta || !scores.length) {
        board.classList.add('hidden');
        return;
    }
    board.classList.remove('hidden');

    if (regEl) {
        regEl.textContent = data.meta_regime
            ? `📊 Regime: ${data.meta_regime.replace('_', ' ').toUpperCase()}`
            : '';
    }

    // Only re-render if scores actually changed
    const key = JSON.stringify(scores.map(s => s.composite));
    if (list.dataset.key === key) return;
    list.dataset.key = key;

    list.innerHTML = scores.map(s => {
        const wantsEntry = s.should_enter;
        const blocked    = s.time_mult === 0;
        const hasError   = !!s.error;

        // Colour coding
        const rowCls = wantsEntry
            ? 'bg-green-900/40 border border-green-700/50'
            : blocked
                ? 'bg-gray-800/40 opacity-50'
                : 'bg-gray-800/40';

        const scoreCls = wantsEntry ? 'text-green-400 font-bold' : 'text-gray-400';
        const dirBadge = s.direction
            ? `<span class="px-1 rounded text-[9px] font-bold ${
                s.direction === 'long' ? 'bg-green-800 text-green-200' : 'bg-red-800 text-red-200'
              }">${s.direction.toUpperCase()}</span>`
            : '';

        // Time blocked indicator
        const timeTag = blocked
            ? '<span class="text-[9px] text-gray-500">⏱ time-blocked</span>'
            : `<span class="text-[9px] text-gray-500">×${s.time_mult.toFixed(1)} time</span>`;

        return `
        <div class="${rowCls} rounded px-2 py-1.5 flex items-center justify-between gap-2">
          <div class="flex items-center gap-1.5 min-w-0">
            <span>${s.emoji}</span>
            <span class="text-[10px] text-white truncate">${s.name}</span>
            ${dirBadge}
            ${hasError ? '<span class="text-[9px] text-red-400">⚠ error</span>' : ''}
          </div>
          <div class="flex items-center gap-2 shrink-0 text-[10px]">
            ${timeTag}
            <span class="text-gray-500">×${s.regime_fit.toFixed(1)} regime</span>
            <span class="text-gray-400">${s.confidence.toFixed(0)}% conf</span>
            <span class="${scoreCls} w-10 text-right">${s.composite.toFixed(0)}</span>
          </div>
        </div>`;
    }).join('');
}


function _renderServerEventLog(events) {
    const el = document.getElementById('at-event-log');
    if (!el || !events.length) return;
    // Only re-render if new events arrived
    const firstTs = events[0]?.ts;
    if (el.dataset.lastTs === firstTs) return;
    el.dataset.lastTs = firstTs;
    el.innerHTML = events.map(e => {
        const s = _LOG_ICON_STYLE[e.icon] || { row: 'border-gray-800', label: 'text-gray-300', detail: 'text-gray-500' };
        return `<div class="flex items-start gap-1.5 py-0.5 px-1 border-b ${s.row}">
          <span class="text-[11px] shrink-0">${e.icon}</span>
          <span class="text-[9px] text-gray-500 shrink-0 font-mono mt-px">${e.ts}</span>
          <span class="text-[10px] font-bold shrink-0 ${s.label}">${e.label}</span>
          <span class="text-[10px] ${s.detail} break-words min-w-0">${e.detail}</span>
        </div>`;
    }).join('');
}

// ── Sync existing Zerodha position into app ───────────────────────
async function syncFromZerodha() {
    const btn = document.getElementById('at-sync-zd-btn');
    const msg = document.getElementById('at-sync-zd-msg');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Scanning Zerodha…'; }
    if (msg) {
        msg.textContent  = '⏳ Fetching live Nifty spot + your positions… (may take ~5s)';
        msg.className    = 'text-[10px] mt-1 text-yellow-400';
        msg.classList.remove('hidden');
    }
    try {
        // 30s timeout — Kite API can be slow on first call
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 30000);
        const resp = await fetch('/api/auto-trader/sync-zerodha', {
            method: 'POST',
            signal: controller.signal,
        });
        clearTimeout(timer);
        const data = await resp.json();
        if (data.success) {
            const detail = [
                data.instrument,
                `${data.quantity}u`,
                `avg ₹${data.avg_price}`,
                data.nifty_spot ? `Nifty ₹${Math.round(data.nifty_spot)}` : '',
                data.sl_level   ? `SL ₹${Math.round(data.sl_level)}` : '',
                data.tgt_level  ? `Tgt ₹${Math.round(data.tgt_level)}` : '',
            ].filter(Boolean).join(' | ');
            if (msg) {
                msg.textContent  = `✅ ${detail}`;
                msg.className    = 'text-[10px] mt-1 text-green-400';
            }
            _atShowToast(`🔗 Linked — ${detail}`, 'info');
            await pollAutoTraderStatus();
        } else {
            if (msg) {
                msg.textContent  = `❌ ${data.error}`;
                msg.className    = 'text-[10px] mt-1 text-red-400';
            }
            _atShowToast(`❌ Sync failed — ${data.error}`, 'error');
        }
    } catch (e) {
        const isTimeout = e.name === 'AbortError';
        const errText   = isTimeout
            ? '⏰ Timed out (30s) — Kite API is slow, try again'
            : `❌ ${e.message || 'Request failed'}`;
        if (msg) {
            msg.textContent = errText;
            msg.className   = 'text-[10px] mt-1 text-red-400';
        }
        _atShowToast(errText, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🔗 Sync trade from Zerodha'; }
    }
}

// ── Trade Managed Toggle ──────────────────────────────────────────
let _tradeManaged = true;  // local state mirror

function _refreshManagedToggle(managed) {
    _tradeManaged = managed;
    const btn = document.getElementById('at-managed-toggle');
    if (!btn) return;
    if (managed) {
        btn.textContent = '🤖 APP MANAGED';
        btn.className   = 'text-[10px] px-2 py-1 rounded-lg font-bold border transition-all ' +
                          'bg-[#0053e2] border-[#0053e2] text-white';
        btn.title       = 'App is managing SL / trailing SL / exit — click to switch to Monitor Only';
    } else {
        btn.textContent = '👁 MONITOR ONLY';
        btn.className   = 'text-[10px] px-2 py-1 rounded-lg font-bold border transition-all ' +
                          'bg-yellow-500 border-yellow-400 text-gray-900';
        btn.title       = 'App is NOT managing SL/exit — click to hand back control to app';
    }
}

async function discardTrade() {
    if (!confirm('Remove this trade from the app?\n\nNo order will be sent to Zerodha — your position stays open there.')) return;
    try {
        const resp = await fetch('/api/auto-trader/discard-trade', { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            _atShowToast(`🗑 Trade removed from app — ${data.discarded}`, 'info');
            await pollAutoTraderStatus();
        } else {
            _atShowToast(`❌ ${data.error}`, 'error');
        }
    } catch (e) {
        _atShowToast(`❌ ${e.message}`, 'error');
    }
}

async function toggleTradeManaged() {
    const newManaged = !_tradeManaged;
    try {
        const resp = await fetch(
            `/api/auto-trader/trade-managed?managed=${newManaged}`,
            { method: 'POST' }
        );
        const data = await resp.json();
        if (data.success) {
            _refreshManagedToggle(data.app_managed);
            _atShowToast(
                data.app_managed
                    ? '🤖 App will manage SL, trailing SL and exit'
                    : '👁 Monitor Only — app will NOT touch your position',
                data.app_managed ? 'info' : 'warning'
            );
        } else {
            _atShowToast(`❌ ${data.error}`, 'error');
        }
    } catch (e) {
        _atShowToast(`❌ Toggle failed: ${e.message}`, 'error');
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
    const trades       = data.trades        || [];
    const realizedPnl  = data.total_pnl     || 0;
    const unrealPnl    = data.unrealized_pnl || 0;
    const dayTotal     = data.day_total_pnl  ?? (realizedPnl + unrealPnl);
    const wins         = trades.filter(t => t.pnl > 0).length;
    const losses       = trades.filter(t => t.pnl < 0).length;
    const breakeven    = trades.filter(t => t.pnl === 0).length;

    const setT = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    setT('ath-count',  trades.length);
    setT('ath-wins',   wins);
    setT('ath-losses', losses);

    // Realized P&L
    const pnlEl = document.getElementById('ath-pnl');
    if (pnlEl) {
        pnlEl.textContent = `₹${realizedPnl >= 0 ? '+' : ''}${realizedPnl.toFixed(0)}`;
        pnlEl.className   = `text-xs font-bold ${realizedPnl >= 0 ? 'text-green-400' : 'text-red-400'}`;
        pnlEl.title       = 'Realized P&L (closed trades only)';
    }

    // Day Total = Realized + Unrealized
    const dayEl = document.getElementById('ath-day-total');
    if (dayEl) {
        const sign = dayTotal >= 0 ? '+' : '';
        dayEl.textContent = `₹${sign}${dayTotal.toLocaleString('en-IN', {minimumFractionDigits:0, maximumFractionDigits:0})}`;
        dayEl.className   = `text-xs font-bold ${dayTotal >= 0 ? 'text-green-400' : 'text-red-400'}`;
        dayEl.title       = unrealPnl !== 0
            ? `Realized ₹${realizedPnl >= 0?'+':''}${realizedPnl.toFixed(0)} + Unrealized ₹${unrealPnl >= 0?'+':''}${unrealPnl.toFixed(0)}`
            : 'Realized P&L only (no open trade)';
    }

    const container = document.getElementById('at-history-table');
    if (!container) return;
    if (!trades.length) {
        container.innerHTML = '<p class="text-gray-600 text-center py-3">No trades yet today</p>';
        return;
    }

    const rows = [...trades].reverse().map(t => {
        const pnl      = t.pnl ?? 0;
        const pnlColor = pnl > 0 ? 'text-green-400' : pnl < 0 ? 'text-red-400' : 'text-gray-400';
        const dirIcon  = t.direction === 'long' ? '⬆' : '⬇';
        const dirColor = t.direction === 'long' ? 'text-green-300' : 'text-red-300';
        const time     = t.exit_time
            ? (t.exit_time.split('T')[1]?.substring(0,5) || '--')
            : (t.timestamp?.split('T')[1]?.substring(0,5) || '--');
        // Show OPTION PREMIUMS not Nifty spot
        const entryPrem = t.entry_premium != null ? `₹${Number(t.entry_premium).toFixed(1)}` : `₹${Number(t.entry_price).toFixed(0)}`;
        const exitPrem  = t.exit_premium  != null ? `₹${Number(t.exit_premium).toFixed(1)}`  : '--';
        const reason    = (t.exit_reason || '').replace(/[🕯⚡📊🔍]/g,'').substring(0, 22);
        const emoji     = pnl > 0 ? '🟢' : pnl < 0 ? '🔴' : '⚪';
        const qty       = t.quantity ? `${t.quantity}u` : '';
        return `<tr class="border-t border-gray-800 hover:bg-gray-800/40 text-[11px]">
            <td class="py-1.5 pl-1 text-gray-400">${time}</td>
            <td class="text-center ${dirColor} font-bold">${dirIcon}</td>
            <td class="text-center text-gray-300">${entryPrem}</td>
            <td class="text-center text-gray-300">${exitPrem}</td>
            <td class="text-center font-bold ${pnlColor}">${emoji} ₹${pnl >= 0 ? '+' : ''}${pnl.toFixed(0)}</td>
            <td class="text-gray-500 text-[10px] truncate max-w-[100px]" title="${t.exit_reason || ''}">${reason}</td>
        </tr>`;
    }).join('');

    // Running total row
    const unrealRow = unrealPnl !== 0 ? `
        <tr class="border-t border-yellow-800/50 bg-yellow-900/10 text-[11px]">
            <td class="py-1 pl-1 text-yellow-400 font-bold" colspan="4">⏳ Open trade (unrealized)</td>
            <td class="text-center font-bold ${unrealPnl >= 0 ? 'text-green-400' : 'text-red-400'}">
                ₹${unrealPnl >= 0 ? '+' : ''}${unrealPnl.toFixed(0)}
            </td>
            <td></td>
        </tr>` : '';

    const totalRow = `
        <tr class="border-t-2 border-gray-600 text-[11px] font-bold">
            <td class="py-1.5 pl-1 text-gray-300" colspan="4">Day Total (${trades.length} trades, ${wins}W/${losses}L)</td>
            <td class="text-center ${dayTotal >= 0 ? 'text-green-400' : 'text-red-400'}">
                ₹${dayTotal >= 0 ? '+' : ''}${dayTotal.toFixed(0)}
            </td>
            <td></td>
        </tr>`;

    container.innerHTML = `<table class="w-full" role="table">
        <thead class="text-[10px] text-gray-500 uppercase">
            <tr>
                <th class="text-left py-1 pl-1">Exit</th>
                <th>Dir</th>
                <th>Entry Prem</th>
                <th>Exit Prem</th>
                <th>P&L</th>
                <th>Reason</th>
            </tr>
        </thead>
        <tbody>${rows}${unrealRow}${totalRow}</tbody>
    </table>`;
}

// ── Init ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        pollAutoTraderStatus();
        loadTradeHistory();
    }, 1500);
});
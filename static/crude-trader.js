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
// ── Trail Mode UI ───────────────────────────────────────────────
function onCrudeTrailModeChange(mode) {
    document.getElementById('crude-trail-fixed-row').classList.toggle('hidden', mode !== 'fixed');
    document.getElementById('crude-trail-atr-row').classList.toggle('hidden',   mode !== 'atr');
    document.getElementById('crude-trail-st-row').classList.toggle('hidden',    mode !== 'supertrend');
}

function _applyCrudeTrailMode(mode) {
    // Sync radio buttons to match server state
    document.querySelectorAll('input[name="crude-trail-mode"]').forEach(r => {
        r.checked = (r.value === mode);
    });
    onCrudeTrailModeChange(mode);
}


// ── Capital Sync from Zerodha ────────────────────────────────────
async function syncCrudeCapital() {
    const btn   = document.getElementById('crude-capital-sync-btn');
    const input = document.getElementById('crude-capital');
    const hint  = document.getElementById('crude-capital-hint');
    if (!input) return;

    if (btn) { btn.textContent = '⏳'; btn.disabled = true; }
    try {
        const r = await fetch('/api/crude/margin');
        const d = await r.json();
        if (d.success && d.free > 0) {
            const free  = Math.floor(d.free);     // FREE margin — not gross net
            const used  = Math.round(d.utilised || 0);
            const net   = Math.round(d.net || 0);
            input.value = free;
            const detail = used > 0
                ? `free ₹${free.toLocaleString('en-IN')} | utilised ₹${used.toLocaleString('en-IN')} | net ₹${net.toLocaleString('en-IN')}`
                : `₹${free.toLocaleString('en-IN')}`;
            if (hint) hint.textContent = `✅ Zerodha: ${detail}`;
            _crudeLog(`💰 Capital synced → ₹${free.toLocaleString('en-IN')} free (net ₹${net.toLocaleString('en-IN')}, used ₹${used.toLocaleString('en-IN')})`, 'ok');
            _crudeToast(`💰 Free margin: ₹${free.toLocaleString('en-IN')}`, 'ok');
            await saveCrudeConfig();      // push to server state immediately
        } else {
            const why = d.error || 'Could not fetch Zerodha balance';
            if (hint) hint.textContent = '⚠️ ' + why;
            _crudeToast('⚠️ ' + why, 'error');
        }
    } catch (e) {
        if (hint) hint.textContent = '❌ ' + e.message;
    }
    if (btn) { btn.textContent = '🔄 Sync'; btn.disabled = false; }
}


// ── Manual Evaluate ──────────────────────────────────────────────
function _renderStrategyDashboard(strategies) {
    const panel = document.getElementById('crude-strategy-panel');
    if (!panel || !strategies?.length) return;
    panel.innerHTML = strategies.map(s => {
        const ok  = s.should_enter;
        const dir = s.direction ? ` <span class="font-bold ${s.direction==='long'?'text-green-400':'text-red-400'}">${s.direction.toUpperCase()}</span>` : '';
        return `
        <div class="flex items-start gap-2 border-b border-gray-700 pb-1 last:border-0">
          <span class="text-[13px] shrink-0 mt-0.5">${ok ? '✅' : '⛔'}</span>
          <div>
            <span class="text-xs font-bold ${ok?'text-green-300':'text-gray-400'}">${s.name}</span>${dir}
            <p class="text-[10px] text-gray-500 leading-tight">${s.reason}</p>
          </div>
        </div>`;
    }).join('');
    panel.classList.remove('hidden');
}

async function crudeManualEvaluate() {
    const btn = document.getElementById('crude-btn-evaluate');
    const msg = document.getElementById('crude-eval-msg');
    const patternContainer = document.getElementById('crude-pattern-charts');
    const patternCards = document.getElementById('crude-pattern-cards');
    if (!btn) return;

    btn.disabled = true;
    btn.innerHTML = '⏳ Evaluating…';
    if (msg) { msg.classList.remove('hidden'); msg.textContent = 'Checking all strategies…'; }
    if (patternContainer) patternContainer.classList.add('hidden');

    try {
        const r = await fetch('/api/crude/evaluate', { method: 'POST' });
        const d = await r.json();
        if (d.success) {
            // Render per-strategy dashboard
            _renderStrategyDashboard(d.strategies);

            const passing  = (d.strategies || []).filter(s => s.should_enter);
            const nPass    = passing.length;
            const nTotal   = (d.strategies || []).length;
            const summary  = nPass > 0
                ? `${nPass}/${nTotal} strategies firing 🚀`
                : `0/${nTotal} strategies — no entry`;

            if (msg) { msg.textContent = summary; msg.classList.remove('hidden'); }
            _crudeLog(`🔍 Eval: ${summary}`, nPass > 0 ? 'ok' : 'info');
            if (nPass > 0) _crudeToast(`🚀 ${passing[0].name} triggered!`, 'ok');
            else           _crudeToast(`🔍 No entry — ${summary}`, 'info');
            
            // ✨ NEW: Fetch and display pattern charts inline!
            if (msg) msg.textContent += ' | Fetching crude patterns...';
            await loadCrudePatternCharts(patternContainer, patternCards);

            await pollCrudeStatus();
        } else {
            const err = d.error || 'Unknown error';
            if (msg) msg.textContent = '❌ ' + err;
            _crudeLog('❌ Evaluate failed: ' + err, 'error');
            _crudeToast('❌ ' + err, 'error');
        }
    } catch (e) {
        if (msg) msg.textContent = '❌ ' + e.message;
        _crudeLog('❌ Network error: ' + e.message, 'error');
    }

    btn.disabled = false;
    btn.innerHTML = '🔍 Evaluate Signal Now';
}

// ✨ NEW: Load crude oil pattern charts inline after evaluation
async function loadCrudePatternCharts(container, cardsDiv) {
    try {
        // For crude, we'll use the same pattern detection API but with crude data
        // NOTE: We might need a separate crude pattern endpoint if crude uses different logic
        const patternsResp = await fetch('/api/crude/patterns?interval=5m');
        const patternsData = await patternsResp.json();
        
        if (!patternsData.success || !patternsData.patterns || patternsData.patterns.length === 0) {
            container.classList.add('hidden');
            return;
        }
        
        const patterns = patternsData.patterns;
        container.classList.remove('hidden');
        cardsDiv.innerHTML = '<div class="text-center text-gray-500 text-xs">📊 Loading crude charts...</div>';
        
        // Render pattern cards with charts
        let html = '';
        for (let i = 0; i < Math.min(patterns.length, 3); i++) {  // Show max 3 patterns
            const p = patterns[i];
            const biasColor = p.bias === 'bullish' ? 'green' : p.bias === 'bearish' ? 'red' : 'yellow';
            const biasEmoji = p.bias === 'bullish' ? '📈' : p.bias === 'bearish' ? '📉' : '↔️';
            
            html += `
                <div class="bg-gray-800 border border-gray-700 rounded-lg p-3 mb-3">
                    <div class="flex items-center justify-between mb-2">
                        <div class="flex items-center gap-2">
                            <span class="text-lg">${biasEmoji}</span>
                            <div>
                                <div class="text-sm font-bold text-white">${p.name}</div>
                                <div class="text-[10px] text-gray-400">${p.description?.substring(0, 60) || ''}...</div>
                            </div>
                        </div>
                        <div class="text-right">
                            <div class="text-xs font-black text-${biasColor}-400">${Math.round(p.confidence * 100)}%</div>
                            <div class="text-[9px] text-gray-500">confidence</div>
                        </div>
                    </div>
                    <div class="crude-pattern-chart-wrapper-${i}">
                        <div id="crude-pattern-chart-${i}" class="bg-black rounded-lg p-2 flex items-center justify-center overflow-hidden transition-all" style="min-height: 120px; max-height: 120px; cursor: pointer;" onclick="toggleCrudePatternChart(${i})">
                            <div class="text-gray-500 text-xs">⏳ Loading chart...</div>
                        </div>
                        <button onclick="toggleCrudePatternChart(${i})" class="w-full mt-1 text-[10px] text-gray-500 hover:text-white transition-colors">
                            <span id="crude-pattern-expand-btn-${i}">▼ Expand</span>
                        </button>
                    </div>
                </div>
            `;
        }
        
        cardsDiv.innerHTML = html;
        
        // Load each chart image
        for (let i = 0; i < Math.min(patterns.length, 3); i++) {
            loadCrudePatternChartImage(i);
        }
        
    } catch (error) {
        console.error('Failed to load crude pattern charts:', error);
        container.classList.add('hidden');
    }
}

// Load individual crude pattern chart image
async function loadCrudePatternChartImage(index) {
    const chartDiv = document.getElementById(`crude-pattern-chart-${index}`);
    if (!chartDiv) return;
    
    try {
        const chartResp = await fetch(`/api/crude/pattern-chart/${index}?interval=5m&lookback=40`);
        const chartData = await chartResp.json();
        
        if (chartData.success && chartData.image) {
            chartDiv.innerHTML = `
                <img src="data:image/png;base64,${chartData.image}" 
                     alt="Crude Pattern Chart" 
                     class="w-full h-auto rounded" />
            `;
        } else {
            chartDiv.innerHTML = `<div class="text-red-400 text-xs">⚠️ Chart failed: ${chartData.error || 'Unknown error'}</div>`;
        }
    } catch (error) {
        chartDiv.innerHTML = `<div class="text-red-400 text-xs">⚠️ ${error.message}</div>`;
    }
}

// Toggle crude pattern chart expansion
function toggleCrudePatternChart(index) {
    const chartDiv = document.getElementById(`crude-pattern-chart-${index}`);
    const btn = document.getElementById(`crude-pattern-expand-btn-${index}`);
    if (!chartDiv || !btn) return;
    
    const isCollapsed = chartDiv.style.maxHeight === '120px';
    
    if (isCollapsed) {
        chartDiv.style.maxHeight = '500px';
        chartDiv.style.cursor = 'zoom-out';
        btn.innerHTML = '▲ Collapse';
    } else {
        chartDiv.style.maxHeight = '120px';
        chartDiv.style.cursor = 'pointer';
        btn.innerHTML = '▼ Expand';
    }
}


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
            const isAuthErr = err.toLowerCase().includes('not authenticated')
                           || err.toLowerCase().includes('not auth');
            _crudeToast(`❌ ${err}`, 'error');
            _crudeLog(`❌ ${action.toUpperCase()} failed: ${err}`, 'error');
            if (isAuthErr) {
                // Kite session expired — prompt re-login
                _crudeLog(
                    '🔑 <a href="/kite/login" target="_blank" '
                    + 'style="color:#60a5fa;text-decoration:underline">'
                    + 'Click here to re-login to Zerodha</a>', 'warn'
                );
                _crudeToast('🔑 Zerodha session expired — re-login required', 'warn');
            }
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

// ──────────────────────────────────────────────────────────────────────
// Toggle Crude Settings Panel (collapsible)
// ──────────────────────────────────────────────────────────────────────
function toggleCrudeSettings() {
    const body = document.getElementById('crude-settings-body');
    const icon = document.getElementById('crude-settings-toggle-icon');
    const text = document.getElementById('crude-settings-toggle-text');
    
    if (!body || !icon || !text) return;
    
    const isHidden = body.classList.contains('hidden');
    
    if (isHidden) {
        // Show settings
        body.classList.remove('hidden');
        icon.textContent = '▼';
        text.textContent = 'Collapse';
    } else {
        // Hide settings
        body.classList.add('hidden');
        icon.textContent = '▶';
        text.textContent = 'Expand';
    }
}

// ──────────────────────────────────────────────────────────────────────
// Apply Crude Settings (validates + saves)
// ──────────────────────────────────────────────────────────────────────
async function applyCrudeSettings() {
    // Just call saveCrudeConfig which does all the validation + save
    await saveCrudeConfig();
}

async function saveCrudeConfig() {
    const sl        = parseFloat(document.getElementById('crude-sl')?.value);
    const trail     = parseFloat(document.getElementById('crude-trail')?.value);
    const rr        = parseFloat(document.getElementById('crude-rr')?.value);
    const capital   = parseFloat(document.getElementById('crude-capital')?.value);
    const maxTrades = parseInt(document.getElementById('crude-max-trades')?.value || '4', 10);
    const trailMode = document.querySelector('input[name="crude-trail-mode"]:checked')?.value || 'fixed';
    const atrMult   = parseFloat(document.getElementById('crude-atr-mult')?.value || '1.5');

    if ([sl, rr, capital].some(isNaN)) {
        _crudeToast('⚠️ Invalid settings — check all fields', 'warn');
        return;
    }
    if (isNaN(maxTrades) || maxTrades < 1 || maxTrades > 20) {
        _crudeToast('⚠️ Max Trades must be between 1 and 20', 'warn');
        return;
    }
    if (trailMode === 'fixed' && (isNaN(trail) || trail >= sl)) {
        _crudeToast('⚠️ Fixed trail must be a number smaller than SL', 'warn');
        return;
    }

    const params = new URLSearchParams({
        sl_points: sl, trail_points: trail || 25, rr_ratio: rr, capital,
        trail_mode: trailMode, atr_multiplier: atrMult, max_trades: maxTrades,
    });
    try {
        const resp = await fetch(`/api/crude/config?${params}`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            const modeLabel = { fixed: `Fixed ₹${trail}`, atr: `ATR×${atrMult}`, supertrend: 'Supertrend' };
            _crudeToast(
                `✅ Saved — SL:₹${sl}  Trail:${modeLabel[trailMode]}  R:R 1:${rr}  Max:${maxTrades}/day`,
                'ok'
            );
        } else {
            _crudeToast('❌ Save failed', 'error');
        }
    } catch (e) {
        _crudeToast(`❌ ${e.message}`, 'error');
    }
}

async function crudeResetDaily() {
    if (!confirm('Reset today’s trade counter?\nThis lets the trader take new entries again.\nActive positions are NOT affected.')) return;
    try {
        const resp = await fetch('/api/crude/reset-daily', { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            _crudeToast(`🔄 Daily counter reset — ${data.message}`, 'ok');
            await pollCrudeStatus();
        } else {
            _crudeToast('❌ Reset failed', 'error');
        }
    } catch (e) {
        _crudeToast(`❌ ${e.message}`, 'error');
    }
}

// ── Status poll ─────────────────────────────────────────────────────
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

    // ── Block reason card ──────────────────────────────────────────
    const blockCard = document.getElementById('crude-block-card');
    const blockMsg  = document.getElementById('crude-block-msg');
    if (blockCard && blockMsg) {
        const blkText = d.block_reason || '';
        // Show the card only when there's a block reason AND trader is running
        const showBlock = !!blkText && d.is_running;
        blockCard.classList.toggle('hidden', !showBlock);
        if (showBlock) blockMsg.textContent = blkText;
    }

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

    // ── Instrument badge ───────────────────────────────────────────────
    const symEl  = document.getElementById('crude-instrument');
    const tokEl  = document.getElementById('crude-instrument-token');
    if (symEl) {
        const sym = d.futures_symbol || '(not fetched yet)';
        const dte = d.days_to_expiry;
        let warn = '';
        if (dte != null && dte <= 5)  warn = dte <= 1 ? ' ⚠️ EXPIRY TODAY' : ` ⚠️ ${dte}d to expiry`;
        symEl.textContent = sym + warn;
        symEl.className   = dte != null && dte <= 2
            ? 'text-xs font-mono font-bold text-yellow-400 tracking-wide animate-pulse'
            : 'text-xs font-mono font-bold text-cyan-400 tracking-wide';
    }
    if (tokEl) tokEl.textContent = d.futures_token ? `token: ${d.futures_token}` : 'token: --';

    // ── Live price strip ───────────────────────────────────────────────
    _setText('crude-spot',       d.crude_price ? `₹${d.crude_price}` : '--');
    _setText('crude-option-ltp', _fmt(d.last_option_ltp));
    _setText('crude-signal',     d.block_reason || d.last_signal || '--');

    // ── Sync trail mode UI + live indicator values ────────────────────
    if (d.trail_mode) _applyCrudeTrailMode(d.trail_mode);
    if (d.last_atr) {
        const atrEl = document.getElementById('crude-atr-live');
        if (atrEl) atrEl.textContent = `ATR: ₹${d.last_atr.toFixed(0)}`;
    }
    if (d.last_st_line) {
        const stEl = document.getElementById('crude-st-live');
        if (stEl) stEl.textContent = `ST: ₹${d.last_st_line.toFixed(0)}`;
    }

    // ── Sync Max Trades input + live used badge ────────────────────
    if (d.max_trades != null) {
        const mtInput = document.getElementById('crude-max-trades');
        if (mtInput && document.activeElement !== mtInput) {
            // Only sync when user isn’t actively typing in the field
            mtInput.value = d.max_trades;
        }
    }
    const usedBadge = document.getElementById('crude-max-trades-used');
    if (usedBadge) {
        const used  = d.orders_placed ?? 0;
        const limit = d.max_trades   ?? 4;
        const pct   = limit > 0 ? used / limit : 0;
        const dateHint = d.trade_date ? ` (${d.trade_date})` : '';
        usedBadge.textContent = `${used} / ${limit} used${dateHint}`;
        usedBadge.className   = [
            'text-[10px] shrink-0',
            pct >= 1     ? 'text-red-400 font-bold'
          : pct >= 0.75  ? 'text-yellow-400'
          :                'text-gray-500',
        ].join(' ');
    }

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

    // ── Active trade card (Nifty-style full banner) ──────────────
    _renderCrudePositionBanner(at, d);

    _crudeRunning = d.is_running;
}

/** Extract strike from CRUDEOILM26APR8950PE → "8950 PE" */
function _crudeStrikeLabel(instrument) {
    if (!instrument) return '--';
    const clean = instrument.replace('MCX:', '');
    // e.g. CRUDEOILM26APR8950PE  or  CRUDEOIL26APR8950CE
    const m = clean.match(/(\d{3,})([CP]E)$/);
    if (!m) return clean;
    return `${m[1]} ${m[2]}`;
}

/** Render the full Nifty-style crude position banner. */
function _renderCrudePositionBanner(at, d) {
    const card   = document.getElementById('crude-trade-card');
    const noPos  = document.getElementById('ct-no-pos');
    const wrap   = document.getElementById('ct-banner-wrap');
    if (!card) return;

    if (!at) {
        card.classList.add('hidden');
        if (noPos) noPos.classList.remove('hidden');
        return;
    }

    // Position exists — show banner, hide placeholder
    card.classList.remove('hidden');
    if (noPos) noPos.classList.add('hidden');

    const isLong   = at.direction?.toLowerCase() === 'long';
    const isShort  = !isLong;
    const dirLabel = isLong ? 'LONG' : 'SHORT';
    const ltp      = at.last_ltp  ?? d.last_option_ltp ?? null;
    const ep       = at.entry_premium;
    const qty      = at.lots ?? at.quantity ?? 1;

    // ── Direction badge ────────────────────────────────────────────
    const badge = document.getElementById('ct-dir-badge');
    if (badge) {
        badge.textContent = dirLabel;
        badge.className   = isLong
            ? 'text-xs font-bold px-2 py-0.5 rounded bg-green-700 text-white'
            : 'text-xs font-bold px-2 py-0.5 rounded bg-red-700 text-white';
    }

    // Banner border colour — green for long, red for short
    if (wrap) {
        wrap.classList.remove('border-green-500', 'border-red-500',
                             'shadow-green-900\/20', 'shadow-red-900\/20');
        wrap.classList.add(
            isLong ? 'border-green-500' : 'border-red-500',
            isLong ? 'shadow-green-900/20' : 'shadow-red-900/20'
        );
    }

    // Paper badge
    const pb = document.getElementById('ct-paper-badge');
    if (pb) pb.classList.toggle('hidden', !at.paper);

    // ── Instrument label ───────────────────────────────────────────
    const instrEl = document.getElementById('ct-instr');
    if (instrEl) {
        instrEl.textContent = at.instrument?.replace('MCX:', '') ?? '--';
        instrEl.title       = at.instrument ?? '';
    }

    // ── Row 1: Option premiums + P&L ──────────────────────────────
    _setText('ct-entry-prem', ep != null ? `₹${ep.toFixed(1)}` : '--');
    _setText('ct-sl-prem',    at.sl_premium   != null ? `₹${at.sl_premium.toFixed(1)}`  : '--');
    _setText('ct-tgt-prem',   at.target_premium != null ? `₹${at.target_premium.toFixed(1)}` : '--');

    const pnl = at.pnl_unrealized;
    const pnlEl = document.getElementById('ct-upnl');
    if (pnlEl) {
        pnlEl.textContent = pnl != null ? `₹${pnl > 0 ? '+' : ''}${pnl.toFixed(0)}` : '--';
        pnlEl.className   = _pnlClass(pnl) + ' text-sm font-bold';
    }

    // ── Row 2: Live crude + option LTP + strike + trailing SL ─────
    const liveEl = document.getElementById('ct-crude-live');
    if (liveEl) {
        // crude_price is null when trader is not running — fall back to entry
        const cPrice = d.crude_price ?? at.entry_price;
        liveEl.textContent = cPrice ? `₹${cPrice.toLocaleString('en-IN')}` : '--';
        liveEl.title       = d.crude_price ? 'Live' : 'Entry price (trader not running)';
        liveEl.className   = d.crude_price
            ? 'text-sm font-bold text-yellow-300'
            : 'text-sm font-bold text-gray-400';
    }

    const ltpEl = document.getElementById('ct-opt-ltp');
    if (ltpEl) {
        const ltpVal = ltp ?? ep;  // fall back to entry premium if no live LTP
        ltpEl.textContent = ltpVal != null ? `₹${ltpVal.toFixed(1)}` : '--';
        ltpEl.title       = ltp != null ? 'Live LTP' : 'Entry premium (no live data)';
        ltpEl.className   = ltp != null
            ? 'text-sm font-bold text-white'
            : 'text-sm font-bold text-gray-400';
    }

    _setText('ct-strike', _crudeStrikeLabel(at.instrument));

    const trailEl = document.getElementById('ct-trail-sl');
    if (trailEl) {
        const tsl = at.trailing_sl ?? at.stop_loss;
        const orig = at.original_sl;
        const moved = orig && Math.abs(tsl - orig) > 0.5;
        trailEl.textContent = tsl != null ? `₹${tsl}` : '--';
        trailEl.className   = moved
            ? 'text-sm font-bold text-orange-300'
            : 'text-sm font-bold text-orange-400';
        trailEl.title = moved ? `Moved from original ₹${orig}` : 'At original SL';
    }

    // ── Row 3: Entry crude + SL + Target + Qty + Auto-exit ────────
    _setText('ct-entry',     at.entry_price != null ? `₹${at.entry_price.toLocaleString('en-IN')}` : '--');
    _setText('ct-sl',        at.stop_loss   != null ? `₹${at.stop_loss}`  : '--');
    _setText('ct-tgt',       at.target      != null ? `₹${at.target}`     : '--');
    _setText('ct-qty',       qty != null ? `${qty} lot${qty !== 1 ? 's' : ''}` : '--');
    _setText('ct-exit-time', d.exit_time ?? '--');
}

function _setText(id, val, cls = '') {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = val;
    if (cls) el.className = cls;
}

async function crudeAddLots() {
    const input = document.getElementById('ct-add-lots-input');
    const msg   = document.getElementById('ct-add-lots-msg');
    const btn   = document.querySelector('[onclick="crudeAddLots()"]');
    const lots  = parseInt(input?.value || '1', 10);

    if (!lots || lots < 1) {
        _crudeToast('⚠️ Enter a valid number of lots', 'warn');
        return;
    }

    // optimistic UI
    if (btn)  { btn.disabled = true; btn.textContent = '⏳ Placing…'; }
    if (msg)  { msg.textContent = ''; msg.classList.add('hidden'); }

    try {
        const resp = await fetch('/api/crude/add-lots', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ lots }),
        });
        const data = await resp.json();

        if (data.success) {
            const detail = `${data.new_qty} lots @ avg ₹${data.avg_premium?.toFixed(1)}`;
            _crudeToast(`✅ Added ${lots} lot(s) — now ${detail}`, 'ok');
            _crudeLog(`➕ Add-lots: +${lots} → ${detail} (order ${data.order_id ?? 'PAPER'})`, 'ok');
            if (msg) {
                msg.textContent = `✅ Now ${data.new_qty} lots @ avg ₹${data.avg_premium?.toFixed(1)}`;
                msg.className   = 'text-[11px] text-green-400';
            }
            await pollCrudeStatus();
        } else {
            const err = data.error ?? 'Unable to add lots';
            _crudeToast(`⛔ ${err}`, 'error');
            _crudeLog(`⛔ Add-lots blocked: ${err}`, 'error');
            if (msg) { msg.textContent = `⛔ ${err}`; msg.className = 'text-[11px] text-red-400'; }
        }
    } catch (e) {
        _crudeToast(`❌ Network error: ${e.message}`, 'error');
        _crudeLog(`❌ Add-lots network error: ${e.message}`, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '➕ Add to Position'; }
    }
}

async function pollCrudeStatus() {
    try {
        const resp = await fetch('/api/crude/status');
        if (!resp.ok) return;
        const data = await resp.json();
        renderCrudeStatus(data);
    } catch (_) { /* ignore network blips */ }
}

// ── Margin Health Card ─────────────────────────────────────────────
async function refreshCrudeMargin() {
    const elFree  = document.getElementById('crude-margin-free');
    const elUsed  = document.getElementById('crude-margin-used');
    const elNet   = document.getElementById('crude-margin-net');
    const lot1    = document.getElementById('crude-margin-1lot');
    const lot2    = document.getElementById('crude-margin-2lot');
    const badge   = document.getElementById('crude-margin-badge');
    const icon    = document.getElementById('crude-margin-icon');
    const sfEl    = document.getElementById('crude-margin-shortfall');
    if (!elFree) return;

    elFree.textContent = '…';
    try {
        const r = await fetch('/api/crude/margin');
        const d = await r.json();
        if (!d.success) {
            elFree.textContent = '❌ ' + (d.error || 'Error');
            return;
        }
        const fmt = v => v != null ? '₹' + Number(v).toLocaleString('en-IN') : '—';

        elFree.textContent = fmt(d.free);
        elUsed.textContent = fmt(d.utilised);
        elNet.textContent  = fmt(d.net);
        lot1.textContent   = fmt(d.margin_1lot);
        lot2.textContent   = fmt(d.margin_2lot);

        // Shortfall warning
        if (d.shortfall > 0 && sfEl) {
            sfEl.textContent = `⛔ Top-up ₹${Number(d.shortfall).toLocaleString('en-IN')} needed`;
            sfEl.classList.remove('hidden');
        } else if (sfEl) sfEl.classList.add('hidden');

        // Badge + icon
        const ml = d.max_lots || 0;
        if (ml >= 2) {
            badge.textContent = `✅ ${ml} lots OK`;
            badge.className = 'font-bold px-2 py-0.5 rounded-full bg-green-900 text-green-300 text-[10px]';
            icon.textContent = '✅';
        } else if (ml === 1) {
            badge.textContent = '⚠️ 1 lot only';
            badge.className = 'font-bold px-2 py-0.5 rounded-full bg-yellow-900 text-yellow-300 text-[10px]';
            icon.textContent = '⚠️';
        } else {
            badge.textContent = '⛔ Can\'t trade';
            badge.className = 'font-bold px-2 py-0.5 rounded-full bg-red-900 text-red-300 text-[10px]';
            icon.textContent = '⛔';
        }
    } catch (e) {
        if (elFree) elFree.textContent = '❌ Network error';
    }
}

// Auto-refresh margin every 60 s when the crude panel is visible
setInterval(() => {
    if (document.getElementById('crude-margin-avail')) refreshCrudeMargin();
}, 60_000);

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
    syncCrudeCapital();       // auto-sync Zerodha balance on tab open
    refreshCrudeMargin();     // show margin + lot affordability immediately
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
/**
 * crude-trader.js — UI logic for the MCX Crude Oil Auto-Trader tab.
 *
 * Polls /api/crude/status every 5s when the tab is visible.
 * All DOM IDs are prefixed 'crude-' to avoid collisions with Nifty AT.
 */

'use strict';

// ── State ────────────────────────────────────────────────────────────────────
let _crudePoller      = null;
let _crudeRunning     = false;
let _crudeKilled      = false;  // track kill switch state
let _crudeLastSignal  = null;   // dedup signal logs
let _crudeLastBlock   = null;   // dedup block_reason logs
let _crudeLastTrade   = null;   // dedup active trade logs
let _crudeLastSL      = null;   // track trailing SL changes

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

// ── Summarize block reason (make it concise for event log) ──────────────
function _summarizeBlockReason(blk) {
    if (!blk) return 'No valid setup';
    
    // Extract strategy names that blocked (lines starting with strategy name)
    const strategies = [];
    const lines = blk.split('║');
    for (const line of lines) {
        const match = line.trim().match(/^([^:]+)\([\d.]+\):/);
        if (match) strategies.push(match[1].trim());
    }
    
    if (strategies.length === 0) return 'No valid setup';
    if (strategies.length === 1) return `${strategies[0]} — no setup`;
    if (strategies.length === 2) return `${strategies[0]} & ${strategies[1]} — no setup`;
    return `${strategies.length} strategies evaluated — no valid setup`;
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
// ── Trail Mode UI ───────────────────────────────────────────
function onCrudeTrailModeChange(mode) {
    const fixedRow = document.getElementById('crude-trail-fixed-row');
    const atrRow = document.getElementById('crude-trail-atr-row');
    const stRow = document.getElementById('crude-trail-st-row');
    
    // Null checks to prevent crash if elements don't exist
    if (fixedRow) fixedRow.classList.toggle('hidden', mode !== 'fixed');
    if (atrRow) atrRow.classList.toggle('hidden', mode !== 'atr');
    if (stRow) stRow.classList.toggle('hidden', mode !== 'supertrend');
}

function _applyCrudeTrailMode(mode) {
    // Sync radio buttons to match server state
    document.querySelectorAll('input[name="crude-trail-mode"]').forEach(r => {
        r.checked = (r.value === mode);
    });
    onCrudeTrailModeChange(mode);
}


// ── Capital Sync from Zerodha ────────────────────────────────
// This shows live Zerodha balance for REFERENCE only.
// It does NOT change the Trading Budget slider.
// The budget is what the user deliberately configured — we should
// never auto-overwrite it with the full account balance.
async function syncCrudeCapital() {
    const btn         = document.getElementById('crude-capital-sync-btn');
    const availDisplay = document.getElementById('crude-available-margin-display');
    const hint        = document.getElementById('crude-capital-hint');

    if (btn) { btn.textContent = '⏳'; btn.disabled = true; }
    try {
        const r = await fetch('/api/crude/margin');
        const d = await r.json();
        if (d.success && d.free > 0) {
            const free = Math.floor(d.free);
            const used = Math.round(d.utilised || 0);
            const net  = Math.round(d.net || 0);

            // Show NET available (the real tradeable amount) in the display
            if (availDisplay) availDisplay.textContent = '\u20b9' + net.toLocaleString('en-IN');

            const detail = `net \u20b9${net.toLocaleString('en-IN')} | free \u20b9${free.toLocaleString('en-IN')} | used \u20b9${used.toLocaleString('en-IN')}`;
            if (hint) hint.textContent = `\u2705 Zerodha: ${detail}`;
            _crudeLog(`\ud83d\udcb0 Zerodha net available: \u20b9${net.toLocaleString('en-IN')} (free \u20b9${free.toLocaleString('en-IN')}, used \u20b9${used.toLocaleString('en-IN')})`, 'ok');
            _crudeToast(`\ud83d\udcb0 Net available: \u20b9${net.toLocaleString('en-IN')}`, 'ok');
        } else {
            const why = d.error || 'Could not fetch Zerodha balance';
            if (hint) hint.textContent = '\u26a0\ufe0f ' + why;
            _crudeToast('\u26a0\ufe0f ' + why, 'error');
        }
    } catch (e) {
        if (hint) hint.textContent = '\u274c ' + e.message;
        console.error('\u274c [Capital Sync] Failed:', e);
    }
    if (btn) { btn.textContent = '\ud83d\udd04 Sync'; btn.disabled = false; }
}

// 🐶 HELPER: Silent balance sync (info only — no toast, no slider change)
async function _syncCapitalFromZerodha() {
    const availDisplay = document.getElementById('crude-available-margin-display');
    try {
        const r = await fetch('/api/crude/margin');
        const d = await r.json();
        if (d.success && d.free > 0) {
            const net = Math.round(d.net || 0);
            const free = Math.floor(d.free);
            if (availDisplay) availDisplay.textContent = '\u20b9' + net.toLocaleString('en-IN');
            console.log(`\ud83d\udcb0 [Balance] Zerodha net: \u20b9${net.toLocaleString('en-IN')} | free: \u20b9${free.toLocaleString('en-IN')}`);
        } else {
            console.warn('\u26a0\ufe0f [Balance Sync] Failed:', d.error || 'No margin data');
        }
    } catch (e) {
        console.error('\u274c [Balance Sync] Error:', e);
    }
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

// ──────────────────────────────────────────────────────────────────────
// Force Entry (Long or Short) - Manual position entry
// ──────────────────────────────────────────────────────────────────────
async function crudeForceEntry(direction) {
    console.log(`🔨 [Force Entry] ${direction.toUpperCase()} clicked`);
    
    const btnLong = document.getElementById('crude-btn-force-long');
    const btnShort = document.getElementById('crude-btn-force-short');
    const isLong = direction.toLowerCase() === 'long';
    const btn = isLong ? btnLong : btnShort;
    
    if (!btn) return;
    
    // Confirm action
    const msg = `Force ${direction.toUpperCase()} entry?\n\nThis will:\n• Enter a ${direction.toUpperCase()} position immediately\n• Use current crude price as entry\n• Calculate SL/Target based on settings\n• Override all strategy checks\n\nContinue?`;
    if (!confirm(msg)) {
        console.log('❌ [Force Entry] User cancelled');
        return;
    }
    
    // Disable buttons
    btn.disabled = true;
    btn.textContent = '⏳ Entering...';
    if (btnLong) btnLong.disabled = true;
    if (btnShort) btnShort.disabled = true;
    
    try {
        console.log(`🌐 [Force Entry] Sending ${direction} request to API...`);
        const resp = await fetch(`/api/crude/force-entry?direction=${direction}`, { method: 'POST' });
        const data = await resp.json();
        
        console.log('📡 [Force Entry] API Response:', data);
        
        if (data.success) {
            _crudeToast(`✅ Force ${direction.toUpperCase()} entry executed!`, 'ok');
            _crudeLog(`🔨 Force ${direction.toUpperCase()} @ ₹${data.entry_price || '???'} | SL ₹${data.stop_loss || '???'} | Tgt ₹${data.target || '???'}`, 'trade');
            await pollCrudeStatus();  // Refresh immediately
        } else {
            const err = data.error || 'Unknown error';
            _crudeToast(`❌ Force entry failed: ${err}`, 'error');
            _crudeLog(`❌ Force ${direction} failed: ${err}`, 'error');
            console.error('❌ [Force Entry] Failed:', err);
        }
    } catch (e) {
        _crudeToast(`❌ ${e.message}`, 'error');
        console.error('❌ [Force Entry] Exception:', e);
    } finally {
        // Re-enable buttons
        btn.disabled = false;
        btn.textContent = isLong ? '📈 Force Long' : '📉 Force Short';
        if (btnLong) btnLong.disabled = false;
        if (btnShort) btnShort.disabled = false;
    }
}

// ──────────────────────────────────────────────────────────────────────
// Force Exit - Manually close active position
// ──────────────────────────────────────────────────────────────────────
async function crudeForceExit() {
    console.log('🚪 [Force Exit] Clicked');
    
    const btn = document.getElementById('crude-btn-force-exit');
    if (!btn) return;
    
    // Confirm action
    if (!confirm('Force exit active position?\n\nThis will close your position at market price.\n\nContinue?')) {
        console.log('❌ [Force Exit] User cancelled');
        return;
    }
    
    btn.disabled = true;
    btn.textContent = '⏳ Exiting...';
    
    try {
        console.log('🌐 [Force Exit] Sending request to API...');
        const resp = await fetch('/api/crude/force-exit', { method: 'POST' });
        const data = await resp.json();
        
        console.log('📡 [Force Exit] API Response:', data);
        
        if (data.success) {
            _crudeToast('✅ Position closed manually', 'ok');
            _crudeLog(`🚪 Force EXIT @ ₹${data.exit_price || '???'} | P&L ₹${data.pnl || '???'}`, 'ok');
            await pollCrudeStatus();  // Refresh immediately
        } else {
            const err = data.error || 'Unknown error';
            _crudeToast(`❌ Force exit failed: ${err}`, 'error');
            _crudeLog(`❌ Force exit failed: ${err}`, 'error');
            console.error('❌ [Force Exit] Failed:', err);
        }
    } catch (e) {
        _crudeToast(`❌ ${e.message}`, 'error');
        console.error('❌ [Force Exit] Exception:', e);
    } finally {
        btn.disabled = false;
        btn.textContent = '🚪 Force Exit';
    }
}

// ── ✨ NEW: Load crude oil pattern charts inline after evaluation
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
    console.log('🔧 [Settings] Apply Settings clicked!');
    const btn = document.getElementById('crude-apply-settings-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ Applying...';
        btn.className = 'w-full bg-gray-600 text-white font-bold py-2 rounded-lg transition cursor-wait';
    }
    
    try {
        // Save config settings
        await saveCrudeConfig();
        // 🎯 Save strategy selection
        await saveCrudeStrategies();
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.className = 'w-full bg-[#0053e2] hover:bg-[#0046c7] text-white font-bold py-2 rounded-lg transition';
            btn.textContent = '✅ Applied!';
            setTimeout(() => {
                btn.textContent = '✅ Apply Settings';
            }, 2000);
        }
    }
}

async function saveCrudeConfig() {
    console.log('💾 [Settings] Starting save...');
    
    // Read all values from UI
    const sl        = parseFloat(document.getElementById('crude-sl')?.value);
    const trail     = parseFloat(document.getElementById('crude-trail-points')?.value || '25');
    const rr        = parseFloat(document.getElementById('crude-rr')?.value);
    const capital   = parseFloat(document.getElementById('crude-capital')?.value);
    const maxTrades = parseInt(document.getElementById('crude-max-trades')?.value || '4', 10);
    const maxLoss   = parseFloat(document.getElementById('crude-max-loss')?.value || '5000');
    const trailMode = window._crudeTrailMode || 'atr1.5';
    const strikeOffset = window._crudeStrikeOffset !== undefined ? window._crudeStrikeOffset : 0;
    
    // Extract ATR multiplier from mode (backend will parse this)
    let atrMult = 1.5;  // default
    if (trailMode === 'atr0.4') atrMult = 0.4;
    else if (trailMode === 'atr0.7') atrMult = 0.7;
    else if (trailMode === 'atr1.5') atrMult = 1.5;
    else if (trailMode === 'atr2') atrMult = 2.0;

    console.log('📊 [Settings] RAW VALUES FROM UI:');
    console.log('  - SL input value:', document.getElementById('crude-sl')?.value);
    console.log('  - Trail input value:', document.getElementById('crude-trail-points')?.value);
    console.log('  - R:R select value:', document.getElementById('crude-rr')?.value);
    console.log('  - Capital slider value:', document.getElementById('crude-capital')?.value);
    console.log('  - Max Trades slider value:', document.getElementById('crude-max-trades')?.value);
    console.log('  - Max Loss slider value:', document.getElementById('crude-max-loss')?.value);
    console.log('  - Trail Mode (from window):', window._crudeTrailMode);
    console.log('  - Strike Offset (from window):', window._crudeStrikeOffset);
    console.log('');
    console.log('📊 [Settings] PARSED VALUES:');
    console.log('  sl:', sl, 'trail:', trail, 'rr:', rr, 'capital:', capital);
    console.log('  maxTrades:', maxTrades, 'maxLoss:', maxLoss);
    console.log('  trailMode:', trailMode, 'strikeOffset:', strikeOffset, 'atrMult:', atrMult);

    // Validation
    if ([sl, rr, capital].some(isNaN)) {
        _crudeToast('⚠️ Invalid settings — check all fields', 'warn');
        console.error('❌ [Settings] Validation failed: NaN values', { sl, rr, capital });
        return;
    }
    if (isNaN(maxTrades) || maxTrades < 1 || maxTrades > 20) {
        _crudeToast('⚠️ Max Trades must be between 1 and 20', 'warn');
        console.error('❌ [Settings] Validation failed: maxTrades out of range', maxTrades);
        return;
    }

    const params = new URLSearchParams({
        sl_points: sl, 
        trail_points: trail, 
        rr_ratio: rr, 
        capital,
        trail_mode: trailMode, 
        atr_multiplier: atrMult, 
        max_trades: maxTrades,
        max_daily_loss: maxLoss,
        strike_offset: strikeOffset,
    });
    
    console.log('🌐 [Settings] Sending to API:', params.toString());
    
    try {
        const resp = await fetch(`/api/crude/config?${params}`, { method: 'POST' });
        console.log('📡 [Settings] API Response:', resp.status, resp.statusText);
        const data = await resp.json();
        console.log('📊 [Settings] Response data:', data);
        if (data.success) {
            const modeLabel = { 
                'off': 'Off',
                'atr0.4': 'ATR×0.4',
                'atr0.7': 'ATR×0.7',
                'atr1.5': 'ATR×1.5',
                'atr2': 'ATR×2',
                'premium': 'Premium%',
                'atr': 'ATR×1.5'  // fallback
            };
            const msg = `✅ Saved — SL:₹${sl}  Trail:${modeLabel[trailMode] || trailMode}  R:R 1:${rr}  Cap:₹${capital.toLocaleString('en-IN')}  Max:${maxTrades}/day`;
            _crudeToast(msg, 'ok');
            // 🐶 ADD EVENT LOG!
            _crudeLog(`⚙️ Settings saved: SL ₹${sl} | Trail ${modeLabel[trailMode]} | R:R 1:${rr} | Capital ₹${capital.toLocaleString('en-IN')} | Max ${maxTrades}/day | Strike ${strikeOffset === 0 ? 'ATM' : strikeOffset > 0 ? `ATM+${strikeOffset}` : `ATM${strikeOffset}`}`, 'ok');
            console.log('✅ [Settings] Settings saved successfully!');
            
            // 🐶 CRITICAL FIX: Reload settings from API response to update UI!
            console.log('🔄 [Settings] Reloading UI from API response...');
            await loadCrudeSettings();
            console.log('✅ [Settings] UI updated with saved values!');
        } else {
            _crudeToast('❌ Save failed', 'error');
            _crudeLog('❌ Settings save failed', 'error');
            console.error('❌ [Settings] API returned success=false:', data);
        }
    } catch (e) {
        _crudeToast(`❌ ${e.message}`, 'error');
        console.error('❌ [Settings] Exception:', e);
    }
}

// ▼▼▼ 🎯 STRATEGY SELECTION FUNCTIONS (NEW!) ▼▼▼

async function loadCrudeStrategies() {
    console.log('🎯 [Strategies] Loading strategy list...');
    try {
        const resp = await fetch('/api/crude/strategies');
        const data = await resp.json();
        
        if (!data.success) {
            console.error('❌ [Strategies] Failed to load:', data);
            return;
        }
        
        const container = document.getElementById('crude-strategy-checkboxes');
        const countBadge = document.getElementById('crude-strategy-count-badge');
        
        if (!container) {
            console.error('❌ [Strategies] Container not found!');
            return;
        }
        
        // Build checkbox grid
        container.innerHTML = data.strategies.map(s => `
            <label class="flex items-center gap-2 p-2 rounded-lg bg-gray-800/80 border border-gray-700 hover:border-[#0053e2] transition cursor-pointer group">
                <input type="checkbox" 
                    id="strategy-${s.id}" 
                    data-strategy-id="${s.id}"
                    class="crude-strategy-checkbox w-4 h-4 rounded border-gray-600 text-[#0053e2] focus:ring-2 focus:ring-[#0053e2] focus:ring-offset-0 cursor-pointer"
                    ${s.enabled ? 'checked' : ''}
                    onchange="updateCrudeStrategyCount()">
                <div class="flex-1">
                    <div class="flex items-center gap-1.5">
                        <span class="text-sm">${s.emoji}</span>
                        <span class="text-[11px] font-bold text-white group-hover:text-[#ffc220] transition">${s.name}</span>
                        <span class="text-[9px] px-1.5 py-0.5 rounded-full bg-gray-700 text-gray-300">${s.category}</span>
                    </div>
                    <div class="text-[9px] text-gray-500 mt-0.5">Win rate: ${s.win_rate}%</div>
                </div>
            </label>
        `).join('');
        
        // Update count badge
        const enabledCount = data.strategies.filter(s => s.enabled).length;
        const totalCount = data.strategies.length;
        if (countBadge) {
            countBadge.textContent = data.all_enabled ? `${totalCount}/${totalCount} Active` : `${enabledCount}/${totalCount} Active`;
            countBadge.className = enabledCount === totalCount 
                ? 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-green-600 text-white'
                : 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-[#0053e2] text-white';
        }
        
        console.log(`✅ [Strategies] Loaded ${enabledCount}/${totalCount} strategies`);
        
    } catch (e) {
        console.error('❌ [Strategies] Load error:', e);
    }
}

function updateCrudeStrategyCount() {
    const checkboxes = document.querySelectorAll('.crude-strategy-checkbox');
    const enabledCount = Array.from(checkboxes).filter(cb => cb.checked).length;
    const totalCount = checkboxes.length;
    
    const countBadge = document.getElementById('crude-strategy-count-badge');
    if (countBadge) {
        countBadge.textContent = `${enabledCount}/${totalCount} Active`;
        countBadge.className = enabledCount === totalCount
            ? 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-green-600 text-white'
            : 'px-2 py-0.5 rounded-full text-[10px] font-bold bg-[#0053e2] text-white';
    }
}

function crudeSelectAllStrategies(selectAll) {
    const checkboxes = document.querySelectorAll('.crude-strategy-checkbox');
    checkboxes.forEach(cb => cb.checked = selectAll);
    updateCrudeStrategyCount();
    
    const action = selectAll ? '🟢 All strategies enabled' : '⚪ None selected';
    _crudeToast(action, selectAll ? 'ok' : 'warn');
}

async function saveCrudeStrategies() {
    console.log('🎯 [Strategies] Saving selection...');
    const checkboxes = document.querySelectorAll('.crude-strategy-checkbox');
    const enabledStrategies = Array.from(checkboxes)
        .filter(cb => cb.checked)
        .map(cb => cb.dataset.strategyId);
    
    console.log('🎯 [Strategies] Enabled:', enabledStrategies);
    
    try {
        const resp = await fetch('/api/crude/strategies', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled_strategies: enabledStrategies })
        });
        
        const data = await resp.json();
        
        if (data.success) {
            const count = data.enabled_count;
            const total = data.total_count;
            const msg = data.all_enabled 
                ? `✅ All ${total} strategies enabled` 
                : `✅ ${count}/${total} strategies enabled`;
            _crudeToast(msg, 'ok');
            console.log('✅ [Strategies] Saved:', data);
        } else {
            _crudeToast(`❌ ${data.error}`, 'error');
            console.error('❌ [Strategies] Save failed:', data);
        }
    } catch (e) {
        _crudeToast(`❌ ${e.message}`, 'error');
        console.error('❌ [Strategies] Save error:', e);
    }
}

// ▲▲▲ END STRATEGY SELECTION ▲▲▲

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
    const wasKilled  = _crudeKilled;  // Track kill switch state too
    
    // Debug state changes
    if (d.is_running !== wasRunning) {
        console.log(`[State Change] Running: ${wasRunning} → ${d.is_running}`);
    }
    
    // Only log state changes, not every poll!
    if (d.is_running !== wasRunning) {
        if (d.is_running && !wasRunning) {
            _crudeLog('▶ Crude trader STARTED', 'ok');
        } else if (!d.is_running && wasRunning) {
            _crudeLog('⏹ Crude trader STOPPED', 'warn');
        }
    }
    
    // Log kill switch activation (only once)
    if (d.kill_switch && !wasKilled) {
        _crudeLog('🚨 Kill switch activated — position exited', 'error');
    }
    
    // Update tracked states (do this at start of function, not end!)
    _crudeRunning = d.is_running;
    _crudeKilled  = d.kill_switch;

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
    
    // Log signals (strategy triggers)
    if (sig && sig !== _crudeLastSignal) {
        const isTradeSignal = sig.startsWith('[ST]') || sig.startsWith('[ORB]') || sig.toLowerCase().includes('entered');
        _crudeLog(`📡 Signal: ${sig}`, isTradeSignal ? 'trade' : 'info');
        _crudeLastSignal = sig;
    }
    
    // Log block reasons ONLY when they change (not every poll!)
    if (blk && blk !== _crudeLastBlock) {
        // Make block reason more concise - just show summary, not full details
        const blockSummary = _summarizeBlockReason(blk);
        _crudeLog(`⚠️ ${blockSummary}`, 'warn');
        _crudeLastBlock = blk;
    }

    // ── Active trade change ───────────────────────────────────────────────────────
    const tradeId = d.active_trade?.id ?? null;
    if (tradeId !== _crudeLastTrade) {
        if (tradeId) {
            const at = d.active_trade;
            _crudeLog(`🛢️ Trade OPEN: ${at.direction?.toUpperCase()} @ ₹${at.entry_price} | SL ₹${at.stop_loss} | Tgt ₹${at.target}`, 'trade');
            // Initialize SL tracking for new trade
            _crudeLastSL = at.stop_loss;
        } else if (_crudeLastTrade) {
            _crudeLog('🏁 Trade CLOSED', 'ok');
            // Clear SL tracking when trade closes
            _crudeLastSL = null;
        }
        _crudeLastTrade = tradeId;
    }

    // ── Trailing SL change detection ─────────────────────────────────────────────
    if (tradeId && d.active_trade?.sl_premium != null) {
        const currentSL = d.active_trade.sl_premium;
        const entrySL = d.active_trade.entry_premium;  // for reference
        // Only log if SL has actually changed (and not first poll)
        if (_crudeLastSL != null && Math.abs(currentSL - _crudeLastSL) > 0.1) {
            const dir = d.active_trade.direction?.toUpperCase() || 'UNKNOWN';
            // For option buyers (we ALWAYS buy options): SL moving UP = tightening (profitable)
            const slMoved = currentSL > _crudeLastSL ? '💚 tightened' : '🟡 adjusted';
            const delta = (currentSL - _crudeLastSL).toFixed(1);
            const sign = currentSL > _crudeLastSL ? '+' : '';
            _crudeLog(`📏 SL Premium ${slMoved}: ₹${_crudeLastSL.toFixed(1)} → ₹${currentSL.toFixed(1)} (${sign}${delta})`, 'ok');
        }
        _crudeLastSL = currentSL;
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

    // ── Live price strip ───────────────────────────────────────────
    _setText('crude-spot',       d.crude_price ? `₹${d.crude_price}` : '--');
    _setText('crude-option-ltp', _fmt(d.last_option_ltp));
    _setText('crude-signal',     d.block_reason || d.last_signal || '--');
    
    // Orders count
    const ordersEl = document.getElementById('crude-orders-count');
    if (ordersEl) {
        const used = d.orders_placed ?? 0;
        const limit = d.max_trades ?? 4;
        ordersEl.textContent = `${used}/${limit}`;
        ordersEl.className = used >= limit ? 'text-xs font-bold text-red-400' : 'text-xs font-bold text-gray-300';
    }
    
    // Exit time display
    _setText('crude-exit-time-display', d.exit_time ?? '23:25');

    // 🐶 NEW: Render backend event_log (contains heartbeats + evals!)
    if (d.event_log && Array.isArray(d.event_log)) {
        const logEl = document.getElementById('crude-event-log');
        if (logEl) {
            // Clear and repopulate (server event log is authoritative)
            logEl.innerHTML = '';
            
            // Render up to 50 most recent events
            const events = d.event_log.slice(0, 50);
            for (const evt of events) {
                const row = document.createElement('div');
                row.className = 'flex gap-2 items-start';
                
                // Color based on icon/label
                let color = 'text-gray-400';
                const icon = evt.icon || '';
                const label = evt.label || '';
                
                if (icon.includes('💓') || label.includes('Heartbeat')) color = 'text-pink-400';
                else if (icon.includes('🔍') || label.includes('Eval')) color = 'text-blue-400';
                else if (icon.includes('📡') || label.includes('Signal')) color = 'text-cyan-400';
                else if (icon.includes('⚠️') || label.includes('No valid')) color = 'text-yellow-400';
                else if (icon.includes('👉') || label.includes('OPENED')) color = 'text-spark-100';
                else if (icon.includes('👈') || label.includes('EXITED')) color = 'text-green-400';
                
                const detail = evt.detail || '';
                const fullText = detail ? `${icon} ${label}: ${detail}` : `${icon} ${label}`;
                
                row.innerHTML = `
                    <span class="text-gray-600 shrink-0">${evt.ts || ''}</span>
                    <span class="${color} break-all">${fullText}</span>
                `;
                logEl.appendChild(row);
            }
        }
    }

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

    // ── Show/Hide Force Exit button based on position ───────────────────
    const btnForceExit = document.getElementById('crude-btn-force-exit');
    const btnForceLong = document.getElementById('crude-btn-force-long');
    const btnForceShort = document.getElementById('crude-btn-force-short');
    
    if (at) {
        // Position open: show Force Exit, hide Force Long/Short
        if (btnForceExit) btnForceExit.classList.remove('hidden');
        if (btnForceLong) btnForceLong.classList.add('hidden');
        if (btnForceShort) btnForceShort.classList.add('hidden');
    } else {
        // No position: hide Force Exit, show Force Long/Short
        if (btnForceExit) btnForceExit.classList.add('hidden');
        if (btnForceLong) btnForceLong.classList.remove('hidden');
        if (btnForceShort) btnForceShort.classList.remove('hidden');
    }

    // ── Active trade card (Nifty-style full banner) ──────────────
    _renderCrudePositionBanner(at, d);
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
                             'shadow-green-900/20', 'shadow-red-900/20');
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

    // ── Row 1: Option premiums + P&L ────────────────────────────
    const pnl = at.pnl_unrealized;
    _setText('ct-entry-prem', ep != null ? `₹${ep.toFixed(1)}` : '--');
    _setText('ct-sl-prem',    at.sl_premium   != null ? `₹${at.sl_premium.toFixed(1)}`  : '--');
    _setText('ct-tgt-prem',   at.target_premium != null ? `₹${at.target_premium.toFixed(1)}` : '--');
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
        // ✅ FIX: Show PREMIUM-based trailing SL, not crude spot price!
        const tslPrem = at.sl_premium;           // premium-based trail SL
        const origPrem = at.entry_premium;       // original entry premium (for comparison)
        const moved = origPrem && tslPrem && Math.abs(tslPrem - origPrem) > 0.5;
        trailEl.textContent = tslPrem != null ? `₹${tslPrem.toFixed(1)}` : '--';
        trailEl.className   = moved
            ? 'text-sm font-bold text-orange-300'  // tightened!
            : 'text-sm font-bold text-orange-400'; // at original
        trailEl.title = moved 
            ? `Trail SL premium (moved from entry ₹${origPrem.toFixed(1)})` 
            : 'Trail SL premium (at entry level)';
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
    const compact = document.getElementById('crude-margin-compact');
    const elFree  = document.getElementById('crude-margin-free');
    const elUsed  = document.getElementById('crude-margin-used');
    const elNet   = document.getElementById('crude-margin-net');
    
    if (!elFree) return;
    
    // Toggle display
    if (compact) {
        if (compact.classList.contains('hidden')) {
            compact.classList.remove('hidden');
        } else {
            compact.classList.add('hidden');
            return; // Just hide, don't fetch again
        }
    }

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
    } catch (e) {
        if (elFree) elFree.textContent = '❌ Network error';
    }
}

// Auto-refresh margin every 60 s when the crude panel is visible
setInterval(() => {
    if (document.getElementById('crude-margin-avail')) refreshCrudeMargin();
}, 60_000);

// ──────────────────────────────────────────────────────────────────────
// Load settings from API and populate UI inputs
// ──────────────────────────────────────────────────────────────────────
async function loadCrudeSettings() {
    console.log('💾 [Settings] Loading from API...');
    try {
        const resp = await fetch('/api/crude/status');
        const data = await resp.json();
        
        console.log('📊 [Settings] API data:', data);
        
        // Populate SL input
        const slInput = document.getElementById('crude-sl');
        if (slInput && data.sl_points != null) {
            slInput.value = data.sl_points;
            console.log('🔄 [Settings] Set SL to:', data.sl_points);
        }
        
        // Populate Trail Points input
        const trailInput = document.getElementById('crude-trail-points');
        if (trailInput && data.trail_points != null) {
            trailInput.value = data.trail_points;
            console.log('🔄 [Settings] Set Trail Points to:', data.trail_points);
        }
        
        // Populate R:R select
        const rrInput = document.getElementById('crude-rr');
        if (rrInput && data.rr_ratio != null) {
            rrInput.value = data.rr_ratio;
            console.log('🔄 [Settings] Set R:R to:', data.rr_ratio);
        }
        
        // 🐶 Populate Capital (trading budget) slider
        const capInput = document.getElementById('crude-capital');
        const capDisplay = document.getElementById('crude-capital-display');
        if (capInput && data.capital != null) {
            capInput.value = Math.round(data.capital);
            if (capDisplay) capDisplay.textContent = Math.round(data.capital).toLocaleString('en-IN');
            console.log('🔄 [Settings] Set Capital to:', data.capital);
        }
        // Show live Zerodha balance next to budget so user sees both at a glance
        const availDisplay = document.getElementById('crude-available-margin-display');
        if (availDisplay && data.available_margin != null && data.available_margin > 0) {
            availDisplay.textContent = '\u20b9' + Math.round(data.available_margin).toLocaleString('en-IN');
        }
        
        // 🐶 Populate Max Trades slider
        const maxTradesInput = document.getElementById('crude-max-trades');
        const maxTradesBadge = document.getElementById('crude-max-trades-badge');
        if (maxTradesInput && data.max_trades != null) {
            maxTradesInput.value = data.max_trades;
            if (maxTradesBadge) maxTradesBadge.textContent = data.max_trades;
            console.log('🔄 [Settings] Set Max Trades to:', data.max_trades);
        }
        
        // 🐶 Populate Max Loss slider
        const maxLossInput = document.getElementById('crude-max-loss');
        const maxLossDisplay = document.getElementById('crude-max-loss-display');
        if (maxLossInput && data.max_daily_loss != null) {
            maxLossInput.value = data.max_daily_loss;
            if (maxLossDisplay) maxLossDisplay.textContent = Math.round(data.max_daily_loss).toLocaleString('en-IN');
            console.log('🔄 [Settings] Set Max Loss to:', data.max_daily_loss);
        }
        
        // 🐶 Set Trail Mode buttons - map 'atr' to specific multiplier
        let trailMode = data.trail_mode || 'atr1.5';
        // If backend returns old 'atr' format, convert based on atr_multiplier
        if (trailMode === 'atr') {
            const mult = data.atr_multiplier || 1.5;
            if (mult === 0.4) trailMode = 'atr0.4';
            else if (mult === 0.7) trailMode = 'atr0.7';
            else if (mult === 1.5) trailMode = 'atr1.5';
            else if (mult === 2) trailMode = 'atr2';
            else trailMode = 'atr1.5';  // default
        }
        setCrudeTrailMode(trailMode);
        console.log('🔄 [Settings] Set Trail Mode to:', trailMode);
        
        // 🐶 Set Strike Offset buttons
        if (data.strike_offset != null) {
            setCrudeStrike(data.strike_offset);
            console.log('🔄 [Settings] Set Strike Offset to:', data.strike_offset);
        } else {
            setCrudeStrike(0);  // default ATM
            console.log('🔄 [Settings] Set Strike Offset to: 0 (default ATM)');
        }
        
        console.log('✅ [Settings] All settings loaded successfully!');
        
        // 🐶 AUTO-SYNC CAPITAL FROM ZERODHA AFTER LOADING SETTINGS!
        console.log('💰 [Settings] Auto-syncing capital from Zerodha...');
        await _syncCapitalFromZerodha();  // Updates UI only, doesn't save
        console.log('✅ [Settings] Capital synced (UI updated, not saved)');
        
    } catch (e) {
        console.error('❌ [Settings] Failed to load:', e);
    }
}

// ──────────────────────────────────────────────────────────────────────
// Sync positions from Zerodha (pull any existing crude options)
// ──────────────────────────────────────────────────────────────────────
async function crudeSyncPositions() {
    console.log('🔄 [Sync] Syncing positions from Zerodha...');
    const btn = document.getElementById('crude-btn-sync');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ Syncing...';
    }
    
    try {
        const resp = await fetch('/api/crude/sync-positions', { method: 'POST' });
        const data = await resp.json();
        
        console.log('📡 [Sync] API Response:', data);
        
        if (data.success) {
            if (data.found) {
                _crudeToast(`✅ Synced! Found ${data.direction?.toUpperCase()} position`, 'ok');
                _crudeLog(`🔄 Synced from Zerodha: ${data.direction?.toUpperCase()} ${data.instrument}`, 'ok');
            } else {
                _crudeToast('🔍 No crude options found in Zerodha', 'info');
                _crudeLog('🔄 Sync: No crude options in Zerodha positions', 'info');
            }
            console.log('🔄 [Sync] About to refresh status...');
            await pollCrudeStatus();  // Refresh immediately
            console.log('🔄 [Sync] Status refreshed, checking banner render...');
            
            // Force another status check after 1 second to ensure banner updates
            setTimeout(async () => {
                console.log('🔄 [Sync] Secondary refresh...');
                await pollCrudeStatus();
            }, 1000);
        } else {
            const err = data.error || 'Unknown error';
            _crudeToast(`❌ Sync failed: ${err}`, 'error');
            console.error('❌ [Sync] Failed:', err);
        }
    } catch (e) {
        _crudeToast(`❌ ${e.message}`, 'error');
        console.error('❌ [Sync] Exception:', e);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '🔄 Sync Zerodha';
        }
    }
}

// ── Trade history ─────────────────────────────────────────
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

// ── Lifecycle (called by dashboard.js switchPage) ─────────
function onCrudeTraderTabOpen() {
    console.log('👀 [Crude] Tab opened - loading settings...');
    _crudeLog('👁 Crude trader tab opened', 'info');
    pollCrudeStatus();        // This loads settings via renderCrudeStatus
    loadCrudeSettings();      // ✅ Loads settings + auto-syncs capital from Zerodha!
    loadCrudeStrategies();    // 🎯 Load strategy checkboxes
    loadCrudeHistory();
    // syncCrudeCapital() removed - loadCrudeSettings() now does it automatically!
    refreshCrudeMargin();     // show margin + lot affordability immediately
    _crudePoller = setInterval(pollCrudeStatus, 5000);
    console.log('✅ [Crude] Tab opened - poller started');
}

function onCrudeTraderTabClose() {
    clearInterval(_crudePoller);
    _crudePoller = null;
}

// 🐶 Explicitly expose functions to window for onclick handlers
window.applyCrudeSettings = applyCrudeSettings;
window.toggleCrudeSettings = toggleCrudeSettings;
window.setCrudeStrike = setCrudeStrike;  // 🐶 NEW!
window.setCrudeTrailMode = setCrudeTrailMode;  // 🐶 NEW!
window.crudeSelectAllStrategies = crudeSelectAllStrategies;  // 🎯 NEW!
window.updateCrudeStrategyCount = updateCrudeStrategyCount;  // 🎯 NEW!
window.crudeTrade = crudeTrade;
window.crudeManualEvaluate = crudeManualEvaluate;
window.crudeForceEntry = crudeForceEntry;
window.crudeForceExit = crudeForceExit;
window.crudeSyncPositions = crudeSyncPositions;
window.crudeResetDaily = crudeResetDaily;
window.crudeAddLots = crudeAddLots;
window.syncCrudeCapital = syncCrudeCapital;
window.refreshCrudeMargin = refreshCrudeMargin;
window.onCrudeTraderTabOpen = onCrudeTraderTabOpen;  // 🐛 CRITICAL FIX!
window.onCrudeTraderTabClose = onCrudeTraderTabClose;  // 🐛 CRITICAL FIX!

// ── Strike Offset Picker ──────────────────────────────
function setCrudeStrike(offset) {
    // Store value for later use in applyCrudeSettings
    window._crudeStrikeOffset = offset;
    
    // Update button styles
    document.querySelectorAll('.crude-strike-btn').forEach(btn => {
        const btnOffset = parseInt(btn.getAttribute('data-offset'));
        if (btnOffset === offset) {
            btn.className = 'crude-strike-btn flex-1 text-[10px] px-1 py-2 font-bold border border-gray-600 bg-[#0053e2] text-white transition';
        } else {
            btn.className = 'crude-strike-btn flex-1 text-[10px] px-1 py-2 font-bold border border-gray-600 bg-gray-700 text-gray-300 transition hover:bg-gray-600';
        }
        // Add border-radius to first/last
        if (btnOffset === -1) btn.classList.add('rounded-l-lg');
        if (btnOffset === 1) btn.classList.add('rounded-r-lg');
    });
}

// ── Trail Mode Picker ───────────────────────────────
function setCrudeTrailMode(mode) {
    // Store value
    window._crudeTrailMode = mode;
    
    // Update button styles
    document.querySelectorAll('.crude-trail-pill').forEach(btn => {
        const btnMode = btn.getAttribute('data-trail');
        if (btnMode === mode) {
            btn.className = 'crude-trail-pill flex-1 text-[11px] py-1 rounded border-2 border-[#0053e2] bg-[#0053e2] text-white font-bold transition';
        } else {
            btn.className = 'crude-trail-pill flex-1 text-[11px] py-1 rounded border border-gray-600 text-gray-400 hover:bg-gray-700 transition';
        }
    });
    
    // Update description
    const descEl = document.getElementById('crude-trail-desc');
    const trailRow = document.getElementById('crude-trail-points-row');
    
    if (mode === 'off') {
        descEl.textContent = 'No trailing - SL stays fixed at entry level';
        if (trailRow) trailRow.classList.add('hidden');
    } else if (mode === 'atr0.4') {
        descEl.textContent = 'Trail 0.4× ATR - tight, prevents whipsaw (RECOMMENDED)';
        if (trailRow) trailRow.classList.add('hidden');
    } else if (mode === 'atr0.7') {
        descEl.textContent = 'Trail 0.7× ATR - balanced, medium volatility';
        if (trailRow) trailRow.classList.add('hidden');
    } else if (mode === 'atr1.5') {
        descEl.textContent = 'Trail 1.5× ATR - loose, high volatility';
        if (trailRow) trailRow.classList.add('hidden');
    } else if (mode === 'atr2') {
        descEl.textContent = 'Trail 2× ATR - very loose, maximize profit';
        if (trailRow) trailRow.classList.add('hidden');
    } else if (mode === 'premium') {
        descEl.textContent = 'Trail based on premium % change';
        if (trailRow) trailRow.classList.remove('hidden');
    } else {
        descEl.textContent = 'ATR-based dynamic trailing';
        if (trailRow) trailRow.classList.add('hidden');
    }
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
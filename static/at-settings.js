// ── Auto-Trader Settings Panel ──────────────────────────────────
// Handles ⚙️ SL / Trail SL / R:R / Quantity
//
// QUANTITY CONCEPTS:
//   1 lot  = 65 units (Nifty lot size as of Apr 2025 SEBI revision)
//   units  = what gets sent to the exchange order
//   lots   = how a trader thinks about size
//   Fixed Lots mode  → trader picks N lots → we send N×65 units
//   Auto Capital mode → trader picks ₹ budget → app picks lots at entry

// Guard: when user is actively editing inputs, poll must NOT overwrite their values.
// Set to true on input focus, cleared on Apply click.
let _atPanelDirty = false;

function toggleAtSettings() {
    const panel = document.getElementById('at-settings-panel');
    const btn   = document.getElementById('at-settings-btn');
    if (!panel) return;
    const open = panel.classList.toggle('hidden');
    if (btn) btn.classList.toggle('bg-[#0053e2]', !open);
    if (btn) btn.classList.toggle('bg-gray-700', open);
    if (!open) {
        _atPanelDirty = false;   // reset dirty when closing
        _syncAtSettingsFromServer();
    }
}

// Call this from onfocus on every settings input to protect edits from poll
function _atMarkDirty() { _atPanelDirty = true; }

// ── Sync inputs from server state (called on panel open + status poll) ──
function syncAtSettingsFromStatus(data) {
    if (!data) return;
    // If user is actively editing any field, don't overwrite their changes
    if (_atPanelDirty) return;
    const set = (id, val) => { const el = document.getElementById(id); if (el && val !== undefined) el.value = val; };
    set('at-sl-pts',   data.sl_points);
    set('at-trail-sl', data.trailing_sl_points);
    set('at-rr',       data.rr_ratio);
    // Server stores units; input is now in lots → convert
    if (data.manual_qty !== undefined)
        set('at-manual-qty', Math.max(1, Math.round(data.manual_qty / LOT_SIZE)));
    set('at-capital', data.capital);
    if (data.qty_mode)      _applyQtyModeUI(data.qty_mode, false);
    // Only sync strike from server if user hasn't explicitly picked one this session
    if (data.strike_offset !== undefined && !_strikeUserPicked) {
        _atStrikeOffset = data.strike_offset;
        _applyStrikeUI(data.strike_offset);
    }
    if (data.max_trades_per_day !== undefined) {
        const el = document.getElementById('at-max-trades');
        if (el) { el.value = data.max_trades_per_day; }
        _updateMaxTradesBadge(data.max_trades_per_day);
    }
    _updateLotsHint();
    _updateCapitalEstimate();
}

async function _syncAtSettingsFromServer() {
    try {
        const resp = await fetch('/api/auto-trader/status');
        const data = await resp.json();
        syncAtSettingsFromStatus(data);
    } catch (e) { /* silent */ }
}

// ── Max trades / day badge ──────────────────────────────────────
function _updateMaxTradesBadge(val) {
    const badge = document.getElementById('at-max-trades-badge');
    if (!badge) return;
    badge.textContent = val;
    // colour: green ≤5, yellow ≤10, red >10
    badge.className = badge.className.replace(/bg-\S+/g, '');
    if (val <= 5)       badge.classList.add('bg-green-400', 'text-gray-900');
    else if (val <= 10) badge.classList.add('bg-[#ffc220]', 'text-gray-900');
    else                badge.classList.add('bg-red-400',   'text-white');
}

// ── Strike offset picker ────────────────────────────────────────
// offset: -3=ITM3, -2=ITM2, -1=ITM1, 0=ATM, 1=OTM1, 2=OTM2, 3=OTM3
let _atStrikeOffset   = 0;
let _lastKnownSpot    = null;
let _strikeUserPicked = false;   // true once user explicitly picks a strike — blocks poll override

const STRIKE_META = {
    '-3': { label: 'ITM3', delta: 0.85 },
    '-2': { label: 'ITM2', delta: 0.75 },
    '-1': { label: 'ITM1', delta: 0.65 },
     '0': { label: 'ATM',  delta: 0.50 },
     '1': { label: 'OTM1', delta: 0.35 },
     '2': { label: 'OTM2', delta: 0.25 },
     '3': { label: 'OTM3', delta: 0.15 },
};

function setAtStrike(offset) {
    _atStrikeOffset   = offset;
    _strikeUserPicked = true;    // lock — poll must not overwrite this
    _applyStrikeUI(offset);
}

function _applyStrikeUI(offset) {
    document.querySelectorAll('.strike-btn').forEach(btn => {
        const btnOffset = parseInt(btn.dataset.offset);
        const active    = btnOffset === offset;
        btn.className = btn.className
            .replace(/bg-\[#0053e2\]|bg-gray-700/g, active ? 'bg-[#0053e2]' : 'bg-gray-700')
            .replace(/text-white|text-gray-300/g,   active ? 'text-white'   : 'text-gray-300');
    });
    _updateStrikeExample(offset);
}

function _updateStrikeExample(offset) {
    const el = document.getElementById('at-strike-example');
    if (!el) return;
    const rawText    = document.getElementById('lm-price')?.textContent || '';
    const niftyPrice = parseFloat(rawText.replace(/[^0-9.]/g, '')) || _lastKnownSpot || 23500;
    const atm        = Math.round(niftyPrice / 50) * 50;
    // CE: OTM = higher strike (+), ITM = lower strike (-)
    // PE: OTM = lower strike  (-), ITM = higher strike (+)
    const ceStrike   = atm + offset * 50;
    const peStrike   = atm - offset * 50;
    const meta       = STRIKE_META[String(offset)] || { label: `${offset > 0 ? 'OTM' : 'ITM'}${Math.abs(offset)}`, delta: 0.50 };
    el.innerHTML = `Nifty ≈ ${niftyPrice.toFixed(0)} → ` +
        `<span class="text-green-400">LONG = ${ceStrike} CE</span> | ` +
        `<span class="text-red-400">SHORT = ${peStrike} PE</span> ` +
        `<span class="text-gray-600">(${meta.label}, delta ≈ ${meta.delta})</span>`;
}

// ── Qty mode toggle ──────────────────────────────────────────────
function setAtQtyMode(mode) {
    _atQtyMode = mode;
    _applyQtyModeUI(mode, true);
    // No auto-save — user must click Apply Settings explicitly
}

function _applyQtyModeUI(mode, animate = false) {
    _atQtyMode = mode;
    const manualBtn  = document.getElementById('at-qty-mode-manual');
    const capitalBtn = document.getElementById('at-qty-mode-capital');
    const manualRow  = document.getElementById('at-qty-manual-row');
    const capitalRow = document.getElementById('at-qty-capital-row');
    const isManual   = mode === 'manual';

    // Active pill = Walmart blue, inactive = dark gray
    if (manualBtn)  manualBtn.className  = manualBtn.className.replace(/bg-\[#0053e2\]|bg-gray-700/g,  isManual  ? 'bg-[#0053e2]' : 'bg-gray-700');
    if (capitalBtn) capitalBtn.className = capitalBtn.className.replace(/bg-\[#0053e2\]|bg-gray-700/g, !isManual ? 'bg-[#0053e2]' : 'bg-gray-700');

    manualRow?.classList.toggle('hidden', !isManual);
    capitalRow?.classList.toggle('hidden', isManual);

    if (isManual) _updateLotsHint(); else _updateCapitalEstimate();
}

// ── Live hints ───────────────────────────────────────────────────
function _updateLotsHint() {
    const lots  = Math.max(1, parseInt(document.getElementById('at-manual-qty')?.value || '10'));
    const units = lots * LOT_SIZE;                     // e.g. 10 × 65 = 650
    const APPROX_PREMIUM = 150;                        // ₹150/unit rough estimate
    const approxCost = units * APPROX_PREMIUM;         // e.g. 750 × 150 = ₹1,12,500

    const line1 = document.getElementById('at-lots-hint-line1');
    const uEl   = document.getElementById('at-lots-hint-units');
    const cEl   = document.getElementById('at-lots-hint-cost');
    if (line1) line1.textContent = `${lots} lot${lots > 1 ? 's' : ''}`;
    if (uEl)   uEl.textContent   = `${units} units`;
    if (cEl)   cEl.textContent   = `~₹${approxCost.toLocaleString('en-IN')}`;
}

// ── Premium estimate cache (avoid spamming the API on every keystroke) ──
let _premiumEstCache = null;
let _premiumEstCacheTs = 0;
const PREMIUM_EST_TTL_MS = 5 * 60 * 1000;   // 5 minutes

/** Render the capital-mode estimate using a given premium (₹/unit). */
function _renderCapitalEstimate(capital, estPremium, vixPct, dte, offset, source, ceLtp, peLtp, ceSym, peSym, vixSource) {
    const lots       = Math.max(1, Math.floor(capital / (estPremium * LOT_SIZE)));
    const units      = lots * LOT_SIZE;
    const approxCost = lots * estPremium * LOT_SIZE;
    const _STRIKE_LABELS = {'-3':'ITM3','-2':'ITM2','-1':'ITM1','0':'ATM','1':'OTM1','2':'OTM2','3':'OTM3'};
    const offsetLabel = _STRIKE_LABELS[String(offset)] || 'ATM';
    const isLive     = source === 'live_kite';

    const lotsEl  = document.getElementById('at-capital-qty-est');
    const unitsEl = document.getElementById('at-capital-units-est');
    const detailEl= document.getElementById('at-capital-detail');
    if (lotsEl)   lotsEl.textContent = lots;
    if (unitsEl)  unitsEl.textContent = units;

    // Main premium tag
    const premiumTag = isLive
        ? `<span class="text-green-700 font-semibold">🟢 ₹${estPremium} LIVE</span>`
        : `<span class="text-gray-700">≈₹${estPremium}</span>`;

    // CE / PE live price pills (shown when Kite is authenticated)
    let pricePills = '';
    if (ceLtp || peLtp) {
        const cePill = ceLtp
            ? `<span class="inline-block bg-blue-50 text-blue-700 text-[9px] font-mono px-1.5 py-0.5 rounded">📈 ${ceSym?.replace('NIFTY','')}: ₹${ceLtp}</span>`
            : '';
        const pePill = peLtp
            ? `<span class="inline-block bg-orange-50 text-orange-700 text-[9px] font-mono px-1.5 py-0.5 rounded">📉 ${peSym?.replace('NIFTY','')}: ₹${peLtp}</span>`
            : '';
        pricePills = `<br><span class="inline-flex gap-1 mt-0.5">${cePill}${pePill}</span>`;
    }

    // Footer source tag
    // vixSource: 'kite' | 'nse' | 'cached' | 'fallback' | null (initial)
    // Persist vixPct to localStorage so next initial render isn't blank
    if (vixPct && vixSource && vixSource !== 'fallback') {
        try { localStorage.setItem('_lastVixPct', String(vixPct)); } catch (_) {}
    }
    const vixBadge = {
        kite:     '🟢 Kite live',
        nse:      '📡 NSE live',
        cached:   '🕐 cached',
        fallback: '📐 estimated',
    }[vixSource] || '📐 estimated';
    const footerTag = isLive
        ? `<span class="text-green-700 text-[9px]">🟢 Live Kite price (ltp API) — exact count</span>`
        : vixPct
            ? `<span class="text-gray-400 text-[9px]">📊 B-S | VIX ${vixPct}% <span class="text-gray-300">(${vixBadge})</span> | ${dte != null ? dte+'d' : '?'} to expiry</span>`
            : `<span class="text-gray-400 text-[9px]">📐 loading estimate…</span>`;

    if (detailEl) detailEl.innerHTML =
        `₹${capital.toLocaleString('en-IN')} ÷ (${premiumTag} ${offsetLabel} × ${LOT_SIZE} units) ` +
        `<span class="text-gray-600">= ${lots} lots | ≈₹${approxCost.toLocaleString('en-IN')}</span>` +
        pricePills +
        `<br>${footerTag}`;
}

async function _updateCapitalEstimate() {
    const capital    = parseFloat(document.getElementById('at-capital')?.value || '96000');
    const offset     = _atStrikeOffset || 0;

    // Read spot from live-monitor element — but it starts as '--' until the
    // first tick arrives. If it's not a real number yet, fetch from API.
    const rawText    = document.getElementById('lm-price')?.textContent || '';
    let   niftyPrice = parseFloat(rawText.replace(/[^0-9.]/g, ''));
    if (!niftyPrice || niftyPrice < 10000) {
        // lm-price not populated yet — fetch directly from live-tick API
        try {
            const r = await fetch('/api/live-tick');
            const d = await r.json();
            niftyPrice = d.last_price || _lastKnownSpot || 23500;
        } catch (_) {
            niftyPrice = _lastKnownSpot || 23500;
        }
    }
    _lastKnownSpot = niftyPrice;   // cache for synchronous callers

    // Show a quick fallback while API loads — seed VIX from localStorage
    // so the initial render never shows the orange "unavailable" flash.
    const fallbackPremium  = Math.round(niftyPrice * 0.0022);
    let   seedVix = null;
    try { seedVix = parseFloat(localStorage.getItem('_lastVixPct')) || null; } catch (_) {}
    // Compute DTE client-side (Nifty 50 weekly expiry = Tuesday = day 2)
    const _clientDte = (() => {
        const now = new Date();
        const dow = now.getDay(); // 0=Sun,1=Mon,2=Tue,...
        const TUESDAY = 2;
        let d = (TUESDAY - dow + 7) % 7 || 7; // days until next Tue; 0→7
        if (d === 7 && dow === TUESDAY && now.getHours() >= 15) d = 7; // post-3PM on Tue
        return Math.max(d, 1);
    })();
    _renderCapitalEstimate(capital, fallbackPremium, seedVix, _clientDte, offset, 'fallback', null, null, null, null, seedVix ? 'cached' : null);

    // ── Cache check — MUST include offset, not just spot ─────────────
    // Bug fix: old code keyed only on spot → switching ATM→2-OTM served
    // the ATM premium but labelled it 2-OTM, giving completely wrong lots.
    // Also: live_kite results have a shorter TTL (30s) so price stays fresh.
    const now    = Date.now();
    const c      = _premiumEstCache;
    const isLive = c?.source === 'live_kite';
    const ttl    = isLive ? 30_000 : PREMIUM_EST_TTL_MS;   // 30s live, 5m BS
    const spotKey= Math.round(niftyPrice / 100) * 100;

    if (c && (now - _premiumEstCacheTs) < ttl
          && c.spot === spotKey
          && c.offset === offset) {               // ← THE FIX
        _renderCapitalEstimate(
            capital, c.est_premium, c.iv_pct, c.dte,
            offset, c.source, c.ce_ltp, c.pe_ltp, c.ce_symbol, c.pe_symbol, c.vix_source
        );
        return;
    }

    try {
        const resp = await fetch(`/api/premium-estimate?spot=${niftyPrice}&offset=${offset}`);
        if (!resp.ok) throw new Error('API error');
        const data = await resp.json();
        _premiumEstCache   = { ...data, spot: spotKey, offset };   // store offset too
        _premiumEstCacheTs = now;
        _renderCapitalEstimate(
            capital, data.est_premium, data.iv_pct, data.dte,
            offset, data.source, data.ce_ltp, data.pe_ltp, data.ce_symbol, data.pe_symbol, data.vix_source
        );
    } catch (_) {
        // Keep fallback already shown
    }
}

// Keep sync alias for places that call the function without await
function _updateCapitalEstimateSync() { _updateCapitalEstimate(); }

// ── Apply settings to server ─────────────────────────────────────
async function applyAtSettings() {
    _updateLotsHint();
    _updateCapitalEstimate();

    // Show loading state on button
    const applyBtn = document.getElementById('at-apply-btn');
    if (applyBtn) { applyBtn.disabled = true; applyBtn.textContent = '⏳ Saving…'; }

    const sl      = parseFloat(document.getElementById('at-sl-pts')?.value    || '30');
    const trail   = parseFloat(document.getElementById('at-trail-sl')?.value  || '15');
    const rr      = parseFloat(document.getElementById('at-rr')?.value        || '2');
    const lots    = Math.max(1, parseInt(document.getElementById('at-manual-qty')?.value || '10'));
    const manQty  = lots * LOT_SIZE;   // server always receives units (lots × 65)
    const capital = parseFloat(document.getElementById('at-capital')?.value   || '96000');
    const mode    = _atQtyMode;

    // Validation with plain-English messages
    if (sl < 5 || sl > 150) { _atShowToast('⚠️ SL must be between 5 and 150 Nifty points', 'warn'); return; }
    if (trail >= sl)         { _atShowToast('⚠️ Trailing SL must be smaller than the initial SL', 'warn'); return; }

    const maxTrades = parseInt(document.getElementById('at-max-trades')?.value || '3', 10);
    const params = new URLSearchParams({
        sl_points: sl, trailing_sl_points: trail, rr_ratio: rr,
        qty_mode: mode, manual_qty: manQty, capital,
        strike_offset: _atStrikeOffset,
        max_trades_per_day: maxTrades,
    });

    try {
        const resp = await fetch(`/api/auto-trader/configure?${params}`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            // After successful save: unlock poll sync and clear dirty flag
            _strikeUserPicked = false;
            _atPanelDirty     = false;
            const strikeLabel = ['ITM3','ITM2','ITM1','ATM','OTM1','OTM2','OTM3'][_atStrikeOffset + 3];
            const qtyDesc = mode === 'capital'
                ? `₹${capital.toLocaleString('en-IN')} capital`
                : `${lots} lot${lots > 1 ? 's' : ''}`;
            if (applyBtn) { applyBtn.textContent = '✅ Saved!'; }
            setTimeout(() => { if (applyBtn) { applyBtn.disabled = false; applyBtn.textContent = '✅ Apply Settings'; } }, 1500);
            _atShowToast(`✅ Saved — ${strikeLabel} | SL:${sl}pts | Trail:${trail}pts | R:R 1:${rr} | ${qtyDesc}`, 'info');
        } else {
            if (applyBtn) { applyBtn.disabled = false; applyBtn.textContent = '✅ Apply Settings'; }
            _atShowToast('❌ Save failed — ' + (data.error || 'unknown error'), 'error');
        }
    } catch (e) {
        if (applyBtn) { applyBtn.disabled = false; applyBtn.textContent = '✅ Apply Settings'; }
        _atShowToast('❌ Failed to save settings', 'error');
    }
}

// ── Symbol preview ───────────────────────────────────────────────
async function loadSymbolPreview() {
    const card = document.getElementById('at-symbol-preview');
    if (!card) return;

    // Show loading state
    card.classList.remove('hidden');
    ['at-sym-ce','at-sym-pe','at-sym-ce-ltp','at-sym-pe-ltp',
     'at-sym-ce-lots','at-sym-pe-lots'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = '⏳ loading...';
    });

    try {
        const resp = await fetch('/api/auto-trader/preview-symbol');
        const d    = await resp.json();

        if (!d.success) {
            _atShowToast(`❌ ${d.error}`, 'error');
            card.classList.add('hidden');
            return;
        }

        // Spot + expiry header
        const spotEl = document.getElementById('at-symbol-spot');
        if (spotEl) spotEl.textContent = `Nifty ₹${d.spot?.toFixed(0)} | Expiry ${d.expiry}`;

        const expiryEl = document.getElementById('at-symbol-expiry');
        if (expiryEl) expiryEl.textContent =
            `${d.offset_label} strikes | SL ${d.sl_points}pts | R:R 1:${d.rr_ratio} | ${d.qty_mode === 'capital' ? '₹'+d.capital?.toLocaleString('en-IN')+' capital' : 'fixed lots'}`;

        // CE (LONG)
        const fmt = (sym, ltp, lots, cost) => ({
            sym:  sym  || '❌ not found',
            ltp:  ltp  ? `₹${ltp} LTP` : '❌ no price',
            lots: lots ? `${lots} lots × 65 = ${lots*65} units  ≈ ₹${cost?.toLocaleString('en-IN')}` : '—',
        });

        const ce = fmt(d.long?.symbol,  d.long?.ltp,  d.long?.lots,  d.long?.cost);
        const pe = fmt(d.short?.symbol, d.short?.ltp, d.short?.lots, d.short?.cost);

        document.getElementById('at-sym-ce')?.setAttribute('textContent', ce.sym);
        document.getElementById('at-sym-ce').textContent      = ce.sym;
        document.getElementById('at-sym-ce-ltp').textContent  = ce.ltp;
        document.getElementById('at-sym-ce-lots').textContent = ce.lots;
        document.getElementById('at-sym-pe').textContent      = pe.sym;
        document.getElementById('at-sym-pe-ltp').textContent  = pe.ltp;
        document.getElementById('at-sym-pe-lots').textContent = pe.lots;

        // Color LTP green if found
        ['at-sym-ce-ltp','at-sym-pe-ltp'].forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            el.className = el.textContent.startsWith('❌')
                ? 'text-red-400 font-bold mt-0.5'
                : 'text-green-400 font-bold mt-0.5';
        });

    } catch(e) {
        _atShowToast('❌ Symbol lookup failed', 'error');
        card.classList.add('hidden');
    }
}

// ── Fetch Zerodha Balance + strike suggestions ──────────────────
let _lastZerodhaBalance = null;

async function fetchZerodhaBalance() {
    const btn   = document.getElementById('at-fetch-balance-btn');
    const panel = document.getElementById('at-balance-panel');
    const orig  = btn.innerHTML;
    btn.innerHTML = '⏳ Fetching…';
    btn.disabled  = true;

    try {
        const resp = await fetch('/api/auto-trader/zerodha-balance');
        const data = await resp.json();

        if (!data.success) {
            _atShowToast('❌ ' + data.error, 'error');
            return;
        }

        _lastZerodhaBalance = data.balance;

        // 1. Switch to capital mode automatically
        _applyQtyModeUI('capital', true);

        // 2. Fill in the live balance and lock the max cap
        const balEl = document.getElementById('at-capital');
        if (balEl) {
            balEl.value = Math.floor(data.balance);
            _updateCapitalEstimate();
        }
        // Apply hard cap so user can't type more than their balance
        if (typeof _setCapitalMax === 'function') _setCapitalMax(data.balance);

        // 3. Mark dirty so polling doesn't overwrite
        _atMarkDirty();

        // 4. Show balance panel
        document.getElementById('at-balance-amount').textContent =
            '₹' + data.balance.toLocaleString('en-IN', {maximumFractionDigits: 0});

        // 5. Render strike suggestion rows
        const sugEl = document.getElementById('at-strike-suggestions');
        sugEl.innerHTML = data.suggestions.map(s => {
            const isRec  = s.offset === data.recommended.offset;
            const canBuy = s.affordable;
            const bg     = isRec  ? 'bg-yellow-900 border-yellow-500 cursor-pointer' :
                           canBuy ? 'bg-gray-800 border-gray-600 hover:border-yellow-600 cursor-pointer' :
                                    'bg-gray-900 border-gray-700 opacity-40 cursor-not-allowed';
            const badge  = isRec  ? '<span class="ml-1 text-[8px] bg-yellow-500 text-black font-bold px-1 rounded">BEST</span>' : '';
            return `<div class="flex items-center gap-2 text-[10px] px-2 py-1.5 rounded border ${bg}"
                onclick="${canBuy ? `_applyBalanceSuggestion(${s.offset}, ${data.balance})` : ''}">
                <span class="font-bold text-white w-10">${s.label}${badge}</span>
                <span class="text-gray-400 flex-1">${s.description}</span>
                <span class="text-gray-300">~₹${s.est_premium}/u</span>
                <span class="font-bold ${canBuy ? 'text-green-400' : 'text-red-400'}">
                    ${canBuy ? s.max_lots + ' lot' + (s.max_lots !== 1 ? 's' : '') : 'Can\'t afford'}
                </span>
            </div>`;
        }).join('');

        panel.classList.remove('hidden');

        // 6. Auto-apply the recommended strike and immediately save config
        if (data.recommended && data.recommended.affordable) {
            await _applyBalanceSuggestion(data.recommended.offset, data.balance);
        } else {
            _atShowToast('⚠️ Very low balance — may not afford even 1 lot. Add funds to Zerodha.', 'error');
        }

    } catch(e) {
        _atShowToast('❌ Failed to fetch balance: ' + e.message, 'error');
    } finally {
        btn.innerHTML = orig;
        btn.disabled  = false;
    }
}

async function _applyBalanceSuggestion(offset, balance) {
    // Set strike
    setAtStrike(offset);

    // Ensure capital mode + fill capital
    _applyQtyModeUI('capital', true);
    const balEl = document.getElementById('at-capital');
    if (balEl) { balEl.value = Math.floor(balance); _updateCapitalEstimate(); }

    // Mark dirty so poll doesn't clobber values
    _atMarkDirty();

    // Auto-save to backend immediately so config is live
    const sl    = parseFloat(document.getElementById('at-sl-pts')?.value   || '30');
    const trail = parseFloat(document.getElementById('at-trail-sl')?.value || '15');
    const rr    = parseFloat(document.getElementById('at-rr')?.value       || '2');
    const maxT  = parseInt(document.getElementById('at-max-trades')?.value  || '3', 10);
    const params = new URLSearchParams({
        sl_points: sl, trailing_sl_points: trail, rr_ratio: rr,
        qty_mode: 'capital',
        manual_qty: 1 * LOT_SIZE,  // fallback; capital mode uses premium calc
        capital: Math.floor(balance),
        strike_offset: offset,
        max_trades_per_day: maxT,
    });
    try {
        const r = await fetch(`/api/auto-trader/configure?${params}`, { method: 'POST' });
        const d = await r.json();
        const label = ['ITM3','ITM2','ITM1','ATM','OTM1','OTM2','OTM3'][offset + 3];
        if (d.success) {
            _atPanelDirty     = false;  // reset — now in sync with server
            _strikeUserPicked = true;   // keep strike locked from future polls
            _atShowToast(
                `✅ Saved — ${label} | ₹${Math.floor(balance).toLocaleString('en-IN')} capital | auto-saved`,
                'info'
            );
        } else {
            _atShowToast('⚠️ Balance applied in UI but save failed: ' + d.error, 'error');
        }
    } catch(e) {
        _atShowToast('⚠️ UI updated but backend save failed: ' + e.message, 'error');
    }
}

// ── Init hints on load ───────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    _updateLotsHint();
    _updateCapitalEstimate();
    _applyStrikeUI(_atStrikeOffset);
});
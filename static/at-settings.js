// ── Auto-Trader Settings Panel ──────────────────────────────────
// Handles ⚙️ SL / Trail SL / R:R / Quantity
//
// QUANTITY CONCEPTS:
//   1 lot  = 65 units (Nifty lot size as of Apr 2025 SEBI revision)
//   units  = what gets sent to the exchange order
//   lots   = how a trader thinks about size
//   Fixed Lots mode  → trader picks N lots → we send N×65 units
//   Auto Capital mode → trader picks ₹ budget → app picks lots at entry

function toggleAtSettings() {
    const panel = document.getElementById('at-settings-panel');
    const btn   = document.getElementById('at-settings-btn');
    if (!panel) return;
    const open = panel.classList.toggle('hidden');
    if (btn) btn.classList.toggle('bg-[#0053e2]', !open);
    if (btn) btn.classList.toggle('bg-gray-700', open);
    if (!open) _syncAtSettingsFromServer();
}

// ── Sync inputs from server state (called on panel open + status poll) ──
function syncAtSettingsFromStatus(data) {
    if (!data) return;
    const set = (id, val) => { const el = document.getElementById(id); if (el && val !== undefined) el.value = val; };
    set('at-sl-pts',   data.sl_points);
    set('at-trail-sl', data.trailing_sl_points);
    set('at-rr',       data.rr_ratio);
    // Server stores units; input is now in lots → convert
    if (data.manual_qty !== undefined)
        set('at-manual-qty', Math.max(1, Math.round(data.manual_qty / LOT_SIZE)));
    set('at-capital', data.capital);
    if (data.qty_mode)      _applyQtyModeUI(data.qty_mode, false);
    if (data.strike_offset !== undefined) {
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
let _atStrikeOffset  = 0;      // 0=ATM, 1=1-OTM, 2=2-OTM
let _lastKnownSpot   = null;   // updated whenever we fetch live-tick

function setAtStrike(offset) {
    _atStrikeOffset = offset;
    _applyStrikeUI(offset);
    applyAtSettings();
}

function _applyStrikeUI(offset) {
    [0, 1, 2].forEach(i => {
        const btn = document.getElementById(`at-strike-${i}`);
        if (!btn) return;
        const active = i === offset;
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
    const ceStrike   = atm + offset * 50;
    const peStrike   = atm - offset * 50;
    const label      = ['ATM', '1-OTM', '2-OTM'][offset];
    const delta      = [0.50, 0.35, 0.20][offset];
    el.innerHTML = `Nifty ≈ ${niftyPrice.toFixed(0)} → ` +
        `<span class="text-green-400">LONG = ${ceStrike} CE</span> | ` +
        `<span class="text-red-400">SHORT = ${peStrike} PE</span> ` +
        `<span class="text-gray-600">(${label}, delta ≈ ${delta})</span>`;
}

// ── Qty mode toggle ──────────────────────────────────────────────
function setAtQtyMode(mode) {
    _atQtyMode = mode;
    _applyQtyModeUI(mode, true);
    applyAtSettings();
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
function _renderCapitalEstimate(capital, estPremium, vixPct, dte, offset, source, ceLtp, peLtp, ceSym, peSym) {
    const lots       = Math.max(1, Math.floor(capital / (estPremium * LOT_SIZE)));
    const units      = lots * LOT_SIZE;
    const approxCost = lots * estPremium * LOT_SIZE;
    const offsetLabel= ['ATM', '1-OTM', '2-OTM'][offset] || 'ATM';
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
    const footerTag = isLive
        ? `<span class="text-green-700 text-[9px]">🟢 Live Kite price (ltp API) — exact count</span>`
        : vixPct
            ? `<span class="text-gray-400 text-[9px]">📊 B-S estimate | VIX ${vixPct}% | ${dte}d to expiry — log in to Kite for real price</span>`
            : `<span class="text-orange-500 text-[9px]">⚠️ VIX unavailable — rough fallback (log in to Kite for real price)</span>`;

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

    // Show a quick fallback first while API loads
    const fallbackPremium = Math.round(niftyPrice * 0.0022);
    _renderCapitalEstimate(capital, fallbackPremium, null, null, offset, 'fallback');

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
            offset, c.source, c.ce_ltp, c.pe_ltp, c.ce_symbol, c.pe_symbol
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
            offset, data.source, data.ce_ltp, data.pe_ltp, data.ce_symbol, data.pe_symbol
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
            const statusEl = document.getElementById('at-settings-status');
            if (statusEl) { statusEl.classList.remove('hidden'); setTimeout(() => statusEl.classList.add('hidden'), 3000); }
            const qtyDesc = mode === 'capital'
                ? `capital ₹${capital.toLocaleString('en-IN')}`
                : `${lots} lot${lots>1?'s':''} (${manQty} units)`;
            _atShowToast(`⚙️ Saved — SL:${sl}pts | Trail:${trail}pts | R:R 1:${rr} | ${qtyDesc}`, 'info');
        }
    } catch (e) {
        _atShowToast('❌ Failed to save settings', 'error');
    }
}

// ── Init hints on load ───────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    _updateLotsHint();
    _updateCapitalEstimate();
    _applyStrikeUI(_atStrikeOffset);
});
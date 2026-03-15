// ── Auto-Trader Settings Panel ──────────────────────────────────
// Handles ⚙️ SL / Trail SL / R:R / Quantity
//
// QUANTITY CONCEPTS:
//   1 lot  = 75 units (the exchange minimum for Nifty options)
//   units  = what gets sent to the exchange order
//   lots   = how a trader thinks about size
//   Fixed Lots mode  → trader picks N lots → we send N×75 units
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
    if (data.qty_mode) _applyQtyModeUI(data.qty_mode, false);
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
    const units = lots * LOT_SIZE;                     // e.g. 10 × 75 = 750
    const APPROX_PREMIUM = 150;                        // ₹150/unit rough estimate
    const approxCost = units * APPROX_PREMIUM;         // e.g. 750 × 150 = ₹1,12,500

    const line1 = document.getElementById('at-lots-hint-line1');
    const uEl   = document.getElementById('at-lots-hint-units');
    const cEl   = document.getElementById('at-lots-hint-cost');
    if (line1) line1.textContent = `${lots} lot${lots > 1 ? 's' : ''}`;
    if (uEl)   uEl.textContent   = `${units} units`;
    if (cEl)   cEl.textContent   = `~₹${approxCost.toLocaleString('en-IN')}`;
}

function _updateCapitalEstimate() {
    const capital    = parseFloat(document.getElementById('at-capital')?.value || '96000');
    const niftyEl    = document.getElementById('lm-price');   // live Nifty price widget
    const niftyPrice = parseFloat(niftyEl?.textContent?.replace(/[^0-9.]/g, '')) || 23500;
    // ATM option premium ≈ 0.35% of Nifty spot (rough, recalculated live at entry)
    const estPremium = Math.round(niftyPrice * 0.0035);
    const lots       = Math.max(1, Math.floor(capital / (estPremium * LOT_SIZE)));
    const units      = lots * LOT_SIZE;

    const lotsEl  = document.getElementById('at-capital-qty-est');
    const unitsEl = document.getElementById('at-capital-units-est');
    if (lotsEl)  lotsEl.textContent  = lots;
    if (unitsEl) unitsEl.textContent = units;
}

// ── Apply settings to server ─────────────────────────────────────
async function applyAtSettings() {
    _updateLotsHint();
    _updateCapitalEstimate();

    const sl      = parseFloat(document.getElementById('at-sl-pts')?.value    || '30');
    const trail   = parseFloat(document.getElementById('at-trail-sl')?.value  || '15');
    const rr      = parseFloat(document.getElementById('at-rr')?.value        || '2');
    const lots    = Math.max(1, parseInt(document.getElementById('at-manual-qty')?.value || '10'));
    const manQty  = lots * LOT_SIZE;   // server always receives units (lots × 75)
    const capital = parseFloat(document.getElementById('at-capital')?.value   || '96000');
    const mode    = _atQtyMode;

    // Validation with plain-English messages
    if (sl < 5 || sl > 150) { _atShowToast('⚠️ SL must be between 5 and 150 Nifty points', 'warn'); return; }
    if (trail >= sl)         { _atShowToast('⚠️ Trailing SL must be smaller than the initial SL', 'warn'); return; }

    const params = new URLSearchParams({
        sl_points: sl, trailing_sl_points: trail, rr_ratio: rr,
        qty_mode: mode, manual_qty: manQty, capital,
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
});
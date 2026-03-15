// ── Auto-Trader Settings Panel ──────────────────────────────────
// Handles ⚙️ SL / Trail SL / R:R / Quantity (manual or capital-based)

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
    set('at-sl-pts',    data.sl_points);
    set('at-trail-sl',  data.trailing_sl_points);
    set('at-rr',        data.rr_ratio);
    set('at-manual-qty', data.manual_qty);
    set('at-capital',    data.capital);
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
    const manualHint = document.getElementById('at-qty-manual-hint');
    const capitalRow = document.getElementById('at-qty-capital-row');
    const capitalHint= document.getElementById('at-qty-capital-hint');

    const isManual = mode === 'manual';

    // Pill buttons
    if (manualBtn)  { manualBtn.className  = manualBtn.className.replace(/bg-\S+/, isManual  ? 'bg-[#0053e2]' : 'bg-gray-700'); }
    if (capitalBtn) { capitalBtn.className = capitalBtn.className.replace(/bg-\S+/, !isManual ? 'bg-[#0053e2]' : 'bg-gray-700'); }

    // Show/hide rows
    manualRow?.classList.toggle('hidden', !isManual);
    manualHint?.classList.toggle('hidden', !isManual);
    capitalRow?.classList.toggle('hidden', isManual);
    capitalHint?.classList.toggle('hidden', isManual);

    if (!isManual) _updateCapitalEstimate();
    else _updateLotsHint();
}

// ── Live hints ───────────────────────────────────────────────────
function _updateLotsHint() {
    const qty  = parseInt(document.getElementById('at-manual-qty')?.value || '780');
    const lots = Math.floor(qty / LOT_SIZE);
    const el   = document.getElementById('at-lots-hint');
    if (el) el.textContent = lots;
    // also update main manual-hint text
    const hint = document.getElementById('at-qty-manual-hint');
    if (hint) hint.innerHTML = `${qty} units = <span id="at-lots-hint">${lots}</span> lots × ${LOT_SIZE}`;
}

function _updateCapitalEstimate() {
    const capital    = parseFloat(document.getElementById('at-capital')?.value || '96000');
    const niftyEl    = document.getElementById('lm-price');  // live price from monitor
    const niftyPrice = parseFloat(niftyEl?.textContent?.replace(/[^0-9.]/g, '')) || 23500;
    const estPremium = Math.round(niftyPrice * 0.0035);
    const lots       = Math.max(1, Math.floor(capital / (estPremium * LOT_SIZE)));
    const qty        = lots * LOT_SIZE;
    const el         = document.getElementById('at-capital-qty-est');
    if (el) el.textContent = `${qty} units (${lots} lots) @ est. ₹${estPremium}/unit`;
}

// ── Apply settings to server ─────────────────────────────────────
async function applyAtSettings() {
    _updateLotsHint();
    _updateCapitalEstimate();

    const sl      = parseFloat(document.getElementById('at-sl-pts')?.value    || '30');
    const trail   = parseFloat(document.getElementById('at-trail-sl')?.value  || '15');
    const rr      = parseFloat(document.getElementById('at-rr')?.value        || '2');
    const manQty  = parseInt(document.getElementById('at-manual-qty')?.value  || '780');
    const capital = parseFloat(document.getElementById('at-capital')?.value   || '96000');
    const mode    = _atQtyMode;

    // Basic validation
    if (sl < 5 || sl > 150) { _atShowToast('⚠️ SL must be 5–150 pts', 'warn'); return; }
    if (trail >= sl)         { _atShowToast('⚠️ Trail SL must be < SL', 'warn'); return; }

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
            _atShowToast(`⚙️ Settings saved — SL:${sl}pts R:R 1:${rr} ${mode==='capital'?'(capital mode)':'qty:'+manQty}`, 'info');
        }
    } catch (e) {
        _atShowToast('❌ Failed to save settings', 'error');
    }
}

// ── Sync live price estimate whenever capital changes ────────────
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('at-capital')?.addEventListener('input', _updateCapitalEstimate);
    document.getElementById('at-manual-qty')?.addEventListener('input', _updateLotsHint);
});
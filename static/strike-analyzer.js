// ── Strike Analyzer ─────────────────────────────────────────────────────────
// Fetches option chain data for ±N strikes around ATM (or custom strike)
// and displays LTP, OI, OI change, volume, ₹/point, and S/R zone tags.

let _saTimer = null;

async function loadStrikeAnalyzer() {
    const customStrike = parseInt(document.getElementById('sa-custom-strike')?.value || '0');
    const range        = parseInt(document.getElementById('sa-range')?.value || '5');

    const params = new URLSearchParams({ range });
    if (customStrike > 0) params.set('center_strike', customStrike);

    try {
        const res  = await fetch(`/api/strike-analyzer?${params}`);
        const data = await res.json();

        if (!data.success) {
            document.getElementById('sa-table-body').innerHTML =
                `<tr><td colspan="12" class="text-center py-6 text-red-400">
                    ❌ ${data.error || 'Failed to load'}
                 </td></tr>`;
            return;
        }

        _renderStrikeTable(data);
        document.getElementById('sa-last-update').textContent =
            'Updated: ' + new Date().toLocaleTimeString('en-IN');

    } catch (e) {
        document.getElementById('sa-table-body').innerHTML =
            `<tr><td colspan="12" class="text-center py-6 text-red-400">❌ ${e.message}</td></tr>`;
    }
}

function _renderStrikeTable(data) {
    // Header info
    const spotEl  = document.getElementById('sa-spot');
    const atmEl   = document.getElementById('sa-atm');
    const expEl   = document.getElementById('sa-expiry');
    if (spotEl)  spotEl.textContent  = data.spot ? `₹${data.spot.toLocaleString('en-IN')}` : '--';
    if (atmEl)   atmEl.textContent   = data.atm_strike ? data.atm_strike.toLocaleString('en-IN') : '--';
    if (expEl)   expEl.textContent   = data.expiry || '--';

    // Summary bar
    if (data.max_ce_strike) document.getElementById('sa-max-ce').textContent =
        `Strike ${data.max_ce_strike.toLocaleString('en-IN')} | OI ${_fmtOI(data.max_ce_oi)}`;
    if (data.max_pe_strike) document.getElementById('sa-max-pe').textContent =
        `Strike ${data.max_pe_strike.toLocaleString('en-IN')} | OI ${_fmtOI(data.max_pe_oi)}`;
    if (data.pcr != null)   document.getElementById('sa-pcr').textContent =
        data.pcr.toFixed(2) + (data.pcr > 1.2 ? ' 🐂 Bullish' : data.pcr < 0.8 ? ' 🐻 Bearish' : ' ⚖️ Neutral');

    // Build rows
    const strikes = data.strikes || [];
    if (!strikes.length) {
        document.getElementById('sa-table-body').innerHTML =
            '<tr><td colspan="12" class="text-center py-6 text-gray-500">No data — login to Kite first</td></tr>';
        return;
    }

    const rows = strikes.map(s => {
        const isATM = s.is_atm;
        const rowBg = isATM ? 'bg-[#ffc220]/10' : (s.strike % 100 === 0 ? 'bg-gray-800/50' : '');

        const strikeLabel = isATM
            ? `<span class="font-black text-[#ffc220]">${s.strike.toLocaleString('en-IN')}</span><span class="ml-1 text-[9px] bg-[#ffc220] text-black px-1 rounded">ATM</span>`
            : `<span class="font-bold text-white">${s.strike.toLocaleString('en-IN')}</span>`;

        const zoneTag = _zoneTag(s.zone);

        // OI change badge
        const ceChg = _oiChgBadge(s.ce?.oi_chg);
        const peChg = _oiChgBadge(s.pe?.oi_chg);

        // ₹/point (approximate delta × LTP / spot_move)
        const cePt = s.ce?.rupee_per_point != null ? `₹${s.ce.rupee_per_point.toFixed(1)}` : '--';
        const pePt = s.pe?.rupee_per_point != null ? `₹${s.pe.rupee_per_point.toFixed(1)}` : '--';

        return `
        <tr class="border-t border-gray-700/50 hover:bg-gray-800/60 transition-colors ${rowBg}">
          <td class="px-2 py-2 text-right text-green-300">${_fmtVol(s.ce?.volume)}</td>
          <td class="px-2 py-2 text-right">${ceChg}</td>
          <td class="px-2 py-2 text-right text-green-300">${_fmtOI(s.ce?.oi)}</td>
          <td class="px-2 py-2 text-right text-green-400 font-mono">${cePt}</td>
          <td class="px-2 py-2 text-right font-bold text-green-400">₹${(s.ce?.ltp || 0).toFixed(1)}</td>
          <td class="px-3 py-2 text-center bg-gray-700/50">${strikeLabel}</td>
          <td class="px-2 py-2 text-center">${zoneTag}</td>
          <td class="px-2 py-2 text-left font-bold text-red-400">₹${(s.pe?.ltp || 0).toFixed(1)}</td>
          <td class="px-2 py-2 text-left text-red-400 font-mono">${pePt}</td>
          <td class="px-2 py-2 text-left text-red-300">${_fmtOI(s.pe?.oi)}</td>
          <td class="px-2 py-2 text-left">${peChg}</td>
          <td class="px-2 py-2 text-left text-red-300">${_fmtVol(s.pe?.volume)}</td>
        </tr>`;
    }).join('');

    document.getElementById('sa-table-body').innerHTML = rows;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function _fmtOI(v) {
    if (v == null || v === 0) return '<span class="text-gray-600">--</span>';
    if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
    if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
    return v.toString();
}

function _fmtVol(v) {
    if (v == null || v === 0) return '<span class="text-gray-600">--</span>';
    if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
    if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
    return v.toString();
}

function _oiChgBadge(chg) {
    if (chg == null) return '<span class="text-gray-600">--</span>';
    if (chg > 0)  return `<span class="text-green-400 font-bold">▲ ${_fmtOI(chg)}</span>`;
    if (chg < 0)  return `<span class="text-red-400 font-bold">▼ ${_fmtOI(Math.abs(chg))}</span>`;
    return '<span class="text-gray-500">─</span>';
}

function _zoneTag(zone) {
    if (!zone) return '<span class="text-gray-600 text-[9px]">--</span>';
    const map = {
        'resistance':  ['bg-red-900/60 text-red-300',    '🚧 Resistance'],
        'support':     ['bg-green-900/60 text-green-300', '🛡️ Support'],
        'swing_high':  ['bg-orange-900/60 text-orange-300', '⬆️ Swing Hi'],
        'swing_low':   ['bg-blue-900/60 text-blue-300',  '⬇️ Swing Lo'],
        'round_level': ['bg-purple-900/60 text-purple-300', '🔵 Round'],
    };
    const [cls, label] = map[zone] || ['bg-gray-700 text-gray-400', zone];
    return `<span class="text-[9px] px-1.5 py-0.5 rounded font-bold ${cls}">${label}</span>`;
}

// ── Auto-refresh when page is active ─────────────────────────────────────────
function _startSaTimer() {
    if (_saTimer) clearInterval(_saTimer);
    _saTimer = setInterval(() => {
        const page = document.getElementById('page-strike-analyzer');
        if (page && page.classList.contains('active')) loadStrikeAnalyzer();
    }, 30000); // every 30s
}

document.addEventListener('DOMContentLoaded', _startSaTimer);

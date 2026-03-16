/**
 * at-health.js — System health check panel for auto-trader
 *
 * Calls /api/health and renders a clear go/no-go panel so the user
 * can confirm every critical subsystem is alive before trusting the
 * auto-trader with real money.
 */

// ── Check definitions ────────────────────────────────────────────
const HEALTH_CHECKS = [
    {
        key:   'kite.authenticated',
        label: 'Kite session',
        get:   d => d.kite?.authenticated,
        okMsg:   h => `✅ Logged in (${h.kite?.session_date ?? '?'})`,
        failMsg: '❌ Not logged in — click Login with Zerodha',
        critical: true,
    },
    {
        key:   'kite.ws_streaming',
        label: 'WebSocket ticks',
        get:   d => d.kite?.ws_streaming,
        okMsg:   h => {
            const age = h.kite?.last_tick_age_s;
            return age != null ? `✅ Live (last tick ${age}s ago)` : '✅ Connected';
        },
        failMsg: '❌ WebSocket offline — SL checked every 5min only!',
        critical: true,
    },
    {
        key:   'trader.nifty_fresh',
        label: 'Nifty price',
        get:   d => d.trader?.nifty_fresh,
        okMsg:   h => `✅ ₹${h.trader?.nifty_price ?? '--'}`,
        failMsg: '⚠️ No Nifty price yet — background loop starting',
        critical: true,
    },
    {
        key:   'trader.tick_guard_live',
        label: 'Tick SL guard',
        get:   d => d.trader?.tick_guard_live,
        okMsg:   '✅ Active — SL checked every ~1s',
        failMsg: '⚠️ Offline — SL checked every 5min (still safe, less precise)',
        critical: false,
    },
    {
        key:   'snapshot.exists',
        label: 'Crash recovery',
        get:   d => d.snapshot?.exists,
        okMsg:   '✅ Snapshot OK — trade survives restarts',
        failMsg: '⚠️ No snapshot — trade state lost on restart',
        critical: false,
    },
    {
        key:   'trader.is_paper_mode',
        label: 'Trade mode',
        get:   d => true,   // always show
        okMsg:   h => h.trader?.is_paper_mode
            ? '📝 PAPER mode — no real money at risk'
            : '🟢 LIVE mode — real orders will be placed',
        failMsg: '?',
        critical: false,
    },
];

// ── Renderer ─────────────────────────────────────────────────────
function _renderHealth(data) {
    const rows    = document.getElementById('at-health-rows');
    const verdict = document.getElementById('at-health-verdict');
    if (!rows) return;

    let html = '';
    let criticalFails = [];

    for (const chk of HEALTH_CHECKS) {
        const val     = chk.get(data);
        const passing = val === true;
        const msgFn   = passing ? chk.okMsg : chk.failMsg;
        const msg     = typeof msgFn === 'function' ? msgFn(data) : msgFn;

        if (!passing && chk.critical) criticalFails.push(chk.label);

        html += `<span class="text-gray-400">${chk.label}:</span>
                 <span class="${passing ? 'text-green-400' : chk.critical ? 'text-red-400' : 'text-yellow-400'}">${msg}</span>`;
    }

    rows.innerHTML = html;

    if (verdict) {
        if (criticalFails.length === 0) {
            verdict.textContent  = '🟢 All systems go — safe to trade';
            verdict.className    = 'mt-2 text-[11px] font-bold text-green-400';
        } else {
            verdict.textContent  = `🔴 Not ready: ${criticalFails.join(', ')} must be fixed first`;
            verdict.className    = 'mt-2 text-[11px] font-bold text-red-400';
        }
        verdict.classList.remove('hidden');
    }
}

// ── Public API ────────────────────────────────────────────────────
async function runHealthCheck() {
    const rows = document.getElementById('at-health-rows');
    if (rows) rows.innerHTML = '<span class="text-gray-500 col-span-2">⏳ checking…</span>';
    const verdict = document.getElementById('at-health-verdict');
    if (verdict) verdict.classList.add('hidden');

    try {
        const resp = await fetch('/api/health');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        _renderHealth(data);
    } catch (e) {
        if (rows) rows.innerHTML =
            `<span class="text-red-400 col-span-2">❌ Health check failed: ${e.message}</span>`;
    }
}

// Run on page load and every 30s
document.addEventListener('DOMContentLoaded', () => {
    // Only run when auto-trader page is visible
    const observer = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting) runHealthCheck();
    }, { threshold: 0.1 });

    const panel = document.getElementById('at-health-panel');
    if (panel) observer.observe(panel);

    // Refresh every 30s
    setInterval(() => {
        const panel = document.getElementById('at-health-panel');
        if (panel && panel.offsetParent !== null) runHealthCheck();
    }, 30_000);
});
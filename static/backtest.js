/* ── Backtest UI Module ──────────────────────────────────────────
 * Handles backtest form, API calls, rendering results with Chart.js
 */

let backtestEquityChart = null;
let backtestDailyChart = null;
let _backtestRunning = false;
let _currentDataSource = 'yahoo';

// Period options per data source
const PERIOD_OPTIONS = {
    yahoo:    [['5d','5 Days'],['30d','30 Days'],['60d','60 Days (max)']],
    zerodha:  [['5d','5 Days'],['30d','30 Days'],['60d','60 Days (max)']],
    truedata: [['5d','5 Days'],['30d','30 Days'],['60d','60 Days'],['90d','90 Days'],
               ['6mo','6 Months'],['1y','1 Year'],['2y','2 Years'],['5y','5 Years']],
};

/**
 * Select a data source and update UI accordingly.
 */
function selectDataSource(source) {
    _currentDataSource = source;

    // Update button styles
    ['yahoo', 'zerodha', 'truedata'].forEach(s => {
        const btn = document.getElementById(`ds-${s}`);
        if (!btn) return;
        if (s === source) {
            btn.className = 'ds-btn active flex items-center gap-1.5 px-3 py-1.5 rounded-lg border-2 border-[#0053e2] bg-blue-50 text-[#0053e2] text-xs font-bold transition';
        } else {
            btn.className = 'ds-btn flex items-center gap-1.5 px-3 py-1.5 rounded-lg border-2 border-gray-200 bg-white text-gray-600 text-xs font-bold transition hover:border-gray-400';
        }
    });

    // Show/hide credential panels
    document.getElementById('truedata-creds').classList.toggle('hidden', source !== 'truedata');
    document.getElementById('zerodha-warn').classList.toggle('hidden', source !== 'zerodha');

    // Update period dropdown options
    const periodEl = document.getElementById('bt-period');
    const options = PERIOD_OPTIONS[source] || PERIOD_OPTIONS.yahoo;
    periodEl.innerHTML = options.map(([val, label]) =>
        `<option value="${val}"${val === '60d' ? ' selected' : ''}>${label}</option>`
    ).join('');

    // Check Zerodha login status
    if (source === 'zerodha') {
        fetch('/api/trader/status').then(r => r.json()).then(d => {
            const badge = document.getElementById('ds-zerodha-badge');
            if (d.is_authenticated) {
                badge.textContent = 'LOGGED IN · 60d';
                badge.className = 'bg-green-100 text-green-700 px-1.5 py-0.5 rounded text-[9px] font-bold';
            } else {
                badge.textContent = 'NOT LOGGED IN';
                badge.className = 'bg-red-100 text-red-700 px-1.5 py-0.5 rounded text-[9px] font-bold';
            }
        }).catch(() => {});
    }

    // Check TrueData credentials
    if (source === 'truedata') {
        fetch('/api/truedata/status').then(r => r.json()).then(d => {
            const statusEl = document.getElementById('td-creds-status');
            if (d.configured) {
                statusEl.textContent = '✅ Credentials saved. Ready to fetch up to 5 years of data!';
                statusEl.className = 'text-xs mt-1 text-green-600 font-semibold';
            } else {
                statusEl.textContent = 'Enter your TrueData credentials above to unlock 5-year history.';
                statusEl.className = 'text-xs mt-1 text-orange-600';
            }
        }).catch(() => {});
    }
}

/**
 * Save TrueData credentials to backend.
 */
async function saveTrueDataCreds() {
    const username = document.getElementById('td-username').value.trim();
    const password = document.getElementById('td-password').value.trim();
    const statusEl = document.getElementById('td-creds-status');

    if (!username || !password) {
        statusEl.textContent = '❌ Please enter both username and password';
        statusEl.className = 'text-xs mt-1 text-red-600';
        return;
    }

    statusEl.textContent = 'Saving...';
    statusEl.className = 'text-xs mt-1 text-gray-500';

    try {
        const params = new URLSearchParams({ username, password });
        const resp = await fetch(`/api/truedata/credentials?${params}`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            statusEl.textContent = '✅ Credentials saved! Ready to backtest with 5-year history.';
            statusEl.className = 'text-xs mt-1 text-green-600 font-semibold';
            document.getElementById('td-password').value = '';
        } else {
            statusEl.textContent = '❌ ' + (data.error || 'Failed to save');
            statusEl.className = 'text-xs mt-1 text-red-600';
        }
    } catch (e) {
        statusEl.textContent = '❌ Error: ' + e.message;
        statusEl.className = 'text-xs mt-1 text-red-600';
    }
}

/**
 * Run backtest with parameters from form inputs.
 */
async function runBacktest() {
    if (_backtestRunning) return;
    _backtestRunning = true;

    const btn = document.getElementById('bt-run-btn');
    const resultsEl = document.getElementById('bt-results');
    const loadingEl = document.getElementById('bt-loading');

    btn.disabled = true;
    btn.textContent = '⏳ Running...';
    btn.className = 'bg-yellow-600 px-6 py-2 rounded-lg font-bold text-sm animate-pulse text-white';
    loadingEl.classList.remove('hidden');
    resultsEl.classList.add('hidden');

    // Collect form values
    const period    = document.getElementById('bt-period').value;
    const slPoints  = document.getElementById('bt-sl').value;
    const trailingSl = document.getElementById('bt-trail').value;
    const rrRatio   = document.getElementById('bt-rr').value;
    const maxTrades = document.getElementById('bt-max-trades').value;
    const strategy  = document.getElementById('bt-strategy').value;
    const quantity  = document.getElementById('bt-qty')?.value || '780';
    const dataSource = _currentDataSource;

    const params = new URLSearchParams({
        period, sl_points: slPoints, trailing_sl: trailingSl,
        rr_ratio: rrRatio, max_trades: maxTrades, strategy,
        quantity, data_source: dataSource,
    });

    // Update loading message based on source
    const loadingMsg = {
        yahoo: 'Fetching from Yahoo Finance... 🌐',
        zerodha: 'Fetching from Zerodha Kite... 🔷',
        truedata: 'Fetching from TrueData (may take ~30s for large periods)... 🏆',
    };
    document.querySelector('#bt-loading p').textContent = loadingMsg[dataSource] || 'Running backtest... 🐶';

    try {
        const resp = await fetch(`/api/backtest?${params}`, { method: 'POST' });
        const data = await resp.json();

        if (!data.success) {
            alert('Backtest failed: ' + (data.error || 'Unknown error'));
            return;
        }

        renderBacktestResults(data);
        resultsEl.classList.remove('hidden');
    } catch (e) {
        alert('Backtest error: ' + e.message);
    } finally {
        _backtestRunning = false;
        btn.disabled = false;
        btn.textContent = '🚀 Run Backtest';
        btn.className = 'bg-[#0053e2] hover:bg-blue-700 px-6 py-2 rounded-lg font-bold text-sm text-white transition';
        loadingEl.classList.add('hidden');
    }
}

/**
 * Render backtest results into the dashboard.
 */
function renderBacktestResults(data) {
    // ── Summary cards ──
    const winRateColor = data.win_rate >= 50 ? 'text-green-600' : 'text-red-600';
    const pnlColor = data.total_pnl_rupees >= 0 ? 'text-green-600' : 'text-red-600';
    const pfColor = data.profit_factor >= 1.0 ? 'text-green-600' : 'text-red-600';
    const sharpeColor = data.sharpe_approx >= 1.0 ? 'text-green-600' : 'text-red-600';

    document.getElementById('bt-total-trades').textContent = data.total_trades;
    document.getElementById('bt-winners').textContent = data.winners;
    document.getElementById('bt-losers').textContent = data.losers;

    const wrEl = document.getElementById('bt-winrate');
    wrEl.textContent = data.win_rate + '%';
    wrEl.className = `text-2xl font-black ${winRateColor}`;

    const pnlEl = document.getElementById('bt-total-pnl');
    pnlEl.textContent = `${data.total_pnl_points > 0 ? '+' : ''}${data.total_pnl_points} pts`;
    pnlEl.className = `text-2xl font-black ${pnlColor}`;

    document.getElementById('bt-pnl-rupees').textContent =
        `₹${data.total_pnl_rupees >= 0 ? '+' : ''}${data.total_pnl_rupees.toLocaleString()}`;
    document.getElementById('bt-pnl-rupees').className = pnlColor + ' text-sm font-bold';

    const pfEl = document.getElementById('bt-profit-factor');
    pfEl.textContent = data.profit_factor;
    pfEl.className = `text-2xl font-black ${pfColor}`;

    document.getElementById('bt-max-win').textContent = `+${data.max_win} pts`;
    document.getElementById('bt-max-loss').textContent = `${data.max_loss} pts`;
    document.getElementById('bt-avg-win').textContent = `+${data.avg_win} pts`;
    document.getElementById('bt-avg-loss').textContent = `${data.avg_loss} pts`;
    document.getElementById('bt-max-dd').textContent = `${data.max_drawdown} pts`;

    const shEl = document.getElementById('bt-sharpe');
    shEl.textContent = data.sharpe_approx;
    shEl.className = `font-bold ${sharpeColor}`;

    document.getElementById('bt-days').textContent = `${data.days_tested} trading days`;
    const sourceEl = document.getElementById('bt-source');
    const sourceBadge = document.getElementById('bt-source-badge');
    sourceEl.textContent = data.data_source;
    const sourceBadgeColors = {
        'Yahoo Finance':  'bg-blue-100 text-blue-700',
        'Zerodha Kite':   'bg-purple-100 text-purple-700',
        'TrueData':       'bg-orange-100 text-orange-700',
    };
    sourceBadge.className = `px-2 py-0.5 rounded font-bold text-xs ${sourceBadgeColors[data.data_source] || 'bg-gray-100 text-gray-600'}`;

    // ── Equity Curve Chart ──
    renderEquityCurve(data.equity_curve);

    // ── Daily P&L Bar Chart ──
    renderDailyPnl(data.daily_pnl);

    // ── Populate Day Replay date picker ──
    if (typeof replayPopulateDates === 'function') {
        replayPopulateDates(data.daily_pnl);
    }

    // ── Trade Log Table ──
    document.getElementById('bt-trade-count').textContent = data.total_trades;
    renderTradeLog(data.trades);
}

/**
 * Render equity curve line chart.
 */
function renderEquityCurve(curve) {
    const ctx = document.getElementById('bt-equity-chart').getContext('2d');

    if (backtestEquityChart) backtestEquityChart.destroy();

    const labels = curve.map((p, i) => `#${i + 1}`);
    const values = curve.map(p => p.cumulative);

    // Color: green if positive, red if negative at end
    const lineColor = values[values.length - 1] >= 0 ? '#2a8703' : '#ea1100';

    backtestEquityChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Cumulative P&L (points)',
                data: values,
                borderColor: lineColor,
                backgroundColor: lineColor + '20',
                borderWidth: 2,
                fill: true,
                tension: 0.3,
                pointRadius: 0,
                pointHitRadius: 8,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: (items) => {
                            const idx = items[0].dataIndex;
                            const pt = curve[idx];
                            return `Trade #${idx + 1} — ${pt.date} ${pt.time}`;
                        },
                        label: (item) => `Cumulative: ${item.raw >= 0 ? '+' : ''}${item.raw} pts`,
                    },
                },
            },
            scales: {
                x: { display: false },
                y: {
                    grid: { color: '#f0f0f0' },
                    ticks: { callback: v => (v >= 0 ? '+' : '') + v },
                },
            },
        },
    });
}

/**
 * Render daily P&L bar chart.
 */
function renderDailyPnl(dailyPnl) {
    const ctx = document.getElementById('bt-daily-chart').getContext('2d');

    if (backtestDailyChart) backtestDailyChart.destroy();

    const dates = Object.keys(dailyPnl);
    const values = Object.values(dailyPnl);
    const colors = values.map(v => v >= 0 ? '#2a8703' : '#ea1100');

    backtestDailyChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: dates.map(d => d.slice(5)),  // MM-DD format
            datasets: [{
                label: 'Daily P&L (points)',
                data: values,
                backgroundColor: colors,
                borderRadius: 3,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: (items) => dates[items[0].dataIndex],
                        label: (item) => {
                            const v = item.raw;
                            return `${v >= 0 ? '+' : ''}${v} pts (₹${(v * 50).toLocaleString()})`;
                        },
                    },
                },
            },
            scales: {
                x: {
                    ticks: { maxRotation: 45, font: { size: 9 } },
                    grid: { display: false },
                },
                y: {
                    grid: { color: '#f0f0f0' },
                    ticks: { callback: v => (v >= 0 ? '+' : '') + v },
                },
            },
            onClick(event, elements) {
                if (!elements.length) return;
                const dateStr = dates[elements[0].index];
                if (dateStr && typeof replaySelectDate === 'function') {
                    replaySelectDate(dateStr);
                    // Scroll to replay panel
                    const replayPanel = document.getElementById('replay-date');
                    if (replayPanel) replayPanel.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            },
        },
    });
}

/**
 * Render trade log table.
 */
function renderTradeLog(trades) {
    // Delegate to live-monitor.js which adds expand buttons + inline detail panels
    if (typeof renderTradeLogWithExpand === 'function') {
        renderTradeLogWithExpand(trades);
    }
}

/* ── Backtest UI Module ──────────────────────────────────────────
 * Handles backtest form, API calls, rendering results with Chart.js
 */

let backtestEquityChart = null;
let backtestDailyChart = null;
let _backtestRunning = false;

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
    const period = document.getElementById('bt-period').value;
    const slPoints = document.getElementById('bt-sl').value;
    const trailingSl = document.getElementById('bt-trail').value;
    const rrRatio = document.getElementById('bt-rr').value;
    const maxTrades = document.getElementById('bt-max-trades').value;

    const params = new URLSearchParams({
        period, sl_points: slPoints, trailing_sl: trailingSl,
        rr_ratio: rrRatio, max_trades: maxTrades,
    });

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
    document.getElementById('bt-source').textContent = data.data_source;

    // ── Equity Curve Chart ──
    renderEquityCurve(data.equity_curve);

    // ── Daily P&L Bar Chart ──
    renderDailyPnl(data.daily_pnl);

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
        },
    });
}

/**
 * Render trade log table.
 */
function renderTradeLog(trades) {
    const tbody = document.getElementById('bt-trades-body');
    if (!trades.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-gray-400">No trades</td></tr>';
        return;
    }

    tbody.innerHTML = trades.map(t => {
        const isWin = t.pnl_points >= 0;
        const rowBg = isWin ? 'bg-green-50' : 'bg-red-50';
        const pnlColor = isWin ? 'text-green-700' : 'text-red-700';
        const dirBadge = t.direction === 'long'
            ? '<span class="bg-green-100 text-green-800 px-2 py-0.5 rounded text-xs font-bold">LONG</span>'
            : '<span class="bg-red-100 text-red-800 px-2 py-0.5 rounded text-xs font-bold">SHORT</span>';

        return `
            <tr class="${rowBg} border-b border-gray-100 hover:bg-gray-100 transition">
                <td class="px-3 py-2 text-xs">${t.date}</td>
                <td class="px-3 py-2 text-xs">${t.entry_time}-${t.exit_time}</td>
                <td class="px-3 py-2">${dirBadge}</td>
                <td class="px-3 py-2 text-xs font-mono">${t.entry_price}</td>
                <td class="px-3 py-2 text-xs font-mono">${t.exit_price}</td>
                <td class="px-3 py-2 text-xs">
                    <span class="bg-gray-200 px-2 py-0.5 rounded">${t.exit_reason}</span>
                </td>
                <td class="px-3 py-2 text-xs font-bold ${pnlColor}">
                    ${isWin ? '+' : ''}${t.pnl_points} pts
                </td>
                <td class="px-3 py-2 text-xs font-bold ${pnlColor}">
                    ₹${isWin ? '+' : ''}${t.pnl_rupees.toLocaleString()}
                </td>
            </tr>
        `;
    }).join('');
}

// Dashboard logic — extracted from index.html inline script

var candlestickChart = null;
var candleSeries = null;
var volumeChart = null;
var liveTickInterval = null;
var selectedChartTF = '5m';

function switchChartTimeframe(tf) {
    selectedChartTF = tf;
    document.querySelectorAll('.tf-btn').forEach(b => {
        b.classList.remove('tf-btn-active');
        b.setAttribute('aria-selected', 'false');
    });
    const activeBtn = document.getElementById('tf-btn-' + tf);
    if (activeBtn) {
        activeBtn.classList.add('tf-btn-active');
        activeBtn.setAttribute('aria-selected', 'true');
    }
    loadAnalysis(true);
}

async function checkStatus() {
    try {
        const resp = await fetch('/api/status');
        const data = await resp.json();
        const badge = document.getElementById('data-source-badge');
        const banner = document.getElementById('login-banner');
        if (data.authenticated) {
            badge.innerHTML = '<span class="flex items-center gap-2 bg-green-500 text-white px-3 py-1.5 rounded-full text-sm font-bold"><span class="w-2 h-2 bg-white rounded-full live-dot"></span>LIVE</span>';
            banner.classList.add('hidden');
            document.getElementById('source-label').textContent = 'Zerodha Kite (LIVE)';
            startLiveTickPolling();
        } else {
            badge.innerHTML = '<span class="flex items-center gap-2 bg-yellow-500 text-gray-900 px-3 py-1.5 rounded-full text-sm font-bold">\u23f1 DELAYED</span>';
            banner.classList.remove('hidden');
        }
    } catch (e) { console.error(e); }
}

// ── Account Capital / Margins ────────────────────────────────────

function _formatCurrency(val) {
    if (val == null || isNaN(val)) return '--';
    return '₹' + Number(val).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

async function loadMargins() {
    const panel = document.getElementById('capital-panel');
    try {
        const resp = await fetch('/api/margins');
        const data = await resp.json();
        if (!data.success) {
            panel.classList.add('hidden');
            return;
        }
        const eq = data.equity;
        document.getElementById('cap-opening').textContent = _formatCurrency(eq.opening_balance);
        document.getElementById('cap-cash').textContent = _formatCurrency(eq.available_cash);
        document.getElementById('cap-margin').textContent = _formatCurrency(eq.available_margin);
        document.getElementById('cap-used').textContent = _formatCurrency(eq.used_margin);
        document.getElementById('cap-collateral').textContent = _formatCurrency(eq.collateral);
        panel.classList.remove('hidden');
    } catch (e) {
        console.error('Margins fetch failed:', e);
        panel.classList.add('hidden');
    }
}


function startLiveTickPolling() {
    if (liveTickInterval) clearInterval(liveTickInterval);
    liveTickInterval = setInterval(async () => {
        try {
            const resp = await fetch('/api/live-tick');
            const data = await resp.json();
            if (data.success) {
                document.getElementById('current-price').textContent = data.last_price.toLocaleString('en-IN');
                document.getElementById('live-price-time').textContent = `Live \u2022 ${new Date().toLocaleTimeString()}`;
            }
        } catch (e) { /* silent */ }
    }, 1000);
}

let refreshInterval = null;
let refreshCountdown = 60;
let isPaused = false;
let isLoadingAnalysis = false;

async function loadAnalysis(isAutoRefresh = false) {
    if (isLoadingAnalysis && isAutoRefresh) return;
    isLoadingAnalysis = true;

    if (!isAutoRefresh) {
        document.getElementById('loading').classList.remove('hidden');
        document.getElementById('dashboard').classList.add('hidden');
        document.getElementById('error-state').classList.add('hidden');
    } else {
        const b = document.getElementById('live-badge');
        if (b) b.textContent = '\u26a1 Refreshing...';
    }
    try {
        const resp = await fetch(`/api/mtf-analyze?chart_tf=${selectedChartTF}`);
        const data = await resp.json();
        if (!data.success) { showError(data.error || 'Unknown error'); return; }
        renderDashboard(data);
        const now = new Date();
        const timeStr = now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const liveBadge = document.getElementById('live-badge');
        if (liveBadge) liveBadge.textContent = `\ud83d\udfe2 LIVE \u2022 Updated ${timeStr}`;
    } catch (e) {
        if (!isAutoRefresh) showError(e.message);
        const errBadge = document.getElementById('live-badge');
        if (errBadge) errBadge.textContent = '\ud83d\udd34 Update failed';
    } finally {
        isLoadingAnalysis = false;
    }
    refreshCountdown = 60;
}

function startAutoRefresh() {
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(() => {
        if (isPaused) return;
        refreshCountdown--;
        const btn = document.getElementById('countdown-text');
        if (btn) btn.textContent = `${refreshCountdown}s`;
        if (refreshCountdown <= 0) {
            loadAnalysis(true);
        }
    }, 1000);
}

function togglePause() {
    isPaused = !isPaused;
    const btn = document.getElementById('pause-btn');
    if (btn) btn.textContent = isPaused ? '\u25b6\ufe0f Resume' : '\u23f8\ufe0f Pause';
    const badge = document.getElementById('live-badge');
    if (badge && isPaused) badge.textContent = '\u23f8\ufe0f PAUSED';
}

function showError(msg) {
    document.getElementById('loading').classList.add('hidden');
    document.getElementById('error-state').classList.remove('hidden');
    document.getElementById('error-msg').textContent = msg;
}

function renderDashboard(data) {
    document.getElementById('loading').classList.add('hidden');
    document.getElementById('dashboard').classList.remove('hidden');

    // Price
    document.getElementById('current-price').textContent = data.current_price.toLocaleString('en-IN');
    const changeEl = document.getElementById('day-change');
    const sign = data.day_change >= 0 ? '+' : '';
    changeEl.textContent = `${sign}${data.day_change} (${sign}${data.day_change_pct}%)`;
    changeEl.className = data.day_change >= 0 ? 'text-sm font-semibold text-green-600' : 'text-sm font-semibold text-red-600';

    // Combined Probability
    document.getElementById('bull-pct').textContent = data.bullish_probability;
    document.getElementById('bear-pct').textContent = data.bearish_probability;
    document.getElementById('prob-bar').style.width = `${data.bullish_probability}%`;

    // Bias
    const biasEl = document.getElementById('overall-bias');
    const biasEmoji = { bullish: '\ud83d\udc02 BULLISH', bearish: '\ud83d\udc3b BEARISH', neutral: '\u2696\ufe0f NEUTRAL' };
    const biasColors = { bullish: 'text-green-600', bearish: 'text-red-600', neutral: 'text-gray-600' };
    biasEl.textContent = biasEmoji[data.overall_bias] || data.overall_bias;
    biasEl.className = `text-2xl font-black ${biasColors[data.overall_bias] || ''}`;

    // Confidence
    const confEl = document.getElementById('confidence');
    confEl.textContent = data.confidence.toUpperCase();
    const confColors = { high: 'text-green-600', medium: 'text-yellow-600', low: 'text-red-500' };
    confEl.className = `font-bold ${confColors[data.confidence] || ''}`;

    // Confluence
    const conflEl = document.getElementById('confluence');
    const conflColors = { strong: 'text-green-600', moderate: 'text-yellow-600', weak: 'text-gray-500', conflicting: 'text-red-600' };
    const conflEmoji = { strong: '\ud83d\udfe2 STRONG', moderate: '\ud83d\udfe1 MODERATE', weak: '\u26aa WEAK', conflicting: '\ud83d\udd34 CONFLICTING' };
    conflEl.textContent = conflEmoji[data.confluence] || data.confluence;
    conflEl.className = `font-bold ${conflColors[data.confluence] || ''}`;

    renderTimeframes(data.timeframes);

    // Recommendation
    const recCard = document.getElementById('recommendation-card');
    const recBg = data.confluence === 'strong'
        ? (data.overall_bias === 'bullish' ? 'bg-green-50 border border-green-200 text-green-900'
            : 'bg-red-50 border border-red-200 text-red-900')
        : data.confluence === 'conflicting'
            ? 'bg-red-50 border border-red-200 text-red-900'
            : 'bg-yellow-50 border border-yellow-200 text-yellow-900';
    recCard.className = `${recBg} rounded-xl shadow-md p-5 slide-in`;
    document.getElementById('recommendation').textContent = data.recommendation;

    // ORB
    if (data.orb_data) {
        document.getElementById('orb-high').textContent = data.orb_data.orb_high || '--';
        document.getElementById('orb-low').textContent = data.orb_data.orb_low || '--';
        const orbStatus = document.getElementById('orb-status');
        orbStatus.textContent = data.orb_data.breakout === 'bullish' ? '\ud83d\ude80 Bullish Breakout!'
            : data.orb_data.breakout === 'bearish' ? '\ud83d\udcc9 Bearish Breakdown!' : '\ud83d\udd04 Inside Range';
        orbStatus.className = data.orb_data.breakout === 'bullish' ? 'text-green-600 font-bold'
            : data.orb_data.breakout === 'bearish' ? 'text-red-600 font-bold' : 'text-gray-600 font-bold';
    }

    renderSignals(data.signals);
    renderPriceChart(data.price_data, data.patterns || [], data.support_resistance || {});
    renderVolumeChart(data.price_data);
    renderPatterns(data.patterns || []);
    renderSupportResistance(data.support_resistance || {}, data.current_price);
    renderInsights(data);

    if (data.trade_signal) renderTradeSignal(data.trade_signal);
    pollAutoTraderStatus();
}

function renderTimeframes(timeframes) {
    const container = document.getElementById('tf-breakdown');
    container.innerHTML = '';
    timeframes.forEach(tf => {
        const biasColor = { bullish: 'border-green-500', bearish: 'border-red-500', neutral: 'border-gray-300' }[tf.bias];
        const biasText = { bullish: 'text-green-600', bearish: 'text-red-600', neutral: 'text-gray-500' }[tf.bias];
        const biasEmoji = { bullish: '\ud83d\udc02', bearish: '\ud83d\udc3b', neutral: '\u2696\ufe0f' }[tf.bias];

        container.innerHTML += `
            <div class="bg-gray-50 rounded-lg p-4 border-l-4 ${biasColor}">
                <div class="flex justify-between items-center mb-2">
                    <span class="font-bold text-gray-800">${tf.label}</span>
                    <span class="text-xs bg-gray-200 text-gray-600 px-2 py-1 rounded-full">${tf.weight}% weight</span>
                </div>
                ${tf.error
                    ? `<p class="text-red-500 text-sm">\u26a0\ufe0f ${tf.error}</p>`
                    : `
                        <div class="flex justify-between text-sm mb-1">
                            <span class="text-green-600">\u2191 ${tf.bullish_pct}%</span>
                            <span class="text-red-600">\u2193 ${tf.bearish_pct}%</span>
                        </div>
                        <div class="w-full bg-red-200 rounded-full h-3 overflow-hidden">
                            <div class="bg-green-500 h-3 rounded-full transition-all" style="width: ${tf.bullish_pct}%"></div>
                        </div>
                        <p class="mt-1 text-sm font-bold ${biasText}">${biasEmoji} ${tf.bias.toUpperCase()} (${tf.confidence})</p>
                    `
                }
            </div>`;
    });
}

function renderSignals(signals) {
    const tbody = document.getElementById('signals-body');
    tbody.innerHTML = '';
    signals.forEach(s => {
        const biasColor = { bullish: 'text-green-600', bearish: 'text-red-600', neutral: 'text-gray-500' }[s.bias];
        const biasBg = { bullish: 'bg-green-50', bearish: 'bg-red-50', neutral: 'bg-gray-50' }[s.bias];
        const barColor = { bullish: 'bg-green-500', bearish: 'bg-red-500', neutral: 'bg-gray-400' }[s.bias];
        const badgeBg = { bullish: 'bg-green-100', bearish: 'bg-red-100', neutral: 'bg-gray-200' }[s.bias];
        const pct = Math.round(s.strength * 100);
        tbody.innerHTML += `
            <tr class="${biasBg} border-b border-gray-200">
                <td class="px-4 py-3 font-semibold text-gray-800">${s.name}</td>
                <td class="px-4 py-3 ${biasColor} font-bold">${s.value}</td>
                <td class="px-4 py-3"><span class="inline-block px-2 py-1 rounded-full text-xs font-bold ${biasColor} ${badgeBg}">${s.bias.toUpperCase()}</span></td>
                <td class="px-4 py-3">
                    <div class="w-full bg-gray-200 rounded-full h-2"><div class="${barColor} h-2 rounded-full" style="width: ${pct}%"></div></div>
                    <span class="text-xs text-gray-500">${pct}%</span>
                </td>
                <td class="px-4 py-3 text-sm text-gray-600">${s.description}</td>
            </tr>`;
    });
}

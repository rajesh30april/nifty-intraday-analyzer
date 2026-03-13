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

var isAuthenticated = false;

async function checkStatus() {
    try {
        const resp = await fetch('/api/status');
        const data = await resp.json();
        const badge = document.getElementById('data-source-badge');
        const banner = document.getElementById('login-banner');
        const loginOverlay = document.getElementById('login-required-overlay');
        if (data.authenticated) {
            isAuthenticated = true;
            badge.innerHTML = '<span class="flex items-center gap-2 bg-green-500 text-white px-3 py-1.5 rounded-full text-sm font-bold"><span class="w-2 h-2 bg-white rounded-full live-dot"></span>LIVE</span>';
            banner.classList.add('hidden');
            if (loginOverlay) loginOverlay.classList.add('hidden');
            startLiveTickPolling();
            loadMargins();
        } else {
            isAuthenticated = false;
            badge.innerHTML = '<span class="flex items-center gap-2 bg-red-500 text-white px-3 py-1.5 rounded-full text-sm font-bold">\u26a0\ufe0f NOT CONNECTED</span>';
            banner.classList.remove('hidden');
            if (loginOverlay) loginOverlay.classList.remove('hidden');
            document.getElementById('loading').classList.add('hidden');
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

// ── Per-Section Loading ──────────────────────────────────────────

function _sectionLoading(sectionId, loading = true) {
    const el = document.getElementById(sectionId);
    if (!el) return;
    const spinner = el.querySelector('.section-spinner');
    const content = el.querySelector('.section-content');
    if (spinner) spinner.classList.toggle('hidden', !loading);
    if (content) content.classList.toggle('opacity-50', loading);
}

function _sectionError(sectionId, msg) {
    const el = document.getElementById(sectionId);
    if (!el) return;
    const spinner = el.querySelector('.section-spinner');
    if (spinner) spinner.innerHTML = `<span class="text-red-500 text-xs">⚠️ ${msg}</span>`;
}

// Load ALL sections in parallel (initial load or full refresh)
async function loadAnalysis() {
    if (!isAuthenticated) return;

    // Show dashboard, hide loading overlay
    document.getElementById('loading').classList.add('hidden');
    document.getElementById('dashboard').classList.remove('hidden');
    document.getElementById('error-state').classList.add('hidden');

    // Fire all sections in parallel
    await Promise.allSettled([
        loadSectionProbability(),
        loadSectionTrendHealth(),
        loadSectionChart(),
        loadSectionTradeSignal(),
    ]);

    pollAutoTraderStatus();
    const now = new Date();
    const liveBadge = document.getElementById('live-badge');
    if (liveBadge) liveBadge.textContent = `\ud83d\udfe2 Updated ${now.toLocaleTimeString('en-IN')}`;
}

// Section 1: MTF Probability (heaviest — fetches 3 timeframes)
async function loadSectionProbability() {
    _sectionLoading('section-probability', true);
    try {
        const resp = await fetch('/api/section/probability');
        const data = await resp.json();
        if (!data.success) { _sectionError('section-probability', data.error); return; }

        // Price
        document.getElementById('current-price').textContent =
            data.current_price ? data.current_price.toLocaleString('en-IN') : '--';
        const changeEl = document.getElementById('day-change');
        if (data.day_change != null) {
            const sign = data.day_change >= 0 ? '+' : '';
            changeEl.textContent = `${sign}${data.day_change} (${sign}${data.day_change_pct}%)`;
            changeEl.className = data.day_change >= 0 ? 'text-sm font-semibold text-green-600' : 'text-sm font-semibold text-red-600';
        }

        // Probability
        document.getElementById('bull-pct').textContent = data.bullish_probability;
        document.getElementById('bear-pct').textContent = data.bearish_probability;
        document.getElementById('prob-bar').style.width = `${data.bullish_probability}%`;

        // Bias
        const biasEl = document.getElementById('overall-bias');
        const biasEmoji = { bullish: '\ud83d\udc02 BULLISH', bearish: '\ud83d\udc3b BEARISH', neutral: '\u2696\ufe0f NEUTRAL' };
        const biasColors = { bullish: 'text-green-600', bearish: 'text-red-600', neutral: 'text-gray-600' };
        biasEl.textContent = biasEmoji[data.overall_bias] || data.overall_bias;
        biasEl.className = `text-2xl font-black ${biasColors[data.overall_bias] || ''}`;

        // Confidence & Confluence
        const confEl = document.getElementById('confidence');
        confEl.textContent = data.confidence.toUpperCase();
        const confColors = { high: 'text-green-600', medium: 'text-yellow-600', low: 'text-red-500' };
        confEl.className = `font-bold ${confColors[data.confidence] || ''}`;

        const conflEl = document.getElementById('confluence');
        const conflEmoji = { strong: '\ud83d\udfe2 STRONG', moderate: '\ud83d\udfe1 MODERATE', weak: '\u26aa WEAK', conflicting: '\ud83d\udd34 CONFLICTING' };
        const conflColors = { strong: 'text-green-600', moderate: 'text-yellow-600', weak: 'text-gray-500', conflicting: 'text-red-600' };
        conflEl.textContent = conflEmoji[data.confluence] || data.confluence;
        conflEl.className = `font-bold ${conflColors[data.confluence] || ''}`;

        // Timeframes
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
        }

        // Signals table
        renderSignals(data.signals || []);
        renderInsights(data);
    } catch (e) {
        _sectionError('section-probability', e.message);
    } finally {
        _sectionLoading('section-probability', false);
    }
}

// Section 2: Chart + Patterns + S/R
async function loadSectionChart() {
    _sectionLoading('section-chart', true);
    try {
        const resp = await fetch(`/api/section/chart?tf=${selectedChartTF}`);
        const data = await resp.json();
        if (!data.success) { _sectionError('section-chart', data.error); return; }

        renderPriceChart(data.price_data, data.patterns || [], data.support_resistance || {});
        renderVolumeChart(data.price_data);
        renderPatterns(data.patterns || []);
        renderSupportResistance(data.support_resistance || {},
            data.price_data.length > 0 ? data.price_data[data.price_data.length - 1].close : 0);
    } catch (e) {
        _sectionError('section-chart', e.message);
    } finally {
        _sectionLoading('section-chart', false);
    }
}

// Section 3: Trade Signal
async function loadSectionTradeSignal() {
    _sectionLoading('section-trade-signal', true);
    try {
        const resp = await fetch('/api/section/trade-signal');
        const data = await resp.json();
        if (!data.success) { _sectionError('section-trade-signal', data.error); return; }
        renderTradeSignal(data);
    } catch (e) {
        _sectionError('section-trade-signal', e.message);
    } finally {
        _sectionLoading('section-trade-signal', false);
    }
}

// Section 4: Trend Health — Continue or Reverse?
async function loadSectionTrendHealth() {
    _sectionLoading('section-trend-health', true);
    try {
        const resp = await fetch('/api/section/trend-health');
        const data = await resp.json();
        if (!data.success) { _sectionError('section-trend-health', data.error); return; }
        renderTrendHealth(data);
    } catch (e) {
        _sectionError('section-trend-health', e.message);
    } finally {
        _sectionLoading('section-trend-health', false);
    }
}

function renderTrendHealth(data) {
    // Verdict banner
    document.getElementById('th-verdict-emoji').textContent = data.verdict_emoji;
    const verdictEl = document.getElementById('th-verdict');
    verdictEl.textContent = data.verdict;

    const verdictColors = {
        'TREND CONTINUES': 'text-green-600',
        'REVERSAL LIKELY': 'text-red-600',
        'REVERSAL BREWING': 'text-yellow-600',
        'MIXED SIGNALS': 'text-gray-600',
    };
    verdictEl.className = `text-2xl font-black ${verdictColors[data.verdict] || 'text-gray-800'}`;

    // Banner background
    const banner = document.getElementById('th-verdict-banner');
    const bannerBg = {
        'TREND CONTINUES': 'bg-green-50 border-b border-green-200',
        'REVERSAL LIKELY': 'bg-red-50 border-b border-red-200',
        'REVERSAL BREWING': 'bg-yellow-50 border-b border-yellow-200',
        'MIXED SIGNALS': 'bg-gray-50 border-b border-gray-200',
    };
    banner.className = `px-5 py-4 flex items-center justify-between ${bannerBg[data.verdict] || 'bg-gray-50 border-b'}`;

    // Trend label
    const trendEmoji = { uptrend: '\ud83d\udcc8 UPTREND', downtrend: '\ud83d\udcc9 DOWNTREND', sideways: '\u2796 SIDEWAYS' };
    document.getElementById('th-trend').textContent = `Current: ${trendEmoji[data.current_trend] || data.current_trend}`;

    // Scores
    document.getElementById('th-continue-score').textContent = data.continuation_score;
    document.getElementById('th-reverse-score').textContent = data.reversal_score;
    document.getElementById('th-total').textContent = data.total_signals;

    // Confidence
    const confEl = document.getElementById('th-confidence');
    confEl.textContent = data.confidence.toUpperCase();
    const confColors = { high: 'text-green-600', medium: 'text-yellow-600', low: 'text-red-500' };
    confEl.className = `font-bold ${confColors[data.confidence] || ''}`;

    // Summary
    document.getElementById('th-summary').textContent = data.summary;

    // Signal checklist
    const container = document.getElementById('th-signals');
    container.innerHTML = '';

    data.signals.forEach(s => {
        const statusColor = {
            continuation: 'border-green-500 bg-green-50',
            reversal: 'border-red-500 bg-red-50',
            neutral: 'border-gray-300 bg-gray-50',
        }[s.status];
        const statusBadge = {
            continuation: '<span class="bg-green-100 text-green-700 px-2 py-0.5 rounded-full text-xs font-bold">CONTINUE</span>',
            reversal: '<span class="bg-red-100 text-red-700 px-2 py-0.5 rounded-full text-xs font-bold">REVERSAL</span>',
            neutral: '<span class="bg-gray-200 text-gray-600 px-2 py-0.5 rounded-full text-xs font-bold">NEUTRAL</span>',
        }[s.status];

        container.innerHTML += `
            <div class="flex items-center gap-3 p-3 rounded-lg border-l-4 ${statusColor}">
                <span class="text-2xl">${s.emoji}</span>
                <div class="flex-1">
                    <div class="flex items-center gap-2">
                        <span class="font-bold text-gray-800">${s.name}</span>
                        ${statusBadge}
                        <span class="text-sm font-bold text-gray-600 ml-auto">${s.value}</span>
                    </div>
                    <p class="text-sm text-gray-600 mt-0.5">${s.detail}</p>
                </div>
            </div>`;
    });
}

function showError(msg) {
    document.getElementById('loading').classList.add('hidden');
    document.getElementById('error-state').classList.remove('hidden');
    document.getElementById('error-msg').textContent = msg;
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

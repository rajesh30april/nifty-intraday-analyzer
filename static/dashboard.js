// Dashboard logic — extracted from index.html inline script

var candlestickChart = null;
var candleSeries = null;
var volumeChart = null;
var liveTickInterval = null;
var selectedChartTF = '5m';
var currentPageId = 'overview';

// ── Sidebar Navigation ──────────────────────────────────────────

function switchPage(pageId) {
    currentPageId = pageId;
    // Hide all pages
    document.querySelectorAll('.page-section').forEach(p => p.classList.remove('active'));
    // Show target page
    const target = document.getElementById('page-' + pageId);
    if (target) target.classList.add('active');
    // Update sidebar active state
    document.querySelectorAll('.sidebar-item').forEach(item => {
        item.classList.toggle('active', item.dataset.page === pageId);
    });
    // Trigger pattern history scan on first open
    if (pageId === 'patterns' && typeof onPatternsTabOpen === 'function') {
        setTimeout(onPatternsTabOpen, 50);
    }
    // Start/stop auto-trader polling when switching in/out
    if (pageId === 'auto-trader' && typeof onAutoTraderTabOpen === 'function') {
        setTimeout(onAutoTraderTabOpen, 50);
    } else if (typeof onAutoTraderTabClose === 'function') {
        onAutoTraderTabClose();
    }
    // Re-render charts if switching to charts page (canvas sizing)
    if (pageId === 'charts') {
        setTimeout(() => {
            // Re-fit main candlestick chart
            const container = document.getElementById('candlestickChart');
            if (container && candlestickChart) {
                candlestickChart.applyOptions({ width: container.clientWidth });
                candlestickChart.timeScale().fitContent();
            }
            // Re-fit all pattern mini charts
            if (window._patternMiniCharts) {
                window._patternMiniCharts.forEach(mc => {
                    try {
                        const el = mc._chartElement || mc.chartElement();
                        if (el && el.parentElement) {
                            mc.applyOptions({ width: el.parentElement.clientWidth });
                            mc.timeScale().fitContent();
                        }
                    } catch(e) { /* skip removed charts */ }
                });
            }
        }, 150);
    }
}

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
            refreshAllPositions();
            if (!positionsInterval) positionsInterval = setInterval(refreshAllPositions, 30000);
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


// ── Open Positions Bar (all exchanges: NFO, MCX, BSE…) ──────────────
async function refreshAllPositions() {
    const bar   = document.getElementById('open-positions-bar');
    const chips = document.getElementById('open-positions-chips');
    if (!bar || !chips) return;
    try {
        const resp = await fetch('/api/positions/all');
        const data = await resp.json();
        if (!data.success || !data.positions || data.positions.length === 0) {
            bar.classList.add('hidden');
            return;
        }
        bar.classList.remove('hidden');
        chips.innerHTML = data.positions.map(p => {
            const isProfit = p.pnl >= 0;
            const pnlColor = isProfit ? 'text-green-400' : 'text-red-400';
            const pnlSign  = isProfit ? '+' : '';
            const qty      = p.quantity > 0 ? `▲${p.quantity}` : `▼${Math.abs(p.quantity)}`;
            const qtyColor = p.quantity > 0 ? 'text-green-300' : 'text-red-300';
            const exBadge  = p.exchange === 'MCX'  ? 'bg-orange-900/60 text-orange-300 border-orange-700' :
                             p.exchange === 'NFO'  ? 'bg-blue-900/60  text-blue-300  border-blue-700'   :
                             p.exchange === 'NSE'  ? 'bg-teal-900/60  text-teal-300  border-teal-700'   :
                                                    'bg-gray-800     text-gray-400  border-gray-700';
            return `
              <div class="flex items-center gap-1.5 border rounded-lg px-2.5 py-1 ${exBadge}"
                   title="Avg: ₹${p.avg_price} | LTP: ₹${p.ltp}">
                <span class="text-[10px] font-bold opacity-70">${p.exchange}</span>
                <span class="text-xs font-semibold text-white">${p.symbol}</span>
                <span class="text-[10px] font-bold ${qtyColor}">${qty}</span>
                <span class="text-[10px] font-bold ${pnlColor}">${pnlSign}₹${p.pnl.toLocaleString('en-IN')}</span>
              </div>`;
        }).join('');
    } catch (e) {
        console.error('positions fetch failed:', e);
    }
}


function startLiveTickPolling() {
    if (liveTickInterval) clearInterval(liveTickInterval);
    liveTickInterval = setInterval(async () => {
        try {
            const resp = await fetch('/api/live-tick');
            const data = await resp.json();
            if (data.success) {
                const priceStr = data.last_price.toLocaleString('en-IN');
                document.getElementById('current-price').textContent = priceStr;
                const headerPrice = document.getElementById('header-price');
                if (headerPrice) headerPrice.textContent = '₹' + priceStr;
                document.getElementById('live-price-time').textContent = `Live \u2022 ${new Date().toLocaleTimeString()}`;
            }
        } catch (e) { /* silent */ }
    }, 1000);
}


// Load ALL sections in parallel (initial load or full refresh)
async function loadAnalysis() {
    if (!isAuthenticated) return;

    // Show dashboard, hide loading overlay
    document.getElementById('loading').classList.add('hidden');
    document.getElementById('dashboard').classList.remove('hidden');
    document.getElementById('error-state').classList.add('hidden');

    // Show progress bar
    _showProgress();

    // Fire all sections in parallel
    await Promise.allSettled([
        loadSectionProbability(),
        loadSectionTrendHealth(),
        loadSectionChart(),
        loadSectionTradeSignal(),
    ]);

    // Ensure progress completes
    _hideProgress();

    pollAutoTraderStatus();
    const now = new Date();
    const liveBadge = document.getElementById('live-badge');
    if (liveBadge) liveBadge.textContent = `\ud83d\udfe2 Updated ${now.toLocaleTimeString('en-IN')}`;
}

// Section 1: MTF Probability (heaviest — fetches 3 timeframes)
async function loadSectionProbability() {
    const _t0 = Date.now();
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
        _updateSidebarBadges(data);
    } catch (e) {
        _sectionError('section-probability', e.message);
    } finally {
        _sectionLoading('section-probability', false);
        _sectionTimings['section-probability'] = Date.now() - _t0;
        _sectionDone('section-probability');
    }
}

// Section 2: Chart + Patterns + S/R
async function loadSectionChart() {
    const _t0 = Date.now();
    _sectionLoading('section-chart', true);
    try {
        const resp = await fetch(`/api/section/chart?tf=${selectedChartTF}`);
        const data = await resp.json();
        if (!data.success) { _sectionError('section-chart', data.error); return; }

        renderPriceChart(data.price_data, data.patterns || [], data.support_resistance || {});
        renderVolumeChart(data.price_data);
        renderPatterns(data.patterns || [], data.pattern_candles || {});
        renderSupportResistance(data.support_resistance || {},
            data.price_data.length > 0 ? data.price_data[data.price_data.length - 1].close : 0);
        // Update trend info banner on charts page
        _updateChartTrendBanner(data.patterns || [], data.support_resistance || {},
            data.price_data.length > 0 ? data.price_data[data.price_data.length - 1].close : 0);
    } catch (e) {
        _sectionError('section-chart', e.message);
    } finally {
        _sectionLoading('section-chart', false);
        _sectionTimings['section-chart'] = Date.now() - _t0;
        _sectionDone('section-chart');
    }
}

// Section 3: Trade Signal
async function loadSectionTradeSignal() {
    const _t0 = Date.now();
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
        _sectionTimings['section-trade-signal'] = Date.now() - _t0;
        _sectionDone('section-trade-signal');
    }
}

// Section 4: Trend Health — Continue or Reverse?
async function loadSectionTrendHealth() {
    const _t0 = Date.now();
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
        _sectionTimings['section-trend-health'] = Date.now() - _t0;
        _sectionDone('section-trend-health');
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

    // Update sidebar trend badge
    const trendBadge = document.getElementById('sidebar-trend-badge');
    if (trendBadge) {
        const badgeColors = {
            'TREND CONTINUES': 'bg-green-100 text-green-700',
            'REVERSAL LIKELY': 'bg-red-100 text-red-700',
            'REVERSAL BREWING': 'bg-yellow-100 text-yellow-700',
            'MIXED SIGNALS': 'bg-gray-200 text-gray-600',
        };
        const shortLabels = {
            'TREND CONTINUES': '✅',
            'REVERSAL LIKELY': '🔴',
            'REVERSAL BREWING': '🟡',
            'MIXED SIGNALS': '⚪',
        };
        trendBadge.textContent = shortLabels[data.verdict] || '--';
        trendBadge.className = `badge ${badgeColors[data.verdict] || 'bg-gray-200 text-gray-500'}`;
    }
}

function showError(msg) {
    document.getElementById('loading').classList.add('hidden');
    document.getElementById('error-state').classList.remove('hidden');
    document.getElementById('error-msg').textContent = msg;
}

// ── Chart Trend Info Banner ───────────────────────────────────────

function _updateChartTrendBanner(patterns, sr, currentPrice) {
    const banner = document.getElementById('chart-trend-banner');
    if (!banner) return;

    // Count patterns by bias
    const bullishPatterns = patterns.filter(p => p.bias === 'bullish');
    const bearishPatterns = patterns.filter(p => p.bias === 'bearish');
    const patternCount = patterns.length;

    // Determine pattern bias
    let patternBias = 'NEUTRAL';
    let patternBiasColor = 'text-gray-600';
    let biasEmoji = '⚖️';
    if (bullishPatterns.length > bearishPatterns.length) {
        patternBias = '🐂 BULLISH';
        patternBiasColor = 'text-green-600';
        biasEmoji = '📈';
    } else if (bearishPatterns.length > bullishPatterns.length) {
        patternBias = '🐻 BEARISH';
        patternBiasColor = 'text-red-600';
        biasEmoji = '📉';
    } else if (patternCount > 0) {
        patternBias = '⚖️ MIXED';
        patternBiasColor = 'text-yellow-600';
        biasEmoji = '⚖️';
    }

    // Determine trend from structure patterns
    const structurePattern = patterns.find(p => p.type === 'structure');
    let trendLabel = 'No Clear Trend';
    let trendDetail = 'Waiting for pattern data...';
    let trendEmoji = '📊';
    let borderColor = 'border-gray-300';

    if (structurePattern) {
        if (structurePattern.name.includes('Higher')) {
            trendLabel = '⬆️ UPTREND (HH/HL)';
            trendDetail = structurePattern.description;
            trendEmoji = '📈';
            borderColor = 'border-green-500';
        } else if (structurePattern.name.includes('Lower')) {
            trendLabel = '⬇️ DOWNTREND (LH/LL)';
            trendDetail = structurePattern.description;
            trendEmoji = '📉';
            borderColor = 'border-red-500';
        } else if (structurePattern.name.includes('Expanding')) {
            trendLabel = '⇔ EXPANDING RANGE';
            trendDetail = structurePattern.description;
            trendEmoji = '📊';
            borderColor = 'border-yellow-500';
        } else {
            trendLabel = structurePattern.name;
            trendDetail = structurePattern.description;
        }
    } else if (patternCount > 0) {
        // Infer from reversal/continuation patterns
        const continuationPatterns = patterns.filter(p => p.type === 'continuation');
        const reversalPatterns = patterns.filter(p => p.type === 'reversal');
        if (continuationPatterns.length > reversalPatterns.length) {
            trendLabel = '➡️ TREND CONTINUING';
            trendDetail = `${continuationPatterns.length} continuation pattern(s) detected`;
            trendEmoji = '➡️';
            borderColor = 'border-blue-500';
        } else if (reversalPatterns.length > 0) {
            trendLabel = '🔄 REVERSAL SIGNALS';
            trendDetail = `${reversalPatterns.length} reversal pattern(s) detected`;
            trendEmoji = '🔄';
            borderColor = 'border-orange-500';
        }
    }

    // S/R proximity
    let srStatus = '--';
    if (sr && currentPrice) {
        const nearestSupport = sr.nearest_support;
        const nearestResistance = sr.nearest_resistance;
        if (nearestSupport && nearestResistance) {
            const distToSupport = currentPrice - nearestSupport;
            const distToResistance = nearestResistance - currentPrice;
            const range = nearestResistance - nearestSupport;
            const posInRange = range > 0 ? ((currentPrice - nearestSupport) / range * 100).toFixed(0) : 50;
            if (distToSupport < distToResistance * 0.3) {
                srStatus = `🟢 Near Support (${posInRange}%)`;
            } else if (distToResistance < distToSupport * 0.3) {
                srStatus = `🔴 Near Resistance (${posInRange}%)`;
            } else {
                srStatus = `Mid-range (${posInRange}%)`;
            }
        }
    }

    // Update DOM
    banner.className = `bg-white rounded-xl shadow-sm p-4 border-l-4 ${borderColor}`;
    document.getElementById('chart-trend-emoji').textContent = trendEmoji;
    document.getElementById('chart-trend-label').textContent = trendLabel;
    document.getElementById('chart-trend-detail').textContent = trendDetail;
    document.getElementById('chart-pattern-count').textContent = patternCount;
    const patternBiasEl = document.getElementById('chart-pattern-bias');
    patternBiasEl.textContent = patternBias;
    patternBiasEl.className = `text-lg font-black ${patternBiasColor}`;
    document.getElementById('chart-sr-status').textContent = srStatus;
}

// ── Sidebar Badge Updates ─────────────────────────────────────

function _updateSidebarBadges(data) {
    // Called from loadSectionProbability with the probability data
    const sidebarBias = document.getElementById('sidebar-bias');
    const sidebarConf = document.getElementById('sidebar-conf');
    if (sidebarBias && data.overall_bias) {
        const biasEmojis = { bullish: '🐂', bearish: '🐻', neutral: '⚖️' };
        const biasColors = { bullish: 'text-green-600', bearish: 'text-red-600', neutral: 'text-gray-600' };
        sidebarBias.textContent = biasEmojis[data.overall_bias] || '--';
        sidebarBias.className = `font-bold ${biasColors[data.overall_bias] || ''}`;
    }
    if (sidebarConf && data.confidence) {
        sidebarConf.textContent = data.confidence.toUpperCase();
        const confColors = { high: 'text-green-600', medium: 'text-yellow-600', low: 'text-red-500' };
        sidebarConf.className = `font-bold ${confColors[data.confidence] || ''}`;
    }
    // Update header change
    const headerChange = document.getElementById('header-change');
    if (headerChange && data.day_change != null) {
        const sign = data.day_change >= 0 ? '+' : '';
        headerChange.textContent = `${sign}${data.day_change} (${sign}${data.day_change_pct}%)`;
        headerChange.className = `text-sm font-semibold ${data.day_change >= 0 ? 'text-green-300' : 'text-red-300'}`;
    }
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

/* ── Strategy Learn Page + Backtest Strategy Dropdown ───────────
 * Fetches strategy catalog from /api/strategies,
 * renders learn cards and populates backtest dropdown.
 */

let _allStrategies = [];
let _activeCategory = 'all';

/**
 * Load strategies from API and render both learn page and backtest dropdown.
 */
async function loadStrategies() {
    try {
        const resp = await fetch('/api/strategies');
        const data = await resp.json();
        if (!data.success) return;
        _allStrategies = data.strategies;
        renderLearnPage(_allStrategies);
        populateBacktestDropdown(_allStrategies);
    } catch (e) {
        console.error('Failed to load strategies:', e);
    }
}

/**
 * Populate the backtest strategy dropdown.
 */
function populateBacktestDropdown(strategies) {
    const select = document.getElementById('bt-strategy');
    if (!select) return;

    select.innerHTML = strategies.map(s => {
        const label = `${s.emoji} ${s.name}`;
        return `<option value="${s.id}">${label}</option>`;
    }).join('');

    // Default to smart_router
    select.value = 'smart_router';

    // Update description on change
    select.addEventListener('change', () => {
        const strat = _allStrategies.find(s => s.id === select.value);
        const descEl = document.getElementById('bt-strategy-desc');
        if (strat && descEl) {
            descEl.textContent = strat.description;
        }
    });
}

/**
 * Filter strategies by category.
 */
function filterStrategies(category) {
    _activeCategory = category;

    // Update tab styling
    document.querySelectorAll('.learn-cat-btn').forEach(btn => {
        btn.classList.remove('learn-cat-active');
        btn.classList.add('bg-gray-200', 'text-gray-600');
    });
    const activeBtn = document.querySelector(`.learn-cat-btn[data-cat="${category}"]`);
    if (activeBtn) {
        activeBtn.classList.add('learn-cat-active');
        activeBtn.classList.remove('bg-gray-200', 'text-gray-600');
    }

    const filtered = category === 'all'
        ? _allStrategies
        : _allStrategies.filter(s => s.category === category);

    renderLearnPage(filtered);
}

/**
 * Render all strategy cards in the learn page.
 */
function renderLearnPage(strategies) {
    const container = document.getElementById('learn-strategies-container');
    if (!container) return;

    if (!strategies.length) {
        container.innerHTML = '<p class="text-gray-400 text-center py-8">No strategies in this category.</p>';
        return;
    }

    container.innerHTML = strategies.map(s => _renderStrategyCard(s)).join('');
}

/**
 * Render a single strategy card with full educational content.
 */
function _renderStrategyCard(s) {
    const catColors = {
        trend: 'border-green-500 bg-green-50',
        reversal: 'border-purple-500 bg-purple-50',
        breakout: 'border-blue-500 bg-blue-50',
        momentum: 'border-orange-500 bg-orange-50',
        adaptive: 'border-indigo-500 bg-indigo-50',
    };
    const catBadgeColors = {
        trend: 'bg-green-100 text-green-800',
        reversal: 'bg-purple-100 text-purple-800',
        breakout: 'bg-blue-100 text-blue-800',
        momentum: 'bg-orange-100 text-orange-800',
        adaptive: 'bg-indigo-100 text-indigo-800',
    };
    const diffClass = `difficulty-${s.difficulty}`;

    const entryRulesHtml = s.entry_rules.map((r, i) =>
        `<div class="flex gap-2 items-start">
            <span class="flex-shrink-0 w-5 h-5 rounded-full bg-[#0053e2] text-white text-xs flex items-center justify-center font-bold">${i + 1}</span>
            <span class="text-sm text-gray-700">${r}</span>
        </div>`
    ).join('');

    const exitRulesHtml = s.exit_rules.map(r =>
        `<div class="flex gap-2 items-start">
            <span class="text-red-500">✘</span>
            <span class="text-sm text-gray-700">${r}</span>
        </div>`
    ).join('');

    const riskHtml = s.risk_tips.map(t =>
        `<div class="flex gap-2 items-start">
            <span>⚠️</span>
            <span class="text-sm text-gray-700">${t}</span>
        </div>`
    ).join('');

    const prosHtml = s.pros.map(p =>
        `<li class="text-sm text-green-800">✅ ${p}</li>`
    ).join('');

    const consHtml = s.cons.map(c =>
        `<li class="text-sm text-red-800">❌ ${c}</li>`
    ).join('');

    return `
    <div class="learn-card border-l-4 ${catColors[s.category] || 'border-gray-300 bg-gray-50'} rounded-xl overflow-hidden" data-category="${s.category}">
        <!-- Header -->
        <div class="p-4">
            <div class="flex items-center justify-between mb-2">
                <div class="flex items-center gap-2">
                    <span class="text-2xl">${s.emoji}</span>
                    <h3 class="font-bold text-gray-900 text-lg">${s.name}</h3>
                </div>
                <div class="flex gap-2">
                    <span class="text-xs px-2 py-0.5 rounded-full font-bold ${catBadgeColors[s.category] || 'bg-gray-200 text-gray-600'}">${s.category}</span>
                    <span class="text-xs px-2 py-0.5 rounded-full font-bold ${diffClass}">${s.difficulty}</span>
                </div>
            </div>
            <p class="text-sm text-gray-600">${s.description}</p>
            <div class="mt-2 flex items-center gap-2">
                <span class="text-xs font-bold text-gray-500">🌡️ Best for:</span>
                <span class="text-xs text-gray-600">${s.market_condition}</span>
            </div>
        </div>

        <!-- Collapsible details -->
        <details class="group">
            <summary class="px-4 py-2 bg-white/60 cursor-pointer text-sm font-bold text-[#0053e2] hover:bg-white/80 transition flex items-center gap-1">
                <span class="group-open:rotate-90 transition-transform">▶</span>
                View Entry Rules, Examples & Tips
            </summary>
            <div class="px-4 pb-4 space-y-4 bg-white/40">
                <!-- Entry Rules -->
                <div>
                    <h4 class="text-xs font-bold text-gray-500 uppercase mb-2">🎯 Entry Rules</h4>
                    <div class="space-y-2">${entryRulesHtml}</div>
                </div>

                <!-- Exit Rules -->
                <div>
                    <h4 class="text-xs font-bold text-gray-500 uppercase mb-2">🚪 Exit Rules</h4>
                    <div class="space-y-1.5">${exitRulesHtml}</div>
                </div>

                <!-- Example Scenario -->
                <div class="bg-white rounded-lg border p-3">
                    <h4 class="text-xs font-bold text-gray-500 uppercase mb-1">📝 Example Trade</h4>
                    <p class="text-sm text-gray-700 leading-relaxed">${s.example_scenario}</p>
                </div>

                <!-- Pros & Cons -->
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div class="bg-green-50 rounded-lg p-3 border border-green-200">
                        <h4 class="text-xs font-bold text-green-700 mb-1">✅ Pros</h4>
                        <ul class="space-y-1">${prosHtml}</ul>
                    </div>
                    <div class="bg-red-50 rounded-lg p-3 border border-red-200">
                        <h4 class="text-xs font-bold text-red-700 mb-1">❌ Cons</h4>
                        <ul class="space-y-1">${consHtml}</ul>
                    </div>
                </div>

                <!-- Risk Tips -->
                <div class="bg-yellow-50 rounded-lg p-3 border border-yellow-200">
                    <h4 class="text-xs font-bold text-yellow-700 mb-1">⚠️ Risk Management Tips</h4>
                    <div class="space-y-1.5">${riskHtml}</div>
                </div>

                <!-- Quick Backtest Button -->
                <div class="text-center">
                    <button onclick="quickBacktest('${s.id}')" class="bg-[#0053e2] hover:bg-blue-700 text-white px-6 py-2 rounded-lg text-sm font-bold transition">
                        🚀 Backtest ${s.name}
                    </button>
                </div>
            </div>
        </details>
    </div>
    `;
}

/**
 * Quick-launch backtest for a specific strategy from learn page.
 */
function quickBacktest(strategyId) {
    // Switch to backtest page
    switchPage('backtester');
    // Set strategy dropdown
    const select = document.getElementById('bt-strategy');
    if (select) {
        select.value = strategyId;
        select.dispatchEvent(new Event('change'));
    }
    // Auto-run
    setTimeout(() => runBacktest(), 300);
}

// Load strategies on page load
document.addEventListener('DOMContentLoaded', loadStrategies);

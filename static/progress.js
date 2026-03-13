// ── Per-Section Loading with Progress Tracking ──────────────────

const SECTIONS = [
    { id: 'section-probability', label: '🎯 Probability', emoji: '🎯', avgTime: 5000 },
    { id: 'section-trend-health', label: '🦠 Trend Health', emoji: '🦠', avgTime: 2500 },
    { id: 'section-chart', label: '📊 Charts', emoji: '📊', avgTime: 4000 },
    { id: 'section-trade-signal', label: '🚦 Trade Signal', emoji: '🚦', avgTime: 2000 },
];

// Track section load times for better ETA estimates
const _sectionTimings = {};
let _progressInterval = null;
let _loadStartTime = 0;
let _sectionStates = {}; // { sectionId: 'pending' | 'loading' | 'done' | 'error' }

function _sectionLoading(sectionId, loading = true) {
    const el = document.getElementById(sectionId);
    if (!el) return;
    const spinner = el.querySelector('.section-spinner');
    const content = el.querySelector('.section-content');
    if (spinner) spinner.classList.toggle('hidden', !loading);
    if (content) content.classList.toggle('opacity-50', loading);

    if (loading) {
        _sectionStates[sectionId] = 'loading';
    }
}

function _sectionDone(sectionId, success = true) {
    _sectionStates[sectionId] = success ? 'done' : 'error';
    _updateProgress();
}

function _sectionError(sectionId, msg) {
    const el = document.getElementById(sectionId);
    if (!el) return;
    const spinner = el.querySelector('.section-spinner');
    if (spinner) spinner.innerHTML = `<span class="text-red-500 text-xs">⚠️ ${msg}</span>`;
    _sectionStates[sectionId] = 'error';
    _updateProgress();
}

function _showProgress() {
    const container = document.getElementById('progress-container');
    if (container) container.classList.remove('hidden');
    _loadStartTime = Date.now();
    _sectionStates = {};
    SECTIONS.forEach(s => { _sectionStates[s.id] = 'pending'; });
    _updateProgress();

    // Smooth progress animation — update every 200ms
    if (_progressInterval) clearInterval(_progressInterval);
    _progressInterval = setInterval(_animateProgress, 200);
}

function _hideProgress() {
    if (_progressInterval) { clearInterval(_progressInterval); _progressInterval = null; }
    const container = document.getElementById('progress-container');
    if (container) {
        // Show 100% briefly then hide
        _setProgressBar(100, 'All done! 🐶');
        setTimeout(() => {
            container.classList.add('hidden');
        }, 800);
    }
}

function _setProgressBar(pct, label) {
    const bar = document.getElementById('progress-bar');
    const pctEl = document.getElementById('progress-pct');
    const labelEl = document.getElementById('progress-label');
    if (bar) bar.style.width = `${pct}%`;
    if (pctEl) pctEl.textContent = `${Math.round(pct)}%`;
    if (labelEl && label) labelEl.textContent = label;

    // Color transitions
    if (bar) {
        if (pct >= 100) {
            bar.className = 'h-3 rounded-full transition-all duration-500 ease-out bg-gradient-to-r from-green-500 to-emerald-400';
        } else if (pct >= 50) {
            bar.className = 'h-3 rounded-full transition-all duration-500 ease-out bg-gradient-to-r from-[#0053e2] to-blue-400';
        }
    }
}

function _updateProgress() {
    const total = SECTIONS.length;
    const done = SECTIONS.filter(s => _sectionStates[s.id] === 'done' || _sectionStates[s.id] === 'error').length;
    const basePct = (done / total) * 100;

    // Render section pills
    const sectionsEl = document.getElementById('progress-sections');
    if (sectionsEl) {
        sectionsEl.innerHTML = SECTIONS.map(s => {
            const state = _sectionStates[s.id] || 'pending';
            const styles = {
                pending: 'bg-gray-100 text-gray-400',
                loading: 'bg-blue-100 text-blue-700 animate-pulse',
                done: 'bg-green-100 text-green-700',
                error: 'bg-red-100 text-red-600',
            };
            const icons = { pending: '⏳', loading: '⚙️', done: '✅', error: '❌' };
            return `<span class="px-2 py-1 rounded-full font-bold ${styles[state]}">${icons[state]} ${s.label}</span>`;
        }).join('');
    }

    // Label
    const label = done >= total ? 'All done! 🐶' : `Loading ${done}/${total} sections...`;

    // ETA calculation
    const elapsed = Date.now() - _loadStartTime;
    const etaEl = document.getElementById('progress-eta');
    if (etaEl) {
        if (done > 0 && done < total) {
            // Use remaining sections' average times
            const remainingSections = SECTIONS.filter(s => _sectionStates[s.id] !== 'done' && _sectionStates[s.id] !== 'error');
            const maxRemaining = Math.max(...remainingSections.map(s => {
                const hist = _sectionTimings[s.id];
                return hist ? hist : s.avgTime;
            }));
            // Since they run in parallel, ETA = max remaining time - already elapsed
            const etaSec = Math.max(0, Math.ceil((maxRemaining - elapsed) / 1000));
            etaEl.textContent = etaSec > 0 ? `~${etaSec}s remaining` : 'Almost done...';
        } else if (done >= total) {
            etaEl.textContent = `Done in ${(elapsed / 1000).toFixed(1)}s`;
        } else {
            // Estimate total from slowest section
            const maxTime = Math.max(...SECTIONS.map(s => _sectionTimings[s.id] || s.avgTime));
            etaEl.textContent = `~${Math.ceil(maxTime / 1000)}s estimated`;
        }
    }

    _setProgressBar(basePct, label);
}

function _animateProgress() {
    // Smooth animation: for loading sections, show intermediate progress
    const total = SECTIONS.length;
    const done = SECTIONS.filter(s => _sectionStates[s.id] === 'done' || _sectionStates[s.id] === 'error').length;
    const loading = SECTIONS.filter(s => _sectionStates[s.id] === 'loading');

    const basePct = (done / total) * 100;
    const elapsed = Date.now() - _loadStartTime;

    // For each loading section, estimate partial progress
    let partialPct = 0;
    loading.forEach(s => {
        const expectedTime = _sectionTimings[s.id] || s.avgTime;
        const sectionProgress = Math.min(0.9, elapsed / expectedTime); // cap at 90%
        partialPct += (sectionProgress / total) * 100;
    });

    const smoothPct = Math.min(99, basePct + partialPct);
    _setProgressBar(smoothPct, null); // don't update label here

    // Update ETA
    const etaEl = document.getElementById('progress-eta');
    if (etaEl && done < total) {
        const remainingSections = SECTIONS.filter(s => _sectionStates[s.id] !== 'done' && _sectionStates[s.id] !== 'error');
        const maxRemaining = Math.max(...remainingSections.map(s => _sectionTimings[s.id] || s.avgTime));
        const etaSec = Math.max(0, Math.ceil((maxRemaining - elapsed) / 1000));
        etaEl.textContent = etaSec > 0 ? `~${etaSec}s remaining` : 'Almost done...';
    }

    // Auto-stop when all done
    if (done >= total) {
        _hideProgress();
    }
}

function renderPriceChart(priceData, patterns, sr) {
    const container = document.getElementById('candlestickChart');
    container.innerHTML = '';

    // Create Lightweight Chart
    candlestickChart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 420,
        layout: { background: { color: '#ffffff' }, textColor: '#333' },
        grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: '#d1d5db' },
        timeScale: { borderColor: '#d1d5db', timeVisible: true, secondsVisible: false },
    });

    // Build candlestick data from full date-time strings ("2026-03-12 09:15")
    // IMPORTANT: Lightweight Charts displays timestamps in UTC.
    // We create UTC timestamps that numerically match IST times
    // so "09:15 IST" displays as "09:15" on the chart (not shifted).
    const candleData = priceData.map(d => {
        // Parse "YYYY-MM-DD HH:MM" format
        const parts = d.time.split(' ');
        const [year, month, day] = parts[0].split('-').map(Number);
        const [h, m] = parts[1].split(':').map(Number);
        const dt = new Date(Date.UTC(year, month - 1, day, h, m, 0));
        return {
            time: Math.floor(dt.getTime() / 1000),
            open: d.open || d.close,
            high: d.high || d.close,
            low: d.low || d.close,
            close: d.close,
        };
    }).filter(d => !isNaN(d.time));

    if (!candleData.length) return;

    // Add candlestick series
    candleSeries = candlestickChart.addCandlestickSeries({
        upColor: '#2a8703',
        downColor: '#ea1100',
        borderUpColor: '#2a8703',
        borderDownColor: '#ea1100',
        wickUpColor: '#2a8703',
        wickDownColor: '#ea1100',
    });
    candleSeries.setData(candleData);

    // Add Volume as histogram series
    const volSeries = candlestickChart.addHistogramSeries({
        color: 'rgba(255, 194, 32, 0.5)',
        priceFormat: { type: 'volume' },
        priceScaleId: 'vol',
    });
    candlestickChart.priceScale('vol').applyOptions({
        scaleMargins: { top: 0.85, bottom: 0 },
    });
    volSeries.setData(candleData.map((d, i) => {
        const vol = (i < priceData.length) ? (priceData[i].volume || 0) : 0;
        return {
            time: d.time,
            value: vol,
            color: d.close >= d.open ? 'rgba(42,135,3,0.3)' : 'rgba(234,17,0,0.3)',
        };
    }));

    // Add S/R horizontal lines
    if (sr) {
        (sr.support_levels || []).forEach(level => {
            const line = candleSeries.createPriceLine({
                price: level,
                color: '#2a8703',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: `S ${level.toFixed(0)}`,
            });
        });
        (sr.resistance_levels || []).forEach(level => {
            const line = candleSeries.createPriceLine({
                price: level,
                color: '#ea1100',
                lineWidth: 1,
                lineStyle: LightweightCharts.LineStyle.Dashed,
                axisLabelVisible: true,
                title: `R ${level.toFixed(0)}`,
            });
        });
    }

    // Add Pattern key levels as highlighted lines
    if (patterns && patterns.length) {
        patterns.forEach(p => {
            if (!p.key_levels) return;
            Object.entries(p.key_levels).forEach(([name, level]) => {
                const isNeckline = name.includes('neckline');
                candleSeries.createPriceLine({
                    price: level,
                    color: isNeckline ? '#7c3aed' : '#a855f7',
                    lineWidth: isNeckline ? 2 : 1,
                    lineStyle: isNeckline ? LightweightCharts.LineStyle.Solid : LightweightCharts.LineStyle.Dotted,
                    axisLabelVisible: true,
                    title: `${p.name}: ${name.replace('_',' ')}`,
                });
            });
        });

        // Add markers for pattern pivot points
        const markers = [];
        patterns.forEach(p => {
            if (!p.pivot_times) return;
            p.pivot_times.forEach((ts, i) => {
                try {
                    const dt = new Date(ts);
                    // Convert to "fake UTC" matching our candle timestamps
                    // Extract IST hours/minutes and create UTC timestamp with those values
                    const istOffset = 5.5 * 3600; // IST is UTC+5:30
                    const utcSec = Math.floor(dt.getTime() / 1000);
                    const fakeUtcSec = utcSec + istOffset;
                    // Find nearest candle time
                    const nearest = candleData.reduce((prev, curr) =>
                        Math.abs(curr.time - fakeUtcSec) < Math.abs(prev.time - fakeUtcSec) ? curr : prev
                    );
                    // Determine marker style based on pivot role:
                    // Double Top/Bottom: P1=trough/peak, P2=neckline, P3=trough/peak
                    // Neckline (middle pivot) gets a circle marker, extremes get arrows
                    const isNeckline = (p.pivot_times.length === 3 && i === 1);
                    const isBearish = p.bias === 'bearish';

                    let position, color, shape, label;
                    if (isNeckline) {
                        // Neckline: opposite side, purple circle
                        position = isBearish ? 'belowBar' : 'aboveBar';
                        color = '#7c3aed';
                        shape = 'circle';
                        label = 'Neckline';
                    } else {
                        position = isBearish ? 'aboveBar' : 'belowBar';
                        color = isBearish ? '#ea1100' : '#2a8703';
                        shape = isBearish ? 'arrowDown' : 'arrowUp';
                        // Label as T1/T2 for double bottom, P1/P2 for double top
                        const pivotNum = i === 0 ? 1 : 2;
                        label = `${p.name} ${isBearish ? 'P' : 'T'}${pivotNum}`;
                    }

                    markers.push({
                        time: nearest.time,
                        position: position,
                        color: color,
                        shape: shape,
                        text: label,
                    });
                } catch (e) { /* skip invalid times */ }
            });
        });
        // Sort markers by time (required by Lightweight Charts)
        markers.sort((a, b) => a.time - b.time);
        if (markers.length) candleSeries.setMarkers(markers);
    }

    // Fit content
    candlestickChart.timeScale().fitContent();

    // Responsive resize
    const ro = new ResizeObserver(() => {
        candlestickChart.applyOptions({ width: container.clientWidth });
    });
    ro.observe(container);
}

function renderVolumeChart(priceData) {
    const ctx = document.getElementById('volumeChart').getContext('2d');
    if (volumeChart) volumeChart.destroy();
    volumeChart = new Chart(ctx, {
        type: 'bar',
        data: { labels: priceData.map(d => d.time), datasets: [{ label: 'Volume', data: priceData.map(d => d.volume), backgroundColor: 'rgba(255,194,32,0.7)', borderColor: '#ffc220', borderWidth: 1 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { ticks: { maxTicksLimit: 10, font: { size: 10 } } }, y: { ticks: { font: { size: 10 } } } } }
    });
}

function renderInsights(data) {
    const el = document.getElementById('insights');
    const insights = [];

    // Confluence insight
    if (data.confluence === 'strong') {
        insights.push(`💪 <strong>All 3 timeframes agree!</strong> This is a high-conviction setup. When 1m, 5m, and 15m align, the probability of follow-through is significantly higher.`);
    } else if (data.confluence === 'conflicting') {
        insights.push('⚠️ <strong>Timeframes are fighting each other!</strong> This is the #1 reason traders lose money — entering when timeframes disagree. Wait for alignment.');
    } else if (data.confluence === 'moderate') {
        insights.push('🟡 <strong>Partial alignment</strong> — 2 out of 3 timeframes agree. Trade with reduced size and tighter stops.');
    }

    // Bias insights
    if (data.bullish_probability > 65) {
        insights.push('🟢 <strong>Strong bullish MTF setup!</strong> Multiple timeframes favor upside. Look for pullbacks for entry.');
    } else if (data.bearish_probability > 65) {
        insights.push('🔴 <strong>Strong bearish MTF pressure!</strong> Multiple timeframes point down. Avoid longs.');
    } else {
        insights.push('🟡 <strong>Mixed signals across timeframes.</strong> No clear edge — best to wait.');
    }

    // Signal-specific
    const vwapSignal = data.signals?.find(s => s.name === 'VWAP');
    if (vwapSignal && vwapSignal.bias !== 'neutral') {
        insights.push(vwapSignal.bias === 'bullish'
            ? '🟢 Price <strong>above VWAP</strong> — dips toward VWAP are buy zones.'
            : '🔴 Price <strong>below VWAP</strong> — rallies toward VWAP are sell zones.');
    }

    const orbSignal = data.signals?.find(s => s.name === 'ORB (15m)');
    if (orbSignal && orbSignal.bias !== 'neutral') {
        insights.push(`🎯 <strong>ORB ${orbSignal.bias === 'bullish' ? 'breakout' : 'breakdown'}!</strong> ${orbSignal.description}`);
    }

    if (data.confidence === 'low') {
        insights.push('🐶 <strong>Puppy says:</strong> Low confidence — reduce size or sit out. Capital preservation > FOMO.');
    }

    el.innerHTML = insights.map(i => `<p>• ${i}</p>`).join('');
}

function renderPatterns(patterns) {
    const container = document.getElementById('patterns-container');
    if (!patterns.length) {
        container.innerHTML = '<p class="text-gray-400 text-center py-4">No clear patterns detected right now. Market may be ranging.</p>';
        return;
    }
    container.innerHTML = '';
    patterns.forEach(p => {
        const biasColor = { bullish: 'border-green-500 bg-green-50', bearish: 'border-red-500 bg-red-50', neutral: 'border-gray-300 bg-gray-50' }[p.bias];
        const biasText = { bullish: 'text-green-700', bearish: 'text-red-700', neutral: 'text-gray-700' }[p.bias];
        const biasEmoji = { bullish: '🐂', bearish: '🐻', neutral: '⚖️' }[p.bias];
        const typeLabel = { reversal: '🔄 Reversal', continuation: '➡️ Continuation', structure: '📀 Structure' }[p.type] || p.type;
        const confPct = Math.round(p.confidence * 100);
        const confColor = confPct >= 80 ? 'text-green-600' : confPct >= 60 ? 'text-yellow-600' : 'text-gray-500';

        let levelsHtml = '';
        if (p.key_levels && Object.keys(p.key_levels).length) {
            levelsHtml = '<div class="flex flex-wrap gap-2 mt-2">' +
                Object.entries(p.key_levels).map(([k, v]) =>
                    `<span class="text-xs bg-white px-2 py-1 rounded border">${k.replace('_',' ')}: <strong>${v}</strong></span>`
                ).join('') + '</div>';
        }

        // Format timestamps for display
        const fmtTime = (ts) => {
            if (!ts) return '';
            try {
                const d = new Date(ts);
                return d.toLocaleString('en-IN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
            } catch { return ts; }
        };

        let timeInfo = '';
        if (p.timeframe || p.start_time) {
            const tfLabel = p.timeframe ? `<span class="bg-[#0053e2] text-white px-2 py-0.5 rounded-full text-xs font-bold">${p.timeframe}</span>` : '';
            const startEnd = p.start_time ? `<span class="text-xs text-gray-500">${fmtTime(p.start_time)} → ${fmtTime(p.end_time)}</span>` : '';
            timeInfo = `<div class="flex items-center gap-2 mt-1">${tfLabel} ${startEnd}</div>`;
        }

        let pivotInfo = '';
        if (p.pivot_times && p.pivot_times.length) {
            pivotInfo = '<div class="flex flex-wrap gap-1 mt-1">' +
                p.pivot_times.map((t, i) => `<span class="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded">Pivot ${i+1}: ${fmtTime(t)}</span>`
                ).join('') + '</div>';
        }

        container.innerHTML += `
            <div class="border-l-4 ${biasColor} rounded-lg p-3">
                <div class="flex justify-between items-start">
                    <div>
                        <span class="font-bold ${biasText}">${biasEmoji} ${p.name}</span>
                        <span class="text-xs bg-gray-200 text-gray-600 px-2 py-0.5 rounded-full ml-2">${typeLabel}</span>
                    </div>
                    <span class="text-sm font-bold ${confColor}">${confPct}% confidence</span>
                </div>
                ${timeInfo}
                <p class="text-sm text-gray-600 mt-1">${p.description}</p>
                ${levelsHtml}
                ${pivotInfo}
            </div>`;
    });
}

function renderSupportResistance(sr, currentPrice) {
    const container = document.getElementById('sr-container');
    if (!sr.support_levels && !sr.resistance_levels) {
        container.innerHTML = '<p class="text-gray-400 text-center py-4">Insufficient data for S/R levels.</p>';
        return;
    }

    let html = '<div class="space-y-3">';

    // Resistance levels (top to bottom)
    const resistances = (sr.resistance_levels || []).slice().reverse();
    resistances.forEach(level => {
        const isNearest = level === sr.nearest_resistance;
        html += `
            <div class="flex items-center gap-3">
                <span class="w-3 h-3 rounded-full ${isNearest ? 'bg-red-500' : 'bg-red-300'}"></span>
                <div class="flex-1 h-0.5 ${isNearest ? 'bg-red-400' : 'bg-red-200'}"></div>
                <span class="text-sm font-mono ${isNearest ? 'font-bold text-red-600' : 'text-red-400'}">
                    R: ${level} ${isNearest ? '← nearest' : ''}
                </span>
            </div>`;
    });

    // Current price
    if (currentPrice) {
        html += `
            <div class="flex items-center gap-3 py-1">
                <span class="w-3 h-3 rounded-full bg-[#0053e2] ring-2 ring-blue-200"></span>
                <div class="flex-1 h-1 bg-[#0053e2]"></div>
                <span class="text-sm font-mono font-bold text-[#0053e2]">▶ ${currentPrice} (now)</span>
            </div>`;
    }

    // Support levels (top to bottom)
    const supports = (sr.support_levels || []).slice().reverse();
    supports.forEach(level => {
        const isNearest = level === sr.nearest_support;
        html += `
            <div class="flex items-center gap-3">
                <span class="w-3 h-3 rounded-full ${isNearest ? 'bg-green-500' : 'bg-green-300'}"></span>
                <div class="flex-1 h-0.5 ${isNearest ? 'bg-green-400' : 'bg-green-200'}"></div>
                <span class="text-sm font-mono ${isNearest ? 'font-bold text-green-600' : 'text-green-400'}">
                    S: ${level} ${isNearest ? '← nearest' : ''}
                </span>
            </div>`;
    });

    html += '</div>';
    container.innerHTML = html;
}


// ── Trade Signal Panel Renderer ──────────────────────────────────────

function renderTradeSignal(ts) {
    if (!ts || ts.error) return;

    const panel = document.getElementById('trade-signal-panel');

    // Panel border color based on action
    const borderColors = {
        'BUY': 'border-green-500 bg-green-50',
        'SELL': 'border-red-500 bg-red-50',
        'EXIT_LONG': 'border-red-600 bg-red-100',
        'EXIT_SHORT': 'border-green-600 bg-green-100',
        'HOLD': 'border-yellow-500 bg-yellow-50',
    };
    const actionColors = {
        'BUY': 'bg-green-600 text-white',
        'SELL': 'bg-red-600 text-white',
        'EXIT_LONG': 'bg-red-700 text-white animate-pulse',
        'EXIT_SHORT': 'bg-green-700 text-white animate-pulse',
        'HOLD': 'bg-yellow-500 text-gray-900',
    };
    const actionLabels = {
        'BUY': '⬆ BUY',
        'SELL': '⬇ SELL',
        'EXIT_LONG': '🚨 EXIT LONG',
        'EXIT_SHORT': '🚨 EXIT SHORT',
        'HOLD': '⏸ HOLD',
    };

    panel.className = `rounded-xl shadow-md p-5 slide-in border-2 ${borderColors[ts.action] || 'border-gray-100 bg-white'}`;

    // Action badge
    const actionEl = document.getElementById('ts-action');
    actionEl.textContent = actionLabels[ts.action] || ts.action;
    actionEl.className = `text-2xl font-black px-4 py-1 rounded-lg ${actionColors[ts.action] || ''}`;

    // Trend
    const trendIcons = { 'uptrend': '⬆️', 'downtrend': '⬇️', 'sideways': '➡️' };
    document.getElementById('ts-trend').textContent =
        `${trendIcons[ts.current_trend] || ''} ${ts.current_trend} (${ts.trend_strength})`;

    // Entry / SL / Target / RR
    document.getElementById('ts-entry').textContent = ts.entry_price ? `₹${ts.entry_price}` : '--';
    document.getElementById('ts-sl').textContent = ts.stop_loss ? `₹${ts.stop_loss}` : '--';
    document.getElementById('ts-target').textContent = ts.target ? `₹${ts.target}` : '--';
    document.getElementById('ts-rr').textContent = ts.risk_reward ? `1:${ts.risk_reward}` : '--';

    // Reversal probability meter
    const revPct = ts.reversal_probability || 0;
    document.getElementById('ts-rev-pct').textContent = `${revPct}%`;
    document.getElementById('ts-rev-pct').className =
        `text-xl font-black ${revPct >= 80 ? 'text-red-600 animate-pulse' : revPct >= 60 ? 'text-yellow-600' : 'text-green-600'}`;

    const revBar = document.getElementById('ts-rev-bar');
    revBar.style.width = `${revPct}%`;
    // Color gradient based on severity
    if (revPct >= 80) {
        revBar.style.background = '#ea1100';
    } else if (revPct >= 60) {
        revBar.style.background = '#ffc220';
    } else if (revPct >= 30) {
        revBar.style.background = 'linear-gradient(90deg, #2a8703, #ffc220)';
    } else {
        revBar.style.background = '#2a8703';
    }

    // Reasoning
    document.getElementById('ts-reasoning').textContent = ts.reasoning || '';

    // Reversal signals breakdown
    const sigContainer = document.getElementById('ts-rev-signals');
    if (ts.reversal_signals && ts.reversal_signals.length) {
        sigContainer.innerHTML = ts.reversal_signals.map(s => {
            const pct = s.score;
            const barW = Math.max(2, pct);
            const color = pct >= 70 ? 'bg-red-500' : pct >= 40 ? 'bg-yellow-500' : 'bg-green-500';
            return `<div class="flex items-center gap-2">
                <span class="w-32 text-xs text-gray-600 truncate" title="${s.name}">${s.name}</span>
                <div class="flex-1 bg-gray-200 rounded-full h-2">
                    <div class="${color} h-2 rounded-full" style="width:${barW}%"></div>
                </div>
                <span class="w-10 text-xs text-right font-bold">${pct}%</span>
                <span class="text-xs text-gray-500 truncate" style="max-width:280px" title="${s.detail}">${s.detail}</span>
            </div>`;
        }).join('');
    }
}

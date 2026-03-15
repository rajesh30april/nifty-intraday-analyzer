/* Paper Trader UI — loads into /static/paper-trader.js */

let _paperPnlChart = null;

// ── Main loader ───────────────────────────────────────────────────────────────
async function loadPaperTrader() {
  const days = document.getElementById('paper-days')?.value || 30;
  try {
    const [trades, summ] = await Promise.all([
      fetch(`/api/paper/trades?days=${days}`).then(r => r.json()),
      fetch(`/api/paper/summary?days=${days}`).then(r => r.json()),
    ]);
    renderSummary(summ);
    renderOpenPositions(trades.filter(t => t.status === 'open'));
    renderHistory(trades.filter(t => t.status === 'closed'));
    renderPnlChart(summ.daily_pnl);
    updateOpenBadge(trades.filter(t => t.status === 'open').length);
  } catch (e) {
    console.error('Paper trader load error', e);
  }
}

// ── Summary cards ─────────────────────────────────────────────────────────────
function renderSummary(s) {
  const el = document.getElementById('paper-summary');
  if (!el) return;
  const rs = (v) => v >= 0
    ? `<span class="text-green-600 font-bold">+₹${v.toLocaleString('en-IN')}</span>`
    : `<span class="text-red-600 font-bold">-₹${Math.abs(v).toLocaleString('en-IN')}</span>`;

  el.innerHTML = [
    card('Total P&L', rs(s.total_pnl_rs), '💰'),
    card('Today P&L', rs(s.today_pnl_rs), '📅'),
    card('Win Rate', `<span class="font-bold text-blue-600">${s.win_rate}%</span>`, '🎯'),
    card('Profit Factor', `<span class="font-bold text-purple-600">${s.profit_factor}</span>`, '⚡'),
    card('Total Trades', `<span class="font-bold">${s.total_trades}</span>`, '📊'),
    card('Open Now', `<span class="font-bold text-green-600">${s.open_trades}</span>`, '🟢'),
    card('Avg Win', s.avg_win_rs ? rs(s.avg_win_rs) : '—', '✅'),
    card('Avg Loss', s.avg_loss_rs ? rs(s.avg_loss_rs) : '—', '❌'),
  ].join('');
}

function card(label, valueHtml, icon) {
  return `
    <div class="bg-white border border-gray-200 rounded-xl p-3 shadow-sm">
      <div class="text-xs text-gray-400 mb-1">${icon} ${label}</div>
      <div class="text-base">${valueHtml}</div>
    </div>`;
}

// ── Open Positions ────────────────────────────────────────────────────────────
function renderOpenPositions(trades) {
  const el = document.getElementById('paper-open-body');
  const cnt = document.getElementById('paper-open-count');
  if (!el) return;
  if (cnt) cnt.textContent = `${trades.length} position${trades.length !== 1 ? 's' : ''}`;

  if (!trades.length) {
    el.innerHTML = '<div class="text-center text-gray-400 text-sm py-8">No open positions</div>';
    return;
  }

  el.innerHTML = trades.map(t => {
    const unPts  = t.unrealized_pts ?? 0;
    const unRs   = t.unrealized_rs  ?? 0;
    const pnlCls = unRs >= 0 ? 'text-green-600' : 'text-red-600';
    const badge  = t.direction === 'long'
      ? '<span class="bg-green-100 text-green-700 px-2 py-0.5 rounded text-xs font-bold">▲ LONG CE</span>'
      : '<span class="bg-red-100 text-red-700 px-2 py-0.5 rounded text-xs font-bold">▼ SHORT PE</span>';

    return `
    <div class="px-4 py-3 flex flex-wrap items-center gap-3 hover:bg-gray-50">
      <div class="flex-1 min-w-[200px]">
        <div class="flex items-center gap-2 mb-1">
          ${badge}
          <span class="text-xs text-gray-400">${t.signal_time} · ${t.strategy}</span>
        </div>
        <div class="text-sm font-mono">
          <span class="font-bold">${t.strike} ${t.option_type}</span>
          <span class="text-gray-400 ml-2">exp ${t.expiry}</span>
        </div>
        <div class="text-xs text-gray-500 mt-0.5">
          Entry: <b>₹${t.entry_premium}</b> &nbsp;|
          Nifty: <b>${t.nifty_entry}</b> &nbsp;|
          SL: ₹${t.sl_premium} &nbsp;|
          Target: ₹${t.target_premium}
        </div>
      </div>
      <div class="text-right">
        <div class="text-sm ${pnlCls} font-bold">
          ${unRs >= 0 ? '+' : ''}₹${Math.abs(unRs).toLocaleString('en-IN')}
        </div>
        <div class="text-xs text-gray-400">
          ${unPts >= 0 ? '+' : ''}${unPts.toFixed(1)} pts
          ${t.current_premium ? `· Curr: ₹${t.current_premium}` : ''}
        </div>
        <div class="flex gap-1 mt-1 justify-end">
          <button onclick="closeAtMarket(${t.id}, 'Target')"
            class="text-xs px-2 py-0.5 bg-green-50 text-green-700 border border-green-200 rounded hover:bg-green-100">
            ✅ Target
          </button>
          <button onclick="closeAtMarket(${t.id}, 'SL')"
            class="text-xs px-2 py-0.5 bg-red-50 text-red-700 border border-red-200 rounded hover:bg-red-100">
            🛑 SL
          </button>
          <button onclick="closeAtMarket(${t.id}, 'Manual')"
            class="text-xs px-2 py-0.5 bg-gray-50 text-gray-600 border border-gray-200 rounded hover:bg-gray-100">
            ✖ Exit
          </button>
        </div>
      </div>
    </div>`;
  }).join('');
}

// ── Trade History ─────────────────────────────────────────────────────────────
function renderHistory(trades) {
  const el = document.getElementById('paper-history-body');
  if (!el) return;

  if (!trades.length) {
    el.innerHTML = '<div class="text-center text-gray-400 text-sm py-8">No closed trades yet</div>';
    return;
  }

  el.innerHTML = `
    <table class="w-full text-xs">
      <thead class="bg-gray-50">
        <tr class="text-gray-500 text-left">
          <th class="px-3 py-2">Date</th>
          <th class="px-3 py-2">Time</th>
          <th class="px-3 py-2">Strategy</th>
          <th class="px-3 py-2">Contract</th>
          <th class="px-3 py-2">Entry ₹</th>
          <th class="px-3 py-2">Exit ₹</th>
          <th class="px-3 py-2">P&L pts</th>
          <th class="px-3 py-2">P&L ₹</th>
          <th class="px-3 py-2">Reason</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-gray-100">
        ${trades.map(t => {
          const pnl   = t.pnl_rupees || 0;
          const pnlCl = pnl >= 0 ? 'text-green-600 font-bold' : 'text-red-600 font-bold';
          const dir   = t.direction === 'long'
            ? '<span class="text-green-600">▲</span>'
            : '<span class="text-red-600">▼</span>';
          return `
          <tr class="hover:bg-gray-50">
            <td class="px-3 py-2">${t.date}</td>
            <td class="px-3 py-2">${t.signal_time}</td>
            <td class="px-3 py-2 text-gray-500">${t.strategy}</td>
            <td class="px-3 py-2 font-mono">${dir} ${t.strike}${t.option_type}</td>
            <td class="px-3 py-2">₹${t.entry_premium}</td>
            <td class="px-3 py-2">₹${t.exit_premium ?? '—'}</td>
            <td class="px-3 py-2 ${pnlCl}">${(t.pnl_points ?? 0) >= 0 ? '+' : ''}${(t.pnl_points ?? 0).toFixed(1)}</td>
            <td class="px-3 py-2 ${pnlCl}">${pnl >= 0 ? '+' : ''}₹${Math.abs(pnl).toLocaleString('en-IN')}</td>
            <td class="px-3 py-2 text-gray-400">${t.exit_reason ?? ''}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
}

// ── Daily P&L Chart ───────────────────────────────────────────────────────────
function renderPnlChart(dailyPnl) {
  const canvas = document.getElementById('paper-pnl-chart');
  if (!canvas || typeof Chart === 'undefined') return;

  const labels = Object.keys(dailyPnl);
  const data   = Object.values(dailyPnl);
  const colors = data.map(v => v >= 0 ? 'rgba(42,135,3,0.7)' : 'rgba(234,17,0,0.7)');

  if (_paperPnlChart) { _paperPnlChart.destroy(); }
  _paperPnlChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [{ label: 'Daily P&L (₹)', data, backgroundColor: colors, borderRadius: 4 }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { ticks: { callback: v => '₹' + v.toLocaleString('en-IN') } },
        x: { ticks: { font: { size: 10 } } },
      },
    },
  });
}

// ── Badge ─────────────────────────────────────────────────────────────────────
function updateOpenBadge(count) {
  const badge = document.getElementById('paper-open-badge');
  if (!badge) return;
  badge.textContent = count;
  badge.classList.toggle('hidden', count === 0);
}

// ── Quick Trade ───────────────────────────────────────────────────────────────
async function openQuickTrade(direction) {
  let data;
  try {
    data = await fetch('/api/paper/quick-entry').then(r => r.json());
  } catch (e) {
    alert('Could not fetch live Nifty price. Is Zerodha connected?');
    return;
  }

  if (!data.connected || !data.nifty) {
    alert('Zerodha not connected — cannot get live Nifty price.');
    return;
  }

  const optType = direction === 'long' ? 'CE' : 'PE';
  const premium = direction === 'long' ? data.ce_premium : data.pe_premium;
  const msg = [
    `📋 Paper Trade Confirmation`,
    ``,
    `Direction : ${direction.toUpperCase()} (Buy ${optType})`,
    `Strike    : ${data.strike} ${optType}`,
    `Expiry    : ${data.expiry}`,
    `Nifty     : ${data.nifty}`,
    `Est. Premium: ₹${premium} (${data.source})`,
    `Lot Size  : ${data.lot_size} units`,
    ``,
    `Max Loss  ≈ ₹${(30 * 0.5 * data.lot_size).toFixed(0)} (30pt SL × delta 0.5)`,
    `Target    ≈ ₹${(60 * 0.5 * data.lot_size).toFixed(0)} (2:1 RR)`,
    ``,
    `Click OK to record this paper trade.`,
  ].join('\n');

  if (!confirm(msg)) return;

  try {
    const resp = await fetch('/api/paper/trades', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        direction,
        nifty_price: data.nifty,
        strategy: 'manual',
        sl_nifty: 30,
        rr: 2.0,
        lot_size: data.lot_size,
        notes: `Manual via UI — ${new Date().toLocaleTimeString('en-IN')}`,
      }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    await loadPaperTrader();
    alert(`✅ Paper trade opened!\n${data.strike} ${optType} @ ₹${premium}`);
  } catch (e) {
    alert('Error opening trade: ' + e.message);
  }
}

// ── Close at market ───────────────────────────────────────────────────────────
async function closeAtMarket(tradeId, reason) {
  if (!confirm(`Close trade #${tradeId} at market (${reason})?`)) return;
  try {
    const resp = await fetch(
      `/api/paper/trades/${tradeId}/close-at-market?exit_reason=${encodeURIComponent(reason)}`,
      { method: 'POST' },
    );
    if (!resp.ok) throw new Error(await resp.text());
    const t = await resp.json();
    const pnl = t.pnl_rupees || 0;
    alert(`Trade closed!\nExit premium: ₹${t.exit_premium}\nP&L: ${pnl >= 0 ? '+' : ''}₹${Math.abs(pnl).toLocaleString('en-IN')}`);
    await loadPaperTrader();
  } catch (e) {
    alert('Error closing trade: ' + e.message);
  }
}
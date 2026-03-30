/* ================================================================
   patterns-guide.js  —  Pattern data + canvas drawing engine
   ================================================================ */

// ── Drawing engine ───────────────────────────────────────────────
function drawPattern(canvasId, candles, opts = {}) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx    = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const pad = { t: 18, b: 22, l: 8, r: 8 };
  const chartH = H - pad.t - pad.b;
  const chartW = W - pad.l - pad.r;

  // Auto-scale
  const allVals = candles.flatMap(c => [c.h, c.l]);
  const minV = Math.min(...allVals), maxV = Math.max(...allVals);
  const range = maxV - minV || 1;

  const toY = v => pad.t + chartH - ((v - minV) / range) * chartH;
  const cw  = Math.max(4, Math.floor(chartW / candles.length) - 2);
  const toX = i => pad.l + i * (chartW / candles.length) + (chartW / candles.length - cw) / 2;

  // Background
  ctx.fillStyle = opts.dark ? '#111827' : '#f9fafb';
  ctx.fillRect(0, 0, W, H);

  // Grid lines
  ctx.strokeStyle = opts.dark ? '#1f2937' : '#e5e7eb';
  ctx.lineWidth = 1;
  [0.25, 0.5, 0.75].forEach(p => {
    const y = pad.t + chartH * p;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
  });

  // Draw annotations first (behind candles)
  if (opts.lines) {
    opts.lines.forEach(line => {
      const y = toY(line.val);
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = line.color;
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
      ctx.setLineDash([]);
      // Label
      ctx.fillStyle = line.color;
      ctx.font = 'bold 9px Inter, sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(line.label, W - pad.r - 2, y - 2);
    });
  }

  // Draw trend lines
  if (opts.trendLines) {
    opts.trendLines.forEach(tl => {
      ctx.setLineDash([3, 2]);
      ctx.strokeStyle = tl.color || '#6b7280';
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(toX(tl.x1) + cw/2, toY(tl.y1));
      ctx.lineTo(toX(tl.x2) + cw/2, toY(tl.y2));
      ctx.stroke();
      ctx.setLineDash([]);
    });
  }

  // Draw candles
  candles.forEach((c, i) => {
    const x  = toX(i);
    const mx = x + cw / 2;
    const yH = toY(c.h), yL = toY(c.l);
    const yO = toY(c.o), yC = toY(c.c);
    const bull = c.c >= c.o;
    const bodyTop = Math.min(yO, yC), bodyH = Math.max(1, Math.abs(yC - yO));
    const color = c.color || (bull ? '#22c55e' : '#ef4444');

    // Wick
    ctx.strokeStyle = color; ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(mx, yH); ctx.lineTo(mx, yL); ctx.stroke();

    // Body
    ctx.fillStyle = bull ? color : color;
    ctx.strokeStyle = color; ctx.lineWidth = 1;
    if (c.doji) {
      ctx.beginPath(); ctx.moveTo(x, yO); ctx.lineTo(x + cw, yO); ctx.stroke();
    } else {
      ctx.fillRect(x, bodyTop, cw, bodyH);
      if (!bull) { ctx.fillStyle = color; ctx.fillRect(x, bodyTop, cw, bodyH); }
    }

    // Candle label
    if (c.label) {
      ctx.fillStyle = '#9ca3af';
      ctx.font = '8px Inter, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(c.label, mx, H - 6);
    }
  });

  // Entry arrow
  if (opts.entry != null) {
    const ex = toX(opts.entry) + cw / 2;
    const ey = toY(opts.entryVal || ((minV + maxV) / 2));
    ctx.fillStyle = '#0053e2';
    ctx.font = 'bold 11px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('▲ ENTER', ex, ey - 4);
  }
}

// ── RSI mini chart ───────────────────────────────────────────────
function drawRSI(canvasId, prices, rsiLine, divergenceType) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const midH = H / 2;

  ctx.fillStyle = '#111827'; ctx.fillRect(0, 0, W, H);

  // Price panel (top half)
  const prices2 = prices;
  const pMin = Math.min(...prices2), pMax = Math.max(...prices2);
  const toY  = (v, panel) => {
    if (panel === 'price') return 4 + (midH - 8) - ((v - pMin) / (pMax - pMin + 1)) * (midH - 8);
    return midH + 4 + (midH - 8) - ((v - 0) / 100) * (midH - 8);
  };
  const toX = i => 8 + i * (W - 16) / (prices2.length - 1);

  // Divider
  ctx.strokeStyle = '#374151'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, midH); ctx.lineTo(W, midH); ctx.stroke();

  // RSI 30/70 lines
  [30, 70].forEach(v => {
    const y = toY(v, 'rsi');
    ctx.setLineDash([3,2]); ctx.strokeStyle = '#374151'; ctx.lineWidth = 0.8;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#6b7280'; ctx.font = '8px sans-serif'; ctx.textAlign = 'left';
    ctx.fillText(v, 2, y - 1);
  });

  // Price line
  ctx.strokeStyle = '#60a5fa'; ctx.lineWidth = 1.5; ctx.setLineDash([]);
  ctx.beginPath();
  prices2.forEach((p, i) => { i === 0 ? ctx.moveTo(toX(i), toY(p,'price')) : ctx.lineTo(toX(i), toY(p,'price')); });
  ctx.stroke();

  // RSI line
  ctx.strokeStyle = '#fbbf24'; ctx.lineWidth = 1.5;
  ctx.beginPath();
  rsiLine.forEach((r, i) => { i === 0 ? ctx.moveTo(toX(i), toY(r,'rsi')) : ctx.lineTo(toX(i), toY(r,'rsi')); });
  ctx.stroke();

  // Divergence arrows
  const lastI  = prices2.length - 1;
  const firstI = prices2.length - 5;
  const bullDiv = divergenceType === 'bullish';
  ctx.fillStyle = bullDiv ? '#22c55e' : '#ef4444';
  ctx.font = 'bold 10px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText(bullDiv ? '↗' : '↘', toX(firstI), toY(prices2[firstI], 'price') - 6);
  ctx.fillText(bullDiv ? '↗' : '↘', toX(lastI),  toY(prices2[lastI],  'price') - 6);

  // Labels
  ctx.fillStyle = '#9ca3af'; ctx.font = '8px sans-serif'; ctx.textAlign = 'left';
  ctx.fillText('Price', 4, 10);
  ctx.fillText('RSI', 4, midH + 10);
}

// ── Pattern Definitions ──────────────────────────────────────────
const PATTERNS = {

  // ── CANDLESTICK ──────────────────────────────────────────────
  hammer: {
    name:'Hammer', cat:'candlestick', bias:'bullish', conf:'75–85%',
    desc:'After a downtrend, a candle with a small body at the top and a long lower wick (2× body) forms. Sellers pushed price down hard but bulls reversed it — a key reversal signal at support.',
    entry:'Buy when next candle closes bullish above Hammer high.',
    sl:'Below the Hammer low (long lower wick).',
    target:'1:2 R:R minimum. Next resistance zone.',
    candles:[
      {o:70,h:71,l:65,c:67},{o:67,h:68,l:62,c:64},{o:64,h:65,l:59,c:61},
      {o:61,h:62,l:56,c:58},{o:57,h:58,l:44,c:56,label:'🔨'}
    ],
    lines:[{val:44,color:'#ef4444',label:'SL'},{val:58,color:'#0053e2',label:'Entry'}]
  },

  shooting_star: {
    name:'Shooting Star', cat:'candlestick', bias:'bearish', conf:'72–82%',
    desc:'After an uptrend, a candle with a small body at the bottom and a long upper wick (2× body) forms. Bulls pushed price up but bears took over — reversal signal at resistance.',
    entry:'Sell when next candle closes bearish below Shooting Star low.',
    sl:'Above the Shooting Star high.',
    target:'1:2 R:R. Next support zone.',
    candles:[
      {o:44,h:48,l:43,c:46},{o:46,h:50,l:45,c:48},{o:48,h:53,l:47,c:51},
      {o:51,h:55,l:50,c:53},{o:52,h:68,l:51,c:53,label:'⭐'}
    ],
    lines:[{val:68,color:'#ef4444',label:'SL'},{val:51,color:'#0053e2',label:'Entry'}]
  },

  bullish_engulfing: {
    name:'Bullish Engulfing', cat:'candlestick', bias:'bullish', conf:'78–88%',
    desc:'A large green candle completely engulfs the prior red candle body. After a downtrend, this signals strong buyer conviction — the bulls overpowered the bears decisively.',
    entry:'Buy at close of the engulfing green candle.',
    sl:'Below the low of the engulfing candle.',
    target:'Previous swing high.',
    candles:[
      {o:70,h:71,l:66,c:67},{o:67,h:68,l:63,c:64},{o:64,h:65,l:60,c:61},
      {o:62,h:63,l:58,c:59,label:'①'},{o:57,h:66,l:56,c:65,label:'②'}
    ],
    lines:[{val:56,color:'#ef4444',label:'SL'},{val:65,color:'#0053e2',label:'Entry'}]
  },

  bearish_engulfing: {
    name:'Bearish Engulfing', cat:'candlestick', bias:'bearish', conf:'76–86%',
    desc:'A large red candle completely engulfs the prior green candle body. After an uptrend, strong selling pressure overwhelms buyers — high-conviction reversal signal.',
    entry:'Sell at close of the engulfing red candle.',
    sl:'Above the high of the engulfing candle.',
    target:'Previous swing low.',
    candles:[
      {o:44,h:48,l:43,c:46},{o:46,h:50,l:45,c:49},{o:49,h:53,l:48,c:52},
      {o:51,h:55,l:50,c:54,label:'①'},{o:56,h:57,l:48,c:49,label:'②'}
    ],
    lines:[{val:57,color:'#ef4444',label:'SL'},{val:49,color:'#0053e2',label:'Entry'}]
  },

  morning_star: {
    name:'Morning Star', cat:'candlestick', bias:'bullish', conf:'80–88%',
    desc:'3-candle reversal: large red → small indecision candle (doji/star) → large green. The middle candle shows seller exhaustion. One of the most reliable bullish reversals.',
    entry:'Buy on close of 3rd (green) candle.',
    sl:'Below the low of the middle star candle.',
    target:'Top of candle 1 or next resistance.',
    candles:[
      {o:70,h:72,l:65,c:66},{o:66,h:67,l:62,c:63},
      {o:64,h:71,l:60,c:61,label:'①'},{o:60,h:61,l:58,c:60,doji:true,label:'②'},
      {o:59,h:68,l:58,c:67,label:'③'}
    ],
    lines:[{val:58,color:'#ef4444',label:'SL'},{val:67,color:'#0053e2',label:'Entry'}]
  },

  evening_star: {
    name:'Evening Star', cat:'candlestick', bias:'bearish', conf:'80–87%',
    desc:'3-candle reversal: large green → small indecision candle → large red. The middle star shows buyer exhaustion at the top. Mirror opposite of Morning Star.',
    entry:'Sell on close of 3rd (red) candle.',
    sl:'Above the high of the middle star candle.',
    target:'Bottom of candle 1 or next support.',
    candles:[
      {o:44,h:46,l:42,c:45},{o:45,h:48,l:44,c:47},
      {o:46,h:53,l:45,c:52,label:'①'},{o:53,h:55,l:52,c:53,doji:true,label:'②'},
      {o:54,h:55,l:46,c:47,label:'③'}
    ],
    lines:[{val:55,color:'#ef4444',label:'SL'},{val:47,color:'#0053e2',label:'Entry'}]
  },

  // ── REVERSAL ─────────────────────────────────────────────────
  double_top: {
    name:'Double Top (M)', cat:'reversal', bias:'bearish', conf:'74–82%',
    desc:'Price hits resistance twice at the same level, forming an "M" shape. The second peak failing to break the first is key. Breakdown below neckline confirms the pattern.',
    entry:'Sell on neckline breakdown (close below it).',
    sl:'Above the second peak.',
    target:'Neckline − pattern height (measured move).',
    candles:[
      {o:45,h:50,l:44,c:49},{o:49,h:55,l:48,c:52},{o:52,h:60,l:51,c:57,label:'①'},
      {o:57,h:58,l:50,c:51},{o:51,h:56,l:50,c:54},{o:54,h:61,l:53,c:57,label:'②'},
      {o:57,h:58,l:49,c:50},{o:50,h:51,l:44,c:45}
    ],
    lines:[{val:61,color:'#ef4444',label:'SL'},{val:50,color:'#ffc220',label:'Neckline'},{val:39,color:'#0053e2',label:'Target'}],
    trendLines:[{x1:2,y1:60,x2:5,y2:61,color:'#ef4444'},{x1:1,y1:50,x2:6,y2:50,color:'#ffc220'}]
  },

  double_bottom: {
    name:'Double Bottom (W)', cat:'reversal', bias:'bullish', conf:'75–83%',
    desc:'Price tests support twice at the same level, forming a "W" shape. The second low failing to break lower signals exhaustion. Breakout above neckline confirms.',
    entry:'Buy on neckline breakout (close above it).',
    sl:'Below the second trough.',
    target:'Neckline + pattern height (measured move).',
    candles:[
      {o:60,h:61,l:55,c:57},{o:57,h:58,l:50,c:52},{o:52,h:53,l:44,c:47,label:'①'},
      {o:47,h:54,l:46,c:53},{o:53,h:58,l:52,c:55},{o:55,h:56,l:44,c:46,label:'②'},
      {o:46,h:55,l:45,c:53},{o:53,h:62,l:52,c:60}
    ],
    lines:[{val:43,color:'#ef4444',label:'SL'},{val:56,color:'#ffc220',label:'Neckline'},{val:68,color:'#0053e2',label:'Target'}],
    trendLines:[{x1:1,y1:56,x2:6,y2:56,color:'#ffc220'}]
  },

  head_shoulders: {
    name:'Head & Shoulders', cat:'reversal', bias:'bearish', conf:'80–88%',
    desc:'Left shoulder → higher head → lower right shoulder, connected by a neckline. One of the most reliable reversal patterns. Breakdown confirms a major trend change.',
    entry:'Sell on neckline breakdown.',
    sl:'Above the right shoulder high.',
    target:'Neckline − head-to-neckline distance.',
    candles:[
      {o:47,h:53,l:46,c:51,label:'LS'},{o:51,h:52,l:47,c:49},
      {o:49,h:62,l:48,c:58,label:'H'},{o:58,h:59,l:50,c:52},
      {o:52,h:57,l:51,c:55,label:'RS'},{o:55,h:56,l:49,c:50},{o:50,h:51,l:44,c:45}
    ],
    lines:[{val:50,color:'#ffc220',label:'Neck'},{val:57,color:'#ef4444',label:'SL'},{val:38,color:'#0053e2',label:'Target'}],
    trendLines:[{x1:0,y1:50,x2:6,y2:50,color:'#ffc220'}]
  },

  inv_head_shoulders: {
    name:'Inv. Head & Shoulders', cat:'reversal', bias:'bullish', conf:'81–89%',
    desc:'Left shoulder → lower head → higher right shoulder, connected by neckline. Mirror of H&S. Breakout above neckline signals a major bullish reversal — very reliable.',
    entry:'Buy on neckline breakout.',
    sl:'Below the right shoulder low.',
    target:'Neckline + head-to-neckline distance.',
    candles:[
      {o:55,h:56,l:50,c:52,label:'LS'},{o:52,h:53,l:48,c:50},
      {o:50,h:51,l:40,c:44,label:'H'},{o:44,h:52,l:43,c:51},
      {o:51,h:52,l:46,c:48,label:'RS'},{o:48,h:55,l:47,c:53},{o:53,h:62,l:52,c:60}
    ],
    lines:[{val:53,color:'#ffc220',label:'Neck'},{val:46,color:'#ef4444',label:'SL'},{val:66,color:'#0053e2',label:'Target'}],
    trendLines:[{x1:0,y1:53,x2:6,y2:53,color:'#ffc220'}]
  },

  triple_top: {
    name:'Triple Top', cat:'reversal', bias:'bearish', conf:'72–80%',
    desc:'Three failed attempts at the same resistance level. Each failure shows weakening bull momentum. Stronger confirmation than Double Top — the third attempt seals it.',
    entry:'Sell on breakdown below the neckline.',
    sl:'Above the third peak.',
    target:'Neckline − 2× pattern height.',
    candles:[
      {o:44,h:56,l:43,c:52,label:'①'},{o:52,h:53,l:47,c:49},
      {o:49,h:57,l:48,c:52,label:'②'},{o:52,h:53,l:48,c:50},
      {o:50,h:57,l:49,c:52,label:'③'},{o:52,h:53,l:45,c:46},{o:46,h:47,l:40,c:41}
    ],
    lines:[{val:57,color:'#ef4444',label:'SL'},{val:48,color:'#ffc220',label:'Neck'},{val:39,color:'#0053e2',label:'Target'}]
  },

  triple_bottom: {
    name:'Triple Bottom', cat:'reversal', bias:'bullish', conf:'73–81%',
    desc:'Three successful tests of the same support level. Bulls keep defending the floor. Stronger than Double Bottom — three rejections signal major accumulation.',
    entry:'Buy on breakout above the neckline.',
    sl:'Below the third trough.',
    target:'Neckline + 2× pattern height.',
    candles:[
      {o:58,h:59,l:47,c:50,label:'①'},{o:50,h:55,l:49,c:53},
      {o:53,h:54,l:47,c:50,label:'②'},{o:50,h:54,l:49,c:52},
      {o:52,h:53,l:47,c:50,label:'③'},{o:50,h:57,l:49,c:55},{o:55,h:63,l:54,c:61}
    ],
    lines:[{val:47,color:'#ef4444',label:'SL'},{val:54,color:'#ffc220',label:'Neck'},{val:63,color:'#0053e2',label:'Target'}]
  },

  // ── CONTINUATION ─────────────────────────────────────────────
  bull_flag: {
    name:'Bull Flag', cat:'continuation', bias:'bullish', conf:'70–85%',
    desc:'Strong vertical impulse up (the pole) followed by a tight sideways/downward consolidation (the flag). A pause-and-continue pattern — the trend resumes with similar momentum.',
    entry:'Buy on breakout above flag upper trendline.',
    sl:'Below the flag low.',
    target:'Flag breakout + length of the pole.',
    candles:[
      {o:40,h:44,l:39,c:43},{o:43,h:49,l:42,c:48},{o:48,h:56,l:47,c:55},
      {o:55,h:57,l:52,c:53,label:'FLAG'},{o:53,h:55,l:50,c:51},{o:51,h:53,l:49,c:52},
      {o:52,h:61,l:51,c:60}
    ],
    lines:[{val:49,color:'#ef4444',label:'SL'},{val:55,color:'#ffc220',label:'Break'},{val:71,color:'#0053e2',label:'Target'}],
    trendLines:[{x1:3,y1:57,x2:5,y2:53,color:'#6b7280'},{x1:3,y1:52,x2:5,y2:49,color:'#6b7280'}]
  },

  bear_flag: {
    name:'Bear Flag', cat:'continuation', bias:'bearish', conf:'70–84%',
    desc:'Sharp vertical drop (the pole) followed by a tight sideways/upward consolidation. The trend resumes down once price breaks the flag support. Volume should contract during flag.',
    entry:'Sell on breakdown below flag lower trendline.',
    sl:'Above the flag high.',
    target:'Flag breakdown − length of the pole.',
    candles:[
      {o:70,h:72,l:65,c:66},{o:66,h:67,l:60,c:61},{o:61,h:62,l:55,c:56},
      {o:55,h:59,l:54,c:58,label:'FLAG'},{o:58,h:61,l:57,c:59},{o:59,h:61,l:57,c:58},
      {o:58,h:59,l:51,c:52}
    ],
    lines:[{val:61,color:'#ef4444',label:'SL'},{val:55,color:'#ffc220',label:'Break'},{val:41,color:'#0053e2',label:'Target'}],
    trendLines:[{x1:3,y1:59,x2:5,y2:61,color:'#6b7280'},{x1:3,y1:54,x2:5,y2:57,color:'#6b7280'}]
  },

  asc_triangle: {
    name:'Ascending Triangle', cat:'continuation', bias:'bullish', conf:'73–82%',
    desc:'Flat resistance + rising lows. Bulls are aggressively buying at higher and higher prices while sellers sit at a fixed level. When supply is exhausted, price breaks out upward.',
    entry:'Buy on close above flat resistance.',
    sl:'Below the last higher low.',
    target:'Resistance + triangle height.',
    candles:[
      {o:44,h:52,l:43,c:49},{o:49,h:50,l:46,c:48},{o:48,h:53,l:47,c:51},
      {o:51,h:52,l:48,c:50},{o:50,h:53,l:49,c:52},{o:52,h:53,l:50,c:52},
      {o:52,h:60,l:51,c:58}
    ],
    lines:[{val:53,color:'#ffc220',label:'Resist'},{val:49,color:'#ef4444',label:'SL'},{val:63,color:'#0053e2',label:'Target'}],
    trendLines:[{x1:0,y1:52,x2:5,y2:52,color:'#ffc220'},{x1:0,y1:43,x2:5,y2:50,color:'#22c55e'}]
  },

  desc_triangle: {
    name:'Descending Triangle', cat:'continuation', bias:'bearish', conf:'73–82%',
    desc:'Flat support + lower highs. Bears are selling at progressively lower levels while buyers defend a fixed floor. When demand is exhausted, price breaks down through support.',
    entry:'Sell on close below flat support.',
    sl:'Above the last lower high.',
    target:'Support − triangle height.',
    candles:[
      {o:60,h:61,l:52,c:55},{o:55,h:56,l:52,c:54},{o:54,h:59,l:52,c:56},
      {o:56,h:57,l:52,c:53},{o:53,h:56,l:52,c:54},{o:54,h:55,l:52,c:53},
      {o:53,h:54,l:44,c:45}
    ],
    lines:[{val:52,color:'#ffc220',label:'Support'},{val:56,color:'#ef4444',label:'SL'},{val:42,color:'#0053e2',label:'Target'}],
    trendLines:[{x1:0,y1:52,x2:5,y2:52,color:'#ffc220'},{x1:0,y1:61,x2:5,y2:55,color:'#ef4444'}]
  },

  sym_triangle: {
    name:'Symmetrical Triangle', cat:'continuation', bias:'neutral', conf:'68–78%',
    desc:'Lower highs + higher lows converge to a point. Coiling energy — the breakout direction determines the trade. Often continues the prior trend. Watch volume for clues.',
    entry:'Trade the breakout direction (above or below triangle).',
    sl:'On the opposite side of the triangle.',
    target:'Breakout point + triangle height.',
    candles:[
      {o:45,h:58,l:44,c:55},{o:55,h:56,l:46,c:48},{o:48,h:55,l:47,c:53},
      {o:53,h:54,l:48,c:50},{o:50,h:53,l:49,c:52},{o:52,h:53,l:50,c:51},
      {o:51,h:59,l:50,c:57}
    ],
    lines:[{val:57,color:'#0053e2',label:'Break↑'},{val:50,color:'#ef4444',label:'Break↓'}],
    trendLines:[{x1:0,y1:58,x2:5,y2:53,color:'#ef4444'},{x1:0,y1:44,x2:5,y2:50,color:'#22c55e'}]
  },

  rising_wedge: {
    name:'Rising Wedge', cat:'continuation', bias:'bearish', conf:'70–80%',
    desc:'Both highs and lows are rising but CONVERGING — the advance is weakening. Despite higher prices, momentum is fading. Breakdown often sharp and fast. Counter-intuitive pattern.',
    entry:'Sell on breakdown below lower wedge trendline.',
    sl:'Above the most recent high in the wedge.',
    target:'Start of the wedge (measured move down).',
    candles:[
      {o:44,h:50,l:43,c:47},{o:47,h:54,l:46,c:51},{o:51,h:57,l:50,c:54},
      {o:54,h:59,l:53,c:56},{o:56,h:61,l:55,c:58},{o:58,h:62,l:57,c:59},
      {o:59,h:60,l:51,c:52}
    ],
    lines:[{val:62,color:'#ef4444',label:'SL'},{val:51,color:'#ffc220',label:'Break'},{val:43,color:'#0053e2',label:'Target'}],
    trendLines:[{x1:0,y1:50,x2:5,y2:62,color:'#ef4444'},{x1:0,y1:43,x2:5,y2:57,color:'#ef4444'}]
  },

  falling_wedge: {
    name:'Falling Wedge', cat:'continuation', bias:'bullish', conf:'72–81%',
    desc:'Both highs and lows fall but CONVERGE — the decline is losing steam. Despite lower prices, selling momentum fades. Breakout is often explosive to the upside.',
    entry:'Buy on breakout above upper wedge trendline.',
    sl:'Below the most recent low in the wedge.',
    target:'Start of the wedge (measured move up).',
    candles:[
      {o:65,h:66,l:58,c:61},{o:61,h:62,l:56,c:58},{o:58,h:59,l:53,c:55},
      {o:55,h:57,l:51,c:53},{o:53,h:55,l:50,c:52},{o:52,h:53,l:49,c:51},
      {o:51,h:62,l:50,c:60}
    ],
    lines:[{val:49,color:'#ef4444',label:'SL'},{val:55,color:'#ffc220',label:'Break'},{val:66,color:'#0053e2',label:'Target'}],
    trendLines:[{x1:0,y1:66,x2:5,y2:53,color:'#22c55e'},{x1:0,y1:58,x2:5,y2:49,color:'#22c55e'}]
  },

  // ── DIVERGENCE ───────────────────────────────────────────────
  rsi_div_bull: {
    name:'RSI Divergence (Bullish)', cat:'divergence', bias:'bullish', conf:'72–82%',
    desc:'Price makes a LOWER LOW but RSI makes a HIGHER LOW. This hidden strength means momentum is improving even as price falls — bulls building positions. Strong reversal signal.',
    entry:'Buy when RSI crosses above 30 or price shows bullish candle.',
    sl:'Below the second price low.',
    target:'Previous swing high.',
    rsiData: {
      prices:[60,56,52,54,50,52,56,60],
      rsi:   [45,40,35,42,38,45,52,58],
      type:'bullish'
    }
  },

  rsi_div_bear: {
    name:'RSI Divergence (Bearish)', cat:'divergence', bias:'bearish', conf:'71–81%',
    desc:'Price makes a HIGHER HIGH but RSI makes a LOWER HIGH. Hidden weakness — momentum fading even as price rises. Distribution happening. Strong reversal/exit signal.',
    entry:'Sell when RSI crosses below 70 or price shows bearish candle.',
    sl:'Above the second price high.',
    target:'Previous swing low.',
    rsiData: {
      prices:[44,48,52,50,54,52,50,46],
      rsi:   [55,60,68,62,64,60,55,50],
      type:'bearish'
    }
  }
};

// ── Card builder ────────────────────────────────────────────────
const CAT_LABELS = {
  candlestick: '🕯 Candlestick',
  reversal:    '↩ Reversal',
  continuation:'➡ Continuation',
  divergence:  '〰 Divergence',
};
const CAT_ORDER = ['candlestick','reversal','continuation','divergence'];

const LEGEND_BY_CAT = {
  candlestick: `<span><span class="legend-dot bg-red-500 mr-1"></span>SL</span>
               <span><span class="legend-dot bg-blue-500 mr-1"></span>Entry/Target</span>`,
  reversal:    `<span><span class="legend-dot bg-red-500 mr-1"></span>SL</span>
               <span><span class="legend-dot bg-yellow-400 mr-1"></span>Neckline</span>
               <span><span class="legend-dot bg-blue-500 mr-1"></span>Target</span>`,
  continuation:`<span><span class="legend-dot bg-red-500 mr-1"></span>SL</span>
               <span><span class="legend-dot bg-yellow-400 mr-1"></span>Breakout</span>
               <span><span class="legend-dot bg-blue-500 mr-1"></span>Target</span>`,
  divergence:  `<span><span class="legend-dot bg-blue-400 mr-1"></span>Price</span>
               <span><span class="legend-dot bg-yellow-400 mr-1"></span>RSI</span>`,
};

function buildBias(bias) {
  const cls = bias === 'bullish' ? 'bias-bull' : bias === 'bearish' ? 'bias-bear' : 'bias-neut';
  const ico = bias === 'bullish' ? '▲ Bullish' : bias === 'bearish' ? '▼ Bearish' : '◆ Neutral';
  return `<span class="text-xs px-2 py-0.5 rounded-full ${cls}">${ico}</span>`;
}

function buildCard(key, p) {
  return `
  <div class="pattern-card bg-gray-800 border border-gray-700 rounded-xl overflow-hidden" data-cat="${p.cat}" id="card-${key}">
    <div class="px-4 pt-4 pb-2 flex items-center justify-between">
      <div class="flex items-center gap-2 flex-wrap">
        <span class="font-bold text-white text-sm">${p.name}</span>
        ${buildBias(p.bias)}
      </div>
      <span class="text-xs text-gray-500 whitespace-nowrap">${p.conf}</span>
    </div>
    <div class="px-3 pb-1">
      <canvas id="canvas-${key}" width="300" height="140"></canvas>
    </div>
    <div class="px-4 pb-2 flex flex-wrap gap-3 text-xs text-gray-400">
      ${LEGEND_BY_CAT[p.cat] || ''}
    </div>
    <div class="px-4 pb-4">
      <p class="text-xs text-gray-400 leading-relaxed mb-2">${p.desc}</p>
      <div class="space-y-1 text-xs">
        <div class="flex gap-1"><span class="text-blue-400 font-semibold shrink-0 w-12">Entry:</span><span class="text-gray-300">${p.entry}</span></div>
        <div class="flex gap-1"><span class="text-red-400 font-semibold shrink-0 w-12">SL:</span><span class="text-gray-300">${p.sl}</span></div>
        <div class="flex gap-1"><span class="text-green-400 font-semibold shrink-0 w-12">Target:</span><span class="text-gray-300">${p.target}</span></div>
      </div>
    </div>
  </div>`;
}

// ── Render all patterns on load ──────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const grid = document.getElementById('pattern-grid');
  if (!grid) return;

  // Render cards grouped by category
  CAT_ORDER.forEach(cat => {
    const keys = Object.keys(PATTERNS).filter(k => PATTERNS[k].cat === cat);
    if (!keys.length) return;

    // Section header
    const hdr = document.createElement('div');
    hdr.className = 'col-span-full mt-4 mb-1';
    hdr.innerHTML = `<h2 class="text-xs font-bold uppercase tracking-widest text-gray-500">${CAT_LABELS[cat]} — ${{candlestick:'single/few-candle signals',reversal:'multi-candle trend change',continuation:'trend resumes after pause',divergence:'price vs momentum mismatch'}[cat]}</h2>`;
    hdr.style.gridColumn = '1/-1';
    grid.appendChild(hdr);

    keys.forEach(key => {
      const wrapper = document.createElement('div');
      wrapper.innerHTML = buildCard(key, PATTERNS[key]);
      grid.appendChild(wrapper.firstElementChild);
    });
  });

  // Draw charts
  Object.entries(PATTERNS).forEach(([key, p]) => {
    if (p.rsiData) {
      drawRSI(`canvas-${key}`, p.rsiData.prices, p.rsiData.rsi, p.rsiData.type);
    } else {
      drawPattern(`canvas-${key}`, p.candles, { dark:true, lines:p.lines, trendLines:p.trendLines });
    }
  });

  // Tab filtering
  document.querySelectorAll('.cat-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      const cat = btn.dataset.cat;
      document.querySelectorAll('.cat-tab').forEach(b => b.classList.remove('active-tab'));
      btn.classList.add('active-tab');
      grid.querySelectorAll('.pattern-card').forEach(card => {
        card.style.display = (cat === 'all' || card.dataset.cat === cat) ? '' : 'none';
      });
      // Section headers
      grid.querySelectorAll('h2').forEach(h => h.closest('div')?.remove());
      // Rebuild section headers on filter? — just hide/show cards is enough
    });
  });
});
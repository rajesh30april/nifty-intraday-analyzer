"""Load strategies into the registry — best-in-class selection only.

Strategy selection rationale (YAGNI — keep one per job):

  TIME-GATED (specific windows only):
    opening_candle_fade   → 9:20 specific fade/continuation play (dual mode)
    gap_and_go            → gap day opener (9:15–9:45)
    orb                   → 9:30–10:30 range breakout

  TREND-FOLLOWING (trending regime):
    supertrend_strat      → best all-round trend follower
                            (replaces ema_crossover + ema_scalp + macd_momentum)

  VOLUME-BASED (requires Zerodha real volume):
    volume_spike          → institutional breakout detection
    obv_divergence        → smart money reversal signal
    volume_profile        → HVN support/resistance levels

  BREAKOUT / COMPRESSION:
    bb_squeeze            → Bollinger Band inside Keltner = compression → explosion.
                            Fires ALL DAY (not just opening), complementary to ORB
                            which only covers the first hour.

  PIVOT / MEAN-REVERSION (sideways regime):
    camarilla_pivots      → mathematical pivot levels
    pdhl_breakout         → Previous Day High/Low breakouts with volume + VWAP gate.
                            Complementary to volume_profile (price levels vs volume nodes).

  CHART & CANDLESTICK:
    chart_patterns        → 78% win rate, PF 4.18 (flag, triangle, double top/bottom)
    candlestick_patterns  → 80.8% win rate, PF 5.34 (engulfing, hammer, star, harami)

Removed (overlapping, redundant, or statistically unfit):
  - trend_follow      → covered by supertrend (better signal)
  - ema_crossover     → covered by supertrend
  - ema_scalp         → covered by supertrend
  - vwap_reversion    → covered by volume_profile (volume-backed)
  - vwap_bounce_scalp → covered by volume_profile
  - rsi_reversal      → covered by obv_divergence (volume-backed)
  - price_rejection   → covered by obv_divergence
  - macd_momentum     → covered by supertrend
  - first_candle_range→ covered by orb (same concept, ORB is better calibrated)
  - vwap_breakout     → REMOVED! 47.2% WR, PF=1.28 — dragging system down.
"""

# ── Time-gated strategies ─────────────────────────────────────────────────
import strategies.opening_candle_fade  # noqa: F401 — 9:20 fade
import strategies.gap_and_go           # noqa: F401 — gap day 9:15–9:45
import strategies.orb                  # noqa: F401 — 9:30–10:30 range breakout

# ── Trend following ───────────────────────────────────────────────────────
import strategies.supertrend_strat     # noqa: F401 — trend + momentum

# ── Breakout / Compression ───────────────────────────────────────────
import strategies.bb_squeeze           # noqa: F401 — compression → explosion (all-day)

# ── Volume-based (require Zerodha real volume) ────────────────────────────
import strategies.volume_spike         # noqa: F401 — institutional breakout
import strategies.obv_divergence       # noqa: F401 — smart money reversal
import strategies.volume_profile       # noqa: F401 — HVN S/R levels

# ── Pivot / Mean-reversion ────────────────────────────────────────────
import strategies.camarilla_pivots     # noqa: F401 — pivot level plays
import strategies.pdhl_breakout        # noqa: F401 — prev day high/low breakout

# ── Chart & Candlestick patterns ─────────────────────────────────────────
import strategies.chart_patterns       # noqa: F401 — flag, triangle, double top/bottom
import strategies.candlestick_patterns # noqa: F401 — engulfing, hammer, star, harami

# NOTE: vwap_breakout REMOVED — 47.2% win rate, PF=1.28 (no real edge after brokerage)
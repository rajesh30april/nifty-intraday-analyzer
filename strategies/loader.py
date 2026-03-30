"""Load strategies into the registry — 8 non-overlapping strategies.

Strategy selection rationale (YAGNI — keep one per job):

  TIME-GATED (specific windows only):
    opening_candle_fade   → 9:20 specific fade play
    gap_and_go            → gap day opener (9:15–9:45)
    orb                   → 9:30–10:30 range breakout

  TREND-FOLLOWING (trending regime):
    supertrend_strat      → best all-round trend follower
                            (replaces ema_crossover + ema_scalp + macd_momentum)

  VOLUME-BASED (requires Zerodha real volume):
    volume_spike          → institutional breakout detection
    obv_divergence        → smart money reversal signal
    volume_profile        → HVN support/resistance levels

  PIVOT / MEAN-REVERSION (sideways regime):
    camarilla_pivots      → mathematical pivot levels

Removed (overlapping or redundant):
  - trend_follow      → covered by supertrend (better signal)
  - ema_crossover     → covered by supertrend
  - ema_scalp         → covered by supertrend
  - vwap_reversion    → covered by volume_profile (volume-backed)
  - vwap_bounce_scalp → covered by volume_profile
  - rsi_reversal      → covered by obv_divergence (volume-backed)
  - price_rejection   → covered by obv_divergence
  - macd_momentum     → covered by supertrend
  - bb_squeeze        → covered by orb + volume_spike
  - pdhl_breakout     → covered by volume_profile HVN
  - first_candle_range→ covered by orb

Active VWAP strategy (NEW):
  - vwap_breakout     → VWAP cross with institutional volume (genuinely new edge)
"""

# ── Time-gated strategies ─────────────────────────────────────────────────
import strategies.opening_candle_fade  # noqa: F401 — 9:20 fade
import strategies.gap_and_go           # noqa: F401 — gap day 9:15–9:45
import strategies.orb                  # noqa: F401 — 9:30–10:30 range breakout

# ── Trend following ───────────────────────────────────────────────────────
import strategies.supertrend_strat     # noqa: F401 — trend + momentum

# ── Volume-based (require Zerodha real volume) ────────────────────────────
import strategies.volume_spike         # noqa: F401 — institutional breakout
import strategies.obv_divergence       # noqa: F401 — smart money reversal
import strategies.volume_profile       # noqa: F401 — HVN S/R levels

# ── Pivot / Mean-reversion ────────────────────────────────────────────────
import strategies.camarilla_pivots     # noqa: F401 — pivot level plays

# ── Chart & Candlestick patterns ─────────────────────────────────────────
import strategies.chart_patterns       # noqa: F401 — flag, triangle, double top/bottom
import strategies.candlestick_patterns # noqa: F401 — engulfing, hammer, star, harami

# ── VWAP ─────────────────────────────────────────────────────────────────
import strategies.vwap_breakout        # noqa: F401 — VWAP cross with institutional volume
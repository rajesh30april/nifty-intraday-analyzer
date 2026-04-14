"""India VIX Monitor — IV Rank & Options Premium Quality Gate.

India VIX is the market's "fear gauge" — it tells you how expensive
options premiums are RIGHT NOW relative to recent history.

Key insight:
    High VIX (>18) → options are EXPENSIVE → prefer SELLING premium
    Low VIX  (<12) → options are CHEAP     → prefer BUYING premium
    Normal   (12–18) → balanced

IV Rank (IVR) = (Current VIX - 52w Low) / (52w High - 52w Low) × 100

    IVR > 80 → options in top 20% of historical expensiveness → SELL
    IVR 40–80 → neutral
    IVR < 20 → options cheap → BUY aggressively

This module:
  1. Fetches India VIX from NSE (yfinance ^INDIAVIX fallback)
  2. Computes IVR and IV Percentile from 52-week rolling data
  3. Produces a VixContext with trading guidance

Author: Code Puppy 🐶
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── Thresholds ─────────────────────────────────────────────────────────────────
_VIX_LOW_THRESHOLD  = 12.0   # Cheap premiums → buy options
_VIX_HIGH_THRESHOLD = 18.0   # Expensive premiums → sell/avoid buying
_VIX_PANIC          = 25.0   # Extreme fear — widened spreads, be very careful

_IVR_SELL_ZONE = 80   # IVR > 80 → selling premium zone
_IVR_BUY_ZONE  = 20   # IVR < 20 → buying premium zone


@dataclass(frozen=True)
class VixContext:
    """India VIX analysis result.

    Attributes:
        vix_current:    Current India VIX value.
        vix_52w_high:   52-week highest VIX.
        vix_52w_low:    52-week lowest VIX.
        iv_rank:        IVR 0-100 (100 = most expensive ever this year).
        iv_percentile:  % of days this year where VIX was below current.
        regime:         'cheap' | 'normal' | 'expensive' | 'panic'.
        buy_quality:    'great' | 'ok' | 'poor' (for buying options).
        sell_quality:   'great' | 'ok' | 'poor' (for selling options).
        size_scale:     Multiplier for option buying size (1.0 = normal).
        guidance:       Human-readable trading guidance.
        data_stale:     True if we couldn't fetch fresh data.
    """
    vix_current: float
    vix_52w_high: float
    vix_52w_low: float
    iv_rank: float
    iv_percentile: float
    regime: str               # 'cheap' | 'normal' | 'expensive' | 'panic'
    buy_quality: str          # 'great' | 'ok' | 'poor'
    sell_quality: str         # 'great' | 'ok' | 'poor'
    size_scale: float         # adjust option buy size
    guidance: str
    data_stale: bool = False


def _classify_regime(vix: float) -> str:
    if vix >= _VIX_PANIC:
        return "panic"
    elif vix >= _VIX_HIGH_THRESHOLD:
        return "expensive"
    elif vix <= _VIX_LOW_THRESHOLD:
        return "cheap"
    else:
        return "normal"


def _buy_quality(iv_rank: float, regime: str) -> str:
    """How good is it to BUY options right now?"""
    if regime == "cheap" or iv_rank < _IVR_BUY_ZONE:
        return "great"
    elif regime == "normal":
        return "ok"
    else:
        return "poor"   # expensive or panic — avoid buying premium


def _sell_quality(iv_rank: float, regime: str) -> str:
    """How good is it to SELL options right now?"""
    if regime in ("expensive", "panic") or iv_rank > _IVR_SELL_ZONE:
        return "great"
    elif regime == "normal":
        return "ok"
    else:
        return "poor"   # cheap premiums — not worth selling


def _size_scale_from_ivr(iv_rank: float, regime: str) -> float:
    """Return size multiplier for BUYING options based on IV environment.

    Logic: scale down aggressively when premiums are expensive.
        IVR < 20  → buy full size  (cheap premiums!)
        IVR 20–50 → full size      (fair value)
        IVR 50–70 → 85% size       (getting pricey)
        IVR 70–85 → 65% size       (expensive)
        IVR > 85  → 40% size       (very expensive / panic)
    """
    if iv_rank < 20:
        return 1.0
    elif iv_rank < 50:
        return 1.0
    elif iv_rank < 70:
        return 0.85
    elif iv_rank < 85:
        return 0.65
    else:
        return 0.40


def _build_guidance(vix: float, iv_rank: float, regime: str) -> str:
    if regime == "cheap":
        return (
            f"🟢 VIX={vix:.1f} (IVR={iv_rank:.0f}) — OPTIONS ARE CHEAP! "
            "Great time to buy calls/puts. Premiums are discounted. "
            "Prefer buying over selling strategies."
        )
    elif regime == "normal":
        return (
            f"🟡 VIX={vix:.1f} (IVR={iv_rank:.0f}) — Normal volatility. "
            "All strategies valid. No special premium advantage."
        )
    elif regime == "expensive":
        return (
            f"🔴 VIX={vix:.1f} (IVR={iv_rank:.0f}) — OPTIONS ARE EXPENSIVE! "
            "You're overpaying for premiums. Prefer shorter-duration exits, "
            "tighter targets, or consider selling strategies instead."
        )
    else:  # panic
        return (
            f"🚨 VIX={vix:.1f} (PANIC! IVR={iv_rank:.0f}) — EXTREME fear. "
            "Spreads are wide, premiums astronomical. "
            "Reduce size by 60%. Only trade if direction is very clear."
        )


def fetch_vix_context(use_cache: bool = True) -> VixContext:
    """Fetch India VIX and compute IVR / IV Percentile.

    Tries yfinance `^NSEBANK` VIX proxy (^INDIAVIX symbol).
    Falls back to reasonable defaults if data unavailable.

    Args:
        use_cache: Use in-memory cache to avoid hammering NSE API.

    Returns:
        VixContext with all derived metrics.
    """
    # Try to fetch via yfinance
    try:
        import yfinance as yf  # type: ignore
        ticker = yf.Ticker("^INDIAVIX")
        end   = datetime.today()
        start = end - timedelta(days=365)
        hist  = ticker.history(start=start.strftime("%Y-%m-%d"),
                               end=end.strftime("%Y-%m-%d"))

        if hist.empty:
            raise ValueError("Empty VIX data from yfinance")

        closes = hist["Close"].dropna()
        vix_current  = float(closes.iloc[-1])
        vix_52w_high = float(closes.max())
        vix_52w_low  = float(closes.min())

        # IVR
        rng = vix_52w_high - vix_52w_low
        iv_rank = ((vix_current - vix_52w_low) / rng * 100) if rng > 0 else 50.0

        # IV Percentile = % of days this year VIX was < today
        iv_pct = float((closes < vix_current).sum() / len(closes) * 100)

    except Exception as e:
        logger.warning("Could not fetch India VIX from yfinance: %s — using defaults", e)
        # Reasonable defaults for a typical Indian market day
        vix_current  = 14.5
        vix_52w_high = 22.0
        vix_52w_low  = 10.5
        iv_rank      = 35.0
        iv_pct       = 40.0
        stale        = True
    else:
        stale = False

    regime      = _classify_regime(vix_current)
    buy_q       = _buy_quality(iv_rank, regime)
    sell_q      = _sell_quality(iv_rank, regime)
    size_sc     = _size_scale_from_ivr(iv_rank, regime)
    guidance    = _build_guidance(vix_current, iv_rank, regime)

    return VixContext(
        vix_current=round(vix_current, 2),
        vix_52w_high=round(vix_52w_high, 2),
        vix_52w_low=round(vix_52w_low, 2),
        iv_rank=round(iv_rank, 1),
        iv_percentile=round(iv_pct, 1),
        regime=regime,
        buy_quality=buy_q,
        sell_quality=sell_q,
        size_scale=round(size_sc, 2),
        guidance=guidance,
        data_stale=stale,
    )


def vix_size_scale() -> float:
    """Quick helper: just the size scale multiplier based on current VIX."""
    return fetch_vix_context().size_scale


# ── CLI demo ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🐶 India VIX Monitor")
    print("=" * 60)
    ctx = fetch_vix_context()
    stale_tag = " (⚠️ stale/default data)" if ctx.data_stale else ""
    print(f"  VIX Current : {ctx.vix_current:.2f}{stale_tag}")
    print(f"  52w High    : {ctx.vix_52w_high:.2f}")
    print(f"  52w Low     : {ctx.vix_52w_low:.2f}")
    print(f"  IV Rank     : {ctx.iv_rank:.1f}%")
    print(f"  IV %ile     : {ctx.iv_percentile:.1f}%")
    print(f"  Regime      : {ctx.regime.upper()}")
    print(f"  Buy quality : {ctx.buy_quality.upper()}")
    print(f"  Sell quality: {ctx.sell_quality.upper()}")
    print(f"  Size scale  : {ctx.size_scale:.2f}x")
    print(f"\n  {ctx.guidance}")
    print("=" * 60)

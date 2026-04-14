"""Kelly Criterion Position Sizer for Nifty Options.

Uses calibrated per-strategy win rates + average win/loss to compute
the mathematically optimal position fraction.

Formula:
    f* = (b·p - q) / b
where:
    p  = win probability
    q  = 1 - p  (loss probability)
    b  = avg_win / avg_loss  (payoff ratio)

We apply a HALF-KELLY as standard to reduce variance in live trading.
An additional drawdown scalar shrinks size further when you're in a
losing streak — protecting capital like a good puppy should. 🐶

Usage:
    from kelly_sizing import get_lot_multiplier

    multiplier = get_lot_multiplier(
        strategy_id="candlestick_patterns",
        consecutive_losses=1,
        today_pnl=-500.0,
        max_daily_loss=3000.0,
    )
    lots = int(base_lots * multiplier)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Hard limits ───────────────────────────────────────────────────────────────

# Never risk more than this fraction of capital on a single trade
_MAX_FRACTION = 0.15   # 15% cap — full Kelly can blow you up, we're adults here
_HALF_KELLY   = 0.50   # Use half-Kelly for live trading (proven to reduce ruin)
_MIN_FRACTION = 0.25   # Floor — never go below 25% of base size

# Drawdown reduction schedule:
#   consecutive_losses → size multiplier
_CONSECUTIVE_LOSS_SCALE: dict[int, float] = {
    0: 1.00,  # Normal — full allowed size
    1: 0.85,  # 1 loss  — slight trim
    2: 0.65,  # 2 losses — meaningful reduction
    3: 0.50,  # 3 losses — half size  (circuit breaker territory)
    4: 0.35,  # 4 losses — minimal exposure
}
_MAX_LOSS_SCALE = 0.20  # 5+ consecutive losses → 20% only (stay in game, don't quit)


@dataclass(frozen=True)
class KellyResult:
    """Output of the Kelly sizing calculation."""
    strategy_id: str
    win_rate: float        # 0-100
    profit_factor: float
    full_kelly: float      # raw Kelly fraction 0-1
    half_kelly: float      # recommended (half of above)
    capped_fraction: float # after applying all limits
    lot_multiplier: float  # multiply base lots by this
    reason: str            # human-readable explanation


def _load_calibration() -> dict:
    """Load calibration.json — strategy performance stats."""
    cal_path = Path(__file__).parent / "calibration.json"
    try:
        with cal_path.open() as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Could not load calibration.json: %s", e)
        return {}


def _kelly_fraction(win_rate_pct: float, avg_win_pts: float, avg_loss_pts: float) -> float:
    """Compute raw Kelly fraction.

    Args:
        win_rate_pct: Win rate as percentage (e.g. 68.2 for 68.2%)
        avg_win_pts:  Average winning trade in index points
        avg_loss_pts: Average losing trade in index points (pass as positive)

    Returns:
        Raw Kelly fraction in [0, 1]. Negative means no edge — don't trade!
    """
    if avg_loss_pts <= 0:
        return 0.0  # Avoid division by zero

    p = win_rate_pct / 100.0
    q = 1.0 - p
    b = abs(avg_win_pts) / abs(avg_loss_pts)  # payoff ratio

    kelly = (b * p - q) / b
    return max(kelly, 0.0)   # Negative Kelly = no edge, don't size up


def _drawdown_scale(consecutive_losses: int) -> float:
    """Return size multiplier based on consecutive loss count."""
    if consecutive_losses >= 5:
        return _MAX_LOSS_SCALE
    return _CONSECUTIVE_LOSS_SCALE.get(consecutive_losses, _MAX_LOSS_SCALE)


def get_lot_multiplier(
    strategy_id: str,
    consecutive_losses: int = 0,
    today_pnl: float = 0.0,
    max_daily_loss: float = 3000.0,
) -> float:
    """Compute the lot multiplier for a given strategy + current state.

    Args:
        strategy_id:        Strategy ID matching calibration.json keys.
        consecutive_losses: Current streak of consecutive losses today.
        today_pnl:          Today's running P&L (negative = loss).
        max_daily_loss:     Daily loss limit (positive number, e.g. 3000).

    Returns:
        Multiplier to apply to your base lot count. Always in [0.2, 1.5].

    Examples:
        get_lot_multiplier("candlestick_patterns", 0)  → ~1.40  (top strategy, no losses)
        get_lot_multiplier("supertrend", 2)            → ~0.50  (mediocre strategy, 2 losses)
        get_lot_multiplier("volume_spike", 0)          → ~0.90
    """
    cal = _load_calibration()
    stats = cal.get(strategy_id)

    if not stats:
        # Unknown strategy — no adjustment (neutral multiplier preserves backward compat).
        # This covers: 'smart_router' meta-router, custom strategies, tests.
        # When the meta router fires, the SELECTED sub-strategy's ID should be
        # passed instead of 'smart_router' for precise Kelly sizing.
        logger.debug("No calibration data for '%s' — 1.0x (no adjustment)", strategy_id)
        return 1.0

    win_rate   = stats.get("win_rate",     50.0)
    avg_win    = stats.get("avg_win_pts",  30.0)
    avg_loss   = abs(stats.get("avg_loss_pts", 20.0))

    # ── 1. Compute raw Kelly ──────────────────────────────────────────────────
    raw_kelly = _kelly_fraction(win_rate, avg_win, avg_loss)

    # ── 2. Apply half-Kelly (standard variance reduction) ────────────────────
    half_k = raw_kelly * _HALF_KELLY

    # ── 3. Cap at max safe fraction ──────────────────────────────────────────
    capped = min(half_k, _MAX_FRACTION)

    # ── 4. Translate fraction → lot multiplier ───────────────────────────────
    # We express multiplier relative to base (1.0 = trade base lots).
    # Kelly fraction of 0.15 (max) → 1.5x, 0.07 → 1.0x, 0.03 → 0.5x
    # Simple linear: multiplier = fraction / 0.10  (0.10 = "normal" fraction)
    lot_mult = capped / 0.10

    # Clamp to sensible range before applying drawdown
    lot_mult = max(_MIN_FRACTION, min(lot_mult, 1.5))

    # ── 5. Drawdown adjustment ────────────────────────────────────────────────
    dd_scale = _drawdown_scale(consecutive_losses)
    lot_mult *= dd_scale

    # ── 6. Daily P&L safety net ───────────────────────────────────────────────
    # If we've already burnt 50% of daily loss limit, halve size again
    loss_used_pct = abs(min(today_pnl, 0)) / max(max_daily_loss, 1)
    if loss_used_pct >= 0.75:
        lot_mult *= 0.50   # emergency shrink
        reason_suffix = " ⚠️ [75%+ daily loss used — emergency 0.5x]"
    elif loss_used_pct >= 0.50:
        lot_mult *= 0.70
        reason_suffix = " ⚠️ [50%+ daily loss used — 0.7x safety scale]"
    else:
        reason_suffix = ""

    # Final clamp
    lot_mult = round(max(0.20, min(lot_mult, 1.5)), 2)

    reason = (
        f"{strategy_id}: WR={win_rate:.1f}%, "
        f"b={avg_win/avg_loss:.2f}, "
        f"Kelly={raw_kelly:.3f}, "
        f"HalfKelly={half_k:.3f}, "
        f"DDscale={dd_scale:.2f} "
        f"→ {lot_mult:.2f}x{reason_suffix}"
    )
    logger.debug(reason)

    return lot_mult


def get_kelly_result(
    strategy_id: str,
    consecutive_losses: int = 0,
    today_pnl: float = 0.0,
    max_daily_loss: float = 3000.0,
) -> KellyResult:
    """Full KellyResult with all details — useful for UI display."""
    cal = _load_calibration()
    stats = cal.get(strategy_id, {})

    win_rate = stats.get("win_rate", 50.0)
    avg_win  = stats.get("avg_win_pts", 30.0)
    avg_loss = abs(stats.get("avg_loss_pts", 20.0))
    pf       = stats.get("profit_factor", 1.0)

    raw_kelly = _kelly_fraction(win_rate, avg_win, avg_loss)
    half_k    = raw_kelly * _HALF_KELLY
    capped    = min(half_k, _MAX_FRACTION)
    lot_mult  = get_lot_multiplier(strategy_id, consecutive_losses, today_pnl, max_daily_loss)

    reason = (
        f"p={win_rate:.1f}%, b={avg_win/max(avg_loss,1):.2f}, "
        f"rawKelly={raw_kelly:.3f}, halfKelly={half_k:.3f}, "
        f"cap={capped:.3f}, ddScale={_drawdown_scale(consecutive_losses):.2f}, "
        f"finalMult={lot_mult:.2f}x"
    )

    return KellyResult(
        strategy_id=strategy_id,
        win_rate=win_rate,
        profit_factor=pf,
        full_kelly=round(raw_kelly, 4),
        half_kelly=round(half_k, 4),
        capped_fraction=round(capped, 4),
        lot_multiplier=lot_mult,
        reason=reason,
    )


# ── CLI demo ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json as _json
    from pathlib import Path as _Path

    cal = _load_calibration()
    strategies = list(cal.keys())

    print("\n🐶 Kelly Criterion Position Sizing — Strategy Comparison")
    print("=" * 70)
    print(f"{'Strategy':<25} {'WR%':>6} {'PF':>5} {'Kelly':>7} {'½Kelly':>7} {'0-loss':>7} {'2-loss':>7} {'3-loss':>7}")
    print("-" * 70)

    for sid in strategies:
        r0 = get_kelly_result(sid, consecutive_losses=0)
        r2 = get_kelly_result(sid, consecutive_losses=2)
        r3 = get_kelly_result(sid, consecutive_losses=3)
        print(
            f"{sid:<25} {r0.win_rate:>5.1f}% {r0.profit_factor:>5.2f} "
            f"{r0.full_kelly:>7.3f} {r0.half_kelly:>7.3f} "
            f"{r0.lot_multiplier:>6.2f}x {r2.lot_multiplier:>6.2f}x {r3.lot_multiplier:>6.2f}x"
        )
    print("=" * 70)
    print("\nℹ️  Use get_lot_multiplier(strategy_id, consecutive_losses) in auto_trader.py")

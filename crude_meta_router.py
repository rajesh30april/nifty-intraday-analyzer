"""Crude Oil Strategy Meta Router — Evaluate ALL strategies, best score wins.

Every registered strategy is scored each candle:

    composite = confidence × regime_fit × time_bonus × vix_boost × dir_align

Highest composite that exceeds MIN_ENTRY_SCORE gets the trade. Simple.

Priority is baked into the score:
- Trending market  → trend/breakout strategies get 1.3-1.4× regime boost
- Sideways market  → reversal/scalping get 1.3-1.4× regime boost
- 9:00-9:15        → ORB gets huge time bonus
- High VIX (>20)   → breakout strategies get extra 1.25× vix boost
- Low VIX (<14)    → scalping/reversal get extra boost
- Evening session  → SuperTrend only (ORB stale after 7 PM)

CRUDE OIL SPECIFIC:
- MCX opens at 9:00 AM (not 9:15 like NSE)
- ORB uses 9:00-9:15 window (3 × 5-min candles)
- Evening session (7 PM - 11:30 PM) → SuperTrend dominates
- Wider ranges (₹20-₹200 vs Nifty's 30-100)
- Faster SuperTrend (period=7, mult=2.5)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dt_time

import pandas as pd

from market_regime import detect_regime, MarketRegime
from strategy import StrategySignal, Direction

# Import crude strategies
from crude_strategy import (
    evaluate_crude_orb,
    evaluate_crude_supertrend,
    evaluate_crude_vwap,
    evaluate_crude_ema_cross,
    evaluate_crude_squeeze,
    evaluate_crude_chart_pattern,
    evaluate_crude_range_fade,  # 🐶 REVERSAL STRATEGY!
    evaluate_crude_divergence,  # 🐶 EARLY WARNING SYSTEM!
)

# VIX regime bands (India VIX)
_VIX_LOW    = 14.0
_VIX_HIGH   = 20.0

# Minimum composite score to enter trade
# 🐶 SIMPLIFIED: Just use confidence! No complex multipliers!
MIN_ENTRY_SCORE = 50.0  # Lowered from 50.0 for more aggressive entries (pure confidence threshold)


def _vix_category_boost(strategy_category: str, vix: float) -> float:
    """Return VIX-based multiplier for a strategy category."""
    if vix < _VIX_LOW:
        # Sleepy market → scalping and reversion work
        boosts = {"scalping": 1.2, "reversal": 1.15, "breakout": 0.8,
                  "momentum": 0.85, "trend": 0.9, "pattern": 1.0}
    elif vix > _VIX_HIGH:
        # Fearful market → big gaps, real breakouts
        boosts = {"breakout": 1.25, "reversal": 1.15, "momentum": 1.1,
                  "scalping": 0.75, "trend": 1.0, "pattern": 1.2}
    else:
        # Normal VIX → no distortion
        boosts = {}
    return boosts.get(strategy_category, 1.0)


# ── Regime fit multipliers per strategy category ─────────────────
_REGIME_FIT: dict[str, dict[str, float]] = {
    MarketRegime.TRENDING_UP:   {"trend": 1.4, "breakout": 1.3, "momentum": 1.2,
                                  "reversal": 0.6, "scalping": 1.0, "pattern": 1.2},
    MarketRegime.TRENDING_DOWN: {"trend": 1.4, "breakout": 1.3, "momentum": 1.2,
                                  "reversal": 0.6, "scalping": 1.0, "pattern": 1.2},
    MarketRegime.SIDEWAYS:      {"reversal": 1.4, "scalping": 1.3, "trend": 0.6,
                                  "breakout": 0.7, "momentum": 0.8, "pattern": 1.1},
    MarketRegime.VOLATILE:      {"reversal": 1.3, "breakout": 1.4, "trend": 0.9,
                                  "momentum": 1.1, "scalping": 0.8, "pattern": 1.15},
}

# Time-of-day bonuses — CRUDE OIL MCX TIMINGS!
_TIME_BONUS: dict[str, list[tuple[dt_time, dt_time, float]]] = {
    # ORB for crude: 9:00-9:15 window (MCX opens at 9:00)
    "orb": [
        (dt_time(9, 0), dt_time(9, 5), 0.0),     # too early (let first candle settle)
        (dt_time(9, 5), dt_time(9, 15), 2.0),    # ORB formation window
        (dt_time(9, 15), dt_time(12, 0), 1.3),   # ORB breakout sweet spot
        (dt_time(12, 0), dt_time(17, 0), 1.0),   # still valid
        (dt_time(17, 0), dt_time(23, 30), 0.0),  # evening session - ORB stale
    ],
    # SuperTrend: works all day, dominates evening session
    "supertrend": [
        (dt_time(9, 0), dt_time(9, 10), 0.8),    # early chop
        (dt_time(9, 10), dt_time(17, 0), 1.2),   # day session
        (dt_time(17, 0), dt_time(19, 0), 1.0),   # post-break warmup
        (dt_time(19, 0), dt_time(23, 30), 1.4),  # evening session - SuperTrend king!
    ],
    # VWAP: day session only
    "vwap": [
        (dt_time(9, 0), dt_time(9, 30), 0.0),    # need VWAP to form
        (dt_time(9, 30), dt_time(14, 0), 1.3),   # sweet spot
        (dt_time(14, 0), dt_time(17, 0), 1.0),   # still valid
        (dt_time(17, 0), dt_time(23, 30), 0.0),  # evening - VWAP resets
    ],
    # EMA Cross: works all day
    "ema_cross": [
        (dt_time(9, 0), dt_time(9, 20), 0.0),    # too early
        (dt_time(9, 20), dt_time(23, 30), 1.2),  # all day
    ],
    # BB Squeeze: works all day but skip first 30 min
    "squeeze": [
        (dt_time(9, 0), dt_time(9, 30), 0.0),    # too early
        (dt_time(9, 30), dt_time(23, 30), 1.2),  # all day/night
    ],
    # Chart Patterns: 🐶 NO TIME FILTER (as per user request!)
    "chart_patterns": [
        (dt_time(9, 0), dt_time(23, 30), 1.2),   # Trade all day/night!
    ],
    # Range Fade: 🐶 REVERSAL - works all day, best in evening session
    "range_fade": [
        (dt_time(9, 0), dt_time(9, 30), 0.0),    # too early (need range to form)
        (dt_time(9, 30), dt_time(17, 0), 1.3),   # day session - ranging common
        (dt_time(17, 0), dt_time(19, 0), 1.0),   # post-break
        (dt_time(19, 0), dt_time(23, 30), 1.4),  # evening session - GOLD for reversals!
    ],
    # Divergence: 🐶 EARLY WARNING - works all day/night
    "divergence": [
        (dt_time(9, 0), dt_time(9, 30), 0.0),    # too early (need data to form)
        (dt_time(9, 30), dt_time(23, 30), 1.3),  # all day - early reversal warnings!
    ],
}


@dataclass
class CrudeMetaRouterResult:
    """Full result from the crude meta router."""
    regime: str
    regime_detail: str
    adx: float
    selected_strategy: str
    selected_emoji: str
    signal: StrategySignal
    scores: list[dict] = field(default_factory=list)  # all candidates ranked
    reason: str = ""


def _time_bonus(strategy_id: str, current_time: dt_time) -> float:
    """Return time-of-day multiplier for a strategy."""
    windows = _TIME_BONUS.get(strategy_id)
    if not windows:
        return 1.0
    for start, end, mult in windows:
        if start <= current_time < end:
            return mult
    return 1.0


def _regime_fit(strategy_category: str, regime: MarketRegime) -> float:
    """Return regime fit multiplier for a strategy category."""
    fits = _REGIME_FIT.get(regime, {})
    return fits.get(strategy_category, 1.0)


def _direction_alignment(strategy_dir: Direction | None, regime: MarketRegime) -> float:
    """Boost if strategy direction aligns with regime trend."""
    if strategy_dir is None:
        return 1.0
    if regime == MarketRegime.TRENDING_UP and strategy_dir == Direction.LONG:
        return 1.2
    elif regime == MarketRegime.TRENDING_DOWN and strategy_dir == Direction.SHORT:
        return 1.2
    else:
        return 1.0


# Strategy metadata (same as Nifty)
CRUDE_STRATEGIES = [
    {"id": "orb", "name": "ORB", "emoji": "🎯", "category": "breakout",
     "eval_fn": evaluate_crude_orb, "win_rate": 55.0},
    {"id": "supertrend", "name": "SuperTrend", "emoji": "📈", "category": "trend",
     "eval_fn": evaluate_crude_supertrend, "win_rate": 60.0},
    {"id": "vwap", "name": "VWAP", "emoji": "〰️", "category": "reversal",
     "eval_fn": evaluate_crude_vwap, "win_rate": 52.0},
    {"id": "ema_cross", "name": "EMA Cross", "emoji": "✂️", "category": "momentum",
     "eval_fn": evaluate_crude_ema_cross, "win_rate": 50.0},
    {"id": "squeeze", "name": "BB Squeeze", "emoji": "💥", "category": "breakout",
     "eval_fn": evaluate_crude_squeeze, "win_rate": 58.0},
    {"id": "chart_patterns", "name": "Chart Patterns", "emoji": "📐", "category": "pattern",
     "eval_fn": evaluate_crude_chart_pattern, "win_rate": 50.0},
    {"id": "range_fade", "name": "Range Fade", "emoji": "🔄", "category": "reversal",
     "eval_fn": evaluate_crude_range_fade, "win_rate": 62.0},  # 🐶 MEAN-REVERSION REVERSAL!
    {"id": "divergence", "name": "Divergence", "emoji": "⚠️", "category": "reversal",
     "eval_fn": evaluate_crude_divergence, "win_rate": 68.0},  # 🐶 EARLY WARNING SYSTEM!
]


def evaluate_crude_meta(df: pd.DataFrame, current_time: dt_time | None = None,
                         vix: float = 16.0, enabled_strategies: list | None = None) -> CrudeMetaRouterResult:
    """Evaluate ALL crude strategies, return highest composite score.

    Args:
        df: OHLCV data with indicators
        current_time: Current time for time-of-day bonus (defaults to last candle time)
        vix: India VIX level for VIX-based boosting
        enabled_strategies: List of strategy IDs to evaluate. None/empty = all enabled.

    Returns:
        CrudeMetaRouterResult with winning strategy and all scores
    """
    if current_time is None:
        current_time = df.index[-1].time()

    # Detect regime
    regime_result = detect_regime(df)
    regime = regime_result.regime
    adx = regime_result.adx
    regime_detail = regime_result.detail

    scores = []

    # 🎯 Filter strategies based on enabled list
    # If enabled_strategies is None or empty, use all strategies
    active_strategies = CRUDE_STRATEGIES
    if enabled_strategies:  # if list provided and not empty
        active_strategies = [s for s in CRUDE_STRATEGIES if s["id"] in enabled_strategies]

    # Evaluate each strategy
    for strat in active_strategies:
        signal = strat["eval_fn"](df)

        # 🐶 SIMPLIFIED SCORING - Just use confidence!
        # No complex regime × time × VIX × direction multipliers
        # Keep it simple like Nifty!
        composite = signal.confidence  # That's it! Pure confidence!

        scores.append({
            "id": strat["id"],
            "name": strat["name"],
            "emoji": strat["emoji"],
            "category": strat["category"],
            "confidence": signal.confidence,
            "win_rate": strat["win_rate"],
            "regime_fit": 1.0,  # Not used anymore (simplified!)
            "time_mult": 1.0,   # Not used anymore (simplified!)
            "vix_boost": 1.0,   # Not used anymore (simplified!)
            "dir_align": 1.0,   # Not used anymore (simplified!)
            "composite": composite,  # Just confidence!
            "should_enter": signal.should_enter,
            "direction": signal.direction.name.lower() if signal.direction else "none",
            "reason": signal.reason,
        })

    # Sort by composite score (highest first)
    scores.sort(key=lambda x: x["composite"], reverse=True)

    # Winner = highest composite that wants to enter AND exceeds MIN_ENTRY_SCORE
    winner = None
    for s in scores:
        if s["should_enter"] and s["composite"] >= MIN_ENTRY_SCORE:
            winner = s
            break

    if winner:
        # Get the actual signal from winner
        winner_fn = next(st["eval_fn"] for st in CRUDE_STRATEGIES if st["id"] == winner["id"])
        signal = winner_fn(df)

        reason = (
            f"{winner['emoji']} {winner['name']} wins! "
            f"(score={winner['composite']:.0f}%)"
        )
    else:
        # No strategy wants to enter or none exceed threshold
        signal = StrategySignal(should_enter=False, direction=Direction.LONG,
                                 confidence=0.0, reason="No strategy triggered")
        reason = f"No strategy exceeds MIN_ENTRY_SCORE={MIN_ENTRY_SCORE}"

    return CrudeMetaRouterResult(
        regime=regime.value,  # Use regime enum value (e.g., "trending_up")
        regime_detail=regime_detail,
        adx=adx,
        selected_strategy=winner["name"] if winner else "None",
        selected_emoji=winner["emoji"] if winner else "⏸️",
        signal=signal,
        scores=scores,
        reason=reason,
    )

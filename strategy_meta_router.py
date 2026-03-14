"""Strategy Meta Router — Evaluate ALL strategies, pick the best one.

Instead of hard-coding which strategy to run, this evaluates every
registered strategy before each trade and picks the one with the
highest composite score:

    composite = confidence × regime_fit × time_bonus

This means:
- Trend strategies score higher when ADX is strong
- Reversal / OCF strategies score higher when market is choppy
- OCF gets a big time bonus at 9:20 (its exact window)
- ORB gets a time bonus after 9:30
- No strategy ever fires blindly — all conditions must pass
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dt_time

import pandas as pd

import strategies.loader  # noqa: F401 — ensures all strategies registered
from market_regime import detect_regime, MarketRegime
from strategies.registry import all_strategies, StrategyInfo
from strategy import StrategySignal


# ── Regime fit multipliers per strategy category ─────────────────
# Strategy categories → how well they fit each regime
_REGIME_FIT: dict[str, dict[str, float]] = {
    MarketRegime.TRENDING_UP:   {"trend": 1.4, "breakout": 1.3, "momentum": 1.2,
                                  "reversal": 0.6, "adaptive": 1.0, "scalping": 1.0},
    MarketRegime.TRENDING_DOWN: {"trend": 1.4, "breakout": 1.3, "momentum": 1.2,
                                  "reversal": 0.6, "adaptive": 1.0, "scalping": 1.0},
    MarketRegime.SIDEWAYS:      {"reversal": 1.4, "scalping": 1.3, "trend": 0.6,
                                  "breakout": 0.7, "momentum": 0.8, "adaptive": 1.0},
    MarketRegime.VOLATILE:      {"reversal": 1.3, "breakout": 1.2, "trend": 0.9,
                                  "momentum": 1.1, "adaptive": 1.0, "scalping": 0.8},
}

# Time-of-day bonuses for specific strategies
_TIME_BONUS: dict[str, list[tuple[dt_time, dt_time, float]]] = {
    # OCF ONLY valid at exactly 9:20 window
    "ocf": [
        (dt_time(9, 20), dt_time(9, 30), 2.0),   # 9:20–9:30 → huge bonus
        (dt_time(9, 15), dt_time(9, 20), 0.0),   # before 9:20 → blocked
        (dt_time(9, 30), dt_time(15, 30), 0.0),  # after 9:30 → blocked
    ],
    # ORB needs at least 15min of range to form
    "orb": [
        (dt_time(9, 15), dt_time(9, 29), 0.0),   # too early
        (dt_time(9, 30), dt_time(10, 30), 1.3),  # sweet spot
        (dt_time(10, 30), dt_time(15, 30), 1.0), # still valid
    ],
}


@dataclass
class MetaRouterResult:
    """Full result from the meta router — transparent scoring."""
    regime: str
    regime_detail: str
    adx: float
    selected_strategy: str
    selected_emoji: str
    signal: StrategySignal
    scores: list[dict] = field(default_factory=list)  # all candidates ranked
    reason: str = ""


def _time_bonus(strategy_id: str, current_time: dt_time) -> float:
    """Return time-of-day multiplier for a strategy. Default = 1.0."""
    windows = _TIME_BONUS.get(strategy_id)
    if not windows:
        return 1.0
    for start, end, mult in windows:
        if start <= current_time < end:
            return mult
    return 1.0  # outside all defined windows — neutral


def _regime_fit(strategy_category: str, regime: MarketRegime) -> float:
    """Return regime fit multiplier for a strategy category."""
    fits = _REGIME_FIT.get(regime, {})
    return fits.get(strategy_category, 1.0)


def evaluate_all(df: pd.DataFrame) -> MetaRouterResult:
    """Evaluate every registered strategy and return the best one.

    Steps:
    1. Detect market regime (ADX, ATR, EMA slope)
    2. For each strategy:
       a. Run evaluate() to get signal + confidence
       b. Apply regime fit multiplier
       c. Apply time-of-day bonus
       d. Compute composite score
    3. Sort by composite score
    4. Return highest-scoring strategy that has should_enter=True
       (if none fire, return the highest-confidence non-entry)

    Args:
        df: Full OHLCV DataFrame with multi-day history.

    Returns:
        MetaRouterResult with selected strategy and full scoring table.
    """
    if len(df) < 10:
        return _empty_result("Insufficient data")

    regime_result = detect_regime(df)
    regime = regime_result.regime
    current_time = df.index[-1].time()

    strategies = all_strategies()
    candidates: list[dict] = []

    for strat in strategies:
        # Skip the smart_router itself to avoid recursion
        if strat.id in ("smart_router", "meta_router"):
            continue

        try:
            signal: StrategySignal = strat.evaluate(df)
        except Exception as exc:  # noqa: BLE001
            candidates.append({
                "id": strat.id, "name": strat.name, "emoji": strat.emoji,
                "category": strat.category,
                "confidence": 0.0, "regime_fit": 1.0, "time_mult": 1.0,
                "composite": 0.0, "should_enter": False,
                "direction": None, "signal": None,
                "error": str(exc),
            })
            continue

        t_mult    = _time_bonus(strat.id, current_time)
        r_fit     = _regime_fit(strat.category, regime)
        raw_conf  = signal.confidence or 0.0
        composite = raw_conf * r_fit * t_mult

        candidates.append({
            "id":          strat.id,
            "name":        strat.name,
            "emoji":       strat.emoji,
            "category":    strat.category,
            "confidence":  round(raw_conf, 1),
            "regime_fit":  r_fit,
            "time_mult":   t_mult,
            "composite":   round(composite, 1),
            "should_enter": signal.should_enter,
            "direction":   signal.direction,
            "signal":      signal,
            "error":       None,
        })

    # Sort by composite score descending
    candidates.sort(key=lambda x: x["composite"], reverse=True)

    # Pick best entry signal
    entry_candidates = [c for c in candidates if c["should_enter"] and c["time_mult"] > 0]
    chosen = entry_candidates[0] if entry_candidates else None

    if chosen:
        sig = chosen["signal"]
        sig.reason = (
            f"[META: {regime.value.upper()}] → {chosen['emoji']} {chosen['name']} "
            f"| score={chosen['composite']:.0f} "
            f"(conf={chosen['confidence']:.0f}% × regime={chosen['regime_fit']} × time={chosen['time_mult']}) "
            f"| {sig.reason}"
        )
        return MetaRouterResult(
            regime=regime.value,
            regime_detail=regime_result.detail,
            adx=regime_result.adx,
            selected_strategy=chosen["name"],
            selected_emoji=chosen["emoji"],
            signal=sig,
            scores=candidates,
            reason=sig.reason,
        )

    # No entry signal — return highest composite (for transparency)
    top = candidates[0] if candidates else None
    no_signal = StrategySignal(
        should_enter=False,
        reason=(
            f"[META: {regime.value.upper()}] No strategy fired. "
            f"Best candidate: {top['emoji'] if top else '?'} {top['name'] if top else 'none'} "
            f"score={top['composite'] if top else 0:.0f}"
        ),
    )
    return MetaRouterResult(
        regime=regime.value,
        regime_detail=regime_result.detail,
        adx=regime_result.adx,
        selected_strategy=top["name"] if top else "none",
        selected_emoji=top["emoji"] if top else "❓",
        signal=no_signal,
        scores=candidates,
        reason=no_signal.reason,
    )


def _empty_result(reason: str) -> MetaRouterResult:
    return MetaRouterResult(
        regime="unknown", regime_detail=reason, adx=0,
        selected_strategy="none", selected_emoji="❓",
        signal=StrategySignal(should_enter=False, reason=reason),
        scores=[], reason=reason,
    )
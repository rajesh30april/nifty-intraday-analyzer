"""Strategy Meta Router — Evaluate ALL strategies, best score wins.

Every registered strategy is scored each candle:

    composite = confidence × regime_fit × time_bonus × vix_boost

Highest composite that exceeds MIN_ENTRY_SCORE gets the trade. Simple.

Priority is baked into the score:
- Trending market  → trend/breakout strategies get 1.3-1.4× regime boost
- Sideways market  → reversal/scalping get 1.3-1.4× regime boost
- 9:20 candle      → OCF gets 2.0× time bonus, naturally dominates
- 9:30-10:30       → ORB gets 1.3× time bonus
- High VIX (>20)   → breakout strategies get extra 1.25× vix boost
- Low VIX (<14)    → scalping/reversal get extra boost

No committees. No voting. No ratio checks. Best strategy wins every candle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dt_time

import pandas as pd

import strategies.loader  # noqa: F401 — ensures all strategies registered
from market_regime import detect_regime, MarketRegime
from reversal_continuation_detector import ReversalContinuationDetector, ReversalContinuationResult  # 🐶 NEW!
from strategies.registry import all_strategies, StrategyInfo
from strategy import StrategySignal

# VIX regime bands — India VIX as of 2025
# Low VIX  (<14)  : dead market, tight ranges → scalping / reversion
# Normal   (14-20): balanced → all strategies
# High VIX (>20)  : fear, gaps, big moves → breakout / gap strategies win
_VIX_LOW    = 14.0
_VIX_HIGH   = 20.0


def _vix_category_boost(strategy_category: str, vix: float) -> float:
    """Return VIX-based multiplier for a strategy category.

    Complements regime_fit (which uses ADX). VIX adds the fear dimension.
    """
    if vix < _VIX_LOW:
        # Sleepy market → scalping and reversion work, breakouts fake out
        boosts = {"scalping": 1.2, "reversal": 1.15, "breakout": 0.8,
                  "momentum": 0.85, "trend": 0.9, "adaptive": 1.0, "pattern": 1.0}
    elif vix > _VIX_HIGH:
        # Fearful market → big gaps, real breakouts, fade the panic
        boosts = {"breakout": 1.25, "reversal": 1.15, "momentum": 1.1,
                  "scalping": 0.75, "trend": 1.0, "adaptive": 1.0, "pattern": 1.2}
    else:
        # Normal VIX → no VIX-based distortion
        boosts = {}
    return boosts.get(strategy_category, 1.0)


# ── Regime fit multipliers per strategy category ─────────────────
# Strategy categories → how well they fit each regime
# Pattern strategies (chart_patterns 78%, candlestick_patterns 80.8%) are
# the HIGHEST proven win-rate strategies — regime fits reflect that.
_REGIME_FIT: dict[str, dict[str, float]] = {
    MarketRegime.TRENDING_UP:   {"trend": 1.4, "breakout": 1.3, "momentum": 1.2,
                                  "reversal": 0.6, "adaptive": 1.0, "scalping": 1.0,
                                  "pattern": 1.35},  # flags, engulfing, continuations fire
    MarketRegime.TRENDING_DOWN: {"trend": 1.4, "breakout": 1.3, "momentum": 1.2,
                                  "reversal": 0.6, "adaptive": 1.0, "scalping": 1.0,
                                  "pattern": 1.35},  # bear flags, evening star, engulfing
    MarketRegime.SIDEWAYS:      {"reversal": 1.4, "scalping": 1.3, "trend": 0.6,
                                  "breakout": 0.7, "momentum": 0.8, "adaptive": 1.0,
                                  "pattern": 1.4},   # double tops/bottoms, hammers SHINE
    MarketRegime.VOLATILE:      {"reversal": 1.3, "breakout": 1.4, "trend": 0.9,
                                  "momentum": 1.1, "adaptive": 1.0, "scalping": 0.8,
                                  "pattern": 1.35},  # engulfing at volatile reversals
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
    # Gap and Go is an early-session play — best at 9:15–11:00
    "gap_and_go": [
        (dt_time(9, 15), dt_time(9, 20), 2.0),   # 9:15 candle = prime entry
        (dt_time(9, 20), dt_time(11, 0),  1.5),  # still good (delayed gap plays need time)
        (dt_time(11, 0), dt_time(15, 30), 0.0),  # too late for gap play
    ],
    # CPR Virgin — valid all day but cut off at 14:30 (needs time to reach CPR)
    "cpr_virgin": [
        (dt_time(9, 15), dt_time(9, 30), 0.5),   # too early — CPR not tested yet
        (dt_time(9, 30), dt_time(13, 0), 1.3),   # sweet spot — full day ahead
        (dt_time(13, 0), dt_time(14, 30), 1.0),  # still valid
        (dt_time(14, 30), dt_time(15, 30), 0.0), # too late — CPR won't be reached
    ],
    # Fibonacci Pullback — needs swing to form (at least 30min/6 candles)
    "fib_pullback": [
        (dt_time(9, 15), dt_time(10, 0), 0.0),   # swing not formed yet
        (dt_time(10, 0), dt_time(13, 30), 1.3),  # sweet spot — swing well formed
        (dt_time(13, 30), dt_time(14, 0), 1.0),  # still valid
        (dt_time(14, 0), dt_time(15, 30), 0.0),  # too late for new Fib entries
    ],
    # Gap Fill — early session only (fills happen fast or not at all)
    "gap_fill": [
        (dt_time(9, 15), dt_time(9, 20), 2.0),   # 9:15 = prime gap fill window
        (dt_time(9, 20), dt_time(10, 0), 1.5),   # still filling early
        (dt_time(10, 0), dt_time(10, 30), 1.0),  # fading relevance
        (dt_time(10, 30), dt_time(15, 30), 0.0), # gap fills done by 10:30
    ],
    # BB Squeeze fires whenever compression releases — neutral all day
    # but skip the chaotic first 15 min
    "bb_squeeze": [
        (dt_time(9, 15), dt_time(9, 30), 0.0),   # too early, no squeeze history
        (dt_time(9, 30), dt_time(15, 30), 1.0),  # any time after 9:30
    ],
    # Camarilla levels are valid all day but best mid-session
    "camarilla": [
        (dt_time(9, 15), dt_time(9, 30), 0.7),   # early — levels not tested yet
        (dt_time(9, 30), dt_time(14, 0), 1.2),   # mid-session sweet spot
        (dt_time(14, 0), dt_time(15, 30), 1.0),  # still valid
    ],
    # FCR fires right after 9:20 — prime window is 9:20–10:00 (early momentum)
    "fcr": [
        (dt_time(9, 15), dt_time(9, 20), 0.0),   # first candle still forming
        (dt_time(9, 20), dt_time(10, 0), 1.5),   # sweet spot — early breakout
        (dt_time(10, 0), dt_time(12, 0), 1.1),   # still valid, slightly lower bonus
        (dt_time(12, 0), dt_time(14, 30), 0.9),  # fading relevance midday
        (dt_time(14, 30), dt_time(15, 30), 0.0), # too late, no new entries
    ],
    # Volume Spike — skip first chaotic candle, great all morning
    "volume_spike": [
        (dt_time(9, 15), dt_time(9, 20), 0.0),   # 9:15 candle always spikes — skip
        (dt_time(9, 20), dt_time(11, 30), 1.3),  # morning breakouts
        (dt_time(11, 30), dt_time(14, 0), 1.0),  # midday — still valid
        (dt_time(14, 0), dt_time(14, 45), 0.8),  # fading
        (dt_time(14, 45), dt_time(15, 30), 0.0), # too late
    ],
    # OBV Divergence — needs at least 30 min of data to form a divergence
    "obv_divergence": [
        (dt_time(9, 15), dt_time(9, 30), 0.0),   # too early — no history
        (dt_time(9, 30), dt_time(12, 0), 1.2),   # morning divergences strongest
        (dt_time(12, 0), dt_time(14, 30), 1.1),  # still valid
        (dt_time(14, 30), dt_time(15, 30), 0.0), # too late
    ],
    # Volume Profile — needs at least 30 min of today's data to be meaningful
    "volume_profile": [
        (dt_time(9, 15), dt_time(9, 45), 0.0),   # profile not formed yet
        (dt_time(9, 45), dt_time(14, 0), 1.2),   # sweet spot — HVNs well tested
        (dt_time(14, 0), dt_time(14, 45), 1.0),  # still valid
        (dt_time(14, 45), dt_time(15, 30), 0.0), # too late
    ],
    # Chart patterns — 🐶 TIME FILTER REMOVED! Trade all day!
    "chart_patterns": [
        (dt_time(9, 15), dt_time(15, 30), 1.2),  # Trade anytime during market hours!
    ],
    # Candlestick patterns — need at least 5 candles (25min) of today's data
    "candlestick_patterns": [
        (dt_time(9, 15), dt_time(9, 30), 0.0),   # first candle noise — skip
        (dt_time(9, 30), dt_time(14, 0), 1.2),   # great all morning
        (dt_time(14, 0), dt_time(14, 30), 1.0),  # post-lunch reversal setups
        (dt_time(14, 30), dt_time(15, 30), 0.0), # too late
    ],
    # PDH/PDL valid all day but sweet spot is morning + post-lunch breakout
    "pdhl_breakout": [
        (dt_time(9, 15), dt_time(9, 25), 0.8),   # too early — level not confirmed
        (dt_time(9, 25), dt_time(11, 0), 1.3),   # morning breakout window
        (dt_time(11, 0), dt_time(13, 30), 1.1),  # midday — still valid
        (dt_time(13, 30), dt_time(14, 30), 1.2), # post-lunch breakouts common
        (dt_time(14, 30), dt_time(15, 30), 0.0), # too late
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
    scores: list[dict] = field(default_factory=list)       # all candidates ranked
    top_conditions: list = field(default_factory=list)     # top strategy's conditions (for UI)
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


def _pattern_boost(strategy_id: str, signal: "StrategySignal") -> float:
    """Extra boost for BOTH pattern strategies based on pattern type + volume confirmation.

    Both chart_patterns (78% win rate) and candlestick_patterns (80.8% win rate)
    are the highest-performing strategies — they deserve the highest boosts.

    Volume-confirmed strong reversals = 2.2x (best signals in the entire system)
    Volume-confirmed medium reversals  = 1.8x
    Volume-confirmed continuations     = 1.5x
    Unconfirmed strong reversals       = 1.4x
    Unconfirmed medium reversals       = 1.2x
    Unconfirmed continuations          = 0.95x (slight penalty — risky without vol)
    """
    if strategy_id not in ("chart_patterns", "candlestick_patterns"):
        return 1.0

    reason = signal.reason.lower() if signal.reason else ""
    has_vol = "✅" in (signal.reason or "") or "vol" in reason

    STRONG_REVERSALS = [
        "bullish engulfing", "bearish engulfing",
        "rsi divergence", "morning star", "evening star",
        "three soldiers", "three crows",
        "inverse h&s", "head and shoulders",
        "triple top", "triple bottom",
    ]
    MEDIUM_REVERSALS = [
        "hammer", "shooting star", "harami",
        "double top", "double bottom",
        "rising wedge", "falling wedge",
    ]
    CONTINUATIONS = [
        "flag", "pennant", "triangle", "channel",
        "ascending", "descending",
    ]

    for p in STRONG_REVERSALS:
        if p in reason:
            return 2.2 if has_vol else 1.4

    for p in MEDIUM_REVERSALS:
        if p in reason:
            return 1.8 if has_vol else 1.2

    for p in CONTINUATIONS:
        if p in reason:
            return 1.5 if has_vol else 0.95

    # Generic pattern detected — small boost
    return 1.3 if has_vol else 1.0


def evaluate_all(df: pd.DataFrame, enabled_strategies: list[str] | None = None) -> MetaRouterResult:
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
        enabled_strategies: List of strategy IDs to evaluate. Empty/None = all enabled.

    Returns:
        MetaRouterResult with selected strategy and full scoring table.
    """
    if len(df) < 10:
        return _empty_result("Insufficient data")

    regime_result = detect_regime(df)
    regime = regime_result.regime
    current_time = df.index[-1].time()

    # 🐶 NEW: Detect reversal vs continuation!
    rev_cont_detector = None
    rev_cont = ReversalContinuationResult(
        reversal_score=0, continuation_score=0, signals=[],
        recommendation='NEUTRAL', confidence=0
    )
    try:
        rev_cont_detector = ReversalContinuationDetector(df, lookback=30)
        rev_cont = rev_cont_detector.analyze()
    except Exception as e:
        # Fallback if detector fails — rev_cont already defaulted above
        rev_cont = ReversalContinuationResult(
            reversal_score=0, continuation_score=0, signals=[],
            recommendation='NEUTRAL', confidence=0
        )
        # Also create a dummy detector for accessing day stats
        try:
            rev_cont_detector = ReversalContinuationDetector(df, lookback=30)
        except Exception:
            pass  # If this also fails, we'll handle it below

    # Fetch VIX once per evaluate_all call (uses the 5-min cached value from app.py)
    try:
        from auto_trader import premium_estimate as _pe  # noqa: PLC0415
        _vix_cache = getattr(_pe, "_cache", {})
        current_vix = float(_vix_cache.get("vix", 16.0))
    except Exception:
        current_vix = 16.0  # sensible fallback

    strategies = all_strategies()
    
    # 🎯 Filter strategies based on selection (empty/None = all enabled)
    if enabled_strategies:
        valid_ids = {s.id for s in strategies}
        enabled_set = {sid for sid in enabled_strategies if sid in valid_ids}
        if enabled_set:  # Only filter if we have valid IDs
            strategies = [s for s in strategies if s.id in enabled_set]
    
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
        v_boost   = _vix_category_boost(strat.category, current_vix)
        p_boost   = _pattern_boost(strat.id, signal)  # 🐶 NEW: Chart pattern boost!

        # ── Calibrated scoring (data-driven, not hand-coded) ──────────────
        # base      = historical win rate from real backtest (0-100)
        #             falls back to 50 (neutral) if not yet calibrated
        # strength  = how well conditions align RIGHT NOW (0.5 – 1.5)
        #             derived from strategy's per-candle confidence
        #             so a strategy with 60% win rate on a perfect setup
        #             scores HIGHER than a 70% win rate strategy on a weak one
        # composite = base × strength × regime × time × vix
        from calibrator import win_rate_for  # noqa: PLC0415
        base      = win_rate_for(strat.id)          # e.g., 63.0 for ORB
        raw_conf  = signal.confidence or 0.0
        strength  = 0.5 + (raw_conf / 100.0)        # maps [0,100] → [0.5, 1.5]

        # ── Direction alignment penalty ─────────────────────────────
        # A signal that fights the detected regime gets penalised 30%.
        # e.g. LONG signal in TRENDING_DOWN regime → × 0.70
        # This prevents a strategy from always winning in the wrong direction.
        d_align = 1.0
        if signal.direction is not None:
            sig_long = signal.direction.value == "long"
            if regime == MarketRegime.TRENDING_DOWN and sig_long:
                d_align = 0.70   # contra-trend LONG in downtrend
            elif regime == MarketRegime.TRENDING_UP and not sig_long:
                d_align = 0.70   # contra-trend SHORT in uptrend
        
        # 🐶 NEW: Reversal/Continuation Alignment ──────────────────────
        # If REVERSAL detected and strategy is trend-following → penalize!
        # If CONTINUATION detected and strategy is reversal → penalize!
        # This prevents buying tops and selling bottoms!
        rc_mult = 1.0
        
        if rev_cont.recommendation == 'REVERSAL' and rev_cont.reversal_score > 60 and rev_cont_detector is not None:
            # Strong reversal signal detected!
            # Penalize trend-following strategies (momentum, breakout, trend)
            # Boost reversal strategies (reversal, scalping)
            if strat.category in ("trend", "momentum", "breakout"):
                # Check if strategy is going WITH the prior trend (bad at reversal!)
                if signal.direction is not None:
                    sig_long = signal.direction.value == "long"
                    # LONG after rally = buying the top! ❌
                    # SHORT after decline = selling the bottom! ❌
                    current_price = rev_cont_detector.current_price
                    day_low = rev_cont_detector.day_low
                    day_range = rev_cont_detector.day_range

                    if (sig_long and current_price > day_low + day_range * 0.6):
                        rc_mult = 0.3  # 70% penalty for buying near top during reversal!
                    elif (not sig_long and current_price < day_low + day_range * 0.4):
                        rc_mult = 0.3  # 70% penalty for selling near bottom during reversal!

                    # 🐶 Strong-trend override: ADX > 40 means the "reversal"
                    # signal is noise — the trend almost always continues.
                    # Floor rc_mult at 0.8 so trend strategies aren't killed
                    # by a phantom reversal call in a confirmed strong trend.
                    # (ADX > 40 = very strong; ADX > 50 = extreme — both trust the trend.)
                    if regime_result.adx > 40 and rc_mult < 0.8:
                        rc_mult = 0.8

            elif strat.category in ("reversal", "scalping"):
                # Boost reversal strategies during reversal!
                rc_mult = 1.3  # 30% bonus!
        
        elif rev_cont.recommendation == 'CONTINUATION' and rev_cont.continuation_score > 60:
            # Strong continuation signal!
            # Boost trend-following, penalize premature reversals
            if strat.category in ("trend", "momentum", "breakout"):
                rc_mult = 1.2  # 20% bonus for trend strategies
            elif strat.category in ("reversal", "scalping"):
                rc_mult = 0.7  # 30% penalty for reversal strategies (too early!)

        composite = base * strength * r_fit * t_mult * v_boost * d_align * p_boost * rc_mult  # 🐶 RC boost!

        candidates.append({
            "id":          strat.id,
            "name":        strat.name,
            "emoji":       strat.emoji,
            "category":    strat.category,
            "confidence":  round(raw_conf, 1),    # current candle signal strength
            "win_rate":    round(base, 1),         # calibrated historical win rate
            "strength":    round(strength, 2),     # 0.5-1.5 multiplier
            "regime_fit":  r_fit,
            "time_mult":   t_mult,
            "vix_boost":   v_boost,
            "dir_align":   d_align,
            "pattern_boost": p_boost,               # 🐶 Chart pattern boost
            "rc_mult":     rc_mult,                 # 🐶 NEW: Reversal/Continuation multiplier
            "composite":   round(composite, 1),
            "should_enter": signal.should_enter,
            "direction":   signal.direction,
            "signal":      signal,
            "error":       None,
        })

    # Sort by composite score descending
    candidates.sort(key=lambda x: x["composite"], reverse=True)

    return _priority_pick(candidates, regime, regime_result, current_vix, rev_cont)


# ── Single gate: minimum composite score to enter ─────────────────────────────
# Keeps truly weak signals out without needing multi-strategy consensus.
# A strategy scoring below this is saying "I see something but barely" — skip it.
# New scale: base(50-70) × strength(0.5-1.5) × regime(0.6-1.4) × time × vix
# A calibrated strategy on a decent setup in the right regime ≈ 50-100
# Tune upward (→ 80) for fewer/higher-quality trades.
MIN_ENTRY_SCORE = 55


def _priority_pick(
    candidates: list[dict],
    regime: MarketRegime,
    regime_result,
    current_vix: float,
    rev_cont: ReversalContinuationResult | None = None,
) -> MetaRouterResult:
    """Best score wins — highest composite that passes MIN_ENTRY_SCORE takes the trade.

    The scoring formula already handles priority:
        composite = confidence × regime_fit × time_mult × vix_boost

    So naturally:
      - Trending market  → trend/breakout strategies score highest
      - Sideways market  → reversal/scalping strategies score highest
      - 9:20 candle      → OCF gets 2× bonus, dominates
      - 9:30-10:30       → ORB gets 1.3× bonus
      - High VIX         → breakout strategies get extra boost

    No voting. No ratios. No committees. Best score wins.
    """
    vix_tag = f"VIX={current_vix:.1f}"

    # Eligible: must want to enter, not time-blocked, score above minimum
    entry_candidates = [
        c for c in candidates
        if c["should_enter"]
        and c["time_mult"] > 0
        and c["composite"] >= MIN_ENTRY_SCORE
    ]

    top = candidates[0] if candidates else None

    if not entry_candidates:
        # Explain WHY no entry — three distinct reasons:
        # 1. Top scorer has good score but conditions not met (should_enter=False)
        # 2. Top scorer is time-blocked (time_mult=0)
        # 3. No strategy scored above the minimum
        if top:
            if top["time_mult"] == 0:
                no_entry_why = (
                    f"{top['emoji']} {top['name']} is time-blocked right now"
                )
            elif not top["should_enter"]:
                # Pull out exactly which conditions failed
                top_sig = top.get("signal")
                if top_sig and top_sig.conditions:
                    failed = [
                        f"❌ {c.name}"
                        for c in top_sig.conditions if not c.met
                    ]
                    passed = [
                        f"✅ {c.name}"
                        for c in top_sig.conditions if c.met
                    ]
                    cond_summary = "  |  ".join(passed + failed)
                else:
                    cond_summary = "conditions not met"
                no_entry_why = (
                    f"{top['emoji']} {top['name']} scored {top['composite']:.0f}  →  "
                    f"{cond_summary}"
                )
            else:
                no_entry_why = (
                    f"best score {top['composite']:.0f} < min {MIN_ENTRY_SCORE}"
                )
        else:
            no_entry_why = "no strategies registered"

        top_sig = top.get("signal") if top else None
        return MetaRouterResult(
            regime=regime.value,
            regime_detail=regime_result.detail,
            adx=regime_result.adx,
            selected_strategy=top["name"] if top else "none",
            selected_emoji=top["emoji"] if top else "❓",
            signal=StrategySignal(
                should_enter=False,
                reason=(
                    f"[META: {regime.value.upper()}] No entry — "
                    f"{no_entry_why} | {vix_tag}"
                ),
            ),
            scores=candidates,
            top_conditions=top_sig.conditions if top_sig else [],
            reason=no_entry_why,
        )

    # ✅ Winner: highest composite score
    winner = entry_candidates[0]
    sig = winner["signal"]
    
    # 🐶 Add reversal/continuation context to signal reason
    rc_note = ""
    if rev_cont is not None:
        if rev_cont.recommendation == 'REVERSAL' and rev_cont.reversal_score > 60:
            rc_note = f" | ⚠️ REVERSAL detected ({rev_cont.reversal_score:.0f}/100)"
        elif rev_cont.recommendation == 'CONTINUATION' and rev_cont.continuation_score > 60:
            rc_note = f" | 🚀 CONTINUATION ({rev_cont.continuation_score:.0f}/100)"
    
    sig.reason = (
        f"[META: {regime.value.upper()}] "
        f"{winner['emoji']} {winner['name']} wins "
        f"| score={winner['composite']:.0f} "
        f"(conf={winner['confidence']:.0f}% "
        f"× regime={winner['regime_fit']} "
        f"× time={winner['time_mult']} "
        f"× vix={winner.get('vix_boost', 1.0)} "
        f"× rc={winner.get('rc_mult', 1.0)}) "
        f"| {vix_tag}{rc_note} | {sig.reason}"
    )
    return MetaRouterResult(
        regime=regime.value,
        regime_detail=regime_result.detail,
        adx=regime_result.adx,
        selected_strategy=winner["name"],
        selected_emoji=winner["emoji"],
        signal=sig,
        scores=candidates,
        top_conditions=sig.conditions,
        reason=sig.reason,
    )


def _empty_result(reason: str) -> MetaRouterResult:
    return MetaRouterResult(
        regime="unknown", regime_detail=reason, adx=0,
        selected_strategy="none", selected_emoji="❓",
        signal=StrategySignal(should_enter=False, reason=reason),
        scores=[], reason=reason,
    )
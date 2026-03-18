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
                  "momentum": 0.85, "trend": 0.9, "adaptive": 1.0}
    elif vix > _VIX_HIGH:
        # Fearful market → big gaps, real breakouts, fade the panic
        boosts = {"breakout": 1.25, "reversal": 1.15, "momentum": 1.1,
                  "scalping": 0.75, "trend": 1.0, "adaptive": 1.0}
    else:
        # Normal VIX → no VIX-based distortion
        boosts = {}
    return boosts.get(strategy_category, 1.0)


# ── Regime fit multipliers per strategy category ─────────────────
# Strategy categories → how well they fit each regime
_REGIME_FIT: dict[str, dict[str, float]] = {
    MarketRegime.TRENDING_UP:   {"trend": 1.4, "breakout": 1.3, "momentum": 1.2,
                                  "reversal": 0.6, "adaptive": 1.0, "scalping": 1.0},
    MarketRegime.TRENDING_DOWN: {"trend": 1.4, "breakout": 1.3, "momentum": 1.2,
                                  "reversal": 0.6, "adaptive": 1.0, "scalping": 1.0},
    MarketRegime.SIDEWAYS:      {"reversal": 1.4, "scalping": 1.3, "trend": 0.6,
                                  "breakout": 0.7, "momentum": 0.8, "adaptive": 1.0},
    MarketRegime.VOLATILE:      {"reversal": 1.3, "breakout": 1.4, "trend": 0.9,
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
    # Gap and Go is an early-session play — best at 9:15–10:30
    "gap_and_go": [
        (dt_time(9, 15), dt_time(9, 20), 2.0),   # 9:15 candle = prime entry
        (dt_time(9, 20), dt_time(10, 30), 1.5),  # still good
        (dt_time(10, 30), dt_time(15, 30), 0.0), # too late for gap play
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

    # Fetch VIX once per evaluate_all call (uses the 5-min cached value from app.py)
    try:
        from auto_trader import premium_estimate as _pe  # noqa: PLC0415
        _vix_cache = getattr(_pe, "_cache", {})
        current_vix = float(_vix_cache.get("vix", 16.0))
    except Exception:
        current_vix = 16.0  # sensible fallback

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
        v_boost   = _vix_category_boost(strat.category, current_vix)
        raw_conf  = signal.confidence or 0.0
        composite = raw_conf * r_fit * t_mult * v_boost

        candidates.append({
            "id":          strat.id,
            "name":        strat.name,
            "emoji":       strat.emoji,
            "category":    strat.category,
            "confidence":  round(raw_conf, 1),
            "regime_fit":  r_fit,
            "time_mult":   t_mult,
            "vix_boost":   v_boost,
            "composite":   round(composite, 1),
            "should_enter": signal.should_enter,
            "direction":   signal.direction,
            "signal":      signal,
            "error":       None,
        })

    # Sort by composite score descending
    candidates.sort(key=lambda x: x["composite"], reverse=True)

    return _consensus_pick(candidates, regime, regime_result, current_vix, df)


# ── Consensus thresholds (tune these if too many / too few trades) ─
# MIN_SCORE:       a strategy must score at least this to count as a vote
# MIN_VOTE_SCORE:  the winning direction's total must reach this
# MIN_RATIO:       winning direction must outvote the other by this factor
# MIN_AGREEING:    at least N strategiesvote the same direction
MIN_SCORE        = 60    # individual strategy minimum to count as a vote
MIN_VOTE_SCORE   = 100   # total directional vote score needed
MIN_RATIO        = 1.5   # e.g. LONG 180 vs SHORT 100 → ratio 1.8 ✅
MIN_AGREEING     = 2     # at least 2 strategies must agree


def _consensus_pick(
    candidates: list[dict],
    regime: MarketRegime,
    regime_result,
    current_vix: float,
    df: pd.DataFrame,
) -> MetaRouterResult:
    """Directional vote consensus.

    Instead of letting ONE strategy win, we tally scores by direction.
    Both LONG and SHORT voters must earn their way in:
      - individual score  >= MIN_SCORE      (no weak signals)
      - winning direction >= MIN_VOTE_SCORE (enough conviction)
      - winning / losing  >= MIN_RATIO      (clear majority)
      - at least           MIN_AGREEING strategies agree

    This prevents a single high-confidence contrarian strategy
    from overriding all the other evidence.
    """
    from strategy import Direction  # noqa: PLC0415

    vix_tag = f"VIX={current_vix:.1f}"

    # Only strategies that WANT to enter AND score high enough get a vote
    voters = [
        c for c in candidates
        if c["should_enter"]
        and c["time_mult"] > 0
        and c["composite"] >= MIN_SCORE
    ]

    long_voters  = [c for c in voters if c["direction"] == Direction.LONG]
    short_voters = [c for c in voters if c["direction"] == Direction.SHORT]

    long_score  = sum(c["composite"] for c in long_voters)
    short_score = sum(c["composite"] for c in short_voters)

    # Volume confirmation — avoid entering on thin candles
    vol_ok, vol_note = _volume_ok(df)

    def _no_entry(why: str) -> MetaRouterResult:
        top = candidates[0] if candidates else None
        return MetaRouterResult(
            regime=regime.value,
            regime_detail=regime_result.detail,
            adx=regime_result.adx,
            selected_strategy=top["name"] if top else "none",
            selected_emoji=top["emoji"] if top else "❓",
            signal=StrategySignal(
                should_enter=False,
                reason=(
                    f"[META: {regime.value.upper()}] {why} "
                    f"| LONG {long_score:.0f} ({len(long_voters)} strats) "
                    f"vs SHORT {short_score:.0f} ({len(short_voters)} strats) "
                    f"| {vix_tag}"
                ),
            ),
            scores=candidates,
            reason=why,
        )

    # Guard: no voters at all
    if not voters:
        return _no_entry("No strategy fired with score ≥ {MIN_SCORE}")

    # Guard: volume too low
    if not vol_ok:
        return _no_entry(f"Low volume — {vol_note}")

    # Determine winning direction
    if long_score >= short_score:
        win_dir, win_voters, win_score = Direction.LONG,  long_voters,  long_score
        los_score                       =                               short_score
    else:
        win_dir, win_voters, win_score = Direction.SHORT, short_voters, short_score
        los_score                       =                               long_score

    ratio = win_score / los_score if los_score > 0 else 999

    # Guard: not enough total conviction
    if win_score < MIN_VOTE_SCORE:
        return _no_entry(
            f"Consensus too weak — winning dir score {win_score:.0f} < {MIN_VOTE_SCORE}"
        )

    # Guard: not enough strategies agree
    if len(win_voters) < MIN_AGREEING:
        return _no_entry(
            f"Only {len(win_voters)} strat(s) agree on "
            f"{'LONG' if win_dir==Direction.LONG else 'SHORT'} — need {MIN_AGREEING}"
        )

    # Guard: ratio too low (contested signal — market unclear)
    if ratio < MIN_RATIO:
        return _no_entry(
            f"Contested — LONG {long_score:.0f} vs SHORT {short_score:.0f} "
            f"(ratio {ratio:.1f}× < {MIN_RATIO}×)"
        )

    # ✅ Consensus reached — build signal from the top-scoring voter
    top_voter = win_voters[0]  # highest composite in winning direction
    sig = top_voter["signal"]
    voter_names = ", ".join(
        f"{c['emoji']}{c['name']} ({c['composite']:.0f})"
        for c in win_voters
    )
    sig.reason = (
        f"[META: {regime.value.upper()}] CONSENSUS {'LONG' if win_dir==Direction.LONG else 'SHORT'} "
        f"| score {win_score:.0f} vs {los_score:.0f} (ratio {ratio:.1f}×) "
        f"| {len(win_voters)} agree: {voter_names} "
        f"| {vix_tag} | {vol_note}"
    )
    return MetaRouterResult(
        regime=regime.value,
        regime_detail=regime_result.detail,
        adx=regime_result.adx,
        selected_strategy=top_voter["name"],
        selected_emoji=top_voter["emoji"],
        signal=sig,
        scores=candidates,
        reason=sig.reason,
    )


# Minimum candle body as % of ATR to confirm conviction.
# Doji / spinning-top candles (tiny body) often signal indecision —
# entering on them is a coin flip regardless of what indicators say.
# Nifty 50 is a cash index (^NSEI) — Yahoo Finance never returns volume
# for it, so we use candle-body-strength as the conviction proxy instead.
MIN_BODY_ATR_PCT = 20  # body must be ≥ 20% of ATR  (0 = disabled)


def _volume_ok(df: pd.DataFrame) -> tuple[bool, str]:
    """Candle body strength check — replaces volume (unavailable on cash index).

    A candle whose body is < MIN_BODY_ATR_PCT% of ATR is a doji/indecision
    candle. Entering on those is low-conviction regardless of what the
    indicators say.

    Returns (ok, note) where note is shown in the event log.
    """
    if MIN_BODY_ATR_PCT <= 0 or len(df) < 15:
        return True, "body-check=off"

    import indicators as _ind  # noqa: PLC0415
    atr_series  = _ind.atr(df["high"], df["low"], df["close"], period=14)
    atr_val     = float(atr_series.iloc[-1])
    if atr_val <= 0:
        return True, "body-check=N/A"

    last        = df.iloc[-1]
    body        = abs(float(last["close"]) - float(last["open"]))
    body_pct    = body / atr_val * 100
    ok          = body_pct >= MIN_BODY_ATR_PCT
    candle_type = "bullish" if last["close"] >= last["open"] else "bearish"
    note        = f"body={body_pct:.0f}% of ATR ({candle_type})"
    return ok, note


def _empty_result(reason: str) -> MetaRouterResult:
    return MetaRouterResult(
        regime="unknown", regime_detail=reason, adx=0,
        selected_strategy="none", selected_emoji="❓",
        signal=StrategySignal(should_enter=False, reason=reason),
        scores=[], reason=reason,
    )


def _empty_result(reason: str) -> MetaRouterResult:
    return MetaRouterResult(
        regime="unknown", regime_detail=reason, adx=0,
        selected_strategy="none", selected_emoji="❓",
        signal=StrategySignal(should_enter=False, reason=reason),
        scores=[], reason=reason,
    )
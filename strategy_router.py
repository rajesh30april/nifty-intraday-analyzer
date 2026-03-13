"""Strategy Router - Smart Strategy Selector.

Detects market regime and routes to the best strategy:
- TRENDING  -> Trend-Following (EMA pullback)
- SIDEWAYS  -> Price Rejection (prev day levels)
- VOLATILE  -> Both evaluated, best confidence wins
"""

import pandas as pd
from dataclasses import dataclass

from strategy import StrategySignal, evaluate_vwap_breakout
from strategy_trend import evaluate_trend_follow
from market_regime import detect_regime, MarketRegime, RegimeResult


@dataclass
class RouterResult:
    """Output of the strategy router."""
    regime: RegimeResult
    selected_strategy: str
    signal: StrategySignal
    all_signals: dict  # strategy_name -> StrategySignal


def route_strategy(df: pd.DataFrame) -> RouterResult:
    """Detect market regime and select the best strategy.

    Priority:
    1. Detect regime (trending/sideways/volatile)
    2. Run the appropriate strategy
    3. In volatile markets, run both and pick highest confidence

    Args:
        df: OHLCV DataFrame with multi-day data.

    Returns:
        RouterResult with regime info, selected strategy, and signal.
    """
    if len(df) < 30:
        empty_signal = StrategySignal(
            should_enter=False, reason="Insufficient data",
        )
        regime = RegimeResult(
            regime=MarketRegime.SIDEWAYS, adx=0, atr_pct=0,
            trend_direction="flat", confidence=0,
            detail="Insufficient data",
        )
        return RouterResult(
            regime=regime,
            selected_strategy="none",
            signal=empty_signal,
            all_signals={},
        )

    # Step 1: Detect regime
    regime = detect_regime(df)

    # Step 2: Evaluate strategies based on regime
    all_signals = {}

    if regime.regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN):
        # Trending -> primary: trend-follow, secondary: rejection
        trend_signal = evaluate_trend_follow(df)
        all_signals["trend_follow"] = trend_signal

        # Also run rejection as backup (sometimes trend reverses at levels)
        rejection_signal = evaluate_vwap_breakout(df)
        all_signals["rejection"] = rejection_signal

        # Prefer trend-follow, but if rejection has ALL conditions met
        # with higher confidence, use it
        if trend_signal.should_enter:
            selected = "trend_follow"
            signal = trend_signal
        elif rejection_signal.should_enter:
            selected = "rejection"
            signal = rejection_signal
        else:
            # Neither fires — use trend-follow (more conditions visible)
            selected = "trend_follow"
            signal = trend_signal

    elif regime.regime == MarketRegime.SIDEWAYS:
        # Sideways -> primary: rejection at levels
        rejection_signal = evaluate_vwap_breakout(df)
        all_signals["rejection"] = rejection_signal

        # Also run trend-follow as backup
        trend_signal = evaluate_trend_follow(df)
        all_signals["trend_follow"] = trend_signal

        if rejection_signal.should_enter:
            selected = "rejection"
            signal = rejection_signal
        elif trend_signal.should_enter:
            selected = "trend_follow"
            signal = trend_signal
        else:
            selected = "rejection"
            signal = rejection_signal

    else:  # VOLATILE
        # Run both, pick the one with highest confidence
        trend_signal = evaluate_trend_follow(df)
        rejection_signal = evaluate_vwap_breakout(df)
        all_signals["trend_follow"] = trend_signal
        all_signals["rejection"] = rejection_signal

        if trend_signal.should_enter and rejection_signal.should_enter:
            # Both fire — pick higher confidence
            if trend_signal.confidence >= rejection_signal.confidence:
                selected = "trend_follow"
                signal = trend_signal
            else:
                selected = "rejection"
                signal = rejection_signal
        elif trend_signal.should_enter:
            selected = "trend_follow"
            signal = trend_signal
        elif rejection_signal.should_enter:
            selected = "rejection"
            signal = rejection_signal
        else:
            # Neither fires — show higher confidence one
            if trend_signal.confidence >= rejection_signal.confidence:
                selected = "trend_follow"
                signal = trend_signal
            else:
                selected = "rejection"
                signal = rejection_signal

    # Enrich reason with regime info
    signal.reason = (
        f"[{regime.regime.value.upper()}] {selected.upper()}: "
        f"{signal.reason}"
    )

    return RouterResult(
        regime=regime,
        selected_strategy=selected,
        signal=signal,
        all_signals=all_signals,
    )

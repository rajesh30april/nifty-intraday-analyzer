"""Smart Router — Auto-selects the best strategy based on market regime.

Now powered by the Meta Router which evaluates ALL registered strategies
before each trade and picks the highest-scoring one.
"""

import pandas as pd
from strategy import StrategySignal
from strategies.registry import register, StrategyInfo


def evaluate_smart_router(df: pd.DataFrame) -> StrategySignal:
    """Evaluate all strategies and return the best signal."""
    # Late import to avoid circular deps
    from strategy_meta_router import evaluate_all
    result = evaluate_all(df)
    return result.signal


register(StrategyInfo(
    id="smart_router",
    name="Smart Router (Auto)",
    emoji="🧠",
    description=(
        "Evaluates EVERY registered strategy before each candle and "
        "picks the highest-scoring one using: confidence × regime fit × time bonus. "
        "OCF gets priority at 9:20, ORB after 9:30, trend strategies in trending markets."
    ),
    category="adaptive",
    difficulty="beginner",
    market_condition="All market conditions — adapts automatically.",
    evaluate=evaluate_smart_router,
    entry_rules=[
        "Step 1: Detect regime (ADX, ATR, EMA slope)",
        "Step 2: Run EVERY strategy and get confidence score",
        "Step 3: Multiply by regime fit (trending → trend bonus, sideways → reversal bonus)",
        "Step 4: Multiply by time bonus (OCF at 9:20, ORB at 9:30+)",
        "Step 5: Pick highest composite score that has all conditions met",
    ],
    exit_rules=[
        "Stop-loss and target set by the selected strategy",
        "Trailing SL managed by the backtester",
    ],
    risk_tips=[
        "OCF fires only at 9:20 — first 5 minutes after open",
        "On strong trend days, trend strategies dominate",
        "On choppy days, reversal strategies take over",
        "If no strategy has all conditions met — no trade taken",
    ],
    pros=[
        "Evaluates ALL strategies, not just 2",
        "Transparent scoring — you can see WHY each strategy was picked",
        "OCF, ORB, VWAP, Trend, Reversal all compete fairly",
        "Regime-aware + time-aware",
    ],
    cons=[
        "Slightly slower (evaluates all strategies each candle)",
        "Strategy scores depend on regime detection quality",
    ],
    example_scenario=(
        "9:20 AM. 9:15 candle was big (30pts). OCF scores 80 (conf=40 × regime=1.3 × time=2.0). "
        "Trend Follow scores 45. ORB scores 0 (too early). "
        "→ Meta Router picks OCF. Trade SHORT."
    ),
))
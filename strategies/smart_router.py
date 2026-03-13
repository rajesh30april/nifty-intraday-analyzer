"""Smart Router — Auto-selects the best strategy based on market regime.

This is the default strategy that uses the existing router logic.
"""

import pandas as pd
from strategy import StrategySignal
from strategy_router import route_strategy
from strategies.registry import register, StrategyInfo


def evaluate_smart_router(df: pd.DataFrame) -> StrategySignal:
    """Auto-detect regime and pick the best strategy."""
    result = route_strategy(df)
    return result.signal


register(StrategyInfo(
    id="smart_router",
    name="Smart Router (Auto)",
    emoji="🧠",
    description=(
        "Automatically detects the market regime (trending/sideways/volatile) "
        "and picks the best strategy. Uses Trend Follow in trends and "
        "Price Rejection in sideways markets."
    ),
    category="adaptive",
    difficulty="beginner",
    market_condition="All market conditions — adapts automatically.",
    evaluate=evaluate_smart_router,
    entry_rules=[
        "Detects market regime using ADX and ATR",
        "Trending (ADX>25): Uses Trend Follow (EMA pullback)",
        "Sideways (ADX<20): Uses Price Rejection at key levels",
        "Volatile: Runs both, picks highest confidence",
    ],
    exit_rules=[
        "Inherits exit rules from the selected strategy",
        "Stop-loss and target set by the active strategy",
    ],
    risk_tips=[
        "The router adds a regime check but can sometimes misclassify",
        "In transition periods (ADX 20-25), signals may be less reliable",
        "Good as a baseline before trying individual strategies",
    ],
    pros=[
        "No need to manually switch strategies",
        "Adapts to changing market conditions",
        "Reduces wrong-strategy-wrong-market risk",
    ],
    cons=[
        "Black box — harder to know which strategy triggered",
        "May switch strategies mid-day causing confusion",
        "Not optimized for any single regime",
    ],
    example_scenario=(
        "Market opens trending (ADX=35, EMA-9 > EMA-21). Router picks Trend Follow. "
        "At noon, market becomes range-bound (ADX drops to 18). Router switches "
        "to Price Rejection. All handled automatically."
    ),
))

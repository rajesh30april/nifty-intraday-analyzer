"""Register existing strategies into the registry."""

from strategies.registry import register, StrategyInfo
from strategy import evaluate_vwap_breakout
from strategy_trend import evaluate_trend_follow

# ── 1. Price Rejection at Previous Day Levels ────────────────────
register(StrategyInfo(
    id="rejection",
    name="Price Rejection",
    emoji="🔄",
    description=(
        "Enters when price rejects a previous day's key level "
        "(close/open) with a long wick candle, body shrink, and "
        "confirmation candle. Classic support/resistance bounce play."
    ),
    category="reversal",
    difficulty="intermediate",
    market_condition="Sideways / Range-bound markets with clear S/R levels.",
    evaluate=evaluate_vwap_breakout,
    entry_rules=[
        "Rejection candle's high/low is near previous day close or open (within 50 pts)",
        "Rejection candle shows a long wick (≥45% of candle range)",
        "Rejection candle's body is smaller than the previous candle (exhaustion)",
        "Current candle confirms by closing in the rejection direction",
        "Not within first 15 minutes of market open",
    ],
    exit_rules=[
        "Stop-loss: Below/above the rejection candle wick",
        "Target: 2x the stop-loss distance (1:2 R:R)",
        "Trailing stop-loss activates after 1R move in favor",
        "Force exit at 3:15 PM (no overnight holds)",
    ],
    risk_tips=[
        "Only trade when the rejection is at a PROVEN level (prev day close/open)",
        "Avoid trading if the body doesn't shrink — that means momentum is strong",
        "Wait for the confirmation candle — never enter on the rejection candle itself",
    ],
    pros=[
        "Works very well in sideways/ranging markets",
        "Clear entry, stop-loss, and target levels",
        "High win rate when conditions align (all 5 conditions met)",
    ],
    cons=[
        "Misses big trending moves (fights the trend)",
        "Fewer signals — may only get 1-2 per day",
        "Fails badly in breakout/volatile sessions",
    ],
    example_scenario=(
        "Nifty closed at 22,500 yesterday. Today it dips to 22,480 and forms "
        "a hammer candle with a long lower wick (70% of range). The body is "
        "smaller than the previous candle. Next candle closes green above 22,500. "
        "\u2192 BUY with SL at 22,460, Target 22,580."
    ),
))

# ── 2. Trend Follow (EMA Pullback) ────────────────────────────
register(StrategyInfo(
    id="trend_follow",
    name="Trend Follow (EMA Pullback)",
    emoji="🏄",
    description=(
        "Rides the trend by entering when price pulls back to the "
        "9-EMA in a strong trending market (ADX > 25). The safest "
        "way to trade with the trend."
    ),
    category="trend",
    difficulty="beginner",
    market_condition="Trending markets with ADX > 25.",
    evaluate=evaluate_trend_follow,
    entry_rules=[
        "EMA-9 is above EMA-21 (uptrend) or below (downtrend)",
        "ADX > 25 (confirms a strong trend exists)",
        "Price pulls back to within 0.10% of EMA-9",
        "Current candle bounces off EMA-9 in the trend direction",
        "Not within first 15 minutes of market open",
    ],
    exit_rules=[
        "Stop-loss: Below/above the EMA-21 line",
        "Target: 2x stop-loss (1:2 R:R)",
        "Trailing SL follows EMA-9",
        "Exit if ADX drops below 20 (trend dying)",
    ],
    risk_tips=[
        "Only trade in the direction of EMA-9 > EMA-21 — never counter-trend",
        "ADX > 25 is non-negotiable. No trend = no trade",
        "Best pullbacks touch EMA-9 and immediately bounce — no lingering",
    ],
    pros=[
        "Trades with the trend (highest probability)",
        "Clear, objective rules based on EMAs",
        "Works on any timeframe",
    ],
    cons=[
        "Gets whipsawed in sideways markets",
        "May enter late in the trend",
        "Requires patience for pullbacks",
    ],
    example_scenario=(
        "Nifty is in an uptrend — EMA-9 (22,550) is above EMA-21 (22,480), "
        "ADX is 32. Price pulls back from 22,600 to 22,555 (near EMA-9). "
        "A green candle forms closing above EMA-9. "
        "\u2192 BUY with SL at 22,480 (EMA-21), Target 22,690."
    ),
))

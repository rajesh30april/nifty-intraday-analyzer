"""Strategy Registry — Central catalog of all trading strategies.

Single source of truth for strategy metadata, evaluation functions,
and educational content. The backtester and UI both pull from here.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from strategy import StrategySignal


@dataclass
class StrategyInfo:
    """Everything about a strategy in one place."""
    id: str
    name: str
    emoji: str
    description: str
    category: str          # 'trend', 'reversal', 'breakout', 'momentum'
    difficulty: str        # 'beginner', 'intermediate', 'advanced'
    market_condition: str  # when to use it
    evaluate: Callable[[pd.DataFrame], StrategySignal]
    entry_rules: list[str] = field(default_factory=list)
    exit_rules: list[str] = field(default_factory=list)
    risk_tips: list[str] = field(default_factory=list)
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    example_scenario: str = ""


# ── Registry ─────────────────────────────────────────────────────

_STRATEGIES: dict[str, StrategyInfo] = {}


def register(info: StrategyInfo) -> StrategyInfo:
    """Register a strategy. Returns the info for convenience."""
    _STRATEGIES[info.id] = info
    return info


def get(strategy_id: str) -> StrategyInfo | None:
    """Look up a strategy by ID."""
    return _STRATEGIES.get(strategy_id)


def all_strategies() -> list[StrategyInfo]:
    """Return all registered strategies in order."""
    return list(_STRATEGIES.values())


def ids() -> list[str]:
    """Return all strategy IDs."""
    return list(_STRATEGIES.keys())


def to_json() -> list[dict]:
    """Serialize all strategies for the frontend."""
    return [
        {
            "id": s.id,
            "name": s.name,
            "emoji": s.emoji,
            "description": s.description,
            "category": s.category,
            "difficulty": s.difficulty,
            "market_condition": s.market_condition,
            "entry_rules": s.entry_rules,
            "exit_rules": s.exit_rules,
            "risk_tips": s.risk_tips,
            "pros": s.pros,
            "cons": s.cons,
            "example_scenario": s.example_scenario,
        }
        for s in all_strategies()
    ]

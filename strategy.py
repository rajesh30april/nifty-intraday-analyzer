"""Trading Strategy Definitions.

Defines entry/exit conditions for automated trading.
Each strategy is a set of rules evaluated against live market data.

Currently implements:
- VWAP Breakout Strategy (Nifty options)
"""

import pandas as pd
from dataclasses import dataclass, field
from enum import Enum

import indicators as ind


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass
class StrategyCondition:
    """A single condition that must be met for entry."""
    name: str
    met: bool
    detail: str
    weight: float = 1.0  # importance (for partial scoring)


@dataclass
class StrategySignal:
    """Output of strategy evaluation."""
    should_enter: bool
    direction: Direction | None = None
    confidence: float = 0.0  # 0-100%
    conditions: list[StrategyCondition] = field(default_factory=list)
    strike_offset: int = 0  # ATM +/- N strikes
    reason: str = ""


def evaluate_vwap_breakout(
    df: pd.DataFrame,
    min_volume_ratio: float = 1.2,
    ema_period: int = 9,
) -> StrategySignal:
    """VWAP Breakout Strategy.

    Entry conditions (ALL must be met):
    1. Price above/below VWAP (direction filter)
    2. 5-min candle closes above/below EMA (momentum confirmation)
    3. Volume > 1.2x average (volume confirmation)
    4. Not within first 15 mins of market open (avoid noise)
    5. ADX > 20 (trend exists, not choppy)

    Args:
        df: OHLCV DataFrame with DatetimeIndex.
        min_volume_ratio: Minimum volume vs average ratio.
        ema_period: EMA period for momentum confirmation.

    Returns:
        StrategySignal with entry decision and conditions.
    """
    conditions = []
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    price = float(close.iloc[-1])

    # Skip if insufficient data
    if len(df) < 30:
        return StrategySignal(
            should_enter=False,
            reason="Insufficient data (need 30+ candles)",
        )

    # 1. VWAP position
    vwap_val = float(ind.vwap(high, low, close, volume).iloc[-1])
    above_vwap = price > vwap_val
    vwap_dist_pct = abs(price - vwap_val) / vwap_val * 100

    conditions.append(StrategyCondition(
        name="VWAP Position",
        met=vwap_dist_pct > 0.05,  # at least 0.05% away from VWAP
        detail=f"Price {'above' if above_vwap else 'below'} VWAP "
               f"({vwap_dist_pct:.2f}% away)",
    ))

    # 2. EMA confirmation
    ema_val = float(ind.ema(close, ema_period).iloc[-1])
    if above_vwap:
        ema_ok = price > ema_val  # Bullish: price above EMA
    else:
        ema_ok = price < ema_val  # Bearish: price below EMA

    conditions.append(StrategyCondition(
        name=f"EMA-{ema_period} Confirmation",
        met=ema_ok,
        detail=f"Price {'above' if price > ema_val else 'below'} "
               f"EMA-{ema_period} ({ema_val:.1f})",
    ))

    # 3. Volume confirmation
    has_volume = volume.sum() > 0
    if has_volume:
        vol_ratio = float(volume.iloc[-1] / volume.mean())
        vol_ok = vol_ratio >= min_volume_ratio
        conditions.append(StrategyCondition(
            name="Volume Confirmation",
            met=vol_ok,
            detail=f"Volume ratio {vol_ratio:.1f}x "
                   f"({'OK' if vol_ok else f'need {min_volume_ratio}x'})",
        ))
    else:
        # Index data has no volume — skip this condition
        conditions.append(StrategyCondition(
            name="Volume Confirmation",
            met=True,  # pass by default for index
            detail="Volume N/A (index data) — skipped",
            weight=0.0,  # don't count towards scoring
        ))

    # 4. Not in first 15 minutes (avoid opening noise)
    last_time = df.index[-1]
    market_open = last_time.replace(hour=9, minute=15, second=0)
    minutes_since_open = (last_time - market_open).total_seconds() / 60
    time_ok = minutes_since_open >= 15

    conditions.append(StrategyCondition(
        name="Opening Filter",
        met=time_ok,
        detail=f"{minutes_since_open:.0f} mins since open "
               f"({'OK' if time_ok else 'too early, wait 15 min'})",
    ))

    # 5. ADX trend strength
    adx_data = ind.adx(high, low, close)
    adx_val = float(adx_data["adx"].iloc[-1])
    adx_ok = adx_val > 20

    conditions.append(StrategyCondition(
        name="ADX Trend Strength",
        met=adx_ok,
        detail=f"ADX={adx_val:.0f} "
               f"({'trending' if adx_ok else 'choppy, avoid'})",
    ))

    # Score
    weighted_conditions = [c for c in conditions if c.weight > 0]
    total_weight = sum(c.weight for c in weighted_conditions)
    met_weight = sum(c.weight for c in weighted_conditions if c.met)
    confidence = (met_weight / total_weight * 100) if total_weight > 0 else 0
    all_met = all(c.met for c in weighted_conditions)

    direction = Direction.LONG if above_vwap else Direction.SHORT

    return StrategySignal(
        should_enter=all_met,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        strike_offset=0,  # ATM
        reason=(
            f"{'ALL' if all_met else 'NOT all'} conditions met for "
            f"{direction.value.upper()} entry ({confidence:.0f}% confidence)"
        ),
    )

"""Trading Strategy Definitions.

Defines entry/exit conditions for automated trading.

Currently implements:
- Price Rejection at Previous Day Levels
  (prev day close/open + rejection candle + body shrink + volume)
"""

import pandas as pd
import numpy as np
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
    weight: float = 1.0


@dataclass
class StrategySignal:
    """Output of strategy evaluation."""
    should_enter: bool
    direction: Direction | None = None
    confidence: float = 0.0
    conditions: list[StrategyCondition] = field(default_factory=list)
    strike_offset: int = 0
    reason: str = ""


# ── Helpers ──────────────────────────────────────────────────────

def _candle_body(open_: float, close: float) -> float:
    """Absolute body size of a candle."""
    return abs(close - open_)


def _candle_range(high: float, low: float) -> float:
    """Total range of a candle (high - low)."""
    return high - low


def _upper_wick(open_: float, close: float, high: float) -> float:
    """Upper wick length."""
    return high - max(open_, close)


def _lower_wick(open_: float, close: float, low: float) -> float:
    """Lower wick length."""
    return min(open_, close) - low


def _get_prev_day_levels(df: pd.DataFrame) -> dict | None:
    """Extract previous trading day's open and close.

    Works with multi-day intraday DataFrames.
    Returns {open, close, high, low} of the previous day.
    """
    dates = df.index.normalize().unique()
    if len(dates) < 2:
        return None

    prev_date = dates[-2]
    prev_day = df[df.index.normalize() == prev_date]
    if prev_day.empty:
        return None

    return {
        "open": float(prev_day["open"].iloc[0]),
        "close": float(prev_day["close"].iloc[-1]),
        "high": float(prev_day["high"].max()),
        "low": float(prev_day["low"].min()),
    }


# ── Main Strategy ────────────────────────────────────────────────

def evaluate_vwap_breakout(
    df: pd.DataFrame,
    proximity_points: float = 150.0,   # was 50 — too tight for Nifty (moved 50pts in 1 candle)
    wick_ratio_min: float = 0.38,      # was 0.45 — real rejections show 35-40% wicks
    body_shrink_ratio: float = 0.80,
    min_volume_ratio: float = 1.0,
) -> StrategySignal:
    """Price Rejection at Previous Day Close/Open with Confirmation.

    Entry conditions (ALL must be met):
    1. PREVIOUS candle's high/low is near prev day close/open
    2. PREVIOUS candle shows rejection (long wick >= 45% of range)
    3. PREVIOUS candle body is smaller than the one before it
    4. CURRENT candle CONFIRMS by closing in rejection direction
       (bullish rejection -> current closes GREEN above prev close)
       (bearish rejection -> current closes RED below prev close)
    5. Not within first 15 mins of market open

    The KEY difference: we don't enter on the rejection candle itself.
    We wait ONE candle for confirmation, then enter.
    """
    conditions = []

    if len(df) < 30:
        return StrategySignal(
            should_enter=False,
            reason="Insufficient data (need 30+ candles)",
        )

    # We need 3 candles: prev-prev (for body compare), rejection (prev), confirmation (curr)
    curr = df.iloc[-1]       # confirmation candle (current)
    rej = df.iloc[-2]        # rejection candle (previous)
    pre_rej = df.iloc[-3]    # candle before rejection (for body compare)

    # Confirmation candle (current)
    c_open = float(curr["open"])
    c_close = float(curr["close"])
    c_high = float(curr["high"])
    c_low = float(curr["low"])
    c_volume = float(curr["volume"])
    c_body = _candle_body(c_open, c_close)
    c_is_green = c_close > c_open
    c_is_red = c_close < c_open

    # Rejection candle (previous)
    r_open = float(rej["open"])
    r_close = float(rej["close"])
    r_high = float(rej["high"])
    r_low = float(rej["low"])
    r_body = _candle_body(r_open, r_close)
    r_range = _candle_range(r_high, r_low)
    r_upper_wick = _upper_wick(r_open, r_close, r_high)
    r_lower_wick = _lower_wick(r_open, r_close, r_low)

    # Pre-rejection candle (for body shrink comparison)
    pr_body = _candle_body(float(pre_rej["open"]), float(pre_rej["close"]))

    price = c_close

    # ── 1. Near Previous Day Close or Open ───────────────────
    prev_day = _get_prev_day_levels(df)
    if prev_day is None:
        return StrategySignal(
            should_enter=False,
            reason="Need at least 2 days of data for prev day levels",
        )

    prev_close = prev_day["close"]
    prev_open = prev_day["open"]

    # Check if REJECTION candle HIGH or LOW touches the prev day level
    dist_h_pc = abs(r_high - prev_close)
    dist_l_pc = abs(r_low - prev_close)
    dist_h_po = abs(r_high - prev_open)
    dist_l_po = abs(r_low - prev_open)

    min_dist_close = min(dist_h_pc, dist_l_pc)
    min_dist_open = min(dist_h_po, dist_l_po)

    nearest_level = "Prev Close" if min_dist_close <= min_dist_open else "Prev Open"
    nearest_dist = min(min_dist_close, min_dist_open)
    nearest_price = prev_close if min_dist_close <= min_dist_open else prev_open
    near_level = nearest_dist <= proximity_points

    conditions.append(StrategyCondition(
        name="Near Prev Day Level",
        met=near_level,
        detail=(
            f"{nearest_dist:.1f} pts from {nearest_level} ({nearest_price:.1f}) "
            f"{'< ' + str(proximity_points) + ' OK' if near_level else '> ' + str(proximity_points) + ' too far'}"
        ),
    ))

    # ── 2. Rejection Candle (long wick on REJECTION candle) ────
    is_above_level = price > nearest_price
    is_below_level = price < nearest_price

    if r_range < 0.01:  # flat rejection candle
        wick_ok = False
        rejection_dir = None
        wick_detail = "Flat rejection candle, no range"
    else:
        lower_wick_ratio = r_lower_wick / r_range
        upper_wick_ratio = r_upper_wick / r_range

        # Bullish rejection: long lower wick (tried down, rejected up)
        bullish_rejection = lower_wick_ratio >= wick_ratio_min
        # Bearish rejection: long upper wick (tried up, rejected down)
        bearish_rejection = upper_wick_ratio >= wick_ratio_min

        if bullish_rejection and not bearish_rejection:
            wick_ok = True
            rejection_dir = Direction.LONG
            wick_detail = (
                f"Lower wick {lower_wick_ratio:.0%} of range "
                f"(buyers rejected downside)"
            )
        elif bearish_rejection and not bullish_rejection:
            wick_ok = True
            rejection_dir = Direction.SHORT
            wick_detail = (
                f"Upper wick {upper_wick_ratio:.0%} of range "
                f"(sellers rejected upside)"
            )
        elif bullish_rejection and bearish_rejection:
            # Doji-like candle — pick direction based on position vs level
            if is_below_level:
                wick_ok = True
                rejection_dir = Direction.LONG
                wick_detail = (
                    f"Doji near support — favoring LONG "
                    f"(lower wick {lower_wick_ratio:.0%})"
                )
            elif is_above_level:
                wick_ok = True
                rejection_dir = Direction.SHORT
                wick_detail = (
                    f"Doji near resistance — favoring SHORT "
                    f"(upper wick {upper_wick_ratio:.0%})"
                )
            else:
                wick_ok = False
                rejection_dir = None
                wick_detail = "Doji exactly at level — no clear direction"
        else:
            wick_ok = False
            rejection_dir = None
            wick_detail = (
                f"No rejection — upper wick {upper_wick_ratio:.0%}, "
                f"lower wick {lower_wick_ratio:.0%} (need {wick_ratio_min:.0%})"
            )

    conditions.append(StrategyCondition(
        name="Rejection Candle",
        met=wick_ok,
        detail=wick_detail,
    ))

    # ── 3. Body Shrink (rejection candle body < pre-rejection body)
    if pr_body < 0.01:
        body_ok = False
        body_detail = "Pre-rejection candle flat — no comparison"
    else:
        shrink_ratio = r_body / pr_body
        body_ok = shrink_ratio <= body_shrink_ratio
        body_detail = (
            f"Rejection body {r_body:.1f} vs prior {pr_body:.1f} "
            f"(ratio {shrink_ratio:.2f} "
            f"{'< ' + str(body_shrink_ratio) + ' OK' if body_ok else '> ' + str(body_shrink_ratio) + ' too big'})"
        )

    conditions.append(StrategyCondition(
        name="Body Exhaustion",
        met=body_ok,
        detail=body_detail,
    ))

    # ── 4. Confirmation Candle (current candle confirms rejection) ──
    #   Bullish rejection → current candle must close GREEN and above rejection close
    #   Bearish rejection → current candle must close RED and below rejection close
    if rejection_dir == Direction.LONG:
        confirm_ok = c_is_green and c_close > r_close
        confirm_detail = (
            f"Confirm LONG: curr {'GREEN' if c_is_green else 'RED'} "
            f"close {c_close:.1f} {'>' if c_close > r_close else '<='} "
            f"rejection close {r_close:.1f}"
        )
    elif rejection_dir == Direction.SHORT:
        confirm_ok = c_is_red and c_close < r_close
        confirm_detail = (
            f"Confirm SHORT: curr {'RED' if c_is_red else 'GREEN'} "
            f"close {c_close:.1f} {'<' if c_close < r_close else '>='} "
            f"rejection close {r_close:.1f}"
        )
    else:
        confirm_ok = False
        confirm_detail = "No rejection direction to confirm"

    conditions.append(StrategyCondition(
        name="Confirmation Candle",
        met=confirm_ok,
        detail=confirm_detail,
    ))

    # ── 5. Opening Filter ────────────────────────────────────
    last_time = df.index[-1]
    market_open = last_time.replace(hour=9, minute=15, second=0)
    mins_since = (last_time - market_open).total_seconds() / 60
    time_ok = mins_since >= 15

    conditions.append(StrategyCondition(
        name="Opening Filter",
        met=time_ok,
        detail=(
            f"{mins_since:.0f} mins since open "
            f"({'OK' if time_ok else 'too early'})"
        ),
    ))

    # ── Score & Decision ─────────────────────────────────────
    weighted = [c for c in conditions if c.weight > 0]
    total_w = sum(c.weight for c in weighted)
    met_w = sum(c.weight for c in weighted if c.met)
    confidence = (met_w / total_w * 100) if total_w > 0 else 0
    all_met = all(c.met for c in weighted)

    direction = rejection_dir

    return StrategySignal(
        should_enter=all_met and direction is not None,
        direction=direction,
        confidence=confidence,
        conditions=conditions,
        strike_offset=0,
        reason=(
            f"{'ALL' if all_met else 'NOT all'} conditions met"
            f"{' for ' + direction.value.upper() if direction else ''}"
            f" entry ({confidence:.0f}% confidence)"
            f" | Near {nearest_level} ({nearest_price:.1f})"
        ),
    )

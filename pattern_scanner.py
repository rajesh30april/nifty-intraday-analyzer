"""Pattern Scanner — scans 60 days of Nifty 50 data across multiple
timeframes using a sliding window, returning all detected chart patterns.

Timeframe guide for intraday Nifty 50:
  5m  → Scalping / fast momentum (flags, double tops within a single day)
  15m → Best balance for intraday Nifty 50 (cleaner, fewer false signals) ✅
  1h  → Multi-day swing context (bigger setups spanning several sessions)
"""

from __future__ import annotations

import pandas as pd
from dataclasses import dataclass, field
from data_fetcher import fetch_intraday_data
from pattern_detector import detect_all_patterns


# ── Timeframe metadata ────────────────────────────────────────────────────────

TIMEFRAME_META: dict[str, dict] = {
    "5m": {
        "label": "5 Min",
        "resample": None,
        "window": 150,       # ~2 trading days of 5m candles
        "step": 50,          # slide forward 50 candles each iteration
        "best_for": "Scalping · fast momentum · intraday reversals",
        "signal_quality": "Medium — more patterns, more noise",
        "recommended": False,
    },
    "15m": {
        "label": "15 Min",
        "resample": "15min",
        "window": 80,        # ~5-6 trading days of 15m candles
        "step": 25,
        "best_for": "Intraday swing · trend continuation · Nifty 50 ideal",
        "signal_quality": "High — fewer false signals ✅ BEST for intraday",
        "recommended": True,
    },
    "1h": {
        "label": "1 Hour",
        "resample": "1h",
        "window": 40,        # ~8 trading days of 1h candles
        "step": 10,
        "best_for": "Multi-day setups · swing context",
        "signal_quality": "Highest — very clean but fewer patterns",
        "recommended": False,
    },
}

PATTERN_EMOJIS: dict[str, str] = {
    "Double Top":              "🔻",
    "Double Bottom":           "🔺",
    "Head & Shoulders":        "⛰️",
    "Inv. Head & Shoulders":   "🏔️",
    "Triple Top":              "📉",
    "Triple Bottom":           "📈",
    "Bull Flag":               "🚩",
    "Bear Flag":               "🏴",
    "Rising Wedge":            "📐",
    "Falling Wedge":           "📐",
    "Ascending Triangle":      "△",
    "Descending Triangle":     "▽",
    "Uptrend":                 "📈",
    "Downtrend":               "📉",
    "Sideways":                "➡️",
    "Breakout Up":             "🚀",
    "Bullish Engulfing":       "🕯️",
    "Bearish Engulfing":       "🕯️",
    "Morning Star":            "⭐",
    "Evening Star":            "⭐",
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ScannedPattern:
    """A detected pattern from the historical scan."""
    name: str
    pattern_type: str       # reversal / continuation / structure
    bias: str               # bullish / bearish / neutral
    confidence: float
    timeframe: str
    start_time: str
    end_time: str
    description: str
    key_levels: dict
    emoji: str = "📊"
    duration_candles: int = 0
    date_label: str = ""
    end_date: str = ""      # sortable YYYY-MM-DD


@dataclass
class ScanResult:
    """Complete result of a pattern scan run."""
    patterns: list[ScannedPattern] = field(default_factory=list)
    total: int = 0
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    by_timeframe: dict = field(default_factory=dict)
    by_type: dict = field(default_factory=dict)
    by_name: dict = field(default_factory=dict)
    trading_days: int = 0
    date_range: str = ""
    error: str = ""


# ── Internal helpers ──────────────────────────────────────────────────────────

def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df.resample(rule).agg({
        "open": "first", "high": "max",
        "low": "min",   "close": "last", "volume": "sum",
    }).dropna(subset=["open", "close"])


def _dedup_key(p: ScannedPattern) -> str:
    """Unique key for deduplication — same pattern at same time = duplicate."""
    ts = (p.end_time or p.start_time or "")[:13]  # up to the hour
    return f"{p.name}|{p.timeframe}|{ts}"


def _scan_tf(df: pd.DataFrame, tf: str) -> list[ScannedPattern]:
    """Run sliding-window pattern detection on a single timeframe df."""
    meta = TIMEFRAME_META[tf]
    window = meta["window"]
    step   = meta["step"]
    n = len(df)

    seen: set[str] = set()
    patterns: list[ScannedPattern] = []

    # Slide window through the full history
    starts = list(range(0, max(1, n - window + 1), step))
    # Always include a window ending at the last candle
    if n >= window and (n - window) not in starts:
        starts.append(n - window)

    for start in starts:
        end = min(start + window, n)
        chunk = df.iloc[start:end]
        if len(chunk) < 20:
            continue

        try:
            result = detect_all_patterns(chunk, timeframe=tf)
        except Exception:
            continue

        for p in result.get("patterns", []):
            emoji = PATTERN_EMOJIS.get(p.name, "📊")
            duration = (p.end_idx - p.start_idx) if p.end_idx and p.start_idx else 0

            date_label, end_date = "", ""
            try:
                dt = pd.to_datetime(p.end_time or p.start_time)
                date_label = dt.strftime("%d %b %Y")
                end_date   = dt.strftime("%Y-%m-%d")
            except Exception:
                pass

            sp = ScannedPattern(
                name=p.name,
                pattern_type=p.pattern_type,
                bias=p.bias,
                confidence=round(p.confidence, 2),
                timeframe=tf,
                start_time=p.start_time or "",
                end_time=p.end_time or "",
                description=p.description,
                key_levels=p.key_levels or {},
                emoji=emoji,
                duration_candles=duration,
                date_label=date_label,
                end_date=end_date,
            )

            key = _dedup_key(sp)
            if key not in seen:
                seen.add(key)
                patterns.append(sp)

    return patterns


# ── Public API ────────────────────────────────────────────────────────────────

def scan_patterns(
    timeframes: list[str] | None = None,
    period: str = "60d",
) -> ScanResult:
    """Scan Nifty 50 history for chart patterns across timeframes.

    Args:
        timeframes: Timeframes to scan. Defaults to ["5m", "15m", "1h"].
        period: Yahoo Finance period ("60d", "30d", etc.).

    Returns:
        ScanResult with all detected unique patterns.
    """
    if timeframes is None:
        timeframes = ["5m", "15m", "1h"]

    result = ScanResult()

    try:
        df5 = fetch_intraday_data(period=period, interval="5m")
        if df5 is None or df5.empty:
            result.error = "No data returned"
            return result

        days = df5.index.normalize().unique()
        result.trading_days = len(days)
        if len(days) > 0:
            result.date_range = (
                f"{days[0].date().strftime('%d %b %Y')} → "
                f"{days[-1].date().strftime('%d %b %Y')}"
            )

        all_patterns: list[ScannedPattern] = []

        for tf in timeframes:
            meta = TIMEFRAME_META.get(tf)
            if meta is None:
                continue
            resample_rule = meta.get("resample")
            df = _resample(df5, resample_rule) if resample_rule else df5.copy()
            if len(df) < 30:
                continue
            found = _scan_tf(df, tf)
            all_patterns.extend(found)

        # Sort newest-first
        all_patterns.sort(
            key=lambda p: p.end_time or p.start_time,
            reverse=True,
        )

        result.patterns      = all_patterns
        result.total         = len(all_patterns)
        result.bullish_count = sum(1 for p in all_patterns if p.bias == "bullish")
        result.bearish_count = sum(1 for p in all_patterns if p.bias == "bearish")
        result.neutral_count = sum(1 for p in all_patterns if p.bias == "neutral")

        by_tf:   dict[str, int] = {}
        by_type: dict[str, int] = {}
        by_name: dict[str, int] = {}
        for p in all_patterns:
            by_tf[p.timeframe]    = by_tf.get(p.timeframe, 0) + 1
            by_type[p.pattern_type] = by_type.get(p.pattern_type, 0) + 1
            by_name[p.name]       = by_name.get(p.name, 0) + 1

        result.by_timeframe = by_tf
        result.by_type      = by_type
        result.by_name      = dict(sorted(by_name.items(), key=lambda x: -x[1]))

    except Exception as e:
        result.error = str(e)

    return result
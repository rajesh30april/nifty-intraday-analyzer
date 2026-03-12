"""Multi-Timeframe Analysis (MTF) for Nifty 50.

Combines 1m, 5m, and 15m timeframes into a single unified
probability. Higher timeframes get more weight because they're
more reliable and less noisy.

Rule: Higher timeframe sets direction, lower timeframe gives entry.
"""

from dataclasses import dataclass, field

from data_fetcher import fetch_intraday_data
from probability import calculate_probability, ProbabilityResult

# Weight per timeframe (must sum to 1.0)
# 15m is most reliable for intraday, 1m is noisiest
TIMEFRAME_WEIGHTS = {
    "15m": 0.50,
    "5m": 0.30,
    "1m": 0.20,
}


@dataclass
class TimeframeResult:
    """Result for a single timeframe."""
    interval: str
    label: str
    weight: float
    bullish_pct: float
    bearish_pct: float
    bias: str
    confidence: str
    signal_count: int
    error: str | None = None


@dataclass
class MTFResult:
    """Combined multi-timeframe analysis result."""
    combined_bullish: float
    combined_bearish: float
    combined_bias: str
    combined_confidence: str
    confluence: str  # 'strong', 'moderate', 'weak', 'conflicting'
    timeframes: list[TimeframeResult] = field(default_factory=list)
    primary_result: ProbabilityResult | None = None  # 5m full analysis for signals/charts
    recommendation: str = ""


def _assess_confluence(timeframes: list[TimeframeResult]) -> str:
    """How well do the timeframes agree?"""
    valid = [tf for tf in timeframes if tf.error is None]
    if not valid:
        return "weak"

    biases = [tf.bias for tf in valid]
    bullish_count = biases.count("bullish")
    bearish_count = biases.count("bearish")

    if bullish_count == len(valid) or bearish_count == len(valid):
        return "strong"
    elif bullish_count >= 2 or bearish_count >= 2:
        return "moderate"
    elif "bullish" in biases and "bearish" in biases:
        return "conflicting"
    else:
        return "weak"


def _generate_recommendation(mtf: MTFResult) -> str:
    """Generate actionable recommendation based on MTF analysis."""
    conf = mtf.confluence
    bias = mtf.combined_bias
    pct = max(mtf.combined_bullish, mtf.combined_bearish)

    if conf == "strong" and bias == "bullish":
        return (
            f"🟢 STRONG BUY SETUP — All timeframes agree bullish ({pct:.0f}%). "
            "Look for pullbacks to VWAP or EMA 9 for entry. "
            "Stop loss below the 15-min Supertrend level."
        )
    elif conf == "strong" and bias == "bearish":
        return (
            f"🔴 STRONG SELL SETUP — All timeframes agree bearish ({pct:.0f}%). "
            "Look for rallies to VWAP for short entry. "
            "Stop loss above the 15-min Supertrend level."
        )
    elif conf == "moderate" and bias == "bullish":
        return (
            f"🟡 MODERATE BUY — Most timeframes lean bullish ({pct:.0f}%). "
            "Enter with smaller size. Watch the dissenting timeframe for early exit signals."
        )
    elif conf == "moderate" and bias == "bearish":
        return (
            f"🟡 MODERATE SELL — Most timeframes lean bearish ({pct:.0f}%). "
            "Enter with smaller size. Watch for reversal in lower timeframe."
        )
    elif conf == "conflicting":
        return (
            "⚠️ CONFLICTING SIGNALS — Timeframes disagree! "
            "This is a NO-TRADE zone. Wait for alignment. "
            "Capital preservation > forcing a trade."
        )
    else:
        return (
            "🟡 WEAK/NEUTRAL — No clear direction across timeframes. "
            "Reduce size or wait for a clearer setup."
        )


def run_mtf_analysis() -> MTFResult:
    """Run multi-timeframe analysis across 1m, 5m, 15m."""
    timeframes: list[TimeframeResult] = []
    primary_result: ProbabilityResult | None = None

    labels = {"1m": "1 Minute", "5m": "5 Minute", "15m": "15 Minute"}

    for interval, weight in TIMEFRAME_WEIGHTS.items():
        try:
            # 1m only supports 7 days of history on Yahoo
            period = "7d" if interval == "1m" else "5d"
            df = fetch_intraday_data(interval=interval, period=period)
            result = calculate_probability(df)

            tf = TimeframeResult(
                interval=interval,
                label=labels[interval],
                weight=weight,
                bullish_pct=result.bullish_probability,
                bearish_pct=result.bearish_probability,
                bias=result.overall_bias,
                confidence=result.confidence,
                signal_count=len(result.signals),
            )
            timeframes.append(tf)

            # Keep the 5m result as primary (for signals table & charts)
            if interval == "5m":
                primary_result = result

        except Exception as e:
            timeframes.append(TimeframeResult(
                interval=interval,
                label=labels[interval],
                weight=weight,
                bullish_pct=50.0,
                bearish_pct=50.0,
                bias="neutral",
                confidence="low",
                signal_count=0,
                error=str(e),
            ))

    # Calculate weighted combined probability
    total_weight = sum(tf.weight for tf in timeframes if tf.error is None)
    if total_weight > 0:
        combined_bull = sum(
            tf.bullish_pct * (tf.weight / total_weight)
            for tf in timeframes if tf.error is None
        )
        combined_bear = 100 - combined_bull
    else:
        combined_bull = 50.0
        combined_bear = 50.0

    combined_bull = round(combined_bull, 1)
    combined_bear = round(combined_bear, 1)

    # Determine combined bias
    if combined_bull > 55:
        combined_bias = "bullish"
    elif combined_bear > 55:
        combined_bias = "bearish"
    else:
        combined_bias = "neutral"

    # Confidence from score spread
    spread = abs(combined_bull - combined_bear)
    if spread > 30:
        combined_conf = "high"
    elif spread > 15:
        combined_conf = "medium"
    else:
        combined_conf = "low"

    confluence = _assess_confluence(timeframes)

    mtf = MTFResult(
        combined_bullish=combined_bull,
        combined_bearish=combined_bear,
        combined_bias=combined_bias,
        combined_confidence=combined_conf,
        confluence=confluence,
        timeframes=timeframes,
        primary_result=primary_result,
    )
    mtf.recommendation = _generate_recommendation(mtf)

    return mtf

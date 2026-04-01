"""Reversal vs Continuation Detection Module.

Intelligently detects whether market is likely to:
- REVERSE (fade the move)
- CONTINUE (ride the trend)

Based on:
1. RSI Divergence (most reliable!)
2. Volume Divergence (confirms exhaustion)
3. Candlestick Patterns at extremes
4. Support/Resistance levels
5. Momentum Analysis (accelerating vs decelerating)

Author: Code Puppy 🐶
Date: March 25, 2026
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Literal


@dataclass
class ReversalContinuationResult:
    """Result of reversal/continuation analysis."""
    reversal_score: float  # 0-100 (higher = reversal more likely)
    continuation_score: float  # 0-100 (higher = continuation more likely)
    signals: list[str]  # Human-readable signals detected
    recommendation: Literal['REVERSAL', 'CONTINUATION', 'NEUTRAL']
    confidence: float  # 0-100 (how confident are we?)
    
    # Individual component scores for debugging
    rsi_divergence_score: float = 0.0
    volume_divergence_score: float = 0.0
    candle_pattern_score: float = 0.0
    sr_level_score: float = 0.0
    momentum_score: float = 0.0


class ReversalContinuationDetector:
    """Detects reversal vs continuation patterns."""
    
    def __init__(self, df: pd.DataFrame, lookback: int = 30):
        """
        Args:
            df: Full OHLCV DataFrame
            lookback: Number of candles to analyze (default 30 = 2.5 hours on 5m)
        """
        self.df = df.tail(lookback).copy()
        self.current_price = float(self.df['close'].iloc[-1])
        self.current_high = float(self.df['high'].iloc[-1])
        self.current_low = float(self.df['low'].iloc[-1])
        
        # Calculate indicators
        self._calculate_rsi()
        self._find_day_extremes()
    
    def _calculate_rsi(self, period: int = 14):
        """Calculate RSI indicator."""
        delta = self.df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        self.df['rsi'] = 100 - (100 / (1 + rs))
    
    def _find_day_extremes(self):
        """Find today's high and low."""
        today = self.df.index[-1].date()
        today_df = self.df[self.df.index.date == today]
        
        if len(today_df) > 0:
            self.day_high = float(today_df['high'].max())
            self.day_low = float(today_df['low'].min())
            self.day_range = self.day_high - self.day_low
        else:
            # Fallback to lookback period
            self.day_high = float(self.df['high'].max())
            self.day_low = float(self.df['low'].min())
            self.day_range = self.day_high - self.day_low
    
    def analyze(self) -> ReversalContinuationResult:
        """Run full reversal/continuation analysis.
        
        Returns:
            ReversalContinuationResult with scores and signals
        """
        signals = []
        reversal_score = 0.0
        continuation_score = 0.0
        
        # 1. RSI Divergence (weight: 30 points)
        rsi_score, rsi_signals = self._check_rsi_divergence()
        reversal_score += max(rsi_score, 0)  # Only add if bearish divergence
        continuation_score += abs(min(rsi_score, 0))  # Only add if bullish confirmation
        signals.extend(rsi_signals)
        
        # 2. Volume Divergence (weight: 25 points)
        vol_score, vol_signals = self._check_volume_divergence()
        reversal_score += max(vol_score, 0)
        continuation_score += abs(min(vol_score, 0))
        signals.extend(vol_signals)
        
        # 3. Candlestick Patterns (weight: 20 points)
        candle_score, candle_signals = self._check_candle_patterns()
        reversal_score += max(candle_score, 0)
        continuation_score += abs(min(candle_score, 0))
        signals.extend(candle_signals)
        
        # 4. Support/Resistance Levels (weight: 15 points)
        sr_score, sr_signals = self._check_sr_levels()
        reversal_score += max(sr_score, 0)
        continuation_score += abs(min(sr_score, 0))
        signals.extend(sr_signals)
        
        # 5. Momentum Analysis (weight: 10 points)
        mom_score, mom_signals = self._check_momentum()
        reversal_score += max(mom_score, 0)
        continuation_score += abs(min(mom_score, 0))
        signals.extend(mom_signals)
        
        # Determine recommendation
        if reversal_score > 60 and reversal_score > continuation_score * 1.5:
            recommendation = 'REVERSAL'
            confidence = min(reversal_score, 100)
        elif continuation_score > 60 and continuation_score > reversal_score * 1.5:
            recommendation = 'CONTINUATION'
            confidence = min(continuation_score, 100)
        else:
            recommendation = 'NEUTRAL'
            confidence = 50
        
        return ReversalContinuationResult(
            reversal_score=min(reversal_score, 100),
            continuation_score=min(continuation_score, 100),
            signals=signals,
            recommendation=recommendation,
            confidence=confidence,
            rsi_divergence_score=rsi_score,
            volume_divergence_score=vol_score,
            candle_pattern_score=candle_score,
            sr_level_score=sr_score,
            momentum_score=mom_score,
        )
    
    def _check_rsi_divergence(self) -> tuple[float, list[str]]:
        """Check for RSI divergence (most reliable reversal signal!).
        
        Returns:
            (score, signals) where:
            - Positive score = reversal likely (bearish divergence)
            - Negative score = continuation likely (no divergence)
        """
        signals = []
        score = 0.0
        
        if len(self.df) < 10 or 'rsi' not in self.df.columns:
            return 0.0, []
        
        # Get recent data
        recent = self.df.tail(20)
        prices = recent['close'].values
        rsi_values = recent['rsi'].values
        
        # Find recent peaks (for bearish divergence at top)
        price_peaks = self._find_peaks(prices)
        rsi_peaks = self._find_peaks(rsi_values)
        
        # Find recent troughs (for bullish divergence at bottom)
        price_troughs = self._find_troughs(prices)
        rsi_troughs = self._find_troughs(rsi_values)
        
        # Check for BEARISH divergence (price higher high, RSI lower high)
        if len(price_peaks) >= 2 and len(rsi_peaks) >= 2:
            last_price_peak = prices[price_peaks[-1]]
            prev_price_peak = prices[price_peaks[-2]]
            last_rsi_peak = rsi_values[rsi_peaks[-1]]
            prev_rsi_peak = rsi_values[rsi_peaks[-2]]
            
            if last_price_peak > prev_price_peak and last_rsi_peak < prev_rsi_peak:
                # BEARISH DIVERGENCE!
                divergence_strength = (prev_rsi_peak - last_rsi_peak) / prev_rsi_peak * 100
                score += min(30, divergence_strength * 3)  # Up to 30 points
                signals.append(f"⚠️ RSI Bearish Divergence: Price {last_price_peak:.0f} > {prev_price_peak:.0f}, RSI {last_rsi_peak:.1f} < {prev_rsi_peak:.1f} (+{score:.0f} reversal)")
        
        # Check for BULLISH divergence (price lower low, RSI higher low)
        if len(price_troughs) >= 2 and len(rsi_troughs) >= 2:
            last_price_trough = prices[price_troughs[-1]]
            prev_price_trough = prices[price_troughs[-2]]
            last_rsi_trough = rsi_values[rsi_troughs[-1]]
            prev_rsi_trough = rsi_values[rsi_troughs[-2]]
            
            if last_price_trough < prev_price_trough and last_rsi_trough > prev_rsi_trough:
                # BULLISH DIVERGENCE!
                divergence_strength = (last_rsi_trough - prev_rsi_trough) / prev_rsi_trough * 100
                score += min(30, divergence_strength * 3)
                signals.append(f"✅ RSI Bullish Divergence: Price {last_price_trough:.0f} < {prev_price_trough:.0f}, RSI {last_rsi_trough:.1f} > {prev_rsi_trough:.1f} (+{score:.0f} reversal)")
        
        # Check current RSI level (overbought/oversold)
        current_rsi = rsi_values[-1]
        if current_rsi > 70:
            score += 10
            signals.append(f"⚠️ RSI Overbought: {current_rsi:.1f} > 70 (+10 reversal)")
        elif current_rsi < 30:
            score += 10
            signals.append(f"✅ RSI Oversold: {current_rsi:.1f} < 30 (+10 reversal)")
        elif 45 <= current_rsi <= 55:
            score -= 10
            signals.append(f"➡️ RSI Neutral: {current_rsi:.1f} (trend likely continues)")
        
        return score, signals
    
    def _check_volume_divergence(self) -> tuple[float, list[str]]:
        """Check if volume is confirming or diverging from price move.
        
        Returns:
            (score, signals)
        """
        signals = []
        score = 0.0
        
        if len(self.df) < 10:
            return 0.0, []
        
        # Get last 10 candles
        recent = self.df.tail(10)
        prices = recent['close'].values
        volumes = recent['volume'].values
        
        # Check if we're in a rally or decline
        price_change = prices[-1] - prices[0]
        
        if abs(price_change) < 20:  # Not enough move to analyze
            return 0.0, []
        
        # Split into first half and second half
        mid = len(volumes) // 2
        first_half_vol = volumes[:mid].mean()
        second_half_vol = volumes[mid:].mean()
        
        vol_change_pct = (second_half_vol - first_half_vol) / first_half_vol * 100
        
        if price_change > 0:  # Rally
            if vol_change_pct < -20:  # Volume declining on rally
                score += 25
                signals.append(f"⚠️ Volume Divergence: Rally +{price_change:.0f}pts but volume declining {vol_change_pct:.0f}% (+25 reversal)")
            elif vol_change_pct > 20:  # Volume increasing on rally
                score -= 15
                signals.append(f"✅ Volume Confirmation: Rally +{price_change:.0f}pts with volume increasing {vol_change_pct:.0f}% (continuation likely)")
        
        else:  # Decline
            if vol_change_pct < -20:  # Volume declining on decline
                score += 25
                signals.append(f"✅ Volume Divergence: Decline {price_change:.0f}pts but volume declining {vol_change_pct:.0f}% (+25 reversal)")
            elif vol_change_pct > 20:  # Volume increasing on decline
                score -= 15
                signals.append(f"⚠️ Volume Confirmation: Decline {price_change:.0f}pts with volume increasing {vol_change_pct:.0f}% (continuation likely)")
        
        return score, signals
    
    def _check_candle_patterns(self) -> tuple[float, list[str]]:
        """Check for reversal candlestick patterns at extremes.
        
        Returns:
            (score, signals)
        """
        signals = []
        score = 0.0
        
        if len(self.df) < 3:
            return 0.0, []
        
        # Get last candle
        last = self.df.iloc[-1]
        prev = self.df.iloc[-2] if len(self.df) >= 2 else last
        
        open_p = last['open']
        high = last['high']
        low = last['low']
        close = last['close']
        
        body = abs(close - open_p)
        total_range = high - low
        upper_shadow = high - max(open_p, close)
        lower_shadow = min(open_p, close) - low
        
        # Check if we're near day high or low
        near_high = (self.current_price > self.day_low + self.day_range * 0.7)
        near_low = (self.current_price < self.day_low + self.day_range * 0.3)
        
        # Shooting Star (reversal at top)
        if near_high and upper_shadow > body * 2 and lower_shadow < body * 0.3:
            score += 20
            signals.append(f"⚠️ Shooting Star at day high (+20 reversal)")
        
        # Hammer (reversal at bottom)
        if near_low and lower_shadow > body * 2 and upper_shadow < body * 0.3:
            score += 20
            signals.append(f"✅ Hammer at day low (+20 reversal)")
        
        # Doji (indecision at extreme)
        if body < total_range * 0.1 and (near_high or near_low):
            score += 15
            signals.append(f"⚠️ Doji at extreme ({('high' if near_high else 'low')}) - indecision (+15 reversal)")
        
        # Engulfing patterns
        if len(self.df) >= 2:
            prev_body = abs(prev['close'] - prev['open'])
            
            # Bearish Engulfing at top
            if near_high and close < open_p and body > prev_body * 1.2:
                score += 15
                signals.append(f"⚠️ Bearish Engulfing at high (+15 reversal)")
            
            # Bullish Engulfing at bottom  
            if near_low and close > open_p and body > prev_body * 1.2:
                score += 15
                signals.append(f"✅ Bullish Engulfing at low (+15 reversal)")
        
        return score, signals
    
    def _check_sr_levels(self) -> tuple[float, list[str]]:
        """Check if price is at major support/resistance.
        
        Returns:
            (score, signals)
        """
        signals = []
        score = 0.0
        
        # Check distance from day high/low
        dist_from_high = self.day_high - self.current_price
        dist_from_low = self.current_price - self.day_low
        
        # At day high (resistance)
        if dist_from_high < self.day_range * 0.05:  # Within 5% of high
            score += 15
            signals.append(f"⚠️ At day high resistance: {dist_from_high:.0f}pts away (+15 reversal)")
        
        # At day low (support)
        if dist_from_low < self.day_range * 0.05:  # Within 5% of low
            score += 15
            signals.append(f"✅ At day low support: {dist_from_low:.0f}pts away (+15 reversal)")
        
        # Check for round number levels (psychological S/R)
        price_rounded = round(self.current_price / 50) * 50  # Round to nearest 50
        dist_from_round = abs(self.current_price - price_rounded)
        
        if dist_from_round < 10:  # Within 10 points of round number
            score += 5
            signals.append(f"➡️ Near round number {price_rounded:.0f} (+5 reversal)")
        
        return score, signals
    
    def _check_momentum(self) -> tuple[float, list[str]]:
        """Check if momentum is accelerating or decelerating.
        
        Returns:
            (score, signals)
        """
        signals = []
        score = 0.0
        
        if len(self.df) < 5:
            return 0.0, []
        
        # Get last 5 candle sizes
        recent = self.df.tail(5)
        candle_sizes = (recent['high'] - recent['low']).values
        
        # Check if candles are getting smaller (deceleration = reversal)
        # or bigger (acceleration = continuation)
        first_half = candle_sizes[:2].mean()
        second_half = candle_sizes[3:].mean()
        
        if first_half > 0:
            momentum_change = (second_half - first_half) / first_half * 100
            
            if momentum_change < -30:  # Decelerating
                score += 10
                signals.append(f"⚠️ Momentum decelerating {momentum_change:.0f}% - exhaustion (+10 reversal)")
            elif momentum_change > 30:  # Accelerating
                score -= 10
                signals.append(f"🚀 Momentum accelerating {momentum_change:.0f}% - continuation likely")
        
        return score, signals
    
    def _find_peaks(self, data: np.ndarray, order: int = 3) -> list[int]:
        """Find peaks in data array."""
        peaks = []
        for i in range(order, len(data) - order):
            if all(data[i] > data[i-j] for j in range(1, order + 1)) and \
               all(data[i] > data[i+j] for j in range(1, order + 1)):
                peaks.append(i)
        return peaks
    
    def _find_troughs(self, data: np.ndarray, order: int = 3) -> list[int]:
        """Find troughs in data array."""
        troughs = []
        for i in range(order, len(data) - order):
            if all(data[i] < data[i-j] for j in range(1, order + 1)) and \
               all(data[i] < data[i+j] for j in range(1, order + 1)):
                troughs.append(i)
        return troughs


# Example usage
if __name__ == "__main__":
    print("✅ Reversal/Continuation Detector module created!")
    print("   Use in strategies to detect market turning points.")

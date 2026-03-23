"""Unit tests for improved pattern detector.

Tests cover:
- Peak/trough detection accuracy
- Pattern detection with volume confirmation
- Dynamic tolerance calculations
- Edge cases and error handling
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
sys.path.insert(0, '/Users/r0s0iv3/nifty-intraday-analyzer')

from pattern_detector_improved import (
    detect_double_top,
    detect_double_bottom,
    detect_flag,
    detect_ascending_triangle,
    detect_descending_triangle,
    _find_peaks_troughs,
    _calculate_atr,
    _check_volume_confirmation,
    _dynamic_tolerance,
)


def create_test_df(prices: list[float], volumes: list[float] = None) -> pd.DataFrame:
    """Helper to create test DataFrame."""
    dates = [datetime(2024, 1, 1) + timedelta(minutes=i*5) for i in range(len(prices))]
    df = pd.DataFrame({
        'open': prices,
        'high': [p * 1.002 for p in prices],
        'low': [p * 0.998 for p in prices],
        'close': prices,
    }, index=pd.DatetimeIndex(dates))
    
    if volumes:
        df['volume'] = volumes
    
    return df


class TestPeakTroughDetection:
    """Test improved peak and trough detection."""
    
    def test_simple_peak_detection(self):
        """Should detect a clear peak."""
        prices = [100, 102, 105, 103, 101]  # Peak at index 2
        df = create_test_df(prices)
        
        peaks, troughs = _find_peaks_troughs(df['high'], df['low'], order=1, min_distance=1)
        
        assert 2 in peaks, "Should detect peak at index 2"
    
    def test_minimum_distance_filter(self):
        """Should filter out peaks too close together."""
        # Two peaks only 2 candles apart
        prices = [100, 105, 103, 106, 104, 100]
        df = create_test_df(prices)
        
        peaks, troughs = _find_peaks_troughs(df['high'], df['low'], order=1, min_distance=3)
        
        # Should only detect one peak (the higher one at index 3)
        assert len(peaks) <= 1, "Should filter out close peaks"
    
    def test_strict_comparison(self):
        """Should use strict comparison (not flat peaks)."""
        # Flat top (not a real peak)
        prices = [100, 105, 105, 105, 100]
        df = create_test_df(prices)
        
        peaks, troughs = _find_peaks_troughs(df['high'], df['low'], order=1)
        
        # Should NOT detect any peak (all equal)
        assert len(peaks) == 0, "Should not detect flat tops as peaks"


class TestATRCalculation:
    """Test ATR calculation for dynamic tolerances."""
    
    def test_atr_basic(self):
        """Should calculate ATR correctly."""
        prices = [100, 102, 101, 103, 102, 104]
        df = create_test_df(prices)
        
        atr = _calculate_atr(df['high'], df['low'], df['close'], period=3)
        
        # ATR should be positive
        assert atr.iloc[-1] > 0, "ATR should be positive"
        # ATR should be reasonable for the price range
        assert atr.iloc[-1] < 10, "ATR should be reasonable"
    
    def test_dynamic_tolerance(self):
        """Should calculate larger tolerance for higher ATR."""
        price = 100
        low_atr = 0.5
        high_atr = 2.0
        
        tol_low = _dynamic_tolerance(price, low_atr, base_pct=0.3)
        tol_high = _dynamic_tolerance(price, high_atr, base_pct=0.3)
        
        assert tol_high > tol_low, "Higher ATR should give wider tolerance"


class TestVolumeConfirmation:
    """Test volume confirmation logic."""
    
    def test_volume_above_threshold(self):
        """Should confirm when volume is above threshold."""
        # Last volume is 2x average
        volumes = pd.Series([1000, 1000, 1000, 2000])
        
        confirmed, detail = _check_volume_confirmation(volumes, 3, lookback=3, threshold=1.5)
        
        assert confirmed == True, "Should confirm breakout volume"
        assert "2.00x" in detail, "Should show volume ratio"
    
    def test_volume_below_threshold(self):
        """Should not confirm when volume is normal."""
        volumes = pd.Series([1000, 1000, 1000, 1100])
        
        confirmed, detail = _check_volume_confirmation(volumes, 3, lookback=3, threshold=1.5)
        
        assert confirmed == False, "Should not confirm low volume"


class TestDoubleTopPattern:
    """Test improved double top detection."""
    
    def test_valid_double_top(self):
        """Should detect a valid double top."""
        # Create a double top: rise to 110, fall to 100, rise to 110, current at 95
        prices = [
            100, 102, 105, 107, 110,  # First peak
            108, 105, 103, 100,  # Valley
            102, 105, 107, 110,  # Second peak (same level)
            108, 105, 102, 95  # Breakdown
        ]
        df = create_test_df(prices)
        
        result = detect_double_top(
            df['high'], df['low'], df['close'],
            volume=None, tolerance_pct=0.5, min_separation=4
        )
        
        assert result is not None, "Should detect double top"
        assert result.name == "Double Top"
        assert result.bias == "bearish"
        assert result.confidence > 0.8, "Should be high confidence (confirmed)"
    
    def test_higher_high_rejection(self):
        """Should NOT detect double top if second peak is higher."""
        # Second peak is higher (bullish)
        prices = [
            100, 105, 110,  # First peak
            108, 105, 103,  # Valley
            105, 110, 112,  # Second peak HIGHER
            110
        ]
        df = create_test_df(prices)
        
        result = detect_double_top(
            df['high'], df['low'], df['close'],
            tolerance_pct=0.3, min_separation=3
        )
        
        assert result is None, "Should NOT detect double top (second peak higher)"
    
    def test_volume_confirmation_increases_confidence(self):
        """Volume confirmation should boost confidence."""
        prices = [
            100, 110, 105, 110, 95  # Simple double top with breakdown
        ] + [95] * 10  # padding
        
        volumes = [1000] * (len(prices) - 1) + [2500]  # High volume on last candle
        df = create_test_df(prices, volumes)
        
        result = detect_double_top(
            df['high'], df['low'], df['close'],
            volume=df['volume'], min_separation=1
        )
        
        if result:
            assert result.volume_confirmed == True
            assert result.confidence >= 0.85


class TestDoubleBottomPattern:
    """Test improved double bottom detection."""
    
    def test_valid_double_bottom(self):
        """Should detect a valid double bottom."""
        prices = [
            110, 105, 100,  # First trough
            102, 105, 107,  # Peak
            105, 102, 100,  # Second trough (same level)
            102, 105, 108  # Breakout
        ]
        df = create_test_df(prices)
        
        result = detect_double_bottom(
            df['high'], df['low'], df['close'],
            tolerance_pct=0.5, min_separation=3
        )
        
        assert result is not None, "Should detect double bottom"
        assert result.bias == "bullish"
        assert result.confidence > 0.8
    
    def test_lower_low_rejection(self):
        """Should NOT detect if second trough is lower."""
        prices = [
            110, 105, 100,  # First trough
            102, 105,  # Peak
            103, 100, 95,  # Second trough LOWER (bearish)
            97
        ]
        df = create_test_df(prices)
        
        result = detect_double_bottom(
            df['high'], df['low'], df['close'],
            tolerance_pct=0.3, min_separation=2
        )
        
        assert result is None, "Should NOT detect (lower low is bearish)"


class TestFlagPattern:
    """Test improved bull/bear flag detection."""
    
    def test_bull_flag(self):
        """Should detect bull flag: sharp rise + small consolidation + breakout."""
        # Sharp rise from 100 to 110
        impulse = [100, 103, 106, 109, 110]
        # Small consolidation (flag) 108-110
        consol = [109, 108, 109, 108]
        # Breakout above 110
        breakout = [111]
        
        prices = impulse + consol + breakout + [111] * 3
        df = create_test_df(prices)
        
        result = detect_flag(df, volume=None, impulse_min_pct=0.2)
        
        assert result is not None, "Should detect bull flag"
        assert result.name == "Bull Flag"
        assert result.bias == "bullish"
    
    def test_bear_flag(self):
        """Should detect bear flag: sharp drop + small bounce + breakdown."""
        impulse = [110, 107, 104, 101, 100]
        consol = [101, 102, 101, 102]
        breakdown = [99] + [99] * 3
        
        prices = impulse + consol + breakdown
        df = create_test_df(prices)
        
        result = detect_flag(df, volume=None, impulse_min_pct=0.2)
        
        assert result is not None, "Should detect bear flag"
        assert result.name == "Bear Flag"
        assert result.bias == "bearish"


class TestTrianglePatterns:
    """Test triangle pattern detection."""
    
    def test_ascending_triangle(self):
        """Should detect ascending triangle."""
        # Flat resistance at 110, rising lows
        prices = [
            100, 105, 110, 108,  # Peak 1, Low 1
            109, 110, 109,  # Peak 2, Low 2 (higher)
            110, 110, 111  # Breakout
        ]
        df = create_test_df(prices)
        
        result = detect_ascending_triangle(
            df['high'], df['low'], df['close'],
            tolerance_pct=0.5
        )
        
        assert result is not None, "Should detect ascending triangle"
        assert result.bias == "bullish"
    
    def test_descending_triangle(self):
        """Should detect descending triangle."""
        # Flat support at 100, falling highs
        prices = [
            110, 105, 100, 102,  # High 1, Low 1
            108, 103, 100, 102,  # High 2 (lower), Low 2
            105, 100, 99  # Breakdown
        ]
        df = create_test_df(prices)
        
        result = detect_descending_triangle(
            df['high'], df['low'], df['close'],
            tolerance_pct=0.5
        )
        
        assert result is not None, "Should detect descending triangle"
        assert result.bias == "bearish"


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_insufficient_data(self):
        """Should handle insufficient data gracefully."""
        prices = [100, 101, 102]  # Too few candles
        df = create_test_df(prices)
        
        result = detect_double_top(df['high'], df['low'], df['close'])
        
        assert result is None, "Should return None for insufficient data"
    
    def test_no_volume_data(self):
        """Should work without volume data."""
        prices = [100, 110, 105, 110, 95] + [95] * 10
        df = create_test_df(prices)
        
        result = detect_double_top(
            df['high'], df['low'], df['close'],
            volume=None
        )
        
        # Should still detect pattern, just without volume confirmation
        if result:
            assert result.volume_confirmed == False


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

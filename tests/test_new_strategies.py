"""Tests for FCR and PDH/PDL Breakout strategies.

All time-sensitive checks are patched to a fixed wall-clock time
so tests are deterministic regardless of when they run.
"""

from __future__ import annotations

import sys
import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ── Helpers ───────────────────────────────────────────────────────────────────

TODAY     = date(2026, 3, 16)          # fixed "today" for all tests
MARKET_DT = datetime(2026, 3, 16, 10, 30)  # mid-morning → strategies active


def _ts(hour: int, minute: int, d: date = TODAY) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute)


def _df_with_today(
    prev_close: float,
    prev_high: float,
    prev_low: float,
    today_candles: list[tuple[float, float, float, float]],  # (o, h, l, c)
    start_hour: int = 9,
    start_min: int = 15,
    vol: int = 60_000,
    prev_candle_count: int = 25,   # enough for all strategies (ORB needs 20)
) -> pd.DataFrame:
    """Build a multi-day DataFrame with one prev day + today's candles.

    Previous day has `prev_candle_count` candles.  Today's candles start at
    9:15 and are passed in as (open, high, low, close) tuples.
    PDH and PDL are forced into the prev day's first and last candle.
    """
    prev_day = date(2026, 3, 13)   # Friday before our Monday

    # Previous day candles — simple oscillation around prev_close
    n = prev_candle_count
    prev_rows = []
    for i in range(n):
        px = prev_close + 5 * np.sin(i * 0.4)   # gentle wave
        prev_rows.append({
            "open":   round(px - 1, 2),
            "high":   round(px + 4, 2),
            "low":    round(px - 4, 2),
            "close":  round(px,     2),
            "volume": vol,
        })
    # Inject PDH into candle 0 high, PDL into last candle low
    prev_rows[0]["high"]   = prev_high
    prev_rows[-1]["low"]   = prev_low
    prev_rows[-1]["close"] = prev_close   # last close = prev_close

    _prev_base = datetime(prev_day.year, prev_day.month, prev_day.day, 9, 15)
    prev_times = [_prev_base + timedelta(minutes=i * 5) for i in range(n)]

    # Today's candles — support optional 5th element for per-candle volume
    today_rows = [
        {"open": o, "high": h, "low": l, "close": c,
         "volume": (candle[4] if len(candle) > 4 else vol)}
        for candle in today_candles
        for o, h, l, c in [(candle[0], candle[1], candle[2], candle[3])]
    ]
    today_times = [
        datetime(TODAY.year, TODAY.month, TODAY.day,
                 start_hour, start_min + i * 5)
        for i in range(len(today_candles))
    ]

    rows  = prev_rows  + today_rows
    times = prev_times + today_times
    return pd.DataFrame(rows, index=pd.DatetimeIndex(times))


# ══════════════════════════════════════════════════════════════════════════════
# FCR Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestFirstCandleRange:

    def _eval(self, df: pd.DataFrame, wall_time: datetime = MARKET_DT):
        from strategies.first_candle_range import evaluate_fcr
        with patch("strategies.first_candle_range.datetime") as mock_dt:
            mock_dt.now.return_value = wall_time
            mock_dt.side_effect      = lambda *a, **kw: datetime(*a, **kw)
            return evaluate_fcr(df)

    def test_no_signal_before_9_20(self):
        df = _df_with_today(
            prev_close=23_200, prev_high=23_300, prev_low=23_100,
            today_candles=[(23_250, 23_310, 23_230, 23_300)],
        )
        # Wall clock = 9:18 → too early
        result = self._eval(df, wall_time=datetime(2026, 3, 16, 9, 18))
        assert result.should_enter is False
        assert "9:20" in result.reason

    def test_no_signal_narrow_range(self):
        """Opening candle is a doji — FCR range < 20 pts → skip."""
        # tiny 5-pt range candle
        df = _df_with_today(
            prev_close=23_200, prev_high=23_300, prev_low=23_100,
            today_candles=[
                (23_200, 23_205, 23_198, 23_202),   # doji (range=7 pts)
                (23_202, 23_208, 23_199, 23_206),   # 2nd candle
            ],
        )
        result = self._eval(df)
        assert result.should_enter is False
        assert "narrow" in result.reason.lower() or "range" in result.reason.lower()

    def test_no_signal_wide_range(self):
        """Opening candle range > 120 pts → skip (SL too large)."""
        df = _df_with_today(
            prev_close=23_200, prev_high=23_300, prev_low=23_100,
            today_candles=[
                (23_000, 23_200, 22_990, 23_150),   # range = 210 pts
                (23_150, 23_160, 23_140, 23_155),
            ],
        )
        result = self._eval(df)
        assert result.should_enter is False

    def test_bullish_breakout_signal(self):
        """Body closes cleanly above FCR high → LONG signal."""
        # prev avg vol=40_000; breakout candle vol=70_000 → ratio=1.75× ✅
        df = _df_with_today(
            prev_close=23_200, prev_high=23_350, prev_low=23_100,
            today_candles=[
                (23_250, 23_300, 23_245, 23_280, 40_000),  # FCR: h=23_300, l=23_245
                (23_305, 23_320, 23_302, 23_315, 70_000),  # body_low=23_305 > 23_300 ✅
            ],
            vol=40_000,   # prev candles at 40k avg
        )
        result = self._eval(df)
        assert result.should_enter is True
        assert result.direction.value == "long"

    def test_bearish_breakout_signal(self):
        """Body closes cleanly below FCR low → SHORT signal."""
        df = _df_with_today(
            prev_close=23_200, prev_high=23_350, prev_low=23_100,
            today_candles=[
                (23_260, 23_300, 23_245, 23_270, 40_000),  # FCR: h=23_300, l=23_245
                (23_240, 23_242, 23_225, 23_228, 70_000),  # body_high=23_240 < 23_245 ✅
            ],
            vol=40_000,
        )
        result = self._eval(df)
        assert result.should_enter is True
        assert result.direction.value == "short"

    def test_no_signal_inside_range(self):
        """Price stays inside FCR → no entry."""
        # FCR: h=23_310, l=23_240 → range=70 pts ✅
        # 2nd candle body: 23_270–23_295 — fully inside FCR
        df = _df_with_today(
            prev_close=23_200, prev_high=23_350, prev_low=23_100,
            today_candles=[
                (23_250, 23_310, 23_240, 23_290),   # FCR h=310 l=240
                (23_290, 23_295, 23_268, 23_275),   # body 23_275–23_290 inside ✅
            ],
        )
        result = self._eval(df)
        assert result.should_enter is False

    def test_no_signal_after_2_30(self):
        """Entries blocked after 2:30 PM."""
        df = _df_with_today(
            prev_close=23_200, prev_high=23_350, prev_low=23_100,
            today_candles=[
                (23_250, 23_310, 23_240, 23_290),
                (23_295, 23_320, 23_292, 23_318),
            ],
        )
        result = self._eval(df, wall_time=datetime(2026, 3, 16, 14, 35))
        assert result.should_enter is False
        assert "late" in result.reason.lower() or "14:30" in result.reason

    def test_stale_data_blocked(self):
        """Data from yesterday → stale data guard fires."""
        from tests.mock_data import trending_up
        df = trending_up(n=20)   # timestamps in 2026-03-16 already
        # Patch wall clock to a DIFFERENT date
        result = self._eval(df, wall_time=datetime(2026, 3, 17, 10, 30))
        assert result.should_enter is False
        assert "stale" in result.reason.lower()


# ══════════════════════════════════════════════════════════════════════════════
# PDH/PDL Breakout Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestPDHLBreakout:

    def _eval(self, df: pd.DataFrame, wall_time: datetime = MARKET_DT):
        from strategies.pdhl_breakout import evaluate_pdhl_breakout
        with patch("strategies.pdhl_breakout.datetime") as mock_dt:
            mock_dt.now.return_value = wall_time
            mock_dt.side_effect      = lambda *a, **kw: datetime(*a, **kw)
            return evaluate_pdhl_breakout(df)

    def test_bullish_breakout_above_pdh(self):
        """Body closes above PDH with volume surge → LONG."""
        # prev avg vol=40_000; breakout vol=70_000 → ratio=1.75× ✅
        df = _df_with_today(
            prev_close=23_300, prev_high=23_350, prev_low=23_150,
            today_candles=[
                (23_310, 23_345, 23_305, 23_330, 40_000),
                (23_355, 23_375, 23_352, 23_370, 70_000),  # body_low=23_355 > 23_350 ✅
            ],
            vol=40_000,
        )
        result = self._eval(df)
        assert result.should_enter is True
        assert result.direction.value == "long"

    def test_bearish_breakdown_below_pdl(self):
        """Body closes below PDL → SHORT."""
        df = _df_with_today(
            prev_close=23_300, prev_high=23_350, prev_low=23_150,
            today_candles=[
                (23_200, 23_210, 23_170, 23_180, 40_000),
                (23_145, 23_147, 23_125, 23_130, 70_000),  # body_high=23_145 < 23_150 ✅
            ],
            vol=40_000,
        )
        result = self._eval(df)
        assert result.should_enter is True
        assert result.direction.value == "short"

    def test_no_signal_inside_pd_range(self):
        """Price inside PDH/PDL range → no entry."""
        # PDH=23_400, PDL=23_100 — all candles stay well inside
        df = _df_with_today(
            prev_close=23_300, prev_high=23_400, prev_low=23_100,
            today_candles=[
                (23_250, 23_280, 23_230, 23_260),
                (23_262, 23_300, 23_255, 23_280),   # inside 23_100–23_400 ✅
            ],
        )
        result = self._eval(df)
        assert result.should_enter is False

    def test_no_signal_after_cutoff(self):
        """No entry allowed after 2:30 PM."""
        df = _df_with_today(
            prev_close=23_300, prev_high=23_350, prev_low=23_150,
            today_candles=[
                (23_310, 23_340, 23_305, 23_330),
                (23_335, 23_365, 23_332, 23_360),
            ],
            vol=80_000,
        )
        result = self._eval(df, wall_time=datetime(2026, 3, 16, 14, 45))
        assert result.should_enter is False

    def test_no_signal_no_prev_data(self):
        """Only today's data available → can't compute PDH/PDL."""
        # Build a DataFrame with only today's timestamps
        times  = [datetime(2026, 3, 16, 9, 15 + i * 5) for i in range(5)]
        prices = [23_250.0 + i * 5 for i in range(5)]
        rows   = [{"open": p, "high": p + 3, "low": p - 3, "close": p,
                   "volume": 50_000} for p in prices]
        df = pd.DataFrame(rows, index=pd.DatetimeIndex(times))
        result = self._eval(df)
        assert result.should_enter is False

    def test_confidence_is_high_on_clean_setup(self):
        """Clean breakout → should_enter=True, confidence >= 75%."""
        df = _df_with_today(
            prev_close=23_300, prev_high=23_350, prev_low=23_150,
            today_candles=[
                (23_310, 23_345, 23_305, 23_330, 40_000),
                (23_355, 23_375, 23_352, 23_370, 70_000),  # body_low=23_355 > PDH ✅
            ],
            vol=40_000,
        )
        result = self._eval(df)
        assert result.should_enter is True
        assert result.confidence >= 75


# ══════════════════════════════════════════════════════════════════════════════
# OCF body size fix
# ══════════════════════════════════════════════════════════════════════════════

class TestOCFBodySizeFix:
    """ATR-relative body check replaces the old hardcoded 15-pt floor."""

    def test_tiny_body_rejected(self):
        """A 10-pt body on a normal-ATR day must fail 'Big Opening Candle'."""
        from tests.mock_data import _make_candles, _times
        from strategies.opening_candle_fade import evaluate_ocf

        # 30 candles of history with spread=15 → ATR ≈ 20-30 pts
        # Required body = max(20, 0.40 × ATR) ≈ 20+ pts
        times   = _times(30)
        prices  = [23_200.0 + 8 * np.sin(i * 0.5) for i in range(30)]
        df_prev = _make_candles(times, prices, spread=15)

        # Today: 10-pt body opening candle — well below ATR threshold
        today_times = [
            datetime(2026, 3, 16, 9, 15),
            datetime(2026, 3, 16, 9, 20),
        ]
        today_rows = [
            {"open": 23_200, "high": 23_218, "low": 23_195, "close": 23_210,
             "volume": 60_000},
            {"open": 23_210, "high": 23_212, "low": 23_206, "close": 23_207,
             "volume": 35_000},
        ]
        df = pd.concat([
            df_prev,
            pd.DataFrame(today_rows, index=pd.DatetimeIndex(today_times)),
        ])

        result = evaluate_ocf(df)  # no datetime patch needed — uses df.index[-1].time()
        cond = next((c for c in result.conditions if c.name == "Big Opening Candle"), None)
        assert cond is not None, "Big Opening Candle condition missing"
        assert cond.met is False, f"Expected body to be rejected, got: {cond.detail}"


# ══════════════════════════════════════════════════════════════════════════════
# ORB range quality filter
# ══════════════════════════════════════════════════════════════════════════════

class TestORBRangeQuality:
    """ORB range quality filter: 30–100 pts only."""

    # ORB uses `from datetime import datetime` so patch targets the class
    # imported into the orb module namespace.
    WALL = datetime(2026, 3, 16, 9, 35)   # 9:35 → 20 min since open → time_ok

    def _eval(self, df, wall_time: datetime | None = None):
        from strategies.orb import evaluate_orb
        wt = wall_time or self.WALL
        with patch("strategies.orb.datetime") as mock_dt:
            mock_dt.now.return_value  = wt
            # Allow datetime(y, m, d, ...) constructor to still work
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            return evaluate_orb(df)

    def test_narrow_orb_rejected(self):
        """ORB high-low < 30 pts → rejected with informative reason."""
        # 25 prev candles so len(df) >= 20 ✅
        # Today: 4 tiny candles with range ≈ 12 pts total
        df = _df_with_today(
            prev_close=23_200, prev_high=23_300, prev_low=23_100,
            today_candles=[
                (23_250, 23_258, 23_248, 23_255),   # h=258, l=248
                (23_255, 23_262, 23_252, 23_258),
                (23_258, 23_265, 23_255, 23_260),   # after 15 min — orb_high=265, orb_low=248 → 17 pts
                (23_262, 23_268, 23_258, 23_264),   # breakout attempt
            ],
            prev_candle_count=25,
        )
        result = self._eval(df)
        assert result.should_enter is False
        # Either "narrow" or "too" in the reason
        low = result.reason.lower()
        assert "narrow" in low or "too" in low or "range" in low

    def test_wide_orb_rejected(self):
        """ORB range > 100 pts → rejected."""
        df = _df_with_today(
            prev_close=23_200, prev_high=23_300, prev_low=23_100,
            today_candles=[
                (23_000, 23_160, 22_990, 23_050),   # h=160, l=22990 → range=170 pts
                (23_055, 23_075, 23_040, 23_060),
                (23_062, 23_080, 23_055, 23_075),
                (23_080, 23_095, 23_072, 23_088),   # breakout attempt
            ],
            prev_candle_count=25,
        )
        result = self._eval(df)
        assert result.should_enter is False
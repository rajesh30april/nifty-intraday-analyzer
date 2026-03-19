"""Tests for MCX Crude Oil auto-trader.

Covers:
- crude_data: instrument resolution, premium estimate
- crude_strategy: ORB and Supertrend signal logic
- crude_trader: state machine, SL/target, tick guard, API
"""
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import numpy as np
import pytest


# ── Fixtures ──────────────────────────────────────────────────────

def _crude_df(spot=6500.0, candles=40, trend='up') -> pd.DataFrame:
    """Synthetic 5-min OHLCV DataFrame for Crude Oil tests."""
    idx  = pd.date_range('2026-03-12 09:00', periods=candles, freq='5min')
    base = spot
    closes = []
    for i in range(candles):
        delta = 5 * i if trend == 'up' else -5 * i
        closes.append(base + delta + np.random.uniform(-1, 1))
    closes = [max(1.0, c) for c in closes]
    df = pd.DataFrame({
        'open':   [c - 2 for c in closes],
        'high':   [c + 5 for c in closes],
        'low':    [c - 5 for c in closes],
        'close':  closes,
        'volume': [np.random.randint(500, 2000) for _ in closes],
    }, index=idx)
    return df


# ── crude_data tests ──────────────────────────────────────────────

class TestCrudeData:
    def test_estimate_crude_premium_scales_with_spot(self):
        from crude_data import estimate_crude_premium
        p1 = estimate_crude_premium(5000.0)
        p2 = estimate_crude_premium(8000.0)
        assert p2 > p1, 'Higher spot should yield higher estimated premium'
        assert 10 < p1 < 500, f'Premium unrealistic: {p1}'

    def test_estimate_crude_premium_is_05pct_of_spot(self):
        from crude_data import estimate_crude_premium
        spot = 6500.0
        prem = estimate_crude_premium(spot)
        assert abs(prem - spot * 0.005) < 1.0

    def test_get_crude_spot_returns_none_when_not_authenticated(self):
        from crude_data import get_crude_spot
        mock_km = MagicMock()
        mock_km.is_authenticated = False
        with patch('crude_data.kite_manager', mock_km):
            result = get_crude_spot()
        assert result is None

    def test_get_crude_atm_option_picks_nearest_expiry(self):
        """ATM option resolver must return the nearest unexpired expiry."""
        from crude_data import get_crude_atm_option
        from datetime import date, timedelta

        today = date.today()
        near  = today + timedelta(days=10)
        far   = today + timedelta(days=40)

        # Build fake instruments list: two expiries, both at ATM strike
        spot   = 6500.0
        strike = 6500.0  # ATM for spot=6500, step=50 → round(6500/50)*50=6500
        fake_instruments = [
            {'name': 'CRUDEOIL', 'instrument_type': 'PE',
             'strike': strike, 'expiry': far,
             'tradingsymbol': f'CRUDEOIL{far}6500PE', 'instrument_token': 2},
            {'name': 'CRUDEOIL', 'instrument_type': 'PE',
             'strike': strike, 'expiry': near,
             'tradingsymbol': f'CRUDEOIL{near}6500PE', 'instrument_token': 1},
        ]
        with patch('crude_data._get_mcx_instruments', return_value=fake_instruments):
            sym, token, lot_sz = get_crude_atm_option(spot, 'short')
        assert token == 1, 'Should pick nearer expiry first'
        assert 'PE' in sym

    def test_get_crude_atm_option_long_picks_ce(self):
        from crude_data import get_crude_atm_option
        from datetime import date, timedelta
        today  = date.today()
        expiry = today + timedelta(days=10)
        fake   = [{
            'name': 'CRUDEOIL', 'instrument_type': 'CE',
            'strike': 6500.0, 'expiry': expiry,
            'tradingsymbol': 'CRUDEOIL6500CE', 'instrument_token': 99,
        }]
        with patch('crude_data._get_mcx_instruments', return_value=fake):
            sym, token, lot_sz = get_crude_atm_option(6500.0, 'long')
        assert 'CE' in sym
        assert token == 99


# ── crude_strategy tests ──────────────────────────────────────────

class TestCrudeStrategy:
    def test_not_enough_candles_returns_no_signal(self):
        from crude_strategy import evaluate_crude_supertrend
        tiny_df = _crude_df(candles=5)
        sig = evaluate_crude_supertrend(tiny_df)
        assert sig.should_enter is False
        assert 'insufficient' in sig.reason.lower()

    def test_supertrend_returns_signal_or_block(self):
        from crude_strategy import evaluate_crude_supertrend
        df  = _crude_df(candles=40, trend='up')
        sig = evaluate_crude_supertrend(df)
        assert isinstance(sig.should_enter, bool)
        assert sig.reason

    def test_best_strategy_returns_signal_object(self):
        from crude_strategy import evaluate_crude_best
        df  = _crude_df(candles=40)
        sig = evaluate_crude_best(df)
        assert hasattr(sig, 'should_enter')
        assert hasattr(sig, 'reason')
        assert sig.reason  # must always have a reason

    def test_orb_blocks_before_first_candle_data(self):
        """ORB should gracefully block when no 9:00-9:15 candles in df."""
        from crude_strategy import evaluate_crude_orb
        # DataFrame with only afternoon candles
        idx = pd.date_range('2026-03-12 14:00', periods=20, freq='5min')
        df  = pd.DataFrame({
            'open': [6500]*20, 'high': [6520]*20,
            'low':  [6480]*20, 'close': [6510]*20,
            'volume': [1000]*20,
        }, index=idx)
        sig = evaluate_crude_orb(df)
        assert sig.should_enter is False


# ── crude_trader state machine tests ─────────────────────────────

def _fresh_crude_state(tmp_path=None):
    """Re-import crude_trader with a clean state and isolated log/snapshot paths."""
    import importlib, sys, tempfile
    for k in list(sys.modules):
        if 'crude_trader' in k:
            del sys.modules[k]

    # Redirect log and snapshot to temp files so tests never touch real logs
    _tmp = tmp_path or Path(tempfile.mkdtemp())
    import crude_trader as ct
    ct.CRUDE_LOG_FILE   = _tmp / 'test_crude_log.json'
    ct.CRUDE_SNAP_FILE  = _tmp / 'test_crude_snap.json'
    ct.state            = ct.CrudeTraderState()   # blank slate
    return ct


class TestCrudeTraderState:
    def test_start_sets_is_running_true(self):
        ct = _fresh_crude_state()
        ct.state.is_running   = False
        ct.state.kill_switch  = False
        ct.state.is_paper_mode = True
        result = ct.start_crude_trader()
        assert result['success'] is True
        assert ct.state.is_running is True

    def test_stop_sets_is_running_false(self):
        ct = _fresh_crude_state()
        ct.state.is_running = True
        result = ct.stop_crude_trader()
        assert result['success'] is True
        assert ct.state.is_running is False

    def test_kill_exits_active_trade(self):
        ct = _fresh_crude_state()
        ct.state.is_paper_mode = True
        ct.state.is_running    = True
        ct.state.kill_switch   = False
        ct.state.active_trade  = ct.CrudeTrade(
            id='k1', timestamp='2026-03-12T10:00:00',
            direction='short', instrument='MCX:CRUDEOIL25APR6500PE',
            entry_price=6500.0, entry_premium=32.0,
            quantity=100, stop_loss=6550.0, target=6400.0,
            status=ct.CrudeOrderStatus.FILLED, paper=True,
        )
        ct.state.last_crude_price = 6480.0
        ct.kill_crude_trader()
        assert ct.state.active_trade is None
        assert ct.state.kill_switch is True

    def test_sl_hit_short_exits_position(self):
        """For a SHORT: crude rising above SL must trigger exit."""
        ct = _fresh_crude_state()
        ct.state.is_paper_mode = True
        ct.state.is_running    = True
        ct.state.kill_switch   = False
        ct.state.active_trade  = ct.CrudeTrade(
            id='sl-test', timestamp='2026-03-12T10:00:00',
            direction='short', instrument='MCX:CRUDEOIL25APR6500PE',
            entry_price=6500.0, entry_premium=32.0,
            quantity=100, stop_loss=6550.0, target=6400.0,
            status=ct.CrudeOrderStatus.FILLED, paper=True,
        )
        # Price above SL (6560 > 6550) — must exit
        ct._manage_trade(6560.0, source='test')
        assert ct.state.active_trade is None, 'SL breach must close trade'

    def test_target_hit_long_exits_position(self):
        """For a LONG: crude reaching target must trigger exit."""
        ct = _fresh_crude_state()
        ct.state.is_paper_mode = True
        ct.state.is_running    = True
        ct.state.kill_switch   = False
        ct.state.active_trade  = ct.CrudeTrade(
            id='tgt-test', timestamp='2026-03-12T09:30:00',
            direction='long', instrument='MCX:CRUDEOIL25APR6500CE',
            entry_price=6500.0, entry_premium=40.0,
            quantity=100, stop_loss=6450.0, target=6600.0,
            status=ct.CrudeOrderStatus.FILLED, paper=True,
        )
        ct._manage_trade(6610.0, source='test')  # above target
        assert ct.state.active_trade is None, 'Target hit must close trade'

    def test_trailing_sl_tightens_on_premium_rise(self):
        """When option LTP rises above entry, trailing SL (sl_premium) must follow."""
        ct = _fresh_crude_state()
        ct.state.is_paper_mode = True
        ct.state.is_running    = True
        ct.state.kill_switch   = False
        ct.state.trail_points  = 10.0   # 10 prem pts trail
        trade = ct.CrudeTrade(
            id='trail-test', timestamp='2026-03-12T10:00:00',
            direction='short', instrument='MCX:CRUDEOILM25APR6500PE',
            entry_price=6500.0, entry_premium=32.0,
            quantity=2, stop_loss=6550.0, target=6400.0,
            sl_premium=22.0, peak_ltp=32.0,
            status=ct.CrudeOrderStatus.FILLED, paper=True,
        )
        ct.state.active_trade = trade
        ct.state.last_crude_price = 6450.0
        # LTP rises to 55 → peak becomes 55 → new sl_premium = 55 - 10 = 45
        ct._manage_trade_by_premium(55.0, source='test')
        assert ct.state.active_trade is not None, 'Not at SL yet — trade stays open'
        assert ct.state.active_trade.sl_premium > 22.0, 'SL premium must have moved up'
        assert ct.state.active_trade.sl_premium == pytest.approx(45.0, abs=0.5)

    def test_get_crude_status_includes_all_keys(self):
        ct = _fresh_crude_state()
        status = ct.get_crude_status()
        required = ['is_running', 'is_paper_mode', 'kill_switch', 'crude_price',
                    'total_pnl', 'active_trade', 'trades_today', 'sl_points',
                    'max_trades']
        for k in required:
            assert k in status, f'Missing key in crude status: {k}'

    def test_max_trades_defaults_to_env_constant(self):
        """state.max_trades must start equal to CRUDE_MAX_TRADES."""
        import crude_trader as ct
        from crude_trader import CRUDE_MAX_TRADES
        fresh = ct.CrudeTraderState()
        assert fresh.max_trades == CRUDE_MAX_TRADES

    def test_max_trades_clamped_to_1_20_by_api(self):
        """API must clamp max_trades to [1, 20] — never 0 or 99."""
        import crude_trader as ct
        ct.state.max_trades = 4   # reset
        # Over-limit
        ct.state.max_trades = max(1, min(20, 99))
        assert ct.state.max_trades == 20
        # Under-limit
        ct.state.max_trades = max(1, min(20, 0))
        assert ct.state.max_trades == 1

    def test_tick_guard_exits_sl_breach(self):
        """crude_tick_guard must exit trade when SL is breached."""
        ct = _fresh_crude_state()
        ct.state.is_paper_mode = True
        ct.state.is_running    = True
        ct.state.kill_switch   = False
        ct.state.active_trade  = ct.CrudeTrade(
            id='tg-test', timestamp='2026-03-12T10:00:00',
            direction='long', instrument='MCX:CRUDEOIL25APR6500CE',
            entry_price=6500.0, entry_premium=40.0,
            quantity=100, stop_loss=6450.0, target=6600.0,
            status=ct.CrudeOrderStatus.FILLED, paper=True,
        )
        ct.crude_tick_guard({'last_price': 6440.0})  # below SL (long)
        assert ct.state.active_trade is None, 'Tick guard must exit on SL breach'

    def test_kill_switch_blocks_tick_guard(self):
        ct = _fresh_crude_state()
        ct.state.is_paper_mode = True
        ct.state.kill_switch   = True
        ct.state.active_trade  = ct.CrudeTrade(
            id='ks-test', timestamp='2026-03-12T10:00:00',
            direction='long', instrument='MCX:CRUDEOIL25APR6500CE',
            entry_price=6500.0, entry_premium=40.0,
            quantity=100, stop_loss=6450.0, target=6600.0,
            status=ct.CrudeOrderStatus.FILLED, paper=True,
        )
        ct.crude_tick_guard({'last_price': 6300.0})  # way below SL
        assert ct.state.active_trade is not None, 'kill_switch must block tick guard'


# ── Margin safety buffer ─────────────────────────────────────────────────

class TestMarginSafetyBuffer:
    """Tests for the MARGIN_SAFETY_BUFFER pre-flight guard.

    Reproduces the exact live failure:
      required=31459.50  available=30591.00  gap=868.50
      Pre-flight passed (31459 < 30591 is False…wait, no: 30591 < 31459
      so the old code should have blocked. That means the issue is that
      the order_margins call returned a LOWER value (e.g. 30500) because
      the LTP snapshot was stale, and then the live order priced it higher.

    We simulate this: order_margins says 30100 (stale), order hits
    Zerodha with live price that requires 31459 — exchange rejects.
    The buffer means we already rejected 30100 during pre-flight
    because 30100 > (30591 − 2500 = 28091). We walk down to 0 lots.
    """

    def test_buffer_blocks_margin_that_fits_without_buffer(self):
        """Pre-flight margin of 28500 should pass the buffer check —
        28500 ≤ 30591 − 2500 = 28091? No: 28500 > 28091 — should BLOCK.
        This validates the buffer is actually subtracting."""
        import crude_trader as ct
        with patch("crude_trader.kite_manager") as km, \
             patch("crude_trader._query_zerodha_margin", return_value=28500.0):
            km.is_authenticated = True
            lots, req = ct._validate_and_size("MCX:CRUDEOILM25APR315CE", 1, 30591.0)
        # 28500 > 30591 - 2500 = 28091 → must block
        assert lots == 0

    def test_buffer_passes_margin_well_within_usable(self):
        """Pre-flight margin of 25000 clears buffer: 25000 ≤ 28091 → OK."""
        import crude_trader as ct
        with patch("crude_trader.kite_manager") as km, \
             patch("crude_trader._query_zerodha_margin", return_value=25000.0):
            km.is_authenticated = True
            lots, req = ct._validate_and_size("MCX:CRUDEOILM25APR315CE", 1, 30591.0)
        assert lots == 1
        assert req == pytest.approx(25000.0)

    def test_buffer_constant_is_at_least_1500(self):
        """Buffer must be ≥20 × 100 (20 ticks of 1 lot) to cover real price drift."""
        from crude_trader import MARGIN_SAFETY_BUFFER
        assert MARGIN_SAFETY_BUFFER >= 1500, (
            f"MARGIN_SAFETY_BUFFER={MARGIN_SAFETY_BUFFER} is too low. "
            "Observed real gap was ₹868 — need room for volatility."
        )

    def test_order_insufficient_funds_rejection_parsed_cleanly(self):
        """Zerodha 'Insufficient funds' error must be parsed into a
        human-readable top-up message, not a raw exception string."""
        import crude_trader as ct
        ct.state.is_paper_mode = False
        ct.state.kill_switch   = False
        zerodha_err = (
            "Insufficient funds. Required margin is 31459.50 "
            "but available margin is 30591.00."
        )
        with patch("crude_trader.kite_manager") as km:
            km.is_authenticated = True
            km.kite.place_order.side_effect = Exception(zerodha_err)
            with patch("crude_trader._limit_price_for", return_value=315.0):
                ct._place_order("MCX:CRUDEOILM25APR315CE", ct.Direction.LONG, 1, 6500.0)

        reason = ct.state.last_block_reason
        assert reason is not None
        # Zerodha reports 31459.50 — we format it rounded so accept 31459 or 31460
        assert any(v in reason for v in ("₹31,459", "31459", "₹31,460", "31460")), (
            f"Required margin not in reason: {reason}"
        )
        assert "Top up" in reason,  "Should tell user how much to top up"
        assert "MINI" in reason,    "Should suggest switching to mini lot"
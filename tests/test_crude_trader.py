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
            sym, token = get_crude_atm_option(spot, 'short')
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
            sym, token = get_crude_atm_option(6500.0, 'long')
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

    def test_trailing_sl_tightens_for_short(self):
        """Trailing SL moves closer when crude falls (SHORT position wins)."""
        ct = _fresh_crude_state()
        ct.state.is_paper_mode    = True
        ct.state.is_running       = True
        ct.state.kill_switch      = False
        ct.state.trail_points     = 25.0
        ct.state.lowest_since_entry = 6500.0
        ct.state.active_trade = ct.CrudeTrade(
            id='trail-test', timestamp='2026-03-12T10:00:00',
            direction='short', instrument='MCX:CRUDEOIL25APR6500PE',
            entry_price=6500.0, entry_premium=32.0,
            quantity=100, stop_loss=6550.0, target=6400.0,
            status=ct.CrudeOrderStatus.FILLED, paper=True,
        )
        # Crude drops to 6440 → new lowest → SL should move to 6440+25=6465
        ct._manage_trade(6440.0, source='test')
        assert ct.state.active_trade is not None, 'Not at SL yet — trade stays open'
        assert ct.state.active_trade.stop_loss < 6550.0, 'Trailing SL must tighten'
        assert ct.state.active_trade.stop_loss == pytest.approx(6465.0, abs=1)

    def test_get_crude_status_includes_all_keys(self):
        ct = _fresh_crude_state()
        status = ct.get_crude_status()
        required = ['is_running', 'is_paper_mode', 'kill_switch', 'crude_price',
                    'total_pnl', 'active_trade', 'trades_today', 'sl_points']
        for k in required:
            assert k in status, f'Missing key in crude status: {k}'

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
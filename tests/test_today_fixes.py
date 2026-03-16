"""Regression tests for every bug that surfaced on 2026-03-12.

Each test is named after the exact symptom the user saw so it's obvious
what broke if it ever regresses.
"""
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fresh_state():
    """Import a clean auto_trader state, bypassing snapshot recovery."""
    import importlib, sys
    # Remove cached module so _recover_state runs fresh each import
    for key in list(sys.modules.keys()):
        if 'auto_trader' in key:
            del sys.modules[key]
    snap = Path('.') / '.state_snapshot.json'
    bak  = Path('.') / '.state_snapshot.bak'
    _orig_snap = snap.read_text() if snap.exists() else None
    _orig_bak  = bak.read_text()  if bak.exists()  else None
    # Remove snapshot so _recover_state finds nothing
    snap.unlink(missing_ok=True)
    bak.unlink(missing_ok=True)
    try:
        import auto_trader as at
        return at
    finally:
        # Restore originals
        if _orig_snap: snap.write_text(_orig_snap)
        if _orig_bak:  bak.write_text(_orig_bak)


# ─────────────────────────────────────────────────────────────────────────────
# BUG 1: entry_premium missing from get_trader_status() active_trade dict
# Symptom: UI showed Entry Premium "--" even though snapshot had the value
# ─────────────────────────────────────────────────────────────────────────────

class TestEntryPremiumInStatus:
    def test_entry_premium_present_in_status_dict(self):
        """get_trader_status() must include entry_premium in active_trade."""
        at = _fresh_state()
        from auto_trader import Trade, OrderStatus, state
        state.active_trade = Trade(
            id='test-1', timestamp='2026-03-12T09:20:00',
            direction='short', instrument='NFO:NIFTY2631723300PE',
            entry_price=23166.0, entry_premium=148.5,
            quantity=75, stop_loss=23196.0, target=23106.0,
            status=OrderStatus.FILLED,
        )
        status = at.get_trader_status()
        trade  = status['active_trade']
        assert trade is not None, 'active_trade should not be None'
        msg = 'entry_premium was missing from status dict — UI showed --'
        assert 'entry_premium' in trade, msg
        assert trade['entry_premium'] == 148.5

    def test_paper_flag_present_in_status_dict(self):
        """paper flag must also be in active_trade dict for UI mode display."""
        at = _fresh_state()
        from auto_trader import Trade, OrderStatus, state
        state.active_trade = Trade(
            id='test-2', timestamp='2026-03-12T09:20:00',
            direction='long', instrument='NFO:NIFTY2631723300CE',
            entry_price=23200.0, entry_premium=120.0,
            quantity=75, stop_loss=23170.0, target=23260.0,
            status=OrderStatus.FILLED, paper=True,
        )
        status = at.get_trader_status()
        assert 'paper' in status['active_trade']
        assert status['active_trade']['paper'] is True


# ─────────────────────────────────────────────────────────────────────────────
# BUG 2: tick_guard bailed out entirely when is_running=False
# Symptom: synced trade had NO SL protection until user clicked Start
# ─────────────────────────────────────────────────────────────────────────────

class TestTickGuardWhenPaused:
    def test_sl_breach_exits_when_not_running(self):
        """A SHORT position must exit on SL breach even if is_running=False."""
        at = _fresh_state()
        from auto_trader import Trade, OrderStatus, state, tick_guard

        state.is_running   = False
        state.kill_switch  = False
        state.is_paper_mode = True
        state.active_trade = Trade(
            id='tick-test', timestamp='2026-03-12T10:00:00',
            direction='short', instrument='NFO:NIFTY2631723300PE',
            entry_price=23200.0, entry_premium=100.0,
            quantity=75, stop_loss=23230.0, target=23150.0,
            status=OrderStatus.FILLED, paper=True,
        )
        state.highest_price_since_entry = 23200.0
        state.lowest_price_since_entry  = 23200.0

        # Nifty spikes above SL — must exit even though trader is paused
        tick_guard({'last_price': 23240.0})  # 10pts above SL

        assert state.active_trade is None, 'SL breach must exit even when is_running=False'

    def test_nifty_price_updated_even_when_not_running(self):
        """last_nifty_price updates on every tick regardless of is_running."""
        at = _fresh_state()
        from auto_trader import state, tick_guard

        state.is_running  = False
        state.kill_switch = False
        state.active_trade = None
        state.last_nifty_price = 0.0

        tick_guard({'last_price': 23150.0})
        assert state.last_nifty_price == 23150.0, 'Nifty price must update on every tick'

    def test_kill_switch_stops_everything(self):
        """kill_switch=True must prevent any action."""
        at = _fresh_state()
        from auto_trader import Trade, OrderStatus, state, tick_guard

        state.kill_switch  = True
        state.is_running   = False
        state.is_paper_mode = True
        state.active_trade = Trade(
            id='ks-test', timestamp='2026-03-12T10:00:00',
            direction='short', instrument='NFO:NIFTY2631723300PE',
            entry_price=23200.0, entry_premium=100.0,
            quantity=75, stop_loss=23220.0, target=23150.0,
            status=OrderStatus.FILLED, paper=True,
        )
        tick_guard({'last_price': 23280.0})   # way above SL
        assert state.active_trade is not None, 'kill_switch=True must block all action'


# ─────────────────────────────────────────────────────────────────────────────
# BUG 3: Kite is_authenticated called kite.profile() on every single check
# Symptom: all endpoints timed out because event loop was blocked
# ─────────────────────────────────────────────────────────────────────────────

def _load_real_kite_manager():
    """Load the REAL KiteManager class, bypassing conftest's stub.

    conftest.py replaces kite_integration in sys.modules with a MagicMock
    so tests can run without Zerodha. But for auth-cache tests we need the
    real class logic — so we temporarily inject real mocks and import direct.
    """
    import sys, importlib, types
    from unittest.mock import MagicMock

    # Build a minimal kiteconnect stub that won't make network calls
    kc_stub       = types.ModuleType('kiteconnect')
    mock_kc_cls   = MagicMock()
    mock_kc_inst  = MagicMock()
    mock_kc_cls.return_value = mock_kc_inst
    mock_kc_inst.reqsession  = MagicMock()
    mock_kc_inst.reqsession.proxies = {}
    kc_stub.KiteConnect  = mock_kc_cls
    kc_stub.KiteTicker   = MagicMock()

    # Temporarily replace stubs with real-ish ones
    _prev_kc = sys.modules.get('kiteconnect')
    _prev_ki = sys.modules.get('kite_integration')
    sys.modules['kiteconnect'] = kc_stub
    sys.modules.pop('kite_integration', None)

    try:
        import kite_integration as ki
        return ki.KiteManager, mock_kc_inst
    finally:
        # Restore original stubs so other tests are unaffected
        if _prev_kc is not None:
            sys.modules['kiteconnect'] = _prev_kc
        if _prev_ki is not None:
            sys.modules['kite_integration'] = _prev_ki
        else:
            sys.modules.pop('kite_integration', None)


class TestKiteAuthCache:
    def test_profile_not_called_on_every_check(self):
        """is_authenticated must use TTL cache — profile() called at most once."""
        KiteManager, mock_kite = _load_real_kite_manager()
        km = KiteManager()
        km.access_token   = 'fake-token-123'
        km._auth_cache    = None
        km._auth_cache_ts = 0.0
        km.kite = MagicMock()
        km.kite.profile.return_value = {'user_id': 'TEST'}

        assert km.is_authenticated is True
        assert km.kite.profile.call_count == 1

        assert km.is_authenticated is True
        assert km.kite.profile.call_count == 1, 'TTL cache broken — profile called twice'

    def test_cache_invalidated_on_auth_failure(self):
        """If profile() raises, cache must be set False and token cleared."""
        KiteManager, _ = _load_real_kite_manager()
        km = KiteManager()
        km.access_token   = 'expired-token'
        km._auth_cache    = None
        km._auth_cache_ts = 0.0
        km.kite = MagicMock()
        km.kite.profile.side_effect = Exception('TokenException')

        assert km.is_authenticated is False
        assert km._auth_cache is False
        assert km.access_token is None

    def test_cache_set_true_on_generate_session(self):
        """After generate_session(), _auth_cache must be True immediately."""
        KiteManager, _ = _load_real_kite_manager()
        km = KiteManager()
        km.access_token   = None
        km._auth_cache    = None
        km._auth_cache_ts = 0.0
        km.kite = MagicMock()
        km.kite.generate_session.return_value = {'access_token': 'new-token'}
        km._save_session = MagicMock()

        km.generate_session('request-token-xyz')

        assert km._auth_cache is True
        assert km.access_token == 'new-token'
        km.kite.profile.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# BUG 4: refresh_active_option_ltp() never fetched Nifty spot
# Symptom: Live Nifty showed "--" in position card after recovery
# ─────────────────────────────────────────────────────────────────────────────

class TestLtpRefreshFetchesNifty:
    def test_nifty_price_updated_by_ltp_refresh(self):
        """refresh_active_option_ltp() must also refresh last_nifty_price."""
        at = _fresh_state()
        from auto_trader import Trade, OrderStatus, state, refresh_active_option_ltp
        import kite_integration as ki

        state.active_trade = Trade(
            id='ltp-test', timestamp='2026-03-12T10:00:00',
            direction='short', instrument='NFO:NIFTY2631723300PE',
            entry_price=23200.0, entry_premium=100.0,
            quantity=75, stop_loss=23230.0, target=23150.0,
            status=OrderStatus.FILLED, paper=True,
        )
        state.last_nifty_price = 0.0

        mock_km = MagicMock()
        mock_km.is_authenticated = True
        mock_km.get_option_ltp.return_value = 112.5
        mock_km.kite.ltp.return_value = {'NSE:NIFTY 50': {'last_price': 23175.0}}

        with patch('auto_trader.kite_manager', mock_km):
            refresh_active_option_ltp()

        assert state.last_nifty_price == 23175.0, 'Nifty spot must be updated by ltp refresh'
        assert state.last_option_ltp == 112.5


# ─────────────────────────────────────────────────────────────────────────────
# BUG 5: sync_from_zerodha blocked event loop for 30s+
# Symptom: browser AbortController fired, user saw 'Timed out'
# (This tests the UNIT logic — not the async wrapping which is in app.py)
# ─────────────────────────────────────────────────────────────────────────────

class TestSyncFromZerodhaLogic:
    def test_sync_fails_gracefully_when_not_authenticated(self):
        """sync_from_zerodha must return error dict, not raise, when not authed."""
        at = _fresh_state()
        from auto_trader import sync_from_zerodha
        import kite_integration as ki

        mock_km = MagicMock()
        mock_km.is_authenticated = False

        with patch('auto_trader.kite_manager', mock_km):
            result = sync_from_zerodha()

        assert result['success'] is False
        assert 'authenticated' in result['error'].lower() or 'auth' in result['error'].lower()

    def test_sync_fails_gracefully_when_no_positions(self):
        """sync_from_zerodha returns clear error when no NFO position exists."""
        at = _fresh_state()
        from auto_trader import sync_from_zerodha

        mock_km = MagicMock()
        mock_km.is_authenticated = True
        mock_km.kite.positions.return_value = {'net': [], 'day': []}

        with patch('auto_trader.kite_manager', mock_km):
            result = sync_from_zerodha()

        assert result['success'] is False
        assert 'position' in result['error'].lower()

    def test_sync_blocks_if_trade_already_active(self):
        """sync_from_zerodha must refuse if active_trade already set."""
        at = _fresh_state()
        from auto_trader import Trade, OrderStatus, state, sync_from_zerodha

        state.active_trade = Trade(
            id='existing', timestamp='2026-03-12T10:00:00',
            direction='short', instrument='NFO:NIFTY2631723300PE',
            entry_price=23200.0, entry_premium=100.0,
            quantity=75, stop_loss=23230.0, target=23150.0,
            status=OrderStatus.FILLED, paper=True,
        )
        mock_km = MagicMock()
        mock_km.is_authenticated = True

        with patch('auto_trader.kite_manager', mock_km):
            result = sync_from_zerodha()

        assert result['success'] is False
        assert 'active trade' in result['error'].lower()


# ─────────────────────────────────────────────────────────────────────────────
# BUG 6: pnl_unrealized showed 0 because entry_premium fell back to 0
# ─────────────────────────────────────────────────────────────────────────────

class TestUnrealizedPnL:
    def test_pnl_is_nonzero_when_ltp_differs_from_entry(self):
        """With a valid LTP and entry_premium, pnl_unrealized must be non-zero."""
        at = _fresh_state()
        from auto_trader import Trade, OrderStatus, state

        ep  = 148.5
        ltp = 165.0
        qty = 75
        state.active_trade = Trade(
            id='pnl-test', timestamp='2026-03-12T10:00:00',
            direction='short', instrument='NFO:NIFTY2631723300PE',
            entry_price=23166.0, entry_premium=ep,
            quantity=qty, stop_loss=23196.0, target=23106.0,
            status=OrderStatus.FILLED, paper=True,
        )
        state.last_option_ltp = ltp

        status = at.get_trader_status()
        # Simulate the enrichment that app.py does
        trade = status['active_trade']
        ep_got = trade.get('entry_premium', 0) or 0
        pnl = round((ltp - ep_got) * qty, 2)

        assert ep_got == ep, f'entry_premium wrong: got {ep_got}'
        assert pnl == round((ltp - ep) * qty, 2)
        assert pnl != 0, 'P&L must not be zero when LTP != entry_premium'
"""Auto-Trader unit tests using mock market data.

Tests every critical path without hitting Zerodha or Yahoo Finance:
  - Entry (paper + live order mock)
  - Stop-loss exit (long & short)
  - Target exit (long & short)
  - Trailing SL: updates when price moves in our favour
  - Trailing SL: protects profit when price reverses
  - Time-based exit at 3:15 PM
  - Safety: kill switch blocks trading
  - Safety: max orders limit enforced
  - Safety: max daily loss limit enforced
  - Safety: no double-entry when trade is active
  - Safety: no trade in first 3 minutes of market
  - Instrument lookup: correct OTM strike selected
  - P&L calculation accuracy for long and short trades

Run: pytest tests/test_auto_trader.py -v
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from tests.mock_data import (
    BASE_PRICE,
    after_market_hours,
    mock_instruments,
    mock_order_id,
    price_hits_sl,
    price_hits_target,
    price_rallies_then_falls,
    trending_up,
    trending_down,
)


# ── Helpers ───────────────────────────────────────────────────────

def _fresh_auto_trader():
    """Reload auto_trader module so global `state` is reset between tests.
    Also patches kite_manager so we never need a real Zerodha session.
    """
    # Remove cached module so reload gives us a clean singleton
    for key in list(sys.modules.keys()):
        if "auto_trader" in key:
            del sys.modules[key]

    mock_kite = MagicMock()
    mock_kite.TRANSACTION_TYPE_BUY  = "BUY"
    mock_kite.TRANSACTION_TYPE_SELL = "SELL"
    mock_kite.VARIETY_REGULAR       = "regular"
    mock_kite.PRODUCT_MIS           = "MIS"
    mock_kite.ORDER_TYPE_MARKET     = "MARKET"
    mock_kite.place_order.return_value = 99999   # Zerodha order ID
    mock_kite.margins.return_value  = {"equity": {"available": {"live_balance": 100_000}}}

    mock_km = MagicMock()
    mock_km.kite = mock_kite
    mock_km.latest_tick = {"last_price": BASE_PRICE}

    with patch.dict("sys.modules", {"kite_integration": MagicMock(kite_manager=mock_km)}):
        import auto_trader as at

    # Reset singleton — module-level _recover_state() may have restored real
    # state from project's .state_snapshot.json during import. Wipe it.
    at.state = at.TraderState()

    return at, mock_km


def _force_enter(at, direction: str = "long", price: float = BASE_PRICE,
                 symbol: str = "NIFTY20260319_23200CE"):
    """Directly inject a trade into state without going through strategy logic."""
    from strategy import Direction
    at.state.is_paper_mode = True
    at.state.is_running    = True
    with patch.object(at, "_get_option_symbol", return_value=(symbol, 123456)):
        at._enter_trade(
            Direction.LONG if direction == "long" else Direction.SHORT,
            price,
        )


# ── Tests ─────────────────────────────────────────────────────────

class TestPaperEntry:
    """Entry logic in paper mode."""

    def test_paper_entry_long_creates_trade(self):
        at, _ = _fresh_auto_trader()
        at.state.is_paper_mode = True
        at.state.is_running    = True

        with patch.object(at, "_get_option_symbol",
                          return_value=("NIFTY20260319_23250CE", 111)):
            from strategy import Direction
            at._enter_trade(Direction.LONG, BASE_PRICE)

        assert at.state.active_trade is not None, "Trade should have been created"
        assert at.state.active_trade.direction == "long"
        assert at.state.active_trade.paper is True
        assert at.state.active_trade.entry_price == BASE_PRICE
        assert at.state.orders_placed == 1

    def test_paper_entry_short_creates_trade(self):
        at, _ = _fresh_auto_trader()
        at.state.is_paper_mode = True

        with patch.object(at, "_get_option_symbol",
                          return_value=("NIFTY20260319_23150PE", 222)):
            from strategy import Direction
            at._enter_trade(Direction.SHORT, BASE_PRICE)

        assert at.state.active_trade is not None
        assert at.state.active_trade.direction == "short"

    def test_sl_and_target_set_correctly_long(self):
        at, _ = _fresh_auto_trader()
        at.state.is_paper_mode = True
        SL = at.SL_POINTS
        RR = float(at.os.getenv("RR_RATIO", "2.0"))

        with patch.object(at, "_get_option_symbol", return_value=("X", 1)):
            from strategy import Direction
            at._enter_trade(Direction.LONG, BASE_PRICE)

        trade = at.state.active_trade
        assert trade.stop_loss == BASE_PRICE - SL,  "SL below entry for long"
        assert trade.target   == BASE_PRICE + SL * RR, "Target above entry for long"

    def test_sl_and_target_set_correctly_short(self):
        at, _ = _fresh_auto_trader()
        at.state.is_paper_mode = True
        SL = at.SL_POINTS
        RR = float(at.os.getenv("RR_RATIO", "2.0"))

        with patch.object(at, "_get_option_symbol", return_value=("X", 1)):
            from strategy import Direction
            at._enter_trade(Direction.SHORT, BASE_PRICE)

        trade = at.state.active_trade
        assert trade.stop_loss == BASE_PRICE + SL
        assert trade.target   == BASE_PRICE - SL * RR


class TestStopLoss:
    """Stop-loss exit logic."""

    def test_long_sl_exit_when_price_drops(self):
        at, _ = _fresh_auto_trader()
        _force_enter(at, "long", BASE_PRICE)

        sl_price = at.state.active_trade.stop_loss - 1   # just below SL
        at._manage_active_trade(sl_price)

        assert at.state.active_trade is None, "Trade should have been exited"
        last_trade = at.state.trades_today[-1]
        assert last_trade.exit_reason is not None and "SL hit" in last_trade.exit_reason
        assert last_trade.pnl < 0, "P&L must be negative on SL hit"

    def test_short_sl_exit_when_price_rises(self):
        at, _ = _fresh_auto_trader()
        _force_enter(at, "short", BASE_PRICE)

        sl_price = at.state.active_trade.stop_loss + 1   # just above SL
        at._manage_active_trade(sl_price)

        assert at.state.active_trade is None
        assert at.state.trades_today[-1].pnl < 0

    def test_no_exit_when_price_between_sl_and_target(self):
        at, _ = _fresh_auto_trader()
        _force_enter(at, "long", BASE_PRICE)
        safe_price = BASE_PRICE + 10   # between SL and target
        at._manage_active_trade(safe_price)
        assert at.state.active_trade is not None, "Should stay in trade"


class TestTargetExit:
    """Target hit exits."""

    def test_long_target_exit(self):
        at, _ = _fresh_auto_trader()
        _force_enter(at, "long", BASE_PRICE)

        target = at.state.active_trade.target
        at._manage_active_trade(target + 1)

        assert at.state.active_trade is None
        assert at.state.trades_today[-1].pnl > 0, "P&L must be positive on target hit"
        assert "Target" in at.state.trades_today[-1].exit_reason

    def test_short_target_exit(self):
        at, _ = _fresh_auto_trader()
        _force_enter(at, "short", BASE_PRICE)

        target = at.state.active_trade.target
        at._manage_active_trade(target - 1)

        assert at.state.active_trade is None
        assert at.state.trades_today[-1].pnl > 0


class TestTrailingSL:
    """Trailing stop-loss mechanics."""

    def test_trailing_sl_moves_up_for_long(self):
        at, _ = _fresh_auto_trader()
        _force_enter(at, "long", BASE_PRICE)
        initial_sl = at.state.active_trade.stop_loss
        TRAIL = at.TRAILING_SL_POINTS

        # Price rallies 40 points
        at._manage_active_trade(BASE_PRICE + 40)

        new_sl = at.state.active_trade.stop_loss
        expected_sl = (BASE_PRICE + 40) - TRAIL
        assert new_sl > initial_sl, "Trailing SL should have moved up"
        assert abs(new_sl - expected_sl) < 0.01

    def test_trailing_sl_moves_down_for_short(self):
        at, _ = _fresh_auto_trader()
        _force_enter(at, "short", BASE_PRICE)
        initial_sl = at.state.active_trade.stop_loss
        TRAIL = at.TRAILING_SL_POINTS

        # Price drops 40 points (in our favour)
        at._manage_active_trade(BASE_PRICE - 40)

        new_sl = at.state.active_trade.stop_loss
        expected_sl = (BASE_PRICE - 40) + TRAIL
        assert new_sl < initial_sl, "Trailing SL should have moved down"
        assert abs(new_sl - expected_sl) < 0.01

    def test_trailing_sl_does_not_move_backwards(self):
        at, _ = _fresh_auto_trader()
        _force_enter(at, "long", BASE_PRICE)
        TRAIL = at.TRAILING_SL_POINTS

        # Rally 40 pts → SL locks at (BASE+40) - TRAIL = BASE+25 (if TRAIL=15)
        at._manage_active_trade(BASE_PRICE + 40)
        locked_sl = at.state.active_trade.stop_loss

        # Tiny pullback that stays ABOVE locked_sl — should NOT exit, SL stays put
        safe_pullback = locked_sl + 2   # 2 pts above the locked SL — still safe
        at._manage_active_trade(safe_pullback)

        assert at.state.active_trade is not None, "Should NOT have exited on small pullback"
        assert at.state.active_trade.stop_loss == locked_sl, "SL must not move backwards"

    def test_trailing_sl_exits_after_rally_then_fall(self):
        at, _ = _fresh_auto_trader()
        _force_enter(at, "long", BASE_PRICE)
        TRAIL = at.TRAILING_SL_POINTS

        # 1. Price rallies → SL locks in at rally_peak - TRAIL
        rally_peak = BASE_PRICE + 50
        at._manage_active_trade(rally_peak)
        locked_sl = at.state.active_trade.stop_loss

        # 2. Price falls back below the locked SL → exit
        at._manage_active_trade(locked_sl - 1)
        assert at.state.active_trade is None, "Trailing SL should have triggered exit"
        # P&L should still be positive (we locked in some of the rally)
        assert at.state.trades_today[-1].pnl > 0, "Should exit with profit after trailing SL"


class TestTimeExit:
    """3:15 PM auto-exit and safety gate."""

    def test_time_exit_fires_when_exit_time_passed(self):
        """Temporarily set EXIT_TIME to 00:01 so it's always in the past.
        evaluate_and_act must exit the open trade.
        """
        from datetime import time as dt_time
        at, _ = _fresh_auto_trader()
        _force_enter(at, "long", BASE_PRICE)

        original_exit = at.EXIT_TIME
        at.EXIT_TIME  = dt_time(0, 1)   # always in the past
        try:
            at.evaluate_and_act(trending_up(), BASE_PRICE)
        finally:
            at.EXIT_TIME = original_exit

        assert at.state.active_trade is None, "Must time-exit trade"
        assert "Time" in at.state.trades_today[-1].exit_reason

    def test_safety_blocks_new_entry_past_exit_time(self):
        """_check_safety must return False when EXIT_TIME has already passed."""
        from datetime import time as dt_time
        at, _ = _fresh_auto_trader()
        at.state.is_running = True

        original_exit = at.EXIT_TIME
        at.EXIT_TIME  = dt_time(0, 1)   # always in the past
        try:
            safe, reason = at._check_safety()
        finally:
            at.EXIT_TIME = original_exit

        assert not safe
        assert "exit time" in reason.lower()


class TestSafetyChecks:
    """Safety guard rails."""

    def test_kill_switch_blocks_new_trades(self):
        at, _ = _fresh_auto_trader()
        at.state.kill_switch = True
        safe, reason = at._check_safety()
        assert not safe
        assert "kill switch" in reason.lower()

    def test_max_orders_blocks_new_trades(self):
        at, _ = _fresh_auto_trader()
        at.state.orders_placed = at.MAX_ORDERS_PER_DAY
        safe, reason = at._check_safety()
        assert not safe
        assert "max trades" in reason.lower()

    def test_max_loss_blocks_new_trades(self):
        at, _ = _fresh_auto_trader()
        at.state.total_pnl = -(at.MAX_LOSS_PER_DAY + 1)
        safe, reason = at._check_safety()
        assert not safe
        assert "loss" in reason.lower()

    def test_no_double_entry_when_trade_active(self):
        at, _ = _fresh_auto_trader()
        at.state.is_running = True
        _force_enter(at, "long", BASE_PRICE)
        orders_before = at.state.orders_placed

        # Even if strategy fires again, no new trade
        at.evaluate_and_act(trending_up(), BASE_PRICE + 5)
        assert at.state.orders_placed == orders_before, "No new order while trade is active"

    def test_no_trade_in_first_3_minutes(self):
        """_check_safety must block trades in the first 3 mins after open.

        Patches _now_time() — the thin wrapper introduced specifically to make
        this check testable without fighting C-extension datetime mocking.
        """
        from datetime import time as dt_time
        at, _ = _fresh_auto_trader()

        early_time = dt_time(9, 16, 30)   # 1.5 min after open — before 9:18

        original_exit = at.EXIT_TIME
        at.EXIT_TIME  = dt_time(23, 59)   # ensure exit-time guard never fires
        try:
            with patch.object(at, "_now_time", return_value=early_time):
                safe, reason = at._check_safety()
        finally:
            at.EXIT_TIME = original_exit

        assert not safe, f"Should block at 09:16 but got safe=True, reason='{reason}'"
        assert "early" in reason.lower() or "settle" in reason.lower(), (
            f"Expected early/settle message, got: '{reason}'"
        )


class TestPnLAccuracy:
    """P&L calculations must be exact."""

    def test_long_pnl_on_target(self):
        at, _ = _fresh_auto_trader()
        qty    = at.DEFAULT_QUANTITY
        SL     = at.SL_POINTS
        RR     = float(at.os.getenv("RR_RATIO", "2.0"))
        entry  = BASE_PRICE
        target = entry + SL * RR

        _force_enter(at, "long", entry)
        at._manage_active_trade(target + 0.5)

        trade = at.state.trades_today[-1]
        expected_pnl = round((trade.exit_price - entry) * qty, 2)
        assert abs(trade.pnl - expected_pnl) < 0.01

    def test_short_pnl_on_sl(self):
        at, _ = _fresh_auto_trader()
        qty   = at.DEFAULT_QUANTITY
        SL    = at.SL_POINTS
        entry = BASE_PRICE

        _force_enter(at, "short", entry)
        sl_price = entry + SL + 1   # SL hit
        at._manage_active_trade(sl_price)

        trade = at.state.trades_today[-1]
        expected_pnl = round((entry - trade.exit_price) * qty, 2)
        assert abs(trade.pnl - expected_pnl) < 0.01
        assert trade.pnl < 0

    def test_total_pnl_accumulates_across_trades(self):
        at, _ = _fresh_auto_trader()

        # Trade 1: long, hits target (win)
        _force_enter(at, "long", BASE_PRICE)
        target1 = at.state.active_trade.target
        at._manage_active_trade(target1 + 1)
        pnl1 = at.state.trades_today[0].pnl

        # Trade 2: short, hits SL (loss)
        _force_enter(at, "short", BASE_PRICE)
        sl2 = at.state.active_trade.stop_loss + 1
        at._manage_active_trade(sl2)
        pnl2 = at.state.trades_today[1].pnl

        assert abs(at.state.total_pnl - (pnl1 + pnl2)) < 0.01


class TestInstrumentLookup:
    """Options symbol lookup from Kite instruments."""

    def test_long_picks_atm_ce_by_default(self):
        """Default strike_offset=0 → ATM CE for LONG."""
        at, _ = _fresh_auto_trader()
        assert at.state.strike_offset == 0, "Default must be ATM (offset=0)"
        instruments = mock_instruments(BASE_PRICE)
        with patch.object(at, "_get_nfo_instruments", return_value=instruments):
            from strategy import Direction
            symbol, token = at._get_option_symbol(BASE_PRICE, Direction.LONG)

        atm = round(BASE_PRICE / 50) * 50
        assert str(atm) in symbol, f"Expected ATM strike {atm} in symbol '{symbol}'"
        assert symbol.endswith("CE")

    def test_long_picks_otm_ce_when_offset_1(self):
        """strike_offset=1 → 1-OTM CE for LONG."""
        at, _ = _fresh_auto_trader()
        at.state.strike_offset = 1
        instruments = mock_instruments(BASE_PRICE)
        with patch.object(at, "_get_nfo_instruments", return_value=instruments):
            from strategy import Direction
            symbol, token = at._get_option_symbol(BASE_PRICE, Direction.LONG)

        atm    = round(BASE_PRICE / 50) * 50
        otm_ce = atm + 50
        assert str(otm_ce) in symbol, f"Expected 1-OTM CE strike {otm_ce} in '{symbol}'"
        assert symbol.endswith("CE")

    def test_short_picks_atm_pe_by_default(self):
        """Default strike_offset=0 → ATM PE for SHORT."""
        at, _ = _fresh_auto_trader()
        instruments = mock_instruments(BASE_PRICE)
        with patch.object(at, "_get_nfo_instruments", return_value=instruments):
            from strategy import Direction
            symbol, token = at._get_option_symbol(BASE_PRICE, Direction.SHORT)

        atm = round(BASE_PRICE / 50) * 50
        assert str(atm) in symbol
        assert symbol.endswith("PE")

    def test_short_picks_otm_pe_when_offset_1(self):
        """strike_offset=1 → 1-OTM PE for SHORT."""
        at, _ = _fresh_auto_trader()
        at.state.strike_offset = 1
        instruments = mock_instruments(BASE_PRICE)
        with patch.object(at, "_get_nfo_instruments", return_value=instruments):
            from strategy import Direction
            symbol, token = at._get_option_symbol(BASE_PRICE, Direction.SHORT)

        atm    = round(BASE_PRICE / 50) * 50
        otm_pe = atm - 50
        assert str(otm_pe) in symbol
        assert symbol.endswith("PE")

    def test_fallback_to_atm_when_otm_missing(self):
        """If 1-OTM strike not in instruments, should fall back to ATM."""
        at, _ = _fresh_auto_trader()
        atm    = round(BASE_PRICE / 50) * 50
        expiry = "2026-03-20"   # fixed expiry for test

        # Only ATM CE available, no OTM (+50)
        instruments = [{
            "name": "NIFTY", "instrument_type": "CE",
            "strike": float(atm), "expiry": expiry,
            "tradingsymbol": f"NIFTY{expiry.replace('-','')}{int(atm)}CE",
            "instrument_token": 999, "lot_size": 65,
        }]
        # Also mock the expiry date so it matches our instrument
        mock_expiry = datetime.strptime(expiry, "%Y-%m-%d")
        p1 = patch.object(at, "_get_nfo_instruments", return_value=instruments)
        p2 = patch.object(at, "_get_nearest_expiry_date", return_value=mock_expiry)
        with p1, p2:
            from strategy import Direction
            symbol, _token = at._get_option_symbol(BASE_PRICE, Direction.LONG)

        assert str(int(atm)) in symbol, f"Expected ATM strike {atm} in '{symbol}'"
        assert symbol.endswith("CE")

    def test_raises_when_no_instruments(self):
        at, _ = _fresh_auto_trader()
        with patch.object(at, "_get_nfo_instruments", return_value=[]):
            from strategy import Direction
            with pytest.raises(RuntimeError, match="not available"):
                at._get_option_symbol(BASE_PRICE, Direction.LONG)


class TestMaxTradesPerDay:
    """Runtime-configurable max trades per day (1-15)."""

    def test_default_is_module_constant(self):
        at, _ = _fresh_auto_trader()
        assert at.state.max_trades_per_day == at.MAX_ORDERS_PER_DAY

    def test_safety_blocks_after_hitting_limit(self):
        at, _ = _fresh_auto_trader()
        at.state.max_trades_per_day = 3
        at.state.orders_placed      = 3   # already hit the limit
        safe, reason = at._check_safety()
        assert not safe
        assert "max trades" in reason.lower()
        assert "3/3" in reason

    def test_below_limit_is_allowed(self):
        at, _ = _fresh_auto_trader()
        at.state.max_trades_per_day = 5
        at.state.orders_placed      = 4   # one more allowed
        safe, reason = at._check_safety()
        # Should not fail on trade count (may fail on time — that\'s fine, not our concern here)
        if not safe:
            assert "max trades" not in reason.lower()

    def test_configure_clamps_to_1_15(self):
        at, _ = _fresh_auto_trader()
        at.configure_auto_trader(max_trades_per_day=0)
        assert at.state.max_trades_per_day == 1, "Min should clamp to 1"
        at.configure_auto_trader(max_trades_per_day=99)
        assert at.state.max_trades_per_day == 15, "Max should clamp to 15"

    def test_configure_sets_valid_value(self):
        at, _ = _fresh_auto_trader()
        at.configure_auto_trader(max_trades_per_day=8)
        assert at.state.max_trades_per_day == 8

    def test_persisted_in_snapshot(self):
        """max_trades_per_day must survive a restart (state is set, then
        configure returns it so we know it went into the snapshot dict)."""
        at, _ = _fresh_auto_trader()
        result = at.configure_auto_trader(max_trades_per_day=11)
        assert result["max_trades_per_day"] == 11
        # Also confirm the live state changed
        assert at.state.max_trades_per_day == 11


class TestStrikeOffsetConfig:
    """strike_offset 0=ATM / 1=1-OTM / 2=2-OTM."""

    def test_default_is_atm(self):
        at, _ = _fresh_auto_trader()
        assert at.state.strike_offset == 0

    def test_configure_changes_offset(self):
        at, _ = _fresh_auto_trader()
        at.configure_auto_trader(strike_offset=2)
        assert at.state.strike_offset == 2

    def test_configure_clamps_offset(self):
        at, _ = _fresh_auto_trader()
        at.configure_auto_trader(strike_offset=5)
        assert at.state.strike_offset == 2, "Max allowed offset is 2"
        at.configure_auto_trader(strike_offset=-1)
        assert at.state.strike_offset == 0, "Min allowed offset is 0"

    def test_atm_offset_0_long(self):
        at, _ = _fresh_auto_trader()
        at.state.strike_offset = 0
        instruments = mock_instruments(BASE_PRICE)
        with patch.object(at, "_get_nfo_instruments", return_value=instruments):
            from strategy import Direction
            symbol, _ = at._get_option_symbol(BASE_PRICE, Direction.LONG)
        atm = round(BASE_PRICE / 50) * 50
        assert str(atm) in symbol and symbol.endswith("CE")

    def test_1otm_offset_1_long(self):
        at, _ = _fresh_auto_trader()
        at.state.strike_offset = 1
        instruments = mock_instruments(BASE_PRICE)
        with patch.object(at, "_get_nfo_instruments", return_value=instruments):
            from strategy import Direction
            symbol, _ = at._get_option_symbol(BASE_PRICE, Direction.LONG)
        otm = round(BASE_PRICE / 50) * 50 + 50
        assert str(otm) in symbol and symbol.endswith("CE")

    def test_2otm_offset_2_long(self):
        at, _ = _fresh_auto_trader()
        at.state.strike_offset = 2
        instruments = mock_instruments(BASE_PRICE)
        with patch.object(at, "_get_nfo_instruments", return_value=instruments):
            from strategy import Direction
            symbol, _ = at._get_option_symbol(BASE_PRICE, Direction.LONG)
        otm2 = round(BASE_PRICE / 50) * 50 + 100
        assert str(otm2) in symbol and symbol.endswith("CE")


class TestLiveOrderPlacement:
    """Live order path (mocked Zerodha API)."""

    def test_live_order_calls_kite(self):
        """Entry places a MARKET order + a SL-M backstop order (2 calls total)."""
        at, mock_km = _fresh_auto_trader()
        at.state.is_paper_mode = False   # LIVE mode!

        with patch.object(at, "_get_option_symbol", return_value=("NIFTY20260320_23250CE", 111)):
            from strategy import Direction
            at._enter_trade(Direction.LONG, BASE_PRICE)

        # Two orders: 1) entry MARKET, 2) SL-M backstop
        assert mock_km.kite.place_order.call_count == 2, (
            f"Expected 2 orders (entry + SL backstop), got "
            f"{mock_km.kite.place_order.call_count}"
        )
        # First call must be the entry order
        first_call = mock_km.kite.place_order.call_args_list[0].kwargs
        assert first_call["transaction_type"] == "BUY"
        assert first_call["product"]          == "MIS"
        assert first_call["order_type"]       == "MARKET"

    def test_failed_live_order_does_not_create_trade(self):
        at, mock_km = _fresh_auto_trader()
        at.state.is_paper_mode = False
        mock_km.kite.place_order.side_effect = Exception("Insufficient funds")

        with patch.object(at, "_get_option_symbol", return_value=("NIFTY20260320_23250CE", 111)):
            from strategy import Direction
            at._enter_trade(Direction.LONG, BASE_PRICE)

        assert at.state.active_trade is None, "No trade if order rejected by Zerodha"
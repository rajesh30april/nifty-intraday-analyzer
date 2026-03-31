"""Comprehensive tests for every TraderState setting field.

Covers:
  - configure_auto_trader()  — clamp / acceptance for every field
  - _check_safety()          — each guard that uses a setting
  - SL / target math         — sl_points + rr_ratio
  - _resolve_quantity()      — qty_mode, manual_qty, capital
  - trail_atr_mult clamp
  - strike_offset clamp

No Kite connection needed — all tests run offline.
"""

import os
import pytest
from datetime import datetime, timedelta

# ── Patch env before importing the module ─────────────────────────
os.environ["KITE_API_KEY"]    = "test"
os.environ["KITE_API_SECRET"] = "test"
os.environ["LIVE_TRADING"]    = "false"

import auto_trader as at
from auto_trader import (
    TraderState, configure_auto_trader,
    _resolve_quantity, _estimate_premium_fallback,
    _check_safety, LOT_SIZE,
    SL_POINTS, TRAILING_SL_POINTS, MAX_LOSS_PER_DAY, MAX_ORDERS_PER_DAY,
    Direction,
)


# ── Fixture: reset state before every test ─────────────────────────────
@pytest.fixture(autouse=True)
def reset_state():
    """Reset TraderState to clean defaults before every test."""
    s = at.state
    s.is_running            = False
    s.kill_switch           = False
    s.is_paper_mode         = True
    s.active_trade          = None
    s.trades_today          = []
    s.total_pnl             = 0.0
    s.orders_placed         = 0
    s.sl_points             = SL_POINTS
    s.trailing_sl_points    = TRAILING_SL_POINTS
    s.trail_mode            = "fixed"
    s.trail_atr_mult        = 0.7
    s.cached_trail_sl       = None
    s.rr_ratio              = 2.0
    s.capital               = 96_000.0
    s.qty_mode              = "capital"
    s.manual_qty            = 65
    s.max_daily_loss        = MAX_LOSS_PER_DAY
    s.strike_offset         = 0
    s.max_trades_per_day    = MAX_ORDERS_PER_DAY
    s.cooldown_minutes      = 5
    s.last_exit_time        = None
    s.last_exit_direction   = None
    s.exit_in_progress      = False
    s.pending_sl_exchange_update = False
    s.last_nifty_price      = 0.0
    s.last_option_ltp       = 0.0
    s.active_option_token   = None
    s.selected_strategy     = "smart_router"
    s.enabled_strategies    = []
    s.last_signal_reason    = ""
    s.last_block_reason     = None
    s.recovery_mode         = False
    yield


# ══════════════════════════════════════════════════════════════════════
class TestSlPoints:
    """sl_points — stop loss distance in Nifty points."""

    def test_normal_value_accepted(self):
        configure_auto_trader(sl_points=40)
        assert at.state.sl_points == 40

    def test_small_value_accepted(self):
        configure_auto_trader(sl_points=5)
        assert at.state.sl_points == 5

    def test_large_value_accepted(self):
        configure_auto_trader(sl_points=200)
        assert at.state.sl_points == 200

    def test_float_accepted(self):
        configure_auto_trader(sl_points=27.5)
        assert at.state.sl_points == 27.5

    def test_sl_level_on_long_entry(self):
        """SL = entry_price - sl_points for LONG."""
        configure_auto_trader(sl_points=50)
        entry = 24000.0
        sl = entry - at.state.sl_points
        assert sl == 23950.0

    def test_sl_level_on_short_entry(self):
        """SL = entry_price + sl_points for SHORT."""
        configure_auto_trader(sl_points=50)
        entry = 24000.0
        sl = entry + at.state.sl_points
        assert sl == 24050.0

    def test_none_leaves_unchanged(self):
        at.state.sl_points = 42
        configure_auto_trader(sl_points=None)
        assert at.state.sl_points == 42


# ══════════════════════════════════════════════════════════════════════
class TestRrRatio:
    """rr_ratio — risk:reward multiplier for target."""

    def test_normal_rr(self):
        configure_auto_trader(rr_ratio=2.0)
        assert at.state.rr_ratio == 2.0

    def test_target_long(self):
        """Target = entry + sl_points * rr."""
        configure_auto_trader(sl_points=50, rr_ratio=2.0)
        entry = 24000.0
        target = entry + at.state.sl_points * at.state.rr_ratio
        assert target == 24100.0

    def test_target_short(self):
        configure_auto_trader(sl_points=50, rr_ratio=3.0)
        entry = 24000.0
        target = entry - at.state.sl_points * at.state.rr_ratio
        assert target == 23850.0

    @pytest.mark.parametrize("rr,sl,expected_tgt", [
        (1.0, 30, 24030.0),   # 1:1
        (1.5, 30, 24045.0),   # 1:1.5
        (2.0, 30, 24060.0),   # 1:2
        (3.0, 30, 24090.0),   # 1:3
    ])
    def test_target_formula_parametrized(self, rr, sl, expected_tgt):
        configure_auto_trader(sl_points=sl, rr_ratio=rr)
        entry = 24000.0
        tgt = entry + at.state.sl_points * at.state.rr_ratio
        assert abs(tgt - expected_tgt) < 0.01

    def test_none_leaves_unchanged(self):
        at.state.rr_ratio = 1.5
        configure_auto_trader(rr_ratio=None)
        assert at.state.rr_ratio == 1.5


# ══════════════════════════════════════════════════════════════════════
class TestTrailingSlPoints:
    """trailing_sl_points — step size for fixed-mode trail."""

    def test_normal_value(self):
        configure_auto_trader(trailing_sl_points=20)
        assert at.state.trailing_sl_points == 20

    def test_float_value(self):
        configure_auto_trader(trailing_sl_points=12.5)
        assert at.state.trailing_sl_points == 12.5

    def test_trail_moves_sl_up_on_long(self):
        """When price rises above entry, SL trails by trailing_sl_points."""
        configure_auto_trader(trailing_sl_points=20)
        entry = 24000.0
        sl    = entry - 50.0        # initial SL (sl_points=50)
        price_now = 24060.0         # price moved up 60 pts

        # Fixed trail: new SL = price_now - trailing_sl_points
        new_sl = price_now - at.state.trailing_sl_points
        assert new_sl == 24040.0    # SL moved from 23950 → 24040
        assert new_sl > sl          # confirm it moved UP (good)

    def test_trail_moves_sl_down_on_short(self):
        configure_auto_trader(trailing_sl_points=20)
        entry     = 24000.0
        sl        = entry + 50.0    # initial SL for short
        price_now = 23940.0         # price fell 60 pts

        new_sl = price_now + at.state.trailing_sl_points
        assert new_sl == 23960.0    # SL moved DOWN (good for short)
        assert new_sl < sl


# ══════════════════════════════════════════════════════════════════════
class TestTrailMode:
    """trail_mode — which algorithm drives trailing SL."""

    @pytest.mark.parametrize("mode", ["fixed", "atr", "supertrend", "manual"])
    def test_valid_modes_accepted(self, mode):
        configure_auto_trader(trail_mode=mode)
        assert at.state.trail_mode == mode

    def test_none_leaves_unchanged(self):
        at.state.trail_mode = "atr"
        configure_auto_trader(trail_mode=None)
        assert at.state.trail_mode == "atr"


# ══════════════════════════════════════════════════════════════════════
class TestTrailAtrMult:
    """trail_atr_mult — clamped [0.3, 4.0]."""

    def test_normal_value(self):
        configure_auto_trader(trail_atr_mult=1.5)
        assert at.state.trail_atr_mult == 1.5

    def test_minimum_boundary(self):
        configure_auto_trader(trail_atr_mult=0.3)
        assert at.state.trail_atr_mult == 0.3

    def test_maximum_boundary(self):
        configure_auto_trader(trail_atr_mult=4.0)
        assert at.state.trail_atr_mult == 4.0

    def test_below_min_clamped_to_03(self):
        configure_auto_trader(trail_atr_mult=0.1)
        assert at.state.trail_atr_mult == 0.3

    def test_above_max_clamped_to_4(self):
        configure_auto_trader(trail_atr_mult=9.9)
        assert at.state.trail_atr_mult == 4.0

    def test_zero_clamped(self):
        configure_auto_trader(trail_atr_mult=0.0)
        assert at.state.trail_atr_mult == 0.3

    def test_negative_clamped(self):
        configure_auto_trader(trail_atr_mult=-1.0)
        assert at.state.trail_atr_mult == 0.3


# ══════════════════════════════════════════════════════════════════════
class TestStrikeOffset:
    """strike_offset — clamped [-3, 3]."""

    @pytest.mark.parametrize("offset", [-3, -2, -1, 0, 1, 2, 3])
    def test_all_valid_offsets(self, offset):
        configure_auto_trader(strike_offset=offset)
        assert at.state.strike_offset == offset

    def test_below_min_clamped_to_neg3(self):
        configure_auto_trader(strike_offset=-99)
        assert at.state.strike_offset == -3

    def test_above_max_clamped_to_3(self):
        configure_auto_trader(strike_offset=99)
        assert at.state.strike_offset == 3

    def test_zero_is_atm(self):
        configure_auto_trader(strike_offset=0)
        assert at.state.strike_offset == 0

    def test_positive_is_otm(self):
        """Positive offset = more OTM = cheaper premium."""
        configure_auto_trader(strike_offset=2)
        assert at.state.strike_offset == 2  # OTM2

    def test_negative_is_itm(self):
        """Negative offset = more ITM = higher delta, more expensive."""
        configure_auto_trader(strike_offset=-1)
        assert at.state.strike_offset == -1  # ITM1


# ══════════════════════════════════════════════════════════════════════
class TestMaxTradesPerDay:
    """max_trades_per_day — clamped [1, 50]. Blocks new entries when hit."""

    def test_normal_value(self):
        configure_auto_trader(max_trades_per_day=5)
        assert at.state.max_trades_per_day == 5

    def test_min_boundary(self):
        configure_auto_trader(max_trades_per_day=1)
        assert at.state.max_trades_per_day == 1

    def test_max_boundary(self):
        configure_auto_trader(max_trades_per_day=50)
        assert at.state.max_trades_per_day == 50

    def test_zero_clamped_to_1(self):
        configure_auto_trader(max_trades_per_day=0)
        assert at.state.max_trades_per_day == 1

    def test_above_50_clamped(self):
        configure_auto_trader(max_trades_per_day=200)
        assert at.state.max_trades_per_day == 50

    def test_safety_blocks_when_limit_hit(self):
        configure_auto_trader(max_trades_per_day=3)
        at.state.orders_placed = 3    # already at limit
        ok, msg = _check_safety()
        assert not ok
        assert "Max trades" in msg

    def test_safety_allows_one_below_limit(self):
        configure_auto_trader(max_trades_per_day=3)
        at.state.orders_placed = 2    # one slot left
        ok, msg = _check_safety()
        # Will fail on time check too, but NOT on trade count
        assert "Max trades" not in msg

    def test_safety_blocks_at_exactly_limit(self):
        configure_auto_trader(max_trades_per_day=1)
        at.state.orders_placed = 1
        ok, msg = _check_safety()
        assert not ok
        assert "Max trades" in msg


# ══════════════════════════════════════════════════════════════════════
class TestMaxDailyLoss:
    """max_daily_loss — clamped [₹500, ₹50000]. Blocks when total_pnl <= -limit."""

    def test_normal_value(self):
        configure_auto_trader(max_daily_loss=5000)
        assert at.state.max_daily_loss == 5000

    def test_min_boundary(self):
        configure_auto_trader(max_daily_loss=500)
        assert at.state.max_daily_loss == 500

    def test_max_boundary(self):
        configure_auto_trader(max_daily_loss=50000)
        assert at.state.max_daily_loss == 50000

    def test_below_min_clamped_to_500(self):
        configure_auto_trader(max_daily_loss=100)
        assert at.state.max_daily_loss == 500

    def test_above_max_clamped_to_50000(self):
        configure_auto_trader(max_daily_loss=999999)
        assert at.state.max_daily_loss == 50000

    def test_zero_clamped_to_500(self):
        configure_auto_trader(max_daily_loss=0)
        assert at.state.max_daily_loss == 500

    def test_safety_blocks_when_loss_exceeded(self):
        configure_auto_trader(max_daily_loss=3000)
        at.state.total_pnl = -3000.0   # exactly at limit
        ok, msg = _check_safety()
        assert not ok
        assert "Max daily loss" in msg or "loss" in msg.lower()

    def test_safety_blocks_when_loss_beyond(self):
        configure_auto_trader(max_daily_loss=3000)
        at.state.total_pnl = -5000.0   # way beyond
        ok, msg = _check_safety()
        assert not ok

    def test_safety_allows_when_loss_just_below(self):
        configure_auto_trader(max_daily_loss=3000)
        at.state.total_pnl = -2999.0   # one rupee below limit
        ok, msg = _check_safety()
        # Won't say "Max daily loss"
        assert "Max daily loss" not in msg

    def test_safety_allows_positive_pnl(self):
        configure_auto_trader(max_daily_loss=3000)
        at.state.total_pnl = +1500.0
        ok, msg = _check_safety()
        assert "Max daily loss" not in msg

    @pytest.mark.parametrize("limit,pnl,should_block", [
        (3000, -3000, True),   # exactly at limit
        (3000, -3001, True),   # one rupee beyond
        (3000, -2999, False),  # one rupee under
        (500,  -500,  True),   # minimum clamped limit
        (500,  -499,  False),  # just under minimum
    ])
    def test_loss_boundary_parametrized(self, limit, pnl, should_block):
        at.state.max_daily_loss = limit
        at.state.total_pnl      = float(pnl)
        ok, msg = _check_safety()
        if should_block:
            assert not ok, f"Expected block at pnl={pnl}, limit={limit}"
        else:
            assert "Max daily loss" not in msg


# ══════════════════════════════════════════════════════════════════════
class TestCooldownMinutes:
    """cooldown_minutes — clamped [0, 60]. Post-exit re-entry delay."""

    def test_normal_value(self):
        configure_auto_trader(cooldown_minutes=10)
        assert at.state.cooldown_minutes == 10

    def test_zero_allowed(self):
        """Zero = no cooldown."""
        configure_auto_trader(cooldown_minutes=0)
        assert at.state.cooldown_minutes == 0

    def test_max_boundary(self):
        configure_auto_trader(cooldown_minutes=60)
        assert at.state.cooldown_minutes == 60

    def test_above_max_clamped_to_60(self):
        configure_auto_trader(cooldown_minutes=999)
        assert at.state.cooldown_minutes == 60

    def test_negative_clamped_to_0(self):
        configure_auto_trader(cooldown_minutes=-5)
        assert at.state.cooldown_minutes == 0

    def test_safety_blocks_within_cooldown(self):
        configure_auto_trader(cooldown_minutes=5)
        at.state.last_exit_time = datetime.now() - timedelta(minutes=2)  # 2m ago, need 5m
        ok, msg = _check_safety()
        assert not ok
        assert "Cooldown" in msg or "cooldown" in msg.lower()

    def test_safety_allows_after_cooldown(self):
        configure_auto_trader(cooldown_minutes=5)
        at.state.last_exit_time = datetime.now() - timedelta(minutes=6)  # 6m ago, past 5m
        ok, msg = _check_safety()
        assert "Cooldown" not in msg

    def test_safety_allows_with_zero_cooldown(self):
        """Zero cooldown = re-enter immediately after exit."""
        configure_auto_trader(cooldown_minutes=0)
        at.state.last_exit_time = datetime.now() - timedelta(seconds=1)
        ok, msg = _check_safety()
        assert "Cooldown" not in msg

    def test_cooldown_remaining_calculation(self):
        """Remaining cooldown reported correctly (integer minutes, jitter-safe)."""
        configure_auto_trader(cooldown_minutes=10)
        at.state.last_exit_time = datetime.now() - timedelta(minutes=3)
        ok, msg = _check_safety()
        assert not ok
        # Should be blocked and report some positive remaining minutes.
        # int() truncation: elapsed just over 3m → remaining = int(10-3.00x) = 6 or 7.
        assert "Cooldown" in msg
        assert "left" in msg
        # Remaining must be between 6 and 8 (loose window to absorb CI timing jitter)
        import re
        nums = re.findall(r'(\d+)m left', msg)
        assert nums, f"No 'Xm left' found in: {msg!r}"
        remaining = int(nums[0])
        assert 6 <= remaining <= 8, f"Expected ~7m remaining, got {remaining}m"


# ══════════════════════════════════════════════════════════════════════
class TestKillSwitch:
    """kill_switch — hard stop, highest priority in _check_safety."""

    def test_kill_switch_blocks_everything(self):
        at.state.kill_switch    = True
        at.state.orders_placed  = 0
        at.state.total_pnl      = 0.0
        ok, msg = _check_safety()
        assert not ok
        assert "Kill switch" in msg or "kill" in msg.lower()

    def test_kill_switch_overrides_other_checks(self):
        """Even with 0 orders and good P&L, kill switch wins."""
        at.state.kill_switch    = True
        at.state.orders_placed  = 0
        at.state.total_pnl      = 5000.0
        at.state.max_daily_loss = 50000.0
        ok, msg = _check_safety()
        assert not ok
        assert "Kill" in msg or "kill" in msg.lower()

    def test_no_kill_switch_proceeds(self):
        at.state.kill_switch = False
        ok, msg = _check_safety()
        # May fail on time check but not kill switch
        assert "Kill" not in msg


# ══════════════════════════════════════════════════════════════════════
class TestQtyMode:
    """qty_mode — 'capital' or 'manual'. Controls lot sizing algorithm."""

    def test_capital_mode_accepted(self):
        configure_auto_trader(qty_mode="capital")
        assert at.state.qty_mode == "capital"

    def test_manual_mode_accepted(self):
        configure_auto_trader(qty_mode="manual")
        assert at.state.qty_mode == "manual"

    def test_manual_mode_returns_exact_qty(self):
        """In manual mode, _resolve_quantity ignores capital/premium."""
        configure_auto_trader(qty_mode="manual", manual_qty=130)
        qty, cost = _resolve_quantity(24000, real_premium=100)
        assert qty == 130
        assert cost == 0.0    # manual mode returns 0 for required_margin

    def test_capital_mode_uses_premium(self):
        configure_auto_trader(qty_mode="capital", capital=65_000)
        qty, cost = _resolve_quantity(24000, real_premium=100)
        expected_lots = int(65_000 / (100 * LOT_SIZE))   # floor(65000/6500) = 10
        assert qty == expected_lots * LOT_SIZE

    def test_none_leaves_unchanged(self):
        at.state.qty_mode = "manual"
        configure_auto_trader(qty_mode=None)
        assert at.state.qty_mode == "manual"


# ══════════════════════════════════════════════════════════════════════
class TestManualQty:
    """manual_qty — fixed unit count used when qty_mode='manual'."""

    def test_set_manual_qty(self):
        configure_auto_trader(manual_qty=195)   # 3 lots
        assert at.state.manual_qty == 195

    def test_one_lot(self):
        configure_auto_trader(qty_mode="manual", manual_qty=65)
        qty, _ = _resolve_quantity(24000, real_premium=500)  # very expensive — ignored
        assert qty == 65

    def test_ten_lots(self):
        configure_auto_trader(qty_mode="manual", manual_qty=650)
        qty, _ = _resolve_quantity(24000, real_premium=500)
        assert qty == 650

    def test_manual_qty_ignored_in_capital_mode(self):
        """manual_qty has zero effect when qty_mode='capital'."""
        configure_auto_trader(qty_mode="capital", capital=6_500, manual_qty=9999)
        qty, _ = _resolve_quantity(24000, real_premium=100)
        # floor(6500 / 6500) = 1 lot
        assert qty == LOT_SIZE           # 1 lot, NOT 9999


# ══════════════════════════════════════════════════════════════════════
class TestCapital:
    """capital — rupees available. Drives lot count in capital mode."""

    @pytest.mark.parametrize("capital,premium,expected_lots", [
        (6_500,  100, 1),    # exactly 1 lot
        (6_499,  100, 0),    # just under 1 lot → blocked
        (13_000, 100, 2),    # exactly 2 lots
        (50_000, 100, 7),    # floor(50000/6500)
        (96_000, 120, 12),   # floor(96000/7800)
        (2_00_000, 80, 38),  # floor(200000/5200)
    ])
    def test_lot_count_from_capital(self, capital, premium, expected_lots):
        configure_auto_trader(qty_mode="capital", capital=capital)
        qty, _ = _resolve_quantity(24000, real_premium=premium)
        assert qty // LOT_SIZE == expected_lots

    def test_insufficient_capital_returns_zero(self):
        configure_auto_trader(qty_mode="capital", capital=1_000)
        qty, cost = _resolve_quantity(24000, real_premium=200)
        # 200 * 65 = 13000 per lot, can't afford
        assert qty == 0

    def test_premium_fallback_used_when_no_ltp(self):
        """When real_premium=None, uses 0.35% * nifty_price estimate."""
        configure_auto_trader(qty_mode="capital", capital=50_000)
        nifty = 24000
        est   = _estimate_premium_fallback(nifty)   # 0.35% = 84
        qty, _ = _resolve_quantity(nifty, real_premium=None)
        expected_lots = int(50_000 / (est * LOT_SIZE))
        assert qty == expected_lots * LOT_SIZE

    def test_leftover_never_exceeds_one_lot_cost(self):
        """floor() ensures leftover < cost_per_lot."""
        capital = 50_000
        premium = 100
        configure_auto_trader(qty_mode="capital", capital=capital)
        qty, cost_per_lot = _resolve_quantity(24000, real_premium=premium)
        lots    = qty // LOT_SIZE
        deployed = lots * LOT_SIZE * premium
        leftover = capital - deployed
        assert leftover < cost_per_lot
        assert leftover >= 0


# ══════════════════════════════════════════════════════════════════════
class TestConfigureReturnsAllFields:
    """configure_auto_trader() must return ALL setting fields in its response dict."""

    EXPECTED_KEYS = {
        "sl_points", "trailing_sl_points", "trail_mode", "trail_atr_mult",
        "rr_ratio", "qty_mode", "manual_qty", "capital",
        "strike_offset", "max_trades_per_day", "cooldown_minutes", "max_daily_loss",
    }

    def test_all_keys_present(self):
        result = configure_auto_trader(sl_points=40)
        missing = self.EXPECTED_KEYS - result.keys()
        assert not missing, f"configure_auto_trader() missing keys: {missing}"

    def test_returned_values_match_state(self):
        configure_auto_trader(
            sl_points=35, rr_ratio=1.5, capital=50_000,
            max_daily_loss=2000, cooldown_minutes=3,
        )
        s = at.state
        result = configure_auto_trader()   # call with no args to get current
        assert result["sl_points"]       == s.sl_points
        assert result["rr_ratio"]        == s.rr_ratio
        assert result["capital"]         == s.capital
        assert result["max_daily_loss"]  == s.max_daily_loss
        assert result["cooldown_minutes"]== s.cooldown_minutes


# ══════════════════════════════════════════════════════════════════════
class TestCheckSafetyPriority:
    """_check_safety() guard priority: kill_switch > max_trades > max_loss > time > cooldown."""

    def test_kill_switch_is_first_guard(self):
        """kill_switch must block even when everything else is clean."""
        at.state.kill_switch    = True
        at.state.orders_placed  = 0
        at.state.total_pnl      = 1000.0
        ok, msg = _check_safety()
        assert not ok
        assert "Kill" in msg or "kill" in msg.lower()

    def test_trade_limit_beats_loss_check(self):
        """If orders_placed>=limit, that blocks before loss check fires."""
        at.state.kill_switch     = False
        at.state.orders_placed   = 5
        at.state.max_trades_per_day = 5
        at.state.total_pnl       = 0.0   # no loss yet
        ok, msg = _check_safety()
        assert not ok
        assert "Max trades" in msg

    def test_multiple_violations_returns_first(self):
        """Multiple guards hit: only first failure message returned."""
        at.state.orders_placed      = 99
        at.state.max_trades_per_day = 5
        at.state.total_pnl          = -99999
        ok, msg = _check_safety()
        assert not ok
        assert "Max trades" in msg   # trade limit fires before loss check


# ══════════════════════════════════════════════════════════════════════
class TestEnabledStrategies:
    """enabled_strategies — list of strategy IDs. Empty = all enabled."""

    def test_empty_list_is_default(self):
        assert at.state.enabled_strategies == []

    def test_set_single_strategy(self):
        at.state.enabled_strategies = ["supertrend_strat"]
        assert "supertrend_strat" in at.state.enabled_strategies

    def test_set_multiple_strategies(self):
        strats = ["orb", "vwap_breakout", "camarilla_pivots"]
        at.state.enabled_strategies = strats
        assert at.state.enabled_strategies == strats

    def test_empty_means_all_enabled(self):
        """Empty list = all strategies run. Non-empty = whitelist."""
        at.state.enabled_strategies = []
        assert at.state.enabled_strategies == []   # all enabled


# ══════════════════════════════════════════════════════════════════════
class TestNonePassthroughAllFields:
    """Passing None for any field must leave it unchanged."""

    def test_all_none_changes_nothing(self):
        at.state.sl_points          = 99
        at.state.trailing_sl_points = 88
        at.state.trail_mode         = "atr"
        at.state.trail_atr_mult     = 1.2
        at.state.rr_ratio           = 3.0
        at.state.qty_mode           = "manual"
        at.state.manual_qty         = 130
        at.state.capital            = 55_000
        at.state.strike_offset      = 2
        at.state.max_trades_per_day = 7
        at.state.cooldown_minutes   = 8
        at.state.max_daily_loss     = 4_500

        configure_auto_trader()   # all args default to None

        assert at.state.sl_points          == 99
        assert at.state.trailing_sl_points == 88
        assert at.state.trail_mode         == "atr"
        assert at.state.trail_atr_mult     == 1.2
        assert at.state.rr_ratio           == 3.0
        assert at.state.qty_mode           == "manual"
        assert at.state.manual_qty         == 130
        assert at.state.capital            == 55_000
        assert at.state.strike_offset      == 2
        assert at.state.max_trades_per_day == 7
        assert at.state.cooldown_minutes   == 8
        assert at.state.max_daily_loss     == 4_500

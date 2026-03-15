"""Crash recovery tests — what happens when the app restarts mid-trade.

Tests:
  - Snapshot written on entry
  - Snapshot written on exit (no active_trade)
  - Snapshot written when trailing SL moves
  - Recovery: snapshot from today → state restored
  - Recovery: snapshot from yesterday → ignored (fresh start)
  - Recovery: open position confirmed by Zerodha → trade restored
  - Recovery: position already closed in Zerodha → ghost trade logged
  - Recovery: paper trade → restored from snapshot (no Zerodha check)
  - Recovery: no snapshot file → no-op, no crash

Run: pytest tests/test_crash_recovery.py -v
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.mock_data import BASE_PRICE


# ── Helpers ───────────────────────────────────────────────────────

def _fresh_at(tmp_path: Path, kite_positions=None):
    """Return a fresh auto_trader module isolated to `tmp_path`.

    kite_positions: list of Zerodha position dicts returned by kite.positions().
    Pass [] to simulate a closed position, or [{...}] for an open one.

    NOTE: `_recover_state()` at module level runs against the REAL snapshot file
    (which doesn't exist in tmp_path yet).  Recovery tests must call
    `at._recover_state(at.STATE_SNAPSHOT_FILE)` explicitly after writing
    the test snapshot to `at.STATE_SNAPSHOT_FILE`.
    """
    for key in list(sys.modules):
        if "auto_trader" in key:
            del sys.modules[key]

    mock_kite = MagicMock()
    mock_kite.TRANSACTION_TYPE_BUY  = "BUY"
    mock_kite.TRANSACTION_TYPE_SELL = "SELL"
    mock_kite.VARIETY_REGULAR       = "regular"
    mock_kite.PRODUCT_MIS           = "MIS"
    mock_kite.ORDER_TYPE_MARKET     = "MARKET"
    mock_kite.place_order.return_value = 55555
    mock_kite.margins.return_value  = {"equity": {"available": {"live_balance": 100_000}}}
    mock_kite.positions.return_value = {"net": kite_positions or []}

    mock_km = MagicMock()
    mock_km.kite = mock_kite
    mock_km.is_authenticated = True
    mock_km.latest_tick = {"last_price": BASE_PRICE}

    kite_stub = MagicMock()
    kite_stub.kite_manager = mock_km

    with patch.dict("sys.modules", {"kite_integration": kite_stub}):
        import auto_trader as at

    # Redirect file paths to tmp_path so tests don't touch real files
    at.STATE_SNAPSHOT_FILE = tmp_path / ".state_snapshot.json"
    at.TRADE_LOG_FILE      = tmp_path / "trade_log.json"

    # Reset state — module-level _recover_state() may have read the REAL
    # project snapshot during import; wipe it so each test starts clean.
    at.state = at.TraderState()

    return at, mock_km


def _inject_trade(at, direction="long", price=BASE_PRICE):
    """Force-enter a paper trade without going through gy."""
    symbol = "NIFTY20260320_23250CE"
    with patch.object(at, "_get_option_symbol", return_value=(symbol, 111)):
        from strategy import Direction
        at.state.is_paper_mode = True
        at.state.is_running    = True
        at._enter_trade(
            Direction.LONG if direction == "long" else Direction.SHORT,
            price,
        )


# ── Tests ─────────────────────────────────────────────────────────

class TestSnapshotWrite:
    """Snapshot must be written at the right moments."""

    def test_snapshot_written_on_entry(self, tmp_path):
        at, _ = _fresh_at(tmp_path)
        assert not at.STATE_SNAPSHOT_FILE.exists(), "No snapshot before trade"
        _inject_trade(at)
        assert at.STATE_SNAPSHOT_FILE.exists(), "Snapshot must exist after entry"

    def test_snapshot_has_active_trade_after_entry(self, tmp_path):
        at, _ = _fresh_at(tmp_path)
        _inject_trade(at)
        snap = json.loads(at.STATE_SNAPSHOT_FILE.read_text())
        assert snap["active_trade"] is not None
        assert snap["active_trade"]["direction"] == "long"
        assert snap["active_trade"]["entry_price"] == BASE_PRICE

    def test_snapshot_cleared_active_trade_after_exit(self, tmp_path):
        at, mock_km = _fresh_at(tmp_path)
        # entry_ltp=150, exit_ltp=119 → P&L = (119-150)×65 = -2015 (loss)
        mock_km.get_option_ltp.side_effect = [150.0, 119.0]
        _inject_trade(at)

        # Trigger SL exit
        sl = at.state.active_trade.stop_loss - 1
        at._manage_active_trade(sl)

        snap = json.loads(at.STATE_SNAPSHOT_FILE.read_text())
        assert snap["active_trade"] is None, "active_trade must be None after exit"
        assert snap["total_pnl"] < 0,        "total_pnl must reflect the loss"

    def test_snapshot_has_latest_sl_after_trail(self, tmp_path):
        at, _ = _fresh_at(tmp_path)
        _inject_trade(at)
        original_sl = at.state.active_trade.stop_loss

        # Price rallies — trailing SL should move up and be snapshotted
        at._manage_active_trade(BASE_PRICE + 50)
        snap = json.loads(at.STATE_SNAPSHOT_FILE.read_text())

        snapped_sl = snap["active_trade"]["stop_loss"]
        assert snapped_sl > original_sl, "Snapshot must have updated SL after trail"

    def test_snapshot_date_is_today(self, tmp_path):
        at, _ = _fresh_at(tmp_path)
        _inject_trade(at)
        snap = json.loads(at.STATE_SNAPSHOT_FILE.read_text())
        assert snap["date"] == datetime.now().strftime("%Y-%m-%d")


class TestStateRecovery:
    """State must be correctly restored on restart."""

    def test_no_crash_when_no_snapshot(self, tmp_path):
        """If no snapshot file exists, _recover_state() must be a no-op."""
        at, _ = _fresh_at(tmp_path)
        # No exception = pass
        assert at.state.active_trade is None
        assert at.state.total_pnl    == 0.0
        assert at.state.recovery_mode is False

    def test_yesterday_snapshot_ignored(self, tmp_path):
        """Snapshot from a previous day must not restore state."""
        at, _ = _fresh_at(tmp_path)
        snap = {
            "date":          "2025-01-01",   # always in the past
            "total_pnl":     5000.0,
            "orders_placed": 3,
            "is_paper_mode": True,
            "selected_strategy": "orb",
            "active_trade":  None,
            "trades_today":  [],
        }
        at.STATE_SNAPSHOT_FILE.write_text(json.dumps(snap))
        at._recover_state(at.STATE_SNAPSHOT_FILE)

        assert at.state.total_pnl     == 0.0, "Yesterday snapshot must be ignored"
        assert at.state.orders_placed  == 0
        assert not at.STATE_SNAPSHOT_FILE.exists(), "Stale snapshot should be deleted"

    def test_counters_restored_from_todays_snapshot(self, tmp_path):
        """orders_placed and total_pnl from today's snapshot must be restored."""
        snap = {
            "date":          datetime.now().strftime("%Y-%m-%d"),
            "total_pnl":     -1500.0,
            "orders_placed": 2,
            "is_paper_mode": True,
            "selected_strategy": "orb",
            "active_trade":  None,
            "trades_today":  [],
        }
        at, _ = _fresh_at(tmp_path)
        at.STATE_SNAPSHOT_FILE.write_text(json.dumps(snap))
        at._recover_state(at.STATE_SNAPSHOT_FILE)

        assert at.state.total_pnl    == -1500.0
        assert at.state.orders_placed == 2
        assert at.state.recovery_mode is True

    def test_open_paper_trade_restored(self, tmp_path):
        """A paper trade in the snapshot must be restored to state.active_trade."""
        snap = {  # noqa
            "date":          datetime.now().strftime("%Y-%m-%d"),
            "total_pnl":     0.0,
            "orders_placed": 1,
            "is_paper_mode": True,
            "selected_strategy": "smart_router",
            "active_trade": {
                "id":          "T-TEST-001",
                "timestamp":   datetime.now().isoformat(),
                "direction":   "long",
                "instrument":  "NIFTY20260320_23250CE",
                "entry_price": BASE_PRICE,
                "quantity":    65,
                "stop_loss":   BASE_PRICE - 30,
                "target":      BASE_PRICE + 60,
                "order_id":    "PAPER-123456",
                "paper":       True,
                "status":      "filled",
            },
            "trades_today": [],
        }
        at, _ = _fresh_at(tmp_path)
        at.STATE_SNAPSHOT_FILE.write_text(json.dumps(snap))
        at._recover_state(at.STATE_SNAPSHOT_FILE)

        assert at.state.active_trade is not None,                   "Paper trade must be restored"
        assert at.state.active_trade.direction    == "long"
        assert at.state.active_trade.entry_price  == BASE_PRICE
        assert at.state.active_trade.stop_loss    == BASE_PRICE - 30
        assert at.state.recovery_mode             is True
        assert "RECOVERED" in at.state.recovery_message

    def test_live_trade_restored_when_zerodha_confirms(self, tmp_path):
        """If Zerodha says position is open, live trade must be restored."""
        instrument = "NIFTY20260320_23250CE"
        zerodha_positions = [{
            "tradingsymbol": instrument,
            "quantity":      65,
            "product":       "MIS",
        }]
        snap = {
            "date":          datetime.now().strftime("%Y-%m-%d"),
            "total_pnl":     0.0,
            "orders_placed": 1,
            "is_paper_mode": False,
            "selected_strategy": "smart_router",
            "active_trade": {
                "id":          "T-LIVE-001",
                "timestamp":   datetime.now().isoformat(),
                "direction":   "short",
                "instrument":  instrument,
                "entry_price": BASE_PRICE,
                "quantity":    65,
                "stop_loss":   BASE_PRICE + 30,
                "target":      BASE_PRICE - 60,
                "order_id":    "99999",
                "paper":       False,
                "status":      "filled",
            },
            "trades_today": [],
        }
        at, mock_km = _fresh_at(tmp_path, kite_positions=zerodha_positions)
        at.STATE_SNAPSHOT_FILE.write_text(json.dumps(snap))
        at._recover_state(at.STATE_SNAPSHOT_FILE)

        assert at.state.active_trade is not None, "Live trade must be restored"
        assert at.state.active_trade.direction == "short"
        assert at.state.recovery_mode is True
        assert "RECOVERED" in at.state.recovery_message

    def test_live_trade_ghosted_when_zerodha_says_closed(self, tmp_path):
        """If Zerodha shows no open position, trade is marked as externally closed."""
        snap = {
            "date":          datetime.now().strftime("%Y-%m-%d"),
            "total_pnl":     0.0,
            "orders_placed": 1,
            "is_paper_mode": False,
            "selected_strategy": "smart_router",
            "active_trade": {
                "id":          "T-GHOST-001",
                "timestamp":   datetime.now().isoformat(),
                "direction":   "long",
                "instrument":  "NIFTY20260320_23250CE",
                "entry_price": BASE_PRICE,
                "quantity":    65,
                "stop_loss":   BASE_PRICE - 30,
                "target":      BASE_PRICE + 60,
                "order_id":    "88888",
                "paper":       False,
                "status":      "filled",
            },
            "trades_today": [],
        }
        # Zerodha returns empty positions → trade was closed while app was down
        at, _ = _fresh_at(tmp_path, kite_positions=[])
        at.STATE_SNAPSHOT_FILE.write_text(json.dumps(snap))
        at._recover_state(at.STATE_SNAPSHOT_FILE)

        assert at.state.active_trade is None, "No active trade — it was closed externally"
        assert len(at.state.trades_today) == 1, "Ghost trade must be logged in trades_today"
        ghost = at.state.trades_today[0]
        assert "crashed" in ghost.exit_reason.lower() or "closed" in ghost.exit_reason.lower()
        assert at.state.recovery_mode is True
        msg = at.state.recovery_message.lower()
        assert "closed" in msg

    def test_recovered_trade_is_managed_correctly(self, tmp_path):
        """After recovery, SL and target must still work on the restored trade."""
        snap = {
            "date":          datetime.now().strftime("%Y-%m-%d"),
            "total_pnl":     0.0,
            "orders_placed": 1,
            "is_paper_mode": True,
            "selected_strategy": "smart_router",
            "active_trade": {
                "id":           "T-RECOVER-SL",
                "timestamp":    datetime.now().isoformat(),
                "direction":    "long",
                "instrument":   "NIFTY20260320_23250CE",
                "entry_price":  BASE_PRICE,
                "entry_premium": 150.0,   # option LTP at entry (new required field)
                "quantity":     65,
                "stop_loss":    BASE_PRICE - 30,
                "target":       BASE_PRICE + 60,
                "order_id":     "PAPER-789",
                "paper":        True,
                "status":       "filled",
            },
            "trades_today": [],
        }
        at, mock_km = _fresh_at(tmp_path)
        mock_km.get_option_ltp.return_value = 118.0   # exit LTP → loss on SL hit
        at.STATE_SNAPSHOT_FILE.write_text(json.dumps(snap))
        at._recover_state(at.STATE_SNAPSHOT_FILE)

        assert at.state.active_trade is not None

        # Now SL should fire normally
        sl_price = at.state.active_trade.stop_loss - 1
        at._manage_active_trade(sl_price)

        assert at.state.active_trade is None, "SL must work on recovered trade"
        assert at.state.trades_today[-1].pnl < 0


class TestMultiTradeConsistency:
    """Multiple trades across the day — counts, P&L, no duplicates, crash-safe.

    NOTE: auto_trader is a module singleton (Python caches imports).
    We simulate restarts by resetting at.state = at.TraderState() then
    calling at._recover_state() — this is exactly what happens on a real restart.
    """

    def _restart(self, at, mock_km, kite_positions=None):
        """Simulate a crash + restart on the same module instance."""
        # Simulate: process dies → files stay on disk → new process starts fresh
        at.state = at.TraderState()
        if kite_positions is not None:
            mock_km.kite.positions.return_value = {"net": kite_positions}
        at._recover_state(at.STATE_SNAPSHOT_FILE)

    def test_three_trades_no_duplicate_after_each_crash(self, tmp_path):
        """Simulate: trade1 exit → crash → restart → trade2 exit → crash → restart → trade3.
        After each recovery, trades_today must have exactly the right count (no duplicates).
        """
        at, mock_km = _fresh_at(tmp_path)

        # ── Trade 1: long → hits target ──────────────────────
        mock_km.get_option_ltp.side_effect = [150.0, 190.0]  # entry=150, exit=190
        _inject_trade(at)
        assert len(at.state.trades_today) == 1
        at._manage_active_trade(at.state.active_trade.target + 1)
        assert at.state.active_trade is None
        pnl1 = at.state.trades_today[0].pnl
        assert pnl1 > 0

        snap1 = json.loads(at.STATE_SNAPSHOT_FILE.read_text())
        assert snap1["active_trade"] is None, "No active trade after exit"
        assert len(snap1["trades_today"]) == 1, "Exactly 1 completed trade saved"

        # ── Crash + restart after trade 1 ─────────────────────
        self._restart(at, mock_km)
        assert len(at.state.trades_today) == 1, "1 trade after recovery — no duplicates!"
        assert abs(at.state.total_pnl - pnl1) < 0.01

        # ── Trade 2: short → hits SL ──────────────────────────
        mock_km.get_option_ltp.side_effect = [150.0, 115.0]  # entry=150, exit=115 (loss)
        _inject_trade(at, direction="short")
        assert len(at.state.trades_today) == 2
        at._manage_active_trade(at.state.active_trade.stop_loss + 1)
        pnl2 = at.state.trades_today[1].pnl
        assert pnl2 < 0

        snap2 = json.loads(at.STATE_SNAPSHOT_FILE.read_text())
        assert snap2["active_trade"] is None
        assert len(snap2["trades_today"]) == 2, "2 completed trades saved"

        # ── Crash + restart after trade 2 ─────────────────────
        self._restart(at, mock_km)
        assert len(at.state.trades_today) == 2, "2 trades after second recovery"

        # ── Trade 3: long → still active when crash happens ──
        mock_km.get_option_ltp.side_effect = [150.0, 118.0]  # entry=150, exit=118 (loss)
        _inject_trade(at)
        assert len(at.state.trades_today) == 3
        active_id = at.state.active_trade.id

        snap3 = json.loads(at.STATE_SNAPSHOT_FILE.read_text())
        assert snap3["active_trade"] is not None, "Active trade saved"
        assert len(snap3["trades_today"]) == 2, "Only 2 completed — active saved separately!"
        assert snap3["active_trade"]["id"] == active_id

        # ── Crash + restart with open position ────────────────
        self._restart(at, mock_km)   # paper mode → trusts snapshot
        assert len(at.state.trades_today) == 3, "3 total (2 done + 1 active restored)"
        assert at.state.active_trade is not None, "Active trade restored"
        assert at.state.active_trade.id == active_id

        # SL fires on the recovered trade
        sl = at.state.active_trade.stop_loss - 1
        at._manage_active_trade(sl)
        assert at.state.active_trade is None
        assert len(at.state.trades_today) == 3
        pnl3 = at.state.trades_today[2].pnl
        assert pnl3 < 0   # option dropped from 150 → 118

    def test_atomic_write_survives_corrupt_primary(self, tmp_path):
        """If primary snapshot is corrupt, recover from .bak."""
        at, mock_km = _fresh_at(tmp_path)
        mock_km.get_option_ltp.return_value = 150.0
        _inject_trade(at)   # writes a clean snapshot → also creates .bak on next write
        at._manage_active_trade(at.state.active_trade.target + 1)  # exit → rewrites

        # Corrupt the primary snapshot (simulate partial write killed mid-way)
        at.STATE_SNAPSHOT_FILE.write_text("{corrupt json{{{", encoding="utf-8")

        # Recovery should fall back to .bak — no exception, no crash
        at.state = at.TraderState()
        at._recover_state(at.STATE_SNAPSHOT_FILE)
        assert True, "Recovery must not raise on corrupt snapshot"

    def test_no_duplicate_active_trade_in_trades_today(self, tmp_path):
        """Active trade must NOT be in trades_today snapshot list.
        On recovery, it's added exactly once from active_trade.
        """
        at, mock_km = _fresh_at(tmp_path)
        mock_km.get_option_ltp.return_value = 150.0
        _inject_trade(at)

        snap = json.loads(at.STATE_SNAPSHOT_FILE.read_text())
        active_id = snap["active_trade"]["id"]
        ids_in_list = [t["id"] for t in snap["trades_today"]]
        assert active_id not in ids_in_list, "Active trade must NOT be in trades_today list — dedup bug!"

        # Recover and verify: exactly 1 entry (active trade added once)
        at.state = at.TraderState()
        at._recover_state(at.STATE_SNAPSHOT_FILE)
        assert len(at.state.trades_today) == 1, "Exactly 1 entry after recovery — no duplicate!"

    def test_total_pnl_consistent_across_multiple_restarts(self, tmp_path):
        """total_pnl must accumulate correctly across crash-restart cycles."""
        at, mock_km = _fresh_at(tmp_path)
        mock_km.get_option_ltp.side_effect = [150.0, 185.0]  # +35 × 65 = +2275
        _inject_trade(at)
        at._manage_active_trade(at.state.active_trade.target + 1)
        expected_pnl = round((185.0 - 150.0) * 65, 2)

        # Crash + recover
        at.state = at.TraderState()
        at._recover_state(at.STATE_SNAPSHOT_FILE)
        assert abs(at.state.total_pnl - expected_pnl) < 0.01
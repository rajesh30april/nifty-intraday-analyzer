"""Auto-Trader Engine — Automated order placement & management via Zerodha.

⚠️  PAPER TRADING MODE IS ON BY DEFAULT.
Set LIVE_TRADING=true in .env to enable real orders.

Features:
- Evaluates strategy conditions on each tick/candle
- Places orders via Kite Connect API
- Manages stop-loss (fixed + trailing)
- Auto-exits by defined time (default 3:28 PM)
- Max loss / max orders safety limits
- Full trade log for review
"""

import os
import json
import threading
from collections import deque
from datetime import datetime, time as dt_time
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum

from dotenv import load_dotenv

from kite_integration import kite_manager
from strategy import evaluate_vwap_breakout, Direction
import strategies.loader  # noqa: F401 — register all strategies
from strategies.registry import get as get_strategy

load_dotenv()

# ── Configuration ────────────────────────────────────────────────
LIVE_TRADING = os.getenv("LIVE_TRADING", "false").lower() == "true"
MAX_LOSS_PER_DAY = float(os.getenv("MAX_LOSS_PER_DAY", "5000"))  # ₹5000
MAX_ORDERS_PER_DAY = int(os.getenv("MAX_ORDERS_PER_DAY", "30"))
DEFAULT_QUANTITY   = int(os.getenv("DEFAULT_QUANTITY",   "780"))   # 12 lots × 65 units
SL_POINTS          = float(os.getenv("SL_POINTS",          "30"))   # Fixed SL in points
TRAILING_SL_POINTS = float(os.getenv("TRAILING_SL_POINTS", "15"))   # Trail by 15pts
DEFAULT_CAPITAL    = float(os.getenv("TRADING_CAPITAL",  "96000"))  # ₹ available
EXIT_TIME = dt_time(15, 28)  # 3:28 PM IST — auto-exit all positions
TRADE_LOG_FILE     = Path(__file__).parent / "trade_log.json"
STATE_SNAPSHOT_FILE = Path(__file__).parent / ".state_snapshot.json"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PLACED = "placed"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXITED = "exited"
    REJECTED = "rejected"


@dataclass
class Trade:
    """A single trade record."""
    id: str
    timestamp: str
    direction: str        # 'long' or 'short'
    instrument: str
    entry_price: float    # Nifty SPOT at entry  — used for SL / target / trailing math
    entry_premium: float  # Option LTP at entry  — used for real P&L calculation
    quantity: int         # units sent to exchange (lots × 65)
    stop_loss: float      # Nifty spot SL level (moves with trailing)
    target: float | None = None
    exit_price: float | None = None    # Nifty spot at exit
    exit_premium: float | None = None  # Option LTP at exit — for real P&L
    exit_time: str | None = None
    exit_reason: str | None = None
    pnl: float = 0.0
    status: str = OrderStatus.PENDING
    order_id: str | None = None        # Zerodha entry order ID
    sl_order_id: str | None = None     # Zerodha SL-M order ID (exchange-level guard)
    paper: bool = True
    app_managed: bool = True   # False = monitor only (no SL/trail/exit by app)


@dataclass
class TraderState:
    """Current state of the auto-trader."""
    is_running: bool = False
    is_paper_mode: bool = not LIVE_TRADING
    active_trade: Trade | None = None
    trades_today: list[Trade] = field(default_factory=list)
    total_pnl: float = 0.0
    orders_placed: int = 0
    highest_price_since_entry: float = 0.0   # for trailing SL
    lowest_price_since_entry:  float = float("inf")
    # ── Exchange SL-M sync ────────────────────────────────────────
    entry_nifty_sl:          float = 0.0   # original Nifty SL at entry
    entry_option_trigger:    float = 0.0   # original option trigger at entry
    pending_sl_exchange_update: bool = False  # tick sets → candle loop clears
    exit_in_progress: bool = False             # debounce: prevent double-exit on rapid ticks
    last_exit_time: datetime | None = None     # when last trade exited — for cooldown
    last_exit_direction: str | None = None    # direction of last exit — for same-dir filter
    last_evaluation: str = ""
    last_signal_reason: str = ""
    last_conditions: list[dict] = field(default_factory=list)
    last_meta_scores: list[dict] = field(default_factory=list)  # full strategy scoreboard
    last_meta_regime: str = ""                                  # regime detected by meta router
    kill_switch: bool = False
    last_option_ltp: float = 0.0           # live option LTP — refreshed each tick via WebSocket
    active_option_token: int | None = None  # KiteTicker token for current option — for tick sub
    last_nifty_price: float = 0.0          # live Nifty price — refreshed each tick
    selected_strategy: str = "smart_router"
    last_block_reason: str | None = None
    # ── Runtime-configurable trade settings (overrideable from UI) ──
    sl_points:          float = SL_POINTS           # Nifty SL in points
    trailing_sl_points: float = TRAILING_SL_POINTS  # trailing SL step (fixed mode)
    trail_mode:         str   = "fixed"             # "fixed" | "atr" | "supertrend" | "manual"
    trail_atr_mult:     float = 1.5                 # ATR multiplier for atr mode
    cached_trail_sl:    float | None = None         # pre-computed SL for atr/supertrend (candle loop)
    rr_ratio:           float = 2.0                 # risk:reward
    capital:            float = DEFAULT_CAPITAL      # ₹ available for qty calc
    qty_mode:           str   = "manual"            # 'manual' | 'capital'
    manual_qty:         int   = DEFAULT_QUANTITY    # used when qty_mode=manual
    strike_offset:      int   = 0                   # -3=ITM3,-2=ITM2,-1=ITM1,0=ATM,1=OTM1,2=OTM2,3=OTM3
    max_trades_per_day: int   = MAX_ORDERS_PER_DAY  # runtime-overridable (1-50)
    cooldown_minutes:   int   = int(os.getenv("COOLDOWN_MINUTES", "5"))  # post-exit wait
    recovery_mode: bool = False    # True if state was restored after a crash
    recovery_message: str = ""     # Human-readable description of what was recovered
    recovery_type: str = ""        # 'open' = trade still live | 'closed' = already exited | 'clean' = no trade


# ── Singleton State ─────────────────────────────────────────────
state = TraderState()

# ── Server-side persistent event log (survives page refresh) ──────
_event_log: deque[dict] = deque(maxlen=120)

def _log(icon: str, label: str, detail: str = "") -> None:
    """Append an event to the server-side log."""
    _event_log.append({
        "ts":     datetime.now().strftime("%H:%M:%S"),
        "icon":   icon,
        "label":  label,
        "detail": detail,
    })


# ── State Snapshot (crash recovery) ─────────────────────────────

def _trade_to_dict(t: "Trade") -> dict:
    """Serialize a Trade to a JSON-safe dict."""
    return {
        "id":            t.id,
        "timestamp":     t.timestamp,
        "direction":     t.direction,
        "instrument":    t.instrument,
        "entry_price":   t.entry_price,
        "entry_premium": t.entry_premium,
        "quantity":      t.quantity,
        "stop_loss":     t.stop_loss,
        "target":        t.target,
        "exit_price":    t.exit_price,
        "exit_premium":  t.exit_premium,
        "exit_time":     t.exit_time,
        "exit_reason":   t.exit_reason,
        "pnl":           t.pnl,
        "status":        t.status,
        "order_id":      t.order_id,
        "sl_order_id":   getattr(t, "sl_order_id", None),
        "paper":         t.paper,
        "app_managed":   getattr(t, "app_managed", True),
    }


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically using a temp file + rename.

    Protects against corrupt snapshots from a mid-write crash (kill -9, OOM, etc.).
    os.replace() is atomic on POSIX — the old file is never partially overwritten.
    Strategy: write to .tmp → backup old → rename .tmp → old
    """
    tmp = path.with_suffix(".tmp")
    bak = path.with_suffix(".bak")
    try:
        tmp.write_text(content, encoding="utf-8")
        if path.exists():
            import shutil
            shutil.copy2(path, bak)   # keep last-known-good backup
        os.replace(tmp, path)         # atomic rename — never a partial write
    except Exception as e:
        print(f"⚠️  Snapshot write failed: {e}")
        tmp.unlink(missing_ok=True)   # clean up partial temp


def _save_state_snapshot():
    """Write a full state snapshot to disk after every significant change.

    Called on: entry, exit, trailing SL update.
    On restart, `_recover_state()` reads this to rebuild state.

    DESIGN: active_trade is stored separately from trades_today.
    trades_today contains ONLY completed (exited) trades.
    This prevents the duplicate-on-recovery bug where the active trade
    would appear twice after a crash (once from trades_today list,
    once from the active_trade recovery append).
    """
    active = state.active_trade

    # Only include COMPLETED trades — active trade is stored separately
    completed_trades = [t for t in state.trades_today if t is not active]

    snapshot = {
        "date":            datetime.now().strftime("%Y-%m-%d"),
        "total_pnl":       round(state.total_pnl, 2),
        "orders_placed":   state.orders_placed,
        "is_paper_mode":   state.is_paper_mode,
        "is_running":      state.is_running,       # ← survive server restarts
        "selected_strategy": state.selected_strategy,
        # ── Runtime trade settings (survive restart) ──
        "sl_points":          state.sl_points,
        "trailing_sl_points": state.trailing_sl_points,
        "trail_mode":         state.trail_mode,
        "trail_atr_mult":     state.trail_atr_mult,
        "rr_ratio":           state.rr_ratio,
        "qty_mode":           state.qty_mode,
        "manual_qty":         state.manual_qty,
        "capital":            state.capital,
        "strike_offset":      state.strike_offset,
        "max_trades_per_day": state.max_trades_per_day,
        "cooldown_minutes":   state.cooldown_minutes,
        # cooldown — survive restarts so re-entry filter stays intact
        "last_exit_time":      state.last_exit_time.isoformat() if state.last_exit_time else None,
        "last_exit_direction": state.last_exit_direction,
        # active_trade stored with full detail (includes sl_order_id for crash cancel)
        "active_trade": _trade_to_dict(active) if active else None,
        # ── Trail tracking — survive restarts so ATR trail resumes correctly ──
        # Without these, entry_nifty_sl=0 after restart → new guard never fires
        "entry_nifty_sl":           state.entry_nifty_sl,
        "lowest_price_since_entry": state.lowest_price_since_entry,
        "highest_price_since_entry":state.highest_price_since_entry,
        "active_option_token":      state.active_option_token,
        # Only completed trades — avoids double-counting on recovery
        "trades_today": [_trade_to_dict(t) for t in completed_trades],
    }
    _atomic_write(STATE_SNAPSHOT_FILE, json.dumps(snapshot, indent=2))


def _subscribe_option_tick(token: int | None) -> None:
    """Add option instrument to KiteTicker so LTP arrives every ~1s."""
    if not token:
        return
    try:
        if kite_manager.ticker and kite_manager.is_streaming:
            kite_manager.ticker.subscribe([token])
            kite_manager.ticker.set_mode(kite_manager.ticker.MODE_LTP, [token])
            print(f"📡 Subscribed option token {token} to WebSocket")
    except Exception as e:
        print(f"⚠️  Option tick subscribe failed: {e}")


def _unsubscribe_option_tick(token: int | None) -> None:
    """Remove option from KiteTicker after trade closes."""
    if not token:
        return
    try:
        if kite_manager.ticker and kite_manager.is_streaming:
            kite_manager.ticker.unsubscribe([token])
            print(f"📡 Unsubscribed option token {token} from WebSocket")
    except Exception as e:
        print(f"⚠️  Option tick unsubscribe failed: {e}")


def _recover_state(snapshot_file: Path | None = None):
    """On startup, restore state from snapshot and cross-check Zerodha positions.

    Recovery steps:
    1. Load snapshot → only proceed if it's from TODAY.
    2. Restore orders_placed, total_pnl, trades_today.
    3. If active_trade in snapshot → verify against Zerodha's live positions.
       a. Zerodha confirms position open → restore active_trade, resume managing it.
       b. Zerodha shows position closed → mark trade as crashed/exited, log PnL.
       c. Kite not authenticated → restore from snapshot (best-effort).
    4. Set recovery_mode=True so the UI can show a warning banner.
    """
    f = snapshot_file or STATE_SNAPSHOT_FILE
    if not f.exists():
        return

    try:
        snap = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        # Primary snapshot corrupt — try the .bak if it exists
        bak = f.with_suffix(".bak")
        if bak.exists():
            print(f"⚠️  Snapshot corrupt ({e}) — trying backup {bak.name}")
            try:
                snap = json.loads(bak.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                print("❌ Backup also corrupt — starting fresh")
                return
        else:
            print(f"⚠️  Snapshot corrupt and no backup — starting fresh ({e})")
            return

    # Only recover if snapshot is from TODAY
    today = datetime.now().strftime("%Y-%m-%d")
    if snap.get("date") != today:
        print("📅 Snapshot is from a previous day — starting fresh")
        f.unlink(missing_ok=True)
        return

    # ── Restore base counters ─────────────────────────────────
    state.total_pnl         = snap.get("total_pnl", 0.0)
    state.is_paper_mode     = not LIVE_TRADING  # always from env — never let snapshot override this
    state.selected_strategy = snap.get("selected_strategy", "smart_router")

    # orders_placed: cross-check against trade_log.json for today
    # so restarts don't reset the daily trade counter
    try:
        if TRADE_LOG_FILE.exists():
            log_data  = json.loads(TRADE_LOG_FILE.read_text(encoding="utf-8"))
            log_today = log_data.get("date", "")
            if log_today == today:
                # Count all trades (including fills) from today's log
                state.orders_placed = len(log_data.get("trades", []))
            else:
                state.orders_placed = snap.get("orders_placed", 0)
        else:
            state.orders_placed = snap.get("orders_placed", 0)
    except Exception:
        state.orders_placed = snap.get("orders_placed", 0)
    print(f"♻️  orders_placed restored from log: {state.orders_placed}")

    # Restore cooldown so a restart mid-cooldown doesn't bypass the filter
    last_exit_raw = snap.get("last_exit_time")
    if last_exit_raw:
        try:
            state.last_exit_time      = datetime.fromisoformat(last_exit_raw)
            state.last_exit_direction = snap.get("last_exit_direction")
        except (ValueError, TypeError):
            pass

    # ── Restore runtime trade settings ───────────────────────
    state.sl_points          = snap.get("sl_points",          SL_POINTS)
    state.trailing_sl_points = snap.get("trailing_sl_points", TRAILING_SL_POINTS)
    state.trail_mode         = snap.get("trail_mode",         "fixed")
    state.trail_atr_mult     = snap.get("trail_atr_mult",     1.5)
    state.rr_ratio           = snap.get("rr_ratio",           2.0)
    state.qty_mode           = snap.get("qty_mode",           "manual")
    state.manual_qty         = snap.get("manual_qty",         DEFAULT_QUANTITY)
    state.cooldown_minutes   = snap.get("cooldown_minutes",   state.cooldown_minutes)
    state.capital            = snap.get("capital",            DEFAULT_CAPITAL)
    state.strike_offset      = snap.get("strike_offset",      0)   # default ATM
    state.max_trades_per_day = snap.get("max_trades_per_day", MAX_ORDERS_PER_DAY)
    # ── Restore running flag (auto-resume after server restart) ──
    state.is_running         = snap.get("is_running",         False)

    # ── Restore historical trades ─────────────────────────────
    for t in snap.get("trades_today", []):
        state.trades_today.append(Trade(
            id=t["id"], timestamp=t["timestamp"],
            direction=t["direction"], instrument=t["instrument"],
            entry_price=t["entry_price"],
            entry_premium=t.get("entry_premium", t["entry_price"]),  # back-compat: old snapshots lack this
            quantity=t["quantity"],
            stop_loss=t["stop_loss"], target=t["target"],
            exit_price=t.get("exit_price"),
            exit_premium=t.get("exit_premium"),
            exit_time=t.get("exit_time"),
            exit_reason=t.get("exit_reason"), pnl=t.get("pnl", 0.0),
            status=t.get("status", OrderStatus.EXITED),
            order_id=t.get("order_id"), paper=t.get("paper", True),
        ))

    # ── Recover active trade if present ───────────────────────
    at = snap.get("active_trade")
    if not at:
        state.recovery_mode    = True
        state.recovery_type    = "clean"
        state.recovery_message = (
            f"✅ State restored: {len(state.trades_today)} trades today, "
            f"PnL=₹{state.total_pnl:+,.0f}, no open position"
        )
        print(f"🔄 [RECOVERY] {state.recovery_message}")
        return

    # ── Guard: reject recovered trades on expired options ────────────
    # If the option's expiry date (encoded in the Kite tradingsymbol) is
    # in the past, the position has already been settled/expired by the
    # exchange. Restoring it would create an un-exitable ghost trade.
    def _option_expiry_is_past(instrument: str) -> bool:
        """Parse option expiry from Kite symbol and check if it's passed.

        Kite weekly Nifty symbols: NIFTY{YY}{M}{DD}{strike}{CE/PE}
        e.g. NIFTY2632422950PE → year=26 month=3 day=24
        Month can be 1-9, O, N, D (single-char NSE month codes).
        """
        import re
        from datetime import date as dt_date
        sym = instrument.replace("NFO:", "").upper()
        # Standard Kite format: NIFTY + 2-digit year + 1-char month + 2-digit day
        m = re.match(r"NIFTY(\d{2})([0-9OND])(\d{2})", sym)
        if not m:
            # Legacy/non-standard format (e.g. NIFTY20260317_23200CE) —
            # try to extract an 8-digit date YYYYMMDD
            m2 = re.search(r"(\d{4})(\d{2})(\d{2})", sym)
            if m2:
                try:
                    expiry = dt_date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
                    return expiry < dt_date.today()
                except ValueError:
                    pass
            return False  # unknown format — don't discard
        year_2d, month_code, day = m.group(1), m.group(2), m.group(3)
        month_map = {'O': 10, 'N': 11, 'D': 12}
        try:
            month = month_map.get(month_code, int(month_code))
            year  = 2000 + int(year_2d)
            expiry = dt_date(year, month, int(day))
            return expiry < dt_date.today()
        except (ValueError, KeyError):
            return False

    instrument_str = at.get("instrument", "")
    if _option_expiry_is_past(instrument_str):
        print(
            f"⏰ [RECOVERY] Option {instrument_str} has expired — "
            f"discarding stale trade from snapshot (no ghost position)."
        )
        state.recovery_mode    = True
        state.recovery_type    = "expired"
        state.recovery_message = (
            f"Option {instrument_str} expired — stale trade discarded. "
            f"Check Zerodha for settlement P&L."
        )
        return

    # Check Zerodha for live position
    zerodha_qty  = _get_zerodha_position_qty(at["instrument"])
    paper_mode   = at.get("paper", True)

    if zerodha_qty != 0 or paper_mode:
        # Position still open in Zerodha (or paper mode → trust snapshot)
        recovered_trade = Trade(
            id=at["id"], timestamp=at["timestamp"],
            direction=at["direction"], instrument=at["instrument"],
            entry_price=at["entry_price"],
            entry_premium=at.get("entry_premium", at["entry_price"]),  # back-compat
            quantity=at["quantity"],
            stop_loss=at["stop_loss"], target=at["target"],
            order_id=at.get("order_id"),
            sl_order_id=at.get("sl_order_id"),   # restore SL order for cancel-on-exit
            paper=paper_mode,
            app_managed=at.get("app_managed", True),  # preserve management mode
            status=OrderStatus.FILLED,
        )
        state.active_trade = recovered_trade
        # Append only if not already in trades_today (guards against old snapshots
        # that included the active trade in the trades_today list)
        existing_ids = {t.id for t in state.trades_today}
        if recovered_trade.id not in existing_ids:
            state.trades_today.append(recovered_trade)
        # ── Restore trail tracking state ─────────────────────────────────────
        # Critical: entry_nifty_sl=0 after restart → ATR activation guard
        # evaluates candidate < 0 → always False → trail never fires!
        # Restore the extremes too so the trail picks up exactly where it left off.
        state.entry_nifty_sl            = snap.get("entry_nifty_sl",            at["stop_loss"])
        state.highest_price_since_entry = snap.get("highest_price_since_entry", at["entry_price"])
        state.lowest_price_since_entry  = snap.get("lowest_price_since_entry",  at["entry_price"])
        # Restore option token so WebSocket subscription resumes on recovery
        opt_tok = snap.get("active_option_token")
        if opt_tok:
            state.active_option_token = opt_tok
            _subscribe_option_tick(opt_tok)   # re-sub if ticker already running
        state.recovery_mode    = True
        state.recovery_type    = "open"
        state.recovery_message = (
            f"🚨 RECOVERED open {'PAPER' if paper_mode else 'LIVE'} trade: "
            f"{at['direction'].upper()} {at['instrument']} "
            f"entry=₹{at['entry_price']:,.0f} SL=₹{at['stop_loss']:,.0f}"
        )
        print(f"🔄 [RECOVERY] {state.recovery_message}")
        print(f"   ⚠️  Auto-trader is NOT running — click START to resume managing it.")
    else:
        # Position already closed in Zerodha while app was down
        ghost_trade = Trade(
            id=at["id"], timestamp=at["timestamp"],
            direction=at["direction"], instrument=at["instrument"],
            entry_price=at["entry_price"],
            entry_premium=at.get("entry_premium", at["entry_price"]),  # back-compat
            quantity=at["quantity"],
            stop_loss=at["stop_loss"], target=at["target"],
            order_id=at.get("order_id"), paper=paper_mode,
            status=OrderStatus.EXITED,
            exit_reason="App crashed — position closed by Zerodha/broker while app was down",
            pnl=0.0,
        )
        existing_ids = {t.id for t in state.trades_today}
        if ghost_trade.id not in existing_ids:
            state.trades_today.append(ghost_trade)
        state.recovery_mode    = True
        state.recovery_type    = "closed"
        state.recovery_message = (
            f"Position was closed while app was down: "
            f"{at['direction'].upper()} {at['instrument']}. "
            f"Check Zerodha for actual P&L."
        )
        print(f"🔄 [RECOVERY] {state.recovery_message}")


def _get_zerodha_position_qty(instrument: str) -> int:
    """Query Zerodha for current net quantity of `instrument`.

    Returns net quantity (positive = long, negative = short, 0 = closed/not found).
    Returns 0 if Kite is not authenticated (paper mode safe fallback).
    """
    try:
        if not kite_manager.is_authenticated:
            return 0
        positions = kite_manager.kite.positions()
        tradingsymbol = instrument.replace("NFO:", "")
        for pos in positions.get("net", []):
            if pos.get("tradingsymbol") == tradingsymbol:
                return int(pos.get("quantity", 0))
        return 0
    except Exception as e:
        print(f"⚠️ Could not check Zerodha positions: {e}")
        return 0   # Assume closed if can't verify


# ── Run recovery immediately at module load ───────────────────────
_recover_state()


# ── Safety Checks ──────────────────────────────────────────────

def _now_time() -> dt_time:
    """Return current wall-clock time. Thin wrapper so tests can patch it."""
    return datetime.now().time()


def _check_safety() -> tuple[bool, str]:
    """Check all safety limits before placing an order."""
    if state.kill_switch:
        return False, "🛑 Kill switch activated — no new trades"

    if state.orders_placed >= state.max_trades_per_day:
        return False, f"Max trades/day reached ({state.orders_placed}/{state.max_trades_per_day})"

    if state.total_pnl <= -MAX_LOSS_PER_DAY:
        return False, f"Max daily loss hit (₹{MAX_LOSS_PER_DAY})"

    now = _now_time()
    if now >= EXIT_TIME:
        return False, f"Past exit time ({EXIT_TIME.strftime('%H:%M')})"

    # No trading in first 3 minutes
    if now < dt_time(9, 18):
        return False, "Too early — waiting for market to settle (first 3 min)"

    # ── Post-exit cooldown ────────────────────────────────────────
    # After any exit, wait COOLDOWN_MINUTES before re-entering.
    # Prevents chasing a trending instrument that already hit SL.
    if state.last_exit_time is not None and state.cooldown_minutes > 0:
        elapsed = (datetime.now() - state.last_exit_time).total_seconds() / 60
        if elapsed < state.cooldown_minutes:
            remaining = int(state.cooldown_minutes - elapsed)
            return False, f"Cooldown: {remaining}m left after last exit (wait {state.cooldown_minutes}m)"

    return True, "All safety checks passed"


def _is_market_hours() -> bool:
    """Check if within NSE market hours."""
    now = datetime.now().time()
    return dt_time(9, 15) <= now <= dt_time(15, 30)


# ── Order Placement ───────────────────────────────────────────────────────

# CAPITAL moved to line 39 as DEFAULT_CAPITAL; LOT_SIZE consolidated below
LOT_SIZE = int(os.getenv("LOT_SIZE", "65"))  # Nifty lot size (65 as of Apr 2025 SEBI revision)


# Cache instruments to avoid repeated API calls
_nfo_instruments_cache: list[dict] | None = None
_nfo_cache_date: str = ""


def _get_nfo_instruments() -> list[dict]:
    """Fetch NFO instruments list from Kite (cached once per day)."""
    global _nfo_instruments_cache, _nfo_cache_date
    today = datetime.now().strftime("%Y-%m-%d")
    if _nfo_cache_date == today and _nfo_instruments_cache:
        return _nfo_instruments_cache
    try:
        instruments = kite_manager.kite.instruments("NFO")
        _nfo_instruments_cache = [i for i in instruments if i["name"] == "NIFTY"]
        _nfo_cache_date = today
        print(f"✅ NFO instruments loaded: {len(_nfo_instruments_cache)} NIFTY options")
    except Exception as e:
        print(f"⚠️ Could not fetch instruments: {e}")
        _nfo_instruments_cache = []
    return _nfo_instruments_cache or []


def _get_nearest_expiry_date() -> datetime:
    """Return nearest FUTURE weekly expiry date for Nifty options.

    NSE changed Nifty 50 weekly option expiry from Thursday → Tuesday
    effective October 2024 (SEBI circular on expiry-day rationalisation).
    weekday(): Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6

    Safety rule: if today IS expiry day and it's past 14:00 (2 PM),
    roll forward to NEXT week. Trading a 90-minute-to-expiry option
    is extremely risky — theta decay is brutal and liquidity dries up.
    This also prevents the scenario where the app enters a trade on a
    same-day contract that expires at 3:30 PM before we can exit.
    """
    from datetime import timedelta
    EXPIRY_WEEKDAY  = 1      # Tuesday (Nifty 50 weekly expiry as of Oct 2024)
    CUTOFF_HOUR     = 14     # 2 PM — no same-day expiry after this
    today = datetime.now()
    days_until_expiry = (EXPIRY_WEEKDAY - today.weekday()) % 7
    if days_until_expiry == 0 and today.hour >= CUTOFF_HOUR:
        # Past 2 PM on expiry day → roll to next week's expiry
        days_until_expiry = 7
    return today + timedelta(days=days_until_expiry)


def _get_option_symbol(nifty_price: float, direction: Direction) -> tuple[str, int]:
    """Find the nearest OTM Nifty option tradingsymbol via Kite instruments.

    Returns: (tradingsymbol, instrument_token) or raises RuntimeError if not found.

    Uses the Kite instruments API — no guessing at symbol strings.
    """
    atm_strike = round(nifty_price / 50) * 50
    option_type = "CE" if direction == Direction.LONG else "PE"

    # strike_offset=0 → ATM, 1 → 1-OTM, 2 → 2-OTM (configurable from UI)
    offset = state.strike_offset * 50   # each step = 50 points
    strike = atm_strike + offset if direction == Direction.LONG else atm_strike - offset

    expiry_date = _get_nearest_expiry_date()
    expiry_str  = expiry_date.strftime("%Y-%m-%d")   # Kite format: "2026-03-13"

    instruments = _get_nfo_instruments()
    if not instruments:
        raise RuntimeError("NFO instruments not available — is Kite authenticated?")

    # Find exact match
    matches = [
        i for i in instruments
        if i["strike"] == float(strike)
        and i["instrument_type"] == option_type
        and str(i["expiry"]) == expiry_str
    ]

    if not matches:
        # Fallback: try ATM strike
        atm_matches = [
            i for i in instruments
            if i["strike"] == float(atm_strike)
            and i["instrument_type"] == option_type
            and str(i["expiry"]) == expiry_str
        ]
        if not atm_matches:
            raise RuntimeError(
                f"No {option_type} option found: strike={strike}, expiry={expiry_str}. "
                f"Check if expiry date is correct ({expiry_date.strftime('%A %d %b %Y')})"
            )
        matches = atm_matches
        strike  = atm_strike
        print(f"⚠️ OTM not found, falling back to ATM {strike} {option_type}")

    instrument = matches[0]
    symbol     = instrument["tradingsymbol"]
    token      = instrument["instrument_token"]

    _strike_labels = {-3:"ITM3",-2:"ITM2",-1:"ITM1",0:"ATM",1:"OTM1",2:"OTM2",3:"OTM3"}
    offset_label = _strike_labels.get(state.strike_offset, f"OTM{abs(state.strike_offset)}")
    print(f"🎯 Strike Selection:")
    print(f"   Nifty: {nifty_price:.0f} | ATM: {atm_strike} | Offset: {offset_label} | Picked: {strike} {option_type}")
    print(f"   Expiry: {expiry_str} | Symbol: {symbol} | Token: {token}")

    return symbol, token


def _place_order(symbol: str, direction: Direction,
                 quantity: int, price: float) -> str | None:
    """Place order via Zerodha or simulate in paper mode."""
    if state.is_paper_mode:
        order_id = f"PAPER-{datetime.now().strftime('%H%M%S')}"
        print(f"📝 [PAPER] {direction.value.upper()} {quantity}x {symbol} @ ₹{price:.2f}")
        return order_id

    # ── Margin check before placing ───────────────────────────
    try:
        margins = kite_manager.kite.margins(segment="equity")
        available = margins.get("net", 0)
        print(f"💰 Available margin: ₹{available:,.0f}")
    except Exception:
        available = 0  # proceed, order will fail at exchange if insufficient

    # LIVE ORDER via Kite Connect
    # LONG = BUY CE option  (bullish: buying a call)
    # SHORT = BUY PE option (bearish: buying a put)
    # Both directions are OPTION BUYER entries — always BUY at entry.
    # The direction only controls WHICH option we buy (CE vs PE),
    # resolved in _get_option_symbol BEFORE this function is called.
    try:
        order_id = kite_manager.kite.place_order(
            variety=kite_manager.kite.VARIETY_REGULAR,
            exchange="NFO",
            tradingsymbol=symbol,   # already clean — no "NFO:" prefix
            transaction_type=kite_manager.kite.TRANSACTION_TYPE_BUY,
            quantity=quantity,
            product=kite_manager.kite.PRODUCT_MIS,   # Intraday MIS
            order_type=kite_manager.kite.ORDER_TYPE_MARKET,
            validity="DAY",
        )
        print(f"✅ [LIVE] BUY {quantity}x {symbol} | ID: {order_id}")
        return str(order_id)
    except Exception as e:
        print(f"❌ [LIVE] Order failed: {e}")
        state.last_signal_reason = f"❌ Order failed: {e}"
        return None


def _estimate_option_sl_trigger(sl_points: float, entry_premium: float) -> float:
    """Estimate option-level SL trigger price from the REAL entry premium.

    Uses a fixed ATM delta of 0.5 (Brenner-Subrahmanyam approximation):
      trigger = entry_premium - (sl_points × delta)

    Example: entry=₹210, SL=30pts, delta=0.5 → trigger=₹195
    (the option should lose ~₹15 when Nifty drops 30 points)

    This is used ONCE at entry to set a fixed exchange crash backstop.
    The tick guard handles all active / trailing SL management.
    """
    ASSUMED_DELTA = 0.5   # ATM 1-week option delta approximation
    expected_loss = sl_points * ASSUMED_DELTA
    trigger = max(entry_premium - expected_loss, 1.0)
    return round(trigger, 1)


def _place_sl_order(trade: "Trade", sl_trigger_price: float) -> str | None:
    """Place a Stop-Loss Market (SL-M) order at the exchange.

    This is a CRASH BACKSTOP — placed once at entry, never updated.
    The tick guard handles all active SL management (no API calls).
    This only fires if the app dies and the exchange holds the order.
    """
    if trade.paper:
        fake_id = f"SL-PAPER-{datetime.now().strftime('%H%M%S')}"
        print(f"📝 [PAPER SL] SL-M backstop @ ₹{sl_trigger_price:.2f} | ID: {fake_id}")
        return fake_id

    try:
        clean = trade.instrument.replace("NFO:", "")
        # We are always an OPTION BUYER (BUY CE or BUY PE at entry).
        # The SL-M backstop closes the position → always SELL.
        order_id = kite_manager.kite.place_order(
            variety=kite_manager.kite.VARIETY_REGULAR,
            exchange="NFO",
            tradingsymbol=clean,
            transaction_type=kite_manager.kite.TRANSACTION_TYPE_SELL,
            quantity=trade.quantity,
            product=kite_manager.kite.PRODUCT_MIS,
            order_type=kite_manager.kite.ORDER_TYPE_SLM,
            trigger_price=sl_trigger_price,
            validity="DAY",
        )
        print(f"🛡 [LIVE] SL-M backstop placed @ ₹{sl_trigger_price:.2f} | ID: {order_id}")
        return str(order_id)
    except Exception as e:
        print(f"⚠️  SL-M order failed (tick guard still protects): {e}")
        return None


def _cancel_sl_order(trade: "Trade") -> None:
    """Cancel the standing SL-M backstop order before placing an exit.

    MUST be called before any exit order to prevent double-fill.
    """
    if not trade.sl_order_id or trade.paper:
        trade.sl_order_id = None
        return
    if trade.sl_order_id.startswith("SL-PAPER"):
        trade.sl_order_id = None
        return
    try:
        kite_manager.kite.cancel_order(
            variety=kite_manager.kite.VARIETY_REGULAR,
            order_id=trade.sl_order_id,
        )
        print(f"🗑 SL-M backstop {trade.sl_order_id} cancelled")
    except Exception as e:
        print(f"⚠️  Could not cancel SL-M {trade.sl_order_id}: {e}")
    finally:
        trade.sl_order_id = None


def _compute_option_trigger_for_nifty_sl(nifty_sl: float) -> float:
    """Convert a Nifty spot SL level → estimated option trigger price.

    Uses the delta offset from the original entry:
      option_trigger = entry_option_trigger
                       + DELTA × (nifty_sl − entry_nifty_sl)

    For a LONG trade: nifty_sl > entry_nifty_sl (SL moved in our favour),
    so option_trigger rises (we're protecting more premium).
    For a SHORT trade: nifty_sl < entry_nifty_sl → same math, negative diff.
    """
    ASSUMED_DELTA = 0.5
    delta_nifty   = nifty_sl - state.entry_nifty_sl        # pts SL moved from entry
    new_trigger   = state.entry_option_trigger + ASSUMED_DELTA * delta_nifty
    return max(round(new_trigger, 1), 1.0)


ASSUMED_DELTA = 0.5   # ATM delta assumption for premium ↔ Nifty conversion


def _nifty_to_option_premium(nifty_level: float, trade: "Trade") -> float:
    """Convert any Nifty spot level → estimated option premium at that level.

    Unified formula (works for SL, target, trailing SL, entry):
        direction_sign = +1 for LONG (CE), -1 for SHORT (PE)
        option_prem = entry_premium + (nifty_level - entry_price)
                      × direction_sign × delta

    Examples:
        SHORT entry=23414 prem=89.29  SL=23444 →  89.29 + (23444-23414) × -1 × 0.5 = 74.3
        SHORT entry=23414 prem=89.29  Tgt=23324 → 89.29 + (23324-23414) × -1 × 0.5 = 134.3
        LONG  entry=23000 prem=150    SL=22970  →  150  + (22970-23000) × +1 × 0.5 = 135.0
        LONG  entry=23000 prem=150    Tgt=23060 →  150  + (23060-23000) × +1 × 0.5 = 180.0
    """
    sign = 1.0 if trade.direction == "long" else -1.0
    delta_nifty = (nifty_level - trade.entry_price) * sign
    result = trade.entry_premium + delta_nifty * ASSUMED_DELTA
    return max(round(result, 1), 0.1)


def _sync_trailing_sl_to_exchange() -> None:
    """Modify the standing SL-M order at Zerodha to the current trailed level.

    Called from the 5-min candle loop — API calls are safe here.
    Skipped in paper mode (logged instead).
    """
    trade = state.active_trade
    if not trade or not state.pending_sl_exchange_update:
        return

    state.pending_sl_exchange_update = False   # clear flag first (idempotent)

    new_trigger = _compute_option_trigger_for_nifty_sl(trade.stop_loss)
    nifty_sl    = trade.stop_loss

    is_paper_order = trade.paper or not trade.sl_order_id or trade.sl_order_id.startswith("SL-PAPER")
    if is_paper_order:
        print(f"📝 [PAPER] Exchange SL-M would update → "
              f"₹{new_trigger:.1f} (Nifty SL ₹{nifty_sl:.0f})")
        return

    try:
        kite_manager.kite.modify_order(
            variety=kite_manager.kite.VARIETY_REGULAR,
            order_id=trade.sl_order_id,
            order_type=kite_manager.kite.ORDER_TYPE_SLM,   # explicit — preserve SL-M type
            trigger_price=new_trigger,
        )
        print(f"🛡 [TRAILING] Exchange SL-M updated → "
              f"₹{new_trigger:.1f} option | Nifty SL ₹{nifty_sl:.0f}")
    except Exception as e:
        # Order may have been filled already (trade closed) — non-fatal
        print(f"⚠️  Exchange SL-M modify failed (order may be filled): {e}")
    # Trailing SL is managed purely in-app by the tick guard (zero API calls).
    # The exchange SL-M is a fixed crash backstop — updating it on every
    # trailing move would block the tick thread with 3 API calls per update.


def _exit_position(reason: str, current_price: float):
    """Exit active trade.

    current_price = Nifty SPOT at the moment of exit decision.
    Real P&L uses option LTP fetched at exit (not Nifty spot change).
    """
    trade = state.active_trade
    if not trade:
        return
    if state.exit_in_progress:
        _log("⏳", "EXIT", "already in-flight — skipping duplicate tick trigger")
        return
    state.exit_in_progress = True

    # ── Cancel exchange SL-M before placing exit order ────────────
    # CRITICAL: if SL-M is still open at exchange and we also place an
    # exit order, both could fill → double-exit (short-sell problem).
    _cancel_sl_order(trade)

    # ── Fetch real option LTP at exit (best-effort) ───────────────
    # Used for accurate P&L. Falls back to entry_premium if unavailable.
    sym_clean      = trade.instrument.replace("NFO:", "")
    exit_ltp     = kite_manager.get_option_ltp(sym_clean)
    exit_premium = (
        exit_ltp if isinstance(exit_ltp, (int, float)) and exit_ltp > 0
        else trade.entry_premium   # fallback: report P&L=0 (better than Nifty×qty math)
    )

    if not state.is_paper_mode:
        try:
            # Option buyer: always SELL to close the long option position.
            kite_manager.kite.place_order(
                variety=kite_manager.kite.VARIETY_REGULAR,
                exchange="NFO",
                tradingsymbol=sym_clean,
                transaction_type=kite_manager.kite.TRANSACTION_TYPE_SELL,
                quantity=trade.quantity,
                product=kite_manager.kite.PRODUCT_MIS,
                order_type=kite_manager.kite.ORDER_TYPE_MARKET,
                validity="DAY",
            )
        except Exception as e:
            err_str = str(e).lower()

            # ── TERMINAL: instrument expired / does not exist ──────────────
            # The option has expired or was never valid (stale snapshot from
            # a previous expiry). Re-arming SL-M is pointless — it will also
            # fail with the same error, creating a death-loop every second.
            # Force-close the paper trade; for live, the exchange has already
            # settled/expired the position so clearing state is correct.
            if "expired" in err_str or "does not exist" in err_str:
                _log("💀", "EXIT — INSTRUMENT EXPIRED",
                     f"Option {sym_clean} has expired. Forcing trade closure (P&L unknown).")
                print(f"💀 [FORCE CLOSE] {sym_clean} expired — clearing stale trade from state")
                trade.exit_reason  = f"Instrument expired: {e}"
                trade.exit_time    = datetime.now().isoformat()
                trade.status       = OrderStatus.EXITED
                trade.pnl          = 0.0   # can't compute — option settled
                state.total_pnl   += 0.0
                state.active_trade = None
                state.exit_in_progress = False
                state.active_option_token = None
                _save_trade_log()
                _save_state_snapshot()
                return   # ← clean exit — no re-arm, no loop

            # ── TRANSIENT: network / timeout / rate-limit ──────────────────
            # Zerodha rejected/timed-out the order. Position is still open.
            # Keep trade alive, re-arm SL-M so exchange backstop holds.
            _log("❌", "EXIT FAILED", str(e))
            print(f"❌ Exit order FAILED ({e}) — trade kept active, re-arming SL-M")
            sl_trigger = _compute_option_trigger_for_nifty_sl(trade.stop_loss)
            new_sl_id  = _place_sl_order(trade, sl_trigger)
            if new_sl_id:
                trade.sl_order_id = new_sl_id
                _log("🛡", "SL-M re-armed", f"trigger ₹{sl_trigger:.1f} order {new_sl_id}")
            _save_state_snapshot()
            state.exit_in_progress = False   # allow retry on next tick
            return   # ← abort exit — trade stays active

    # ── Real P&L: (option exit price − option entry price) × units ─
    # We are always an OPTION BUYER so:
    #   LONG (bought CE): profit when CE premium rises
    #   SHORT (bought PE): profit when PE premium rises
    # Both directions profit when their option premium goes UP.
    pnl = (exit_premium - trade.entry_premium) * trade.quantity

    trade.exit_price   = current_price    # Nifty spot at exit (for logs)
    trade.exit_premium = exit_premium     # Option LTP at exit
    trade.exit_time    = datetime.now().isoformat()
    trade.exit_reason  = reason
    trade.pnl          = round(pnl, 2)
    trade.status       = OrderStatus.EXITED

    state.total_pnl         += pnl
    # Unsubscribe option from WebSocket before clearing active trade
    _unsubscribe_option_tick(state.active_option_token)
    state.active_option_token = None
    state.active_trade       = None
    state.exit_in_progress   = False          # exit completed — reset debounce
    state.last_exit_time      = datetime.now() # cooldown starts now
    state.last_exit_direction = trade.direction

    mode  = "📝 PAPER" if trade.paper else "🟢 LIVE"
    emoji = "🟢" if pnl >= 0 else "🔴"
    print(
        f"{emoji} [{mode}] EXIT {trade.direction.upper()} {trade.quantity}u {sym_clean}\n"
        f"   Nifty: entry ₹{trade.entry_price:.0f} → exit ₹{current_price:.0f} "
        f"({current_price - trade.entry_price:+.0f} pts)\n"
        f"   Option: entry ₹{trade.entry_premium:.2f} → exit ₹{exit_premium:.2f} "
        f"({exit_premium - trade.entry_premium:+.2f})\n"
        f"   P&L: ₹{pnl:+,.2f} | Qty: {trade.quantity} units | Reason: {reason}"
    )
    _log(emoji, f"EXIT {trade.direction.upper()}",
         f"₹{pnl:+,.0f} | {reason} | Nifty ₹{current_price:.0f} | Opt ₹{exit_premium:.2f}")

    _save_trade_log()
    _save_state_snapshot()   # ← crash recovery: snapshot immediately on exit (no active_trade)


# ── Core Logic: Evaluate & Act ────────────────────────────────

def evaluate_and_act(df, current_price: float):
    """Main loop logic: evaluate strategy, manage positions.

    Called on each new candle or tick interval.

    Args:
        df: Latest OHLCV DataFrame.
        current_price: Latest Nifty price.
    """
    now = datetime.now()
    state.last_evaluation = now.isoformat()

    # 1. Time-based exit
    if now.time() >= EXIT_TIME and state.active_trade:
        _exit_position("Time-based exit (3:15 PM)", current_price)
        return

    # 2. If we have an active trade — manage it
    #    Acquire the tick guard lock so we don't race with real-time ticks.
    if state.active_trade:
        trade = state.active_trade
        sl_before = trade.stop_loss
        _update_trail_sl_cache(df)   # pre-compute ATR/Supertrend SL before tick thread uses it
        with _tick_guard_lock:
            if state.active_trade:   # re-check: tick may have just closed it
                _manage_active_trade(current_price, source="🕯 candle")
        # Log trail move so it appears in the UI event log
        if state.active_trade and state.active_trade.stop_loss != sl_before:
            t            = state.active_trade
            old_sl_prem  = _nifty_to_option_premium(sl_before, t)
            new_sl_prem  = _nifty_to_option_premium(t.stop_loss, t)
            new_tgt_prem = _nifty_to_option_premium(t.target, t) if t.target else None
            ltp_val      = state.last_option_ltp
            tgt_part = (' | Target ₹' + f'{new_tgt_prem:.1f}') if new_tgt_prem else ''
            ltp_part = (' | LTP ₹'    + f'{ltp_val:.1f}')      if ltp_val      else ''
            _log('\U0001f4c8', 'Trail SL moved',
                 'SL Prem ₹' + f'{old_sl_prem:.1f}->₹{new_sl_prem:.1f}'
                 + tgt_part + ltp_part + ' (Nifty ₹' + f'{current_price:.0f})')
        # ── Refresh live option LTP for unrealized P&L display ────
        if state.active_trade:
            t   = state.active_trade
            sym = t.instrument.replace('NFO:', '')
            ltp = kite_manager.get_option_ltp(sym)
            if ltp and ltp > 0:
                state.last_option_ltp = ltp
                prem_sl  = _nifty_to_option_premium(t.stop_loss, t)
                prem_tgt = _nifty_to_option_premium(t.target, t) if t.target else None
                tgt_part = (' | Target ₹' + f'{prem_tgt:.1f}') if prem_tgt else ''
                _log('\U0001f4b0', 'LTP refresh',
                     'LTP ₹' + f'{ltp:.1f} | SL ₹{prem_sl:.1f}' + tgt_part + ' | Entry ₹' + f'{t.entry_premium:.1f}')
        # ── Candle-close SL sync: belt-and-suspenders on top of the 10s worker ──
        _sync_trailing_sl_to_exchange()   # no-op if worker already cleared the flag
        return

    # 3. Guard: no strategy evaluation outside NSE market hours.
    #    Outside 9:15–15:30 IST there is no live candle data — only yesterday's
    #    stale OHLCV. Strategies that look at timestamps (ORB, VWAP, etc.) will
    #    always see conditions as met (375 min since "open"), producing phantom
    #    signals that pollute the UI and confuse the user.
    if not _is_market_hours():
        state.last_block_reason  = "Market closed — evaluation paused until 9:15 AM"
        state.last_signal_reason = "⏸ Market closed"
        state.last_conditions    = []
        return

    # 4. No active trade — evaluate selected strategy
    #    For smart_router: capture full meta scores so UI can show scoreboard
    meta_result = None
    if state.selected_strategy == "smart_router":
        from strategy_meta_router import evaluate_all   # noqa: PLC0415
        meta_result = evaluate_all(df)
        signal = meta_result.signal
        state.last_meta_regime = meta_result.regime
        # Trim scores to what the UI needs (drop heavy signal objects)
        state.last_meta_scores = [
            {
                "id":          s["id"],
                "name":        s["name"],
                "emoji":       s["emoji"],
                "category":    s["category"],
                "confidence":  s["confidence"],
                "win_rate":    s.get("win_rate", 50.0),
                "regime_fit":  s["regime_fit"],
                "time_mult":   s["time_mult"],
                "composite":   s["composite"],
                "should_enter": s["should_enter"],
                "direction":   s["direction"].value if s["direction"] else None,
                "error":       s.get("error"),
            }
            for s in meta_result.scores
        ]
    else:
        strat_info = get_strategy(state.selected_strategy)
        signal = strat_info.evaluate(df) if strat_info else evaluate_vwap_breakout(df)
        state.last_meta_scores = []   # not applicable for single-strategy mode
        state.last_meta_regime = ""

    state.last_signal_reason = f"[{state.selected_strategy}] {signal.reason}"

    # For smart_router: show top strategy's conditions in UI even when no entry
    # so user sees exactly what passed/failed, not a blank panel
    cond_source = signal.conditions
    if meta_result is not None:
        top_conds = getattr(meta_result, "top_conditions", [])
        if top_conds:
            cond_source = top_conds

    state.last_conditions = [
        {"name": c.name, "met": c.met, "detail": c.detail}
        for c in cond_source
    ]
    met   = sum(1 for c in signal.conditions if c.met)
    total = len(signal.conditions)
    icon  = "🚦" if met == total and total > 0 else "🔍"
    _log(icon, f"Eval {met}/{total} conds", signal.reason[:120])

    # 5. Check safety before actually placing orders
    safe, safety_msg = _check_safety()
    if not safe:
        state.last_signal_reason = f"{signal.reason} | ⚠️ {safety_msg}"
        state.last_block_reason  = safety_msg
        _log("🚫", "Blocked", safety_msg)          # ← outcome visible in log
        return

    state.last_block_reason = None

    if not signal.should_enter or signal.direction is None:
        state.last_block_reason = None
        _log("⏸", "No entry", signal.reason[:150])  # ← outcome: conditions not fully met
        return

    # All conditions met — enter trade!
    dir_label = "LONG 📈" if signal.direction == Direction.LONG else "SHORT 📉"
    _log("✅", f"Entering {dir_label}", f"@ Nifty {current_price:.0f}")
    _enter_trade(signal.direction, current_price)


# LOT_SIZE: 65 units/lot as of the Apr-2025 SEBI contract-size revision.
# Override via LOT_SIZE env var if NSE revises it again.


def _estimate_premium_fallback(nifty_price: float) -> float:
    """Rough ATM premium estimate used ONLY when live LTP is unavailable.

    Formula: 0.35% of Nifty spot ≈ ATM option premium.
    OTM (1 strike away) is typically 0.20–0.25%, so this is conservative
    — you'll trade slightly fewer lots than possible. Safe to use in paper
    mode or when Kite quote API is down.
    """
    return round(nifty_price * 0.0035, 1)


def _resolve_quantity(nifty_price: float, real_premium: float | None = None) -> int:
    """Return qty (units) to trade based on qty_mode.

    manual  → return state.manual_qty directly
    capital → use real_premium if available (fetched via Kite quote just
               before entry), else fall back to 0.35% spot estimate.
               qty = floor(capital / (premium × LOT_SIZE)) × LOT_SIZE
    """
    if state.qty_mode == "manual":
        return state.manual_qty

    # Use real LTP when available — MUCH more accurate than formula
    if real_premium and real_premium > 0:
        premium = real_premium
        source  = f"live LTP ₹{premium:.1f}"
    else:
        premium = _estimate_premium_fallback(nifty_price)
        source  = f"estimated ₹{premium:.1f} (0.35% of spot — Kite unavailable)"

    cost_per_lot = premium * LOT_SIZE
    lots = max(1, int(state.capital / cost_per_lot))
    qty  = lots * LOT_SIZE
    print(f"📐 Capital mode: ₹{state.capital:,.0f} ÷ "
          f"(₹{cost_per_lot:.0f}/lot via {source}) = {lots} lots → {qty} units")
    return qty


def _enter_trade(direction: Direction, price: float):
    """Open a new trade."""
    try:
        symbol, opt_token = _get_option_symbol(price, direction)
    except RuntimeError as e:
        print(f"❌ Cannot enter trade: {e}")
        state.last_signal_reason = f"❌ Instrument lookup failed: {e}"
        return

    # ── Fetch real option LTP for accurate capital-mode lot sizing ──
    # We know the exact symbol now — get its actual live price.
    # Falls back to 0.35%-of-spot estimate if Kite is unavailable.
    real_ltp = kite_manager.get_option_ltp(symbol.replace("NFO:", ""))

    # ── Resolve trade settings from runtime state (overrideable from UI) ──
    sl_pts  = state.sl_points
    trail   = state.trailing_sl_points
    rr      = state.rr_ratio
    qty     = _resolve_quantity(price, real_premium=real_ltp)

    if direction == Direction.LONG:
        sl     = price - sl_pts
        target = price + sl_pts * rr
    else:
        sl     = price + sl_pts
        target = price - sl_pts * rr

    order_id = _place_order(symbol, direction, qty, price)
    if not order_id:
        return

    # Resolve entry_premium BEFORE building Trade (needed as a field)
    entry_premium = (
        real_ltp
        if isinstance(real_ltp, (int, float)) and real_ltp > 0
        else _estimate_premium_fallback(price)
    )

    trade = Trade(
        id=f"T-{datetime.now().strftime('%Y%m%d-%H%M%S%f')}",
        timestamp=datetime.now().isoformat(),
        direction=direction.value,
        instrument=symbol,
        entry_price=price,                  # Nifty spot — SL/target math
        entry_premium=entry_premium,        # Option LTP  — real P&L math
        quantity=qty,
        stop_loss=sl,
        target=target,
        status=OrderStatus.FILLED,
        order_id=order_id,
        paper=state.is_paper_mode,
    )

    # Reset ALL per-trade state BEFORE exposing the trade to the tick thread.
    # If we set active_trade first the tick handler can read stale values
    # from the previous trade and fire an instant exit.
    state.highest_price_since_entry    = price
    state.lowest_price_since_entry     = price
    state.entry_nifty_sl               = sl        # original SL for delta math
    state.pending_sl_exchange_update   = False
    state.cached_trail_sl              = None       # stale ATR/ST cache from prev trade
    # ── Critical: zero out the option LTP from the previous trade. ──────────
    # If we traded CE and now we're entering a PE (or vice versa), the old LTP
    # is for a completely different instrument. Leaving it set means the premium
    # SL check fires immediately using the wrong price and kills the new trade
    # before the first real tick arrives for the new instrument.
    state.last_option_ltp              = 0.0
    state.active_option_token          = opt_token   # store for WebSocket subscription
    state.active_trade = trade
    state.trades_today.append(trade)
    state.orders_placed += 1
    # Subscribe option to KiteTicker so LTP updates every ~1s via WebSocket
    # (instead of waiting for the 15s REST poll)
    _subscribe_option_tick(opt_token)

    mode = "📝 PAPER" if trade.paper else "🟢 LIVE"
    lots = max(1, qty // LOT_SIZE)
    print(f"🚀 [{mode}] ENTRY {direction.value.upper()} {qty}x {symbol} "
          f"@ ₹{price} | SL: ₹{sl:.0f} (−{sl_pts}pts) | Target: ₹{target:.0f} (R:R 1:{rr})")
    prem_sl  = _nifty_to_option_premium(sl, trade)
    prem_tgt = _nifty_to_option_premium(target, trade) if target else None
    tgt_part = (' | Target ₹' + f'{prem_tgt:.1f}') if prem_tgt else ''
    _log('\U0001f680', f'ENTRY {direction.value.upper()}',
         'Entry ₹' + f'{entry_premium:.1f} | SL ₹{prem_sl:.1f}' + tgt_part + f' | {lots}L ({qty}u) | Nifty ₹{price:.0f}')

    # ── Place SL-M backstop at exchange immediately after entry ───
    # Crash protection: if app dies, exchange still holds this order.
    # entry_premium is already resolved above (before Trade constructor).
    sl_trigger = _estimate_option_sl_trigger(state.sl_points, entry_premium)
    sl_order_id   = _place_sl_order(trade, sl_trigger)
    trade.sl_order_id        = sl_order_id
    state.entry_option_trigger = sl_trigger   # remember for trailing delta math

    print(f"🛡 Exchange SL-M backstop @ ₹{sl_trigger:.2f} (option) "
          f"| Nifty SL: ₹{sl:.0f} | order: {sl_order_id}")

    _save_trade_log()
    _save_state_snapshot()   # ← crash recovery: snapshot immediately on entry


def _update_trail_sl_cache(df) -> None:
    """Pre-compute trail SL for ATR/Supertrend modes — called once per candle.

    This keeps the tick thread fast — heavy indicator math runs here in the
    candle loop, result cached in state.cached_trail_sl for the tick thread.
    """
    if not state.active_trade:
        return
    import indicators as ind  # noqa: PLC0415
    trade   = state.active_trade
    is_long = trade.direction == "long"
    mode    = state.trail_mode

    if mode == "atr":
        atr_val   = float(ind.atr(df["high"], df["low"], df["close"]).iloc[-1])
        offset    = atr_val * state.trail_atr_mult
        entry_p   = trade.entry_price
        orig_sl   = state.entry_nifty_sl  # SL as placed at entry

        # Activation guard: activate the ATR trail as soon as the candidate
        # trailing SL is strictly BETTER than the original SL.
        #
        # Old guard: activated only after price moved ≥ offset in profit
        #   → For ATR where offset(38.6) > SL_dist(30), this means the trail
        #     never fires even when price moved 29pts and trail would be at
        #     23267 — already 22pts better than original SL 23289!  Bug!
        #
        # New guard: activate whenever trail SL improves on the original SL.
        #   LONG : trail_sl = highest - offset  → activate if > orig_sl
        #   SHORT: trail_sl = lowest  + offset  → activate if < orig_sl
        #
        # This correctly handles both cases:
        #   - offset < SL_dist (fixed-like): trail only helps once profitable
        #   - offset > SL_dist (wide ATR)  : trail helps earlier, never worse
        if is_long:
            candidate = state.highest_price_since_entry - offset
            activated = candidate > orig_sl   # trail beat original SL
            new_sl    = candidate if activated else None
        else:
            candidate = state.lowest_price_since_entry + offset
            activated = candidate < orig_sl   # trail beat original SL
            new_sl    = candidate if activated else None

        state.cached_trail_sl = round(new_sl, 2) if new_sl is not None else None
        status = f"SL={state.cached_trail_sl:.0f}" if activated else (
            f"waiting — trail={candidate:.0f} not better than orig SL={orig_sl:.0f} yet"
        )
        _log("📐", "ATR Trail",
             f"ATR={atr_val:.1f} × {state.trail_atr_mult} = {offset:.1f}pts  →  {status}")

    elif mode == "supertrend":
        st      = ind.supertrend(df["high"], df["low"], df["close"])
        st_val  = float(st["supertrend"].iloc[-1])
        entry_p = trade.entry_price
        # Activation guard: only use ST as the trail once the ST line itself
        # has crossed the entry price. Before that the trade hasn't moved
        # enough in profit and the ST line can sit tighter than the initial SL.
        # For LONG: ST line rises above entry = trade is solidly profitable.
        # For SHORT: ST line falls below entry = same.
        if is_long:
            activated = st_val > entry_p
        else:
            activated = st_val < entry_p
        state.cached_trail_sl = round(st_val, 2) if activated else None
        status = f"SL={state.cached_trail_sl:.0f}" if activated else f"waiting — ST={st_val:.0f} not past entry {entry_p:.0f} yet"
        _log("📈", "ST Trail", f"Supertrend line={st_val:.0f}  →  {status}")

    else:
        state.cached_trail_sl = None  # fixed/manual compute in _manage_active_trade


def _manage_active_trade(current_price: float, source: str = "🕯 candle"):
    """Manage stop-loss, trailing SL, and target for active trade."""
    trade = state.active_trade
    if not trade:
        return

    # ── Monitor-only mode: track P&L but never touch the position ─
    if not getattr(trade, "app_managed", True):
        # Still update price extremes so UI shows live data
        if trade.direction == "long":
            state.highest_price_since_entry = max(state.highest_price_since_entry, current_price)
        else:
            state.lowest_price_since_entry = min(state.lowest_price_since_entry, current_price)
        return   # ← no SL, no trail, no exit — you manage it yourself

    is_long = trade.direction == "long"

    # Update price extremes for trailing SL
    if is_long:
        state.highest_price_since_entry = max(
            state.highest_price_since_entry, current_price
        )
    else:
        state.lowest_price_since_entry = min(
            state.lowest_price_since_entry, current_price
        )

    # ── Trailing stop-loss (multi-mode) ─────────────────────────────
    # Tick-thread safe: ZERO heavy computation here.
    # ATR/Supertrend SL is pre-computed in _update_trail_sl_cache (candle loop)
    # and stored in state.cached_trail_sl.
    mode   = state.trail_mode
    new_sl = None

    if mode == "fixed":
        # Trailing SL must NOT tighten the initial stop-loss.
        # Only activate once price has moved at least `trailing_sl_points`
        # in our favour — at that point the first trail lands at breakeven.
        # Without this gate, the trail fires instantly at entry (peak = entry)
        # and silently cuts the user's intended 30-pt buffer to 15 pts.
        entry = trade.entry_price
        trail = state.trailing_sl_points
        if is_long:
            activated = state.highest_price_since_entry >= entry + trail
            new_sl = (state.highest_price_since_entry - trail) if activated else None
        else:
            activated = state.lowest_price_since_entry <= entry - trail
            new_sl = (state.lowest_price_since_entry + trail) if activated else None
    elif mode in ("atr", "supertrend"):
        # None means trail hasn't activated yet — original SL holds
        new_sl = state.cached_trail_sl   # pre-computed in candle loop (None = not active yet)
    elif mode == "manual":
        new_sl = None                    # user controls SL — never auto-trail

    def _apply_sl_move(new_sl_val: float) -> None:
        """Update SL in state + log. Inlined helper to avoid duplicate code."""
        trade.stop_loss = round(new_sl_val, 2)
        state.pending_sl_exchange_update = True
        _save_state_snapshot()
        prem_sl  = _nifty_to_option_premium(trade.stop_loss, trade)
        prem_tgt = _nifty_to_option_premium(trade.target, trade) if trade.target else None
        ltp_val  = state.last_option_ltp
        tgt_part = (' | Target ₹' + f'{prem_tgt:.1f}') if prem_tgt else ''
        ltp_part = (' | LTP ₹'    + f'{ltp_val:.1f}') if ltp_val else ''
        icon     = '🔼' if is_long else '🔽'
        label    = {'fixed': 'Fixed', 'atr': 'ATR', 'supertrend': 'ST'}.get(mode, mode)
        _log(icon, f'Trail [{label}] SL moved',
             'SL Prem ₹' + f'{prem_sl:.1f}' + tgt_part + ltp_part + ' (Nifty ₹' + f'{current_price:.0f})')

    if new_sl is not None:
        if is_long and new_sl > trade.stop_loss:
            _apply_sl_move(new_sl)
        elif not is_long and new_sl < trade.stop_loss:
            _apply_sl_move(new_sl)

    # Check stop-loss hit
    if is_long and current_price <= trade.stop_loss:
        _exit_position(f"{source} — SL hit ₹{trade.stop_loss} @ ₹{current_price:.0f}", current_price)
        return
    if not is_long and current_price >= trade.stop_loss:
        _exit_position(f"{source} — SL hit ₹{trade.stop_loss} @ ₹{current_price:.0f}", current_price)
        return

    # ── Secondary guard: option premium SL ────────────────────────
    # If the option LTP itself drops below the SL premium level,
    # exit immediately — don't wait for Nifty spot to cross SL level.
    # This catches: theta crush, vega collapse, illiquid gap fills.
    opt_ltp = state.last_option_ltp
    if opt_ltp and opt_ltp > 0 and trade.entry_premium > 0:
        prem_sl = _nifty_to_option_premium(trade.stop_loss, trade)
        if opt_ltp <= prem_sl:
            _exit_position(
                f"{source} — Premium SL ₹{prem_sl:.1f} hit (LTP ₹{opt_ltp:.1f})",
                current_price,
            )
            return

    # Check target hit
    if trade.target:
        if is_long and current_price >= trade.target:
            _exit_position(f"{source} — Target ₹{trade.target} @ ₹{current_price:.0f}", current_price)
            return
        if not is_long and current_price <= trade.target:
            _exit_position(f"{source} — Target ₹{trade.target} @ ₹{current_price:.0f}", current_price)
            return


# ── Tick-Level Guard (called on every WebSocket tick) ────────────

_tick_guard_lock = threading.Lock()

# ── Exchange SL sync worker ───────────────────────────────────────
# Trailing SL is updated IN MEMORY on every tick (zero API calls).
# This thread flushes the pending exchange SL-M modify within 10s of
# any trail move — far safer than waiting for the 5-min candle loop.

_SL_SYNC_INTERVAL = 10   # seconds between exchange SL-M syncs


def _sl_sync_worker() -> None:
    """Background daemon: pushes trailing SL to Zerodha within ~10 seconds.

    Runs forever as a daemon thread (dies when main process exits).
    Only fires kite.modify_order() when pending_sl_exchange_update=True
    so it costs zero API calls when the SL hasn't moved.
    """
    import time
    while True:
        time.sleep(_SL_SYNC_INTERVAL)
        try:
            if state.pending_sl_exchange_update and state.active_trade:
                _sync_trailing_sl_to_exchange()
        except Exception as e:
            print(f"⚠️  SL sync worker error: {e}")


_sl_sync_thread = threading.Thread(target=_sl_sync_worker, daemon=True, name="sl-sync")
_sl_sync_thread.start()


def _tick_guard_sl_only(tick: dict) -> None:
    """Minimal SL/time-exit guard used when auto-trader is paused.

    Called when is_running=False but active_trade exists (e.g. synced
    trade waiting for user to click Start, or mid-session pause).
    Trails SL are NOT moved here — only hard exits on breach or time.
    """
    price = tick.get("last_price")
    if not price or price <= 0:
        return
    acquired = _tick_guard_lock.acquire(blocking=False)
    if not acquired:
        return
    try:
        if not state.active_trade:
            return
        trade = state.active_trade
        now   = datetime.now()
        # Time-based exit
        if now.time() >= EXIT_TIME:
            _exit_position(f"⚡ Tick exit — time limit ({EXIT_TIME.strftime('%H:%M')})", price)
            return
        # Hard SL breach (no trail — just protect)
        sl = trade.stop_loss
        if trade.direction == "long"  and price <= sl:
            _exit_position(f"⚡ Tick SL hit (paused guard) @ ₹{price:.0f}", price)
        elif trade.direction == "short" and price >= sl:
            _exit_position(f"⚡ Tick SL hit (paused guard) @ ₹{price:.0f}", price)
    finally:
        _tick_guard_lock.release()


def tick_guard(tick: dict) -> None:
    """Real-time SL / target / trailing-SL protection on every Kite tick.

    Entry decisions stay on 5-min candles (need closed candle data).
    Exit decisions run here — every ~1s tick — so we never overshoot SL
    by a whole candle.

    NOTE: SL/target protection runs even when is_running=False, as long
    as there is an active_trade. This covers the 'synced but not started'
    state — your position is live in Zerodha so it must be protected.
    Only the kill_switch hard-stops everything.

    This runs in the KiteTicker background thread so we use a lock
    to avoid racing with the 5-min candle loop.
    """
    if state.kill_switch:
        return
    # Always update live Nifty price (used by status endpoint)
    price = tick.get("last_price")
    if price and price > 0:
        state.last_nifty_price = price
    # Guard: only protect if there's an open position
    if not state.active_trade:
        return
    # Guard: entry signals need is_running, but SL/exit protection does not
    # (if trader is stopped mid-trade, still honour SL to protect capital)
    if not state.is_running:
        # Only do time-exit and SL-breach — NOT trailing (trail needs is_running)
        _tick_guard_sl_only(tick)
        return
    if not state.active_trade:
        return   # nothing to protect

    # price was already read and validated above — re-read here for clarity
    price = tick.get("last_price")
    if not price or price <= 0:
        return

    # Non-blocking: if the 5-min loop is already inside _manage_active_trade
    # just skip this tick — next one will catch it.
    acquired = _tick_guard_lock.acquire(blocking=False)
    if not acquired:
        return
    try:
        # Re-check inside lock — 5-min loop may have just closed the trade
        if not state.active_trade:
            return
        now = datetime.now()
        # Time-based exit via tick (catches 15:15 to the second)
        if now.time() >= EXIT_TIME:
            _exit_position(f"⚡ Tick exit — time limit ({EXIT_TIME.strftime('%H:%M')})", price)
            return
        _manage_active_trade(price, source="⚡ tick")
    finally:
        _tick_guard_lock.release()


# ── Trade Log ─────────────────────────────────────────────────

def _save_trade_log():
    """Persist trade log to JSON file."""
    trades = []
    for t in state.trades_today:
        trades.append({
            "id": t.id, "timestamp": t.timestamp,
            "direction": t.direction, "instrument": t.instrument,
            "entry_price":   t.entry_price,    # Nifty spot at entry
            "entry_premium": t.entry_premium,  # Option LTP at entry
            "quantity": t.quantity,
            "stop_loss": t.stop_loss, "target": t.target,
            "exit_price":   t.exit_price,      # Nifty spot at exit
            "exit_premium": t.exit_premium,    # Option LTP at exit
            "exit_time": t.exit_time,
            "exit_reason": t.exit_reason, "pnl": t.pnl,
            "status": t.status, "order_id": t.order_id,
            "paper": t.paper,
        })
    _atomic_write(TRADE_LOG_FILE, json.dumps({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_pnl": round(state.total_pnl, 2),
        "orders_placed": state.orders_placed,
        "paper_mode": state.is_paper_mode,
        "trades": trades,
    }, indent=2))


# ── Public API ────────────────────────────────────────────────

def refresh_active_option_ltp() -> float | None:
    """Fetch and cache live option LTP + Nifty spot for the active trade.

    Safe to call at any time — even when auto-trader is not running.
    Used to keep unrealized P&L and Live Nifty fresh during recovery.
    Returns the fetched option LTP or None on failure.
    """
    if not state.active_trade:
        return None
    if not kite_manager.is_authenticated:
        return None
    try:
        # ── Option LTP ──────────────────────────────────────────────
        # Strip exchange prefix and any paper-trade date encoding
        raw = state.active_trade.instrument
        sym = raw.replace("NFO:", "").replace("NSE:", "")
        ltp = kite_manager.get_option_ltp(sym)
        if ltp and ltp > 0:
            state.last_option_ltp = ltp
            print(f"📊 LTP refresh {sym}: ₹{ltp:.2f}")

        # ── Nifty spot — always refresh, regardless of option result ─
        try:
            nifty_resp = kite_manager.kite.ltp(["NSE:NIFTY 50"])
            spot = float(nifty_resp["NSE:NIFTY 50"]["last_price"])
            if spot > 0:
                state.last_nifty_price = spot
        except Exception:
            pass

        return ltp if (ltp and ltp > 0) else None
    except Exception as e:
        print(f"⚠️  LTP refresh failed: {e}")
    return None


def get_trader_status() -> dict:
    """Get current auto-trader state for dashboard."""
    active = state.active_trade
    return {
        "is_running": state.is_running,
        "is_paper_mode": state.is_paper_mode,
        "kill_switch": state.kill_switch,
        "active_trade": {
            "id":              active.id,
            "direction":       active.direction,
            "instrument":      active.instrument,
            # ── Nifty spot levels (internal math only) ───────────
            "entry_price":     active.entry_price,
            "stop_loss":       active.stop_loss,
            "target":          active.target,
            # ── Option premium levels (what trader sees) ─────────
            "entry_premium":         active.entry_premium,
            "option_sl_premium":     _nifty_to_option_premium(active.stop_loss, active),
            "option_target_premium": _nifty_to_option_premium(active.target, active)
                                     if active.target else None,
            # ─────────────────────────────────────────────────────
            "exchange_sl_pending": state.pending_sl_exchange_update,
            "quantity":        active.quantity,
            "paper":           active.paper,
            "app_managed":     getattr(active, "app_managed", True),
            "pnl_unrealized":  0,   # enriched by app.py caller with live LTP
        } if active else None,
        "total_pnl": round(state.total_pnl, 2),
        "orders_placed": state.orders_placed,
        "max_orders": MAX_ORDERS_PER_DAY,
        "max_loss": MAX_LOSS_PER_DAY,
        "trades_today": len(state.trades_today),
        "last_evaluation": state.last_evaluation,
        "last_signal": state.last_signal_reason,
        "conditions": state.last_conditions,
        "meta_scores": state.last_meta_scores,
        "meta_regime": state.last_meta_regime,
        "exit_time": EXIT_TIME.strftime("%H:%M"),
        "sl_points": SL_POINTS,
        "trailing_sl_points": TRAILING_SL_POINTS,
        "selected_strategy": state.selected_strategy,
        "block_reason":     state.last_block_reason,
        "cooldown_minutes":  state.cooldown_minutes,
        "cooldown_remaining": (
            max(0, round(state.cooldown_minutes - (datetime.now() - state.last_exit_time).total_seconds() / 60, 1))
            if state.last_exit_time else 0
        ),
        "recovery_mode":    state.recovery_mode,
        "recovery_type":    state.recovery_type,
        "recovery_message": state.recovery_message,
        # ── Runtime trade settings ──
        "sl_points":          state.sl_points,
        "trailing_sl_points": state.trailing_sl_points,
        "trail_mode":         state.trail_mode,
        "trail_atr_mult":     state.trail_atr_mult,
        "rr_ratio":           state.rr_ratio,
        "qty_mode":           state.qty_mode,
        "manual_qty":         state.manual_qty,
        "capital":            state.capital,
        "strike_offset":      state.strike_offset,
        "max_trades_per_day": state.max_trades_per_day,
        # ── Server-side event log (last 40 events, newest first) ──
        "event_log": list(reversed(list(_event_log)))[:40],
    }


def configure_auto_trader(
    sl_points:          float | None = None,
    trailing_sl_points: float | None = None,
    trail_mode:         str   | None = None,   # "fixed"|"atr"|"supertrend"|"manual"
    trail_atr_mult:     float | None = None,   # 1.0 – 3.0
    rr_ratio:           float | None = None,
    qty_mode:           str   | None = None,
    manual_qty:         int   | None = None,
    capital:            float | None = None,
    strike_offset:      int   | None = None,
    max_trades_per_day: int   | None = None,
    cooldown_minutes:   int   | None = None,
) -> dict:
    """Update runtime trade settings without restarting."""
    if sl_points          is not None: state.sl_points          = sl_points
    if trailing_sl_points is not None: state.trailing_sl_points = trailing_sl_points
    if trail_mode         is not None: state.trail_mode         = trail_mode
    if trail_atr_mult     is not None: state.trail_atr_mult     = max(0.5, min(4.0, trail_atr_mult))
    if rr_ratio           is not None: state.rr_ratio           = rr_ratio
    if qty_mode           is not None: state.qty_mode           = qty_mode
    if manual_qty         is not None: state.manual_qty         = manual_qty
    if capital            is not None: state.capital            = capital
    if strike_offset      is not None: state.strike_offset      = max(-3, min(3, strike_offset))
    if max_trades_per_day is not None: state.max_trades_per_day = max(1, min(50, max_trades_per_day))
    if cooldown_minutes   is not None: state.cooldown_minutes   = max(0, min(60, cooldown_minutes))
    _save_state_snapshot()
    return {
        "sl_points":          state.sl_points,
        "trailing_sl_points": state.trailing_sl_points,
        "trail_mode":         state.trail_mode,
        "trail_atr_mult":     state.trail_atr_mult,
        "rr_ratio":           state.rr_ratio,
        "qty_mode":           state.qty_mode,
        "manual_qty":         state.manual_qty,
        "capital":            state.capital,
        "strike_offset":      state.strike_offset,
        "max_trades_per_day": state.max_trades_per_day,
        "cooldown_minutes":   state.cooldown_minutes,
    }


def start_auto_trader(strategy_id: str | None = None):
    """Start the auto-trader loop.

    If live mode and no active trade in state, automatically attempts a
    Zerodha position sync — so clicking START mid-session after a crash
    or false exit immediately re-links any open position without needing
    a separate manual Sync step.
    """
    if state.is_running:
        return {"status": "already_running"}

    if strategy_id:
        state.selected_strategy = strategy_id

    state.is_running  = True
    state.kill_switch = False
    mode       = "📝 PAPER" if state.is_paper_mode else "🟢 LIVE"
    strat_name = state.selected_strategy
    print(f"\n🚀 Auto-Trader STARTED [{mode} MODE]")
    print(f"   Strategy: {strat_name}")
    print(f"   Max loss: ₹{MAX_LOSS_PER_DAY} | Max orders: {MAX_ORDERS_PER_DAY}")
    print(f"   SL: {state.sl_points}pts | Trail: {state.trailing_sl_points}pts | R:R 1:{state.rr_ratio}")
    print(f"   Auto-exit: {EXIT_TIME.strftime('%H:%M')} IST\n")

    return {"status": "started", "mode": mode, "strategy": strat_name}


def stop_auto_trader():
    """Stop the auto-trader and exit any active position."""
    state.is_running = False
    if state.active_trade:
        try:
            tick = kite_manager.latest_tick
            price = tick["last_price"] if tick and isinstance(tick, dict) else state.active_trade.entry_price
        except Exception:
            price = state.active_trade.entry_price
        _exit_position("Manual stop — auto-trader stopped", price)

    print("🛑 Auto-Trader STOPPED")
    return {"status": "stopped"}


def sync_from_zerodha() -> dict:
    """Scan Zerodha positions and import any open NFO option into app state.

    Use when the app was restarted with an open trade that isn't in the snapshot.
    Returns a summary dict with success / imported trade details.

    Past-exit-time behaviour:
      If it's already past EXIT_TIME (3:15 PM), the trade is imported in
      MONITOR-ONLY mode (app_managed=False) so the app tracks P&L but does
      NOT auto-exit again. This lets you see the position and exit manually.
    """
    if not kite_manager.is_authenticated:
        return {"success": False, "error": "Not authenticated with Zerodha"}
    if state.active_trade:
        return {"success": False, "error": "App already has an active trade. Exit it first."}

    # ── Past-exit-time flag — monitor only, don't auto-manage ────
    now = datetime.now()
    past_exit_time = now.time() >= EXIT_TIME
    if past_exit_time:
        _log("⚠️", "Sync past exit time",
             f"It's {now.strftime('%H:%M')} — past {EXIT_TIME.strftime('%H:%M')}. "
             f"Importing in MONITOR ONLY mode (no auto-exit, no SL management). "
             f"Exit the position manually from Zerodha or use Manual Exit.")

    # ── Fetch positions ───────────────────────────────────────────
    try:
        positions = kite_manager.kite.positions()
    except Exception as e:
        return {"success": False, "error": f"Could not fetch positions: {e}"}

    nfo_positions = [
        p for p in positions.get("net", [])
        if p.get("exchange") == "NFO"
        and p.get("tradingsymbol", "").startswith("NIFTY")   # NIFTY only — excludes BANKNIFTY, FINNIFTY, MIDCPNIFTY
        and int(p.get("quantity", 0)) != 0
    ]
    if not nfo_positions:
        return {"success": False, "error": "No open Nifty NFO positions found in Zerodha"}

    # ── Consolidate all open Nifty NFO positions ──────────────────
    # Group by direction: CE positions = LONG, PE positions = SHORT.
    # If user added to a position (same or different strikes same direction),
    # sum the quantities and compute weighted-average premium.
    ce_positions = [p for p in nfo_positions if p["tradingsymbol"].endswith("CE")]
    pe_positions = [p for p in nfo_positions if p["tradingsymbol"].endswith("PE")]

    # Pick dominant direction (more total qty wins; CE wins tie)
    def _total_qty(lst): return sum(abs(int(p["quantity"])) for p in lst)
    ce_qty = _total_qty(ce_positions)
    pe_qty = _total_qty(pe_positions)

    if ce_qty == 0 and pe_qty == 0:
        return {"success": False, "error": "No open Nifty NFO positions found in Zerodha"}

    if ce_qty > 0 and pe_qty > 0:
        # Both sides open — warn and pick larger
        _log("⚠️", "Sync", f"Both CE ({ce_qty}u) and PE ({pe_qty}u) open — picking larger side")

    chosen_positions = ce_positions if ce_qty >= pe_qty else pe_positions
    direction_val    = "long"  if ce_qty >= pe_qty else "short"

    # Weighted-average premium across all chosen positions
    total_qty = _total_qty(chosen_positions)
    weighted_premium = (
        sum(
            abs(int(p["quantity"])) * float(p.get("average_price") or p.get("buy_price") or 0)
            for p in chosen_positions
        ) / total_qty
        if total_qty > 0 else 0.0
    )

    # Use the largest-qty position as the primary instrument
    pos       = max(chosen_positions, key=lambda p: abs(int(p["quantity"])))
    sym       = pos["tradingsymbol"]
    qty       = total_qty   # ← TOTAL qty across all added positions
    avg_price = round(weighted_premium, 2)   # ← weighted avg premium
    opt_ltp   = float(pos.get("last_price") or avg_price)

    _log("🔗", "Sync positions found",
         f"{len(chosen_positions)} position(s) | total qty={qty} | wtd avg prem=₹{avg_price:.2f}"
         + (f" | symbols: {[p['tradingsymbol'] for p in chosen_positions]}" if len(chosen_positions) > 1 else ""))

    # ── Fetch live Nifty spot from Kite ───────────────────────────
    # Priority: Kite LTP → last known tick → 0 (show warning)
    nifty_spot = state.last_nifty_price or 0
    try:
        ltp_resp  = kite_manager.kite.ltp(["NSE:NIFTY 50"])
        nifty_spot = float(ltp_resp["NSE:NIFTY 50"]["last_price"])
        state.last_nifty_price = nifty_spot
    except Exception:
        pass   # use whatever we had

    if nifty_spot <= 0:
        return {
            "success": False,
            "error":   "Could not fetch live Nifty price — wait a moment for the first tick then try again.",
        }

    # ── Build SL and Target as price LEVELS, not point deltas ─────
    sl_pts  = state.sl_points
    tgt_pts = sl_pts * state.rr_ratio
    if direction_val == "short":
        sl_level  = round(nifty_spot + sl_pts, 2)    # SL above current for SHORT
        tgt_level = round(nifty_spot - tgt_pts, 2)   # Target below current for SHORT
    else:
        sl_level  = round(nifty_spot - sl_pts, 2)    # SL below current for LONG
        tgt_level = round(nifty_spot + tgt_pts, 2)   # Target above current for LONG

    trade = Trade(
        id            = f"sync-{datetime.now().strftime('%H%M%S')}",
        timestamp     = datetime.now().isoformat(),
        direction     = direction_val,
        instrument    = f"NFO:{sym}",
        entry_price   = nifty_spot,         # current Nifty spot (best proxy for "where we are")
        entry_premium = avg_price,          # what was actually paid for the option
        quantity      = qty,
        stop_loss     = sl_level,
        target        = tgt_level,
        status        = OrderStatus.FILLED,
        paper         = False,
        # Past exit-time → app tracks P&L only, does NOT auto-manage or auto-exit.
        # You must exit manually from Zerodha or the Manual Exit button.
        app_managed   = not past_exit_time,
    )
    state.active_trade              = trade
    state.last_option_ltp           = opt_ltp
    state.highest_price_since_entry = nifty_spot
    state.lowest_price_since_entry  = nifty_spot
    state.entry_nifty_sl            = sl_level
    state.entry_option_trigger      = _estimate_option_sl_trigger(sl_pts, avg_price)
    state.pending_sl_exchange_update = False
    state.orders_placed            += 1
    _save_state_snapshot()   # ← persist immediately so a restart restores this trade
    all_syms = [p["tradingsymbol"] for p in chosen_positions]
    _log("🔗", "Synced from Zerodha",
         f"{all_syms} | {qty}u | avg ₹{avg_price:.2f} | Nifty ₹{nifty_spot:.0f} | SL ₹{sl_level:.0f} | Tgt ₹{tgt_level:.0f}")
    return {
        "success":          True,
        "instrument":       sym,
        "all_instruments":  all_syms,
        "positions_merged": len(chosen_positions),
        "direction":        direction_val,
        "quantity":         qty,
        "avg_price":        avg_price,
        "nifty_spot":       nifty_spot,
        "sl_level":         sl_level,
        "tgt_level":        tgt_level,
        "note":             (
            f"Merged {len(chosen_positions)} position(s) — "
            "SL/Target set from current Nifty spot + your SL settings — trailing SL active"
        ),
        "past_exit_time":   past_exit_time,
        "warning":          (
            f"⚠️ Imported past exit time ({EXIT_TIME.strftime('%H:%M')}). "
            "App is in MONITOR ONLY mode — exit manually from Zerodha or Manual Exit button."
        ) if past_exit_time else None,
    }


def set_trade_managed(managed: bool) -> dict:
    """Toggle app management of the active trade.

    managed=True  → app handles SL, trailing SL, target exit, time exit.
    managed=False → app tracks price & P&L only — you manage the trade yourself.
    """
    trade = state.active_trade
    if not trade:
        return {"success": False, "error": "No active trade to configure"}
    trade.app_managed = managed
    _save_state_snapshot()
    mode = "APP MANAGED" if managed else "MONITOR ONLY"
    _log("🎛", "Trade mode", f"{mode} — {'SL/trail/exit active' if managed else 'app will NOT touch position'}")
    return {"success": True, "app_managed": managed, "mode": mode}


def discard_trade_from_app() -> dict:
    """Remove active trade from app state only — zero Zerodha API calls."""
    trade = state.active_trade
    if not trade:
        return {"success": False, "error": "No active trade in app state"}
    instr = trade.instrument
    state.active_trade     = None
    state.exit_in_progress = False
    _log("🗑", "Trade removed", f"{instr} cleared from app — no order sent to Zerodha")
    _save_state_snapshot()
    return {"success": True, "discarded": instr}


def activate_kill_switch():
    """Emergency stop — exit all positions, block new trades."""
    state.kill_switch = True
    state.is_running = False
    if state.active_trade:
        try:
            tick = kite_manager.latest_tick
            price = tick["last_price"] if tick and isinstance(tick, dict) else state.active_trade.entry_price
        except Exception:
            price = state.active_trade.entry_price
        _exit_position("🚨 KILL SWITCH — emergency exit", price)

    print("🚨 KILL SWITCH ACTIVATED — all trading halted!")
    return {"status": "killed"}

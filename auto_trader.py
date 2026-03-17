"""Auto-Trader Engine — Automated order placement & management via Zerodha.

⚠️  PAPER TRADING MODE IS ON BY DEFAULT.
Set LIVE_TRADING=true in .env to enable real orders.

Features:
- Evaluates strategy conditions on each tick/candle
- Places orders via Kite Connect API
- Manages stop-loss (fixed + trailing)
- Auto-exits by defined time (default 3:15 PM)
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
MAX_ORDERS_PER_DAY = int(os.getenv("MAX_ORDERS_PER_DAY", "6"))
DEFAULT_QUANTITY   = int(os.getenv("DEFAULT_QUANTITY",   "780"))   # 12 lots × 65 units
SL_POINTS          = float(os.getenv("SL_POINTS",          "30"))   # Fixed SL in points
TRAILING_SL_POINTS = float(os.getenv("TRAILING_SL_POINTS", "15"))   # Trail by 15pts
DEFAULT_CAPITAL    = float(os.getenv("TRADING_CAPITAL",  "96000"))  # ₹ available
EXIT_TIME = dt_time(15, 15)  # 3:15 PM IST — auto-exit all positions
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
    last_evaluation: str = ""
    last_signal_reason: str = ""
    last_conditions: list[dict] = field(default_factory=list)
    kill_switch: bool = False
    last_option_ltp: float = 0.0           # live option LTP — refreshed each candle loop
    last_nifty_price: float = 0.0          # live Nifty price — refreshed each tick
    selected_strategy: str = "smart_router"
    last_block_reason: str | None = None
    # ── Runtime-configurable trade settings (overrideable from UI) ──
    sl_points:          float = SL_POINTS           # Nifty SL in points
    trailing_sl_points: float = TRAILING_SL_POINTS  # trailing SL step
    rr_ratio:           float = 2.0                 # risk:reward
    capital:            float = DEFAULT_CAPITAL      # ₹ available for qty calc
    qty_mode:           str   = "manual"            # 'manual' | 'capital'
    manual_qty:         int   = DEFAULT_QUANTITY    # used when qty_mode=manual
    strike_offset:      int   = 0                   # -3=ITM3,-2=ITM2,-1=ITM1,0=ATM,1=OTM1,2=OTM2,3=OTM3
    max_trades_per_day: int   = MAX_ORDERS_PER_DAY  # runtime-overridable (1-15)
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
        "selected_strategy": state.selected_strategy,
        # ── Runtime trade settings (survive restart) ──
        "sl_points":          state.sl_points,
        "trailing_sl_points": state.trailing_sl_points,
        "rr_ratio":           state.rr_ratio,
        "qty_mode":           state.qty_mode,
        "manual_qty":         state.manual_qty,
        "capital":            state.capital,
        "strike_offset":      state.strike_offset,
        "max_trades_per_day": state.max_trades_per_day,
        # active_trade stored with full detail (includes sl_order_id for crash cancel)
        "active_trade": _trade_to_dict(active) if active else None,
        # Only completed trades — avoids double-counting on recovery
        "trades_today": [_trade_to_dict(t) for t in completed_trades],
    }
    _atomic_write(STATE_SNAPSHOT_FILE, json.dumps(snapshot, indent=2))


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
    state.orders_placed     = snap.get("orders_placed", 0)
    state.is_paper_mode     = not LIVE_TRADING  # always from env — never let snapshot override this
    state.selected_strategy = snap.get("selected_strategy", "smart_router")

    # ── Restore runtime trade settings ───────────────────────
    state.sl_points          = snap.get("sl_points",          SL_POINTS)
    state.trailing_sl_points = snap.get("trailing_sl_points", TRAILING_SL_POINTS)
    state.rr_ratio           = snap.get("rr_ratio",           2.0)
    state.qty_mode           = snap.get("qty_mode",           "manual")
    state.manual_qty         = snap.get("manual_qty",         DEFAULT_QUANTITY)
    state.capital            = snap.get("capital",            DEFAULT_CAPITAL)
    state.strike_offset      = snap.get("strike_offset",      0)   # default ATM
    state.max_trades_per_day = snap.get("max_trades_per_day", MAX_ORDERS_PER_DAY)

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
            status=OrderStatus.FILLED,
        )
        state.active_trade = recovered_trade
        # Append only if not already in trades_today (guards against old snapshots
        # that included the active trade in the trades_today list)
        existing_ids = {t.id for t in state.trades_today}
        if recovered_trade.id not in existing_ids:
            state.trades_today.append(recovered_trade)
        state.highest_price_since_entry = at["entry_price"]
        state.lowest_price_since_entry  = at["entry_price"]
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
    market_open = dt_time(9, 15)
    if now < dt_time(9, 18):
        return False, "Too early — waiting for market to settle (first 3 min)"

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
    """Return nearest weekly expiry date.

    NSE changed Nifty 50 weekly option expiry from Thursday → Tuesday
    effective October 2024 (SEBI circular on expiry-day rationalisation).
    weekday(): Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6
    """
    from datetime import timedelta
    EXPIRY_WEEKDAY = 1   # Tuesday (Nifty 50 weekly expiry as of Oct 2024)
    today = datetime.now()
    days_until_expiry = (EXPIRY_WEEKDAY - today.weekday()) % 7
    if days_until_expiry == 0 and today.hour >= 15:
        # Past 3 PM on expiry day → roll to next week
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
            # Option buyer strategy: we always BUY at entry (CE or PE).
            # Exit closes the long option position → always SELL.
            kite_manager.kite.place_order(
                variety=kite_manager.kite.VARIETY_REGULAR,
                exchange="NFO",
                tradingsymbol=sym_clean,
                transaction_type=kite_manager.kite.TRANSACTION_TYPE_SELL,
                quantity=trade.quantity,       # ← same qty as entry, always
                product=kite_manager.kite.PRODUCT_MIS,
                order_type=kite_manager.kite.ORDER_TYPE_MARKET,
                validity="DAY",
            )
        except Exception as e:
            print(f"❌ Exit order failed: {e}")

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

    state.total_pnl += pnl
    state.active_trade = None

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
        with _tick_guard_lock:
            if state.active_trade:   # re-check: tick may have just closed it
                _manage_active_trade(current_price, source="🕯 candle")
        # ── Refresh live option LTP for unrealized P&L display ────
        if state.active_trade:
            sym = state.active_trade.instrument.replace("NFO:", "")
            ltp = kite_manager.get_option_ltp(sym)
            if ltp and ltp > 0:
                state.last_option_ltp = ltp
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
    strat_info = get_strategy(state.selected_strategy)
    if strat_info:
        signal = strat_info.evaluate(df)
    else:
        signal = evaluate_vwap_breakout(df)  # fallback

    state.last_signal_reason = f"[{state.selected_strategy}] {signal.reason}"
    state.last_conditions = [
        {"name": c.name, "met": c.met, "detail": c.detail}
        for c in signal.conditions
    ]
    met = sum(1 for c in signal.conditions if c.met)
    total = len(signal.conditions)
    icon = "🚦" if met == total and total > 0 else "🔍"
    _log(icon, f"Eval {met}/{total} conds", signal.reason[:80])

    # 5. Check safety before actually placing orders
    safe, safety_msg = _check_safety()
    if not safe:
        state.last_signal_reason = f"{signal.reason} | ⚠️ {safety_msg}"
        state.last_block_reason  = safety_msg   # ← UI uses this for no-pos message
        return

    state.last_block_reason = None   # clear any previous block

    if not signal.should_enter or signal.direction is None:
        state.last_block_reason = None
        return

    # All conditions met — enter trade!
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
        symbol, _token = _get_option_symbol(price, direction)
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

    state.active_trade = trade
    state.trades_today.append(trade)
    state.orders_placed += 1
    state.highest_price_since_entry    = price
    state.lowest_price_since_entry     = price
    state.entry_nifty_sl               = sl        # original SL for delta math
    state.pending_sl_exchange_update   = False

    mode = "📝 PAPER" if trade.paper else "🟢 LIVE"
    lots = max(1, qty // 65)
    print(f"🚀 [{mode}] ENTRY {direction.value.upper()} {qty}x {symbol} "
          f"@ ₹{price} | SL: ₹{sl:.0f} (−{sl_pts}pts) | Target: ₹{target:.0f} (R:R 1:{rr})")
    _log("🚀", f"ENTRY {direction.value.upper()}",
         f"Nifty ₹{price:.0f} | SL ₹{sl:.0f} | Tgt ₹{target:.0f} | {lots}L ({qty}u) | Prem ₹{entry_premium:.2f}")

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


def _manage_active_trade(current_price: float, source: str = "🕯 candle"):
    """Manage stop-loss, trailing SL, and target for active trade."""
    trade = state.active_trade
    if not trade:
        return

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

    # Trailing stop-loss — in-app only (ZERO API calls here — tick thread safe)
    # Flag pending_sl_exchange_update so the next 5-min candle loop syncs Zerodha.
    if is_long:
        new_sl = state.highest_price_since_entry - state.trailing_sl_points
        if new_sl > trade.stop_loss:
            trade.stop_loss = round(new_sl, 2)
            state.pending_sl_exchange_update = True
            _save_state_snapshot()
            _log("🔼", "Trail SL moved UP", f"New SL ₹{trade.stop_loss:.0f} | Nifty ₹{current_price:.0f}")
    else:
        new_sl = state.lowest_price_since_entry + state.trailing_sl_points
        if new_sl < trade.stop_loss:
            trade.stop_loss = round(new_sl, 2)
            state.pending_sl_exchange_update = True
            _save_state_snapshot()
            _log("🔽", "Trail SL moved DOWN", f"New SL ₹{trade.stop_loss:.0f} | Nifty ₹{current_price:.0f}")

    # Check stop-loss hit
    if is_long and current_price <= trade.stop_loss:
        _exit_position(f"{source} — SL hit ₹{trade.stop_loss} @ ₹{current_price:.0f}", current_price)
        return
    if not is_long and current_price >= trade.stop_loss:
        _exit_position(f"{source} — SL hit ₹{trade.stop_loss} @ ₹{current_price:.0f}", current_price)
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
            "entry_price":     active.entry_price,
            "entry_premium":   active.entry_premium,   # ← was missing! needed for P&L + display
            "stop_loss":       active.stop_loss,
            "exchange_sl_pending": state.pending_sl_exchange_update,
            "target":          active.target,
            "quantity":        active.quantity,
            "paper":           active.paper,
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
        "exit_time": EXIT_TIME.strftime("%H:%M"),
        "sl_points": SL_POINTS,
        "trailing_sl_points": TRAILING_SL_POINTS,
        "selected_strategy": state.selected_strategy,
        "block_reason":     state.last_block_reason,
        "recovery_mode":    state.recovery_mode,
        "recovery_type":    state.recovery_type,
        "recovery_message": state.recovery_message,
        # ── Runtime trade settings ──
        "sl_points":          state.sl_points,
        "trailing_sl_points": state.trailing_sl_points,
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
    rr_ratio:           float | None = None,
    qty_mode:           str   | None = None,
    manual_qty:         int   | None = None,
    capital:            float | None = None,
    strike_offset:      int   | None = None,   # 0=ATM, 1=1-OTM, 2=2-OTM
    max_trades_per_day: int   | None = None,   # 1-15
) -> dict:
    """Update runtime trade settings without restarting."""
    if sl_points          is not None: state.sl_points          = sl_points
    if trailing_sl_points is not None: state.trailing_sl_points = trailing_sl_points
    if rr_ratio           is not None: state.rr_ratio           = rr_ratio
    if qty_mode           is not None: state.qty_mode           = qty_mode
    if manual_qty         is not None: state.manual_qty         = manual_qty
    if capital            is not None: state.capital            = capital
    if strike_offset      is not None: state.strike_offset      = max(-3, min(3, strike_offset))
    if max_trades_per_day is not None: state.max_trades_per_day = max(1, min(15, max_trades_per_day))
    _save_state_snapshot()
    return {
        "sl_points":          state.sl_points,
        "trailing_sl_points": state.trailing_sl_points,
        "rr_ratio":           state.rr_ratio,
        "qty_mode":           state.qty_mode,
        "manual_qty":         state.manual_qty,
        "capital":            state.capital,
        "strike_offset":      state.strike_offset,
        "max_trades_per_day": state.max_trades_per_day,
    }


def start_auto_trader(strategy_id: str | None = None):
    """Start the auto-trader loop."""
    if state.is_running:
        return {"status": "already_running"}

    if strategy_id:
        state.selected_strategy = strategy_id

    state.is_running = True
    state.kill_switch = False
    mode = "📝 PAPER" if state.is_paper_mode else "🟢 LIVE"
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
    """
    if not kite_manager.is_authenticated:
        return {"success": False, "error": "Not authenticated with Zerodha"}
    if state.active_trade:
        return {"success": False, "error": "App already has an active trade. Exit it first."}

    # ── Fetch positions ───────────────────────────────────────────
    try:
        positions = kite_manager.kite.positions()
    except Exception as e:
        return {"success": False, "error": f"Could not fetch positions: {e}"}

    nfo_positions = [
        p for p in positions.get("net", [])
        if p.get("exchange") == "NFO"
        and "NIFTY" in p.get("tradingsymbol", "")
        and int(p.get("quantity", 0)) != 0
    ]
    if not nfo_positions:
        return {"success": False, "error": "No open Nifty NFO positions found in Zerodha"}

    pos = nfo_positions[0]
    sym       = pos["tradingsymbol"]
    qty       = abs(int(pos["quantity"]))
    avg_price = float(pos.get("average_price") or pos.get("buy_price") or 0)
    opt_ltp   = float(pos.get("last_price") or avg_price)   # current option LTP from position

    # CE = bought call = directional LONG on Nifty; PE = bought put = directional SHORT
    direction_val = "long" if sym.endswith("CE") else "short"

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
    )
    state.active_trade              = trade
    state.last_option_ltp           = opt_ltp
    state.highest_price_since_entry = nifty_spot
    state.lowest_price_since_entry  = nifty_spot
    state.entry_nifty_sl            = sl_level
    state.pending_sl_exchange_update = False
    state.orders_placed            += 1
    _save_state_snapshot()
    _log("🔗", "Synced from Zerodha",
         f"{sym} | {qty}u | avg ₹{avg_price:.2f} | Nifty ₹{nifty_spot:.0f} | SL ₹{sl_level:.0f} | Tgt ₹{tgt_level:.0f}")
    return {
        "success":    True,
        "instrument": sym,
        "direction":  direction_val,
        "quantity":   qty,
        "avg_price":  avg_price,
        "nifty_spot": nifty_spot,
        "sl_level":   sl_level,
        "tgt_level":  tgt_level,
        "note":       "SL/Target set from current Nifty spot + your SL settings — trailing SL active",
    }


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

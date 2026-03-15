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
    direction: str  # 'long' or 'short'
    instrument: str
    entry_price: float
    quantity: int
    stop_loss: float
    target: float | None = None
    exit_price: float | None = None
    exit_time: str | None = None
    exit_reason: str | None = None
    pnl: float = 0.0
    status: str = OrderStatus.PENDING
    order_id: str | None = None     # Zerodha entry order ID
    sl_order_id: str | None = None  # Zerodha SL-M order ID (exchange-level guard)
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
    selected_strategy: str = "smart_router"
    last_block_reason: str | None = None
    # ── Runtime-configurable trade settings (overrideable from UI) ──
    sl_points:          float = SL_POINTS           # Nifty SL in points
    trailing_sl_points: float = TRAILING_SL_POINTS  # trailing SL step
    rr_ratio:           float = 2.0                 # risk:reward
    capital:            float = DEFAULT_CAPITAL      # ₹ available for qty calc
    qty_mode:           str   = "manual"            # 'manual' | 'capital'
    manual_qty:         int   = DEFAULT_QUANTITY    # used when qty_mode=manual
    recovery_mode: bool = False    # True if state was restored after a crash
    recovery_message: str = ""     # Human-readable description of what was recovered
    recovery_type: str = ""        # 'open' = trade still live | 'closed' = already exited | 'clean' = no trade


# ── Singleton State ─────────────────────────────────────────────
state = TraderState()


# ── State Snapshot (crash recovery) ─────────────────────────────

def _save_state_snapshot():
    """Write a full state snapshot to disk after every significant change.

    Called on: entry, exit, trailing SL update.
    On restart, `_recover_state()` reads this to rebuild state.
    """
    active = state.active_trade
    snapshot = {
        "date":            datetime.now().strftime("%Y-%m-%d"),
        "total_pnl":       state.total_pnl,
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
        "active_trade":    {
            "id":          active.id,
            "timestamp":   active.timestamp,
            "direction":   active.direction,
            "instrument":  active.instrument,
            "entry_price": active.entry_price,
            "quantity":    active.quantity,
            "stop_loss":   active.stop_loss,
            "target":      active.target,
            "order_id":    active.order_id,
            "sl_order_id": active.sl_order_id,
            "paper":       active.paper,
            "status":      active.status,
        } if active else None,
        "trades_today": [
            {
                "id":          t.id,
                "timestamp":   t.timestamp,
                "direction":   t.direction,
                "instrument":  t.instrument,
                "entry_price": t.entry_price,
                "quantity":    t.quantity,
                "stop_loss":   t.stop_loss,
                "target":      t.target,
                "exit_price":  t.exit_price,
                "exit_time":   t.exit_time,
                "exit_reason": t.exit_reason,
                "pnl":         t.pnl,
                "status":      t.status,
                "order_id":    t.order_id,
                "paper":       t.paper,
            }
            for t in state.trades_today
        ],
    }
    STATE_SNAPSHOT_FILE.write_text(json.dumps(snapshot, indent=2))


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
        snap = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError):
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
    state.is_paper_mode     = snap.get("is_paper_mode", not LIVE_TRADING)
    state.selected_strategy = snap.get("selected_strategy", "smart_router")

    # ── Restore runtime trade settings ───────────────────────
    state.sl_points          = snap.get("sl_points",          SL_POINTS)
    state.trailing_sl_points = snap.get("trailing_sl_points", TRAILING_SL_POINTS)
    state.rr_ratio           = snap.get("rr_ratio",           2.0)
    state.qty_mode           = snap.get("qty_mode",           "manual")
    state.manual_qty         = snap.get("manual_qty",         DEFAULT_QUANTITY)
    state.capital            = snap.get("capital",            DEFAULT_CAPITAL)

    # ── Restore historical trades ─────────────────────────────
    for t in snap.get("trades_today", []):
        state.trades_today.append(Trade(
            id=t["id"], timestamp=t["timestamp"],
            direction=t["direction"], instrument=t["instrument"],
            entry_price=t["entry_price"], quantity=t["quantity"],
            stop_loss=t["stop_loss"], target=t["target"],
            exit_price=t.get("exit_price"), exit_time=t.get("exit_time"),
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
            entry_price=at["entry_price"], quantity=at["quantity"],
            stop_loss=at["stop_loss"], target=at["target"],
            order_id=at.get("order_id"),
            sl_order_id=at.get("sl_order_id"),   # restore SL order for cancel-on-exit
            paper=paper_mode,
            status=OrderStatus.FILLED,
        )
        state.active_trade = recovered_trade
        state.trades_today.append(recovered_trade)   # keep trades_today in sync
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
            entry_price=at["entry_price"], quantity=at["quantity"],
            stop_loss=at["stop_loss"], target=at["target"],
            order_id=at.get("order_id"), paper=paper_mode,
            status=OrderStatus.EXITED,
            exit_reason="App crashed — position closed by Zerodha/broker while app was down",
            pnl=0.0,
        )
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

def _check_safety() -> tuple[bool, str]:
    """Check all safety limits before placing an order."""
    if state.kill_switch:
        return False, "🛑 Kill switch activated — no new trades"

    if state.orders_placed >= MAX_ORDERS_PER_DAY:
        return False, f"Max orders reached ({MAX_ORDERS_PER_DAY}/day)"

    if state.total_pnl <= -MAX_LOSS_PER_DAY:
        return False, f"Max daily loss hit (₹{MAX_LOSS_PER_DAY})"

    now = datetime.now().time()
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
LOT_SIZE = int(os.getenv("LOT_SIZE", "75"))  # Nifty lot size (75 since Jul 2024)


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
    """Return nearest weekly expiry date (Thursday)."""
    from datetime import timedelta
    today = datetime.now()
    days_until_thursday = (3 - today.weekday()) % 7
    if days_until_thursday == 0 and today.hour >= 15:
        days_until_thursday = 7
    return today + timedelta(days=days_until_thursday)


def _get_option_symbol(nifty_price: float, direction: Direction) -> tuple[str, int]:
    """Find the nearest OTM Nifty option tradingsymbol via Kite instruments.

    Returns: (tradingsymbol, instrument_token) or raises RuntimeError if not found.

    Uses the Kite instruments API — no guessing at symbol strings.
    """
    atm_strike = round(nifty_price / 50) * 50
    option_type = "CE" if direction == Direction.LONG else "PE"
    # 1 strike OTM for cheaper premium + more lots
    strike = atm_strike + 50 if direction == Direction.LONG else atm_strike - 50

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

    print(f"🎯 Strike Selection:")
    print(f"   Nifty: {nifty_price:.0f} | ATM: {atm_strike} | Picked: {strike} {option_type}")
    print(f"   Expiry: {expiry_str} | Symbol: {symbol} | Token: {token}")
    print(f"   Why: OTM = cheaper premium = more lots per ₹{state.capital:.0f}")

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
    try:
        transaction = (
            kite_manager.kite.TRANSACTION_TYPE_BUY
            if direction == Direction.LONG
            else kite_manager.kite.TRANSACTION_TYPE_SELL
        )
        order_id = kite_manager.kite.place_order(
            variety=kite_manager.kite.VARIETY_REGULAR,
            exchange="NFO",
            tradingsymbol=symbol,          # already clean — no "NFO:" prefix
            transaction_type=transaction,
            quantity=quantity,
            product=kite_manager.kite.PRODUCT_MIS,   # Intraday MIS
            order_type=kite_manager.kite.ORDER_TYPE_MARKET,
        )
        print(f"✅ [LIVE] Order placed: {direction.value.upper()} {quantity}x {symbol} | ID: {order_id}")
        return str(order_id)
    except Exception as e:
        print(f"❌ [LIVE] Order failed: {e}")
        state.last_signal_reason = f"❌ Order failed: {e}"
        return None


def _estimate_option_sl_trigger(direction: str, sl_points: float) -> float:
    """Estimate option-level SL trigger price WITHOUT any API call.

    Logic: ATM options have delta ≈ 0.5. So a 30-point Nifty move
    ≈ ₹15 move in option premium. We use this to estimate the
    option price at which our Nifty SL would be hit.

    This is used ONCE at entry to set a fixed exchange backstop.
    The tick guard handles all intelligent in-app SL management.
    """
    ASSUMED_DELTA = 0.5         # ATM option delta approximation
    ASSUMED_ENTRY_PREMIUM = float(os.getenv("ASSUMED_PREMIUM", "150"))  # configurable

    # Premium drop expected when Nifty moves SL_POINTS against us
    expected_premium_loss = sl_points * ASSUMED_DELTA
    trigger = max(ASSUMED_ENTRY_PREMIUM - expected_premium_loss, 1.0)
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
        transaction = (
            kite_manager.kite.TRANSACTION_TYPE_SELL
            if trade.direction == "long"
            else kite_manager.kite.TRANSACTION_TYPE_BUY
        )
        order_id = kite_manager.kite.place_order(
            variety=kite_manager.kite.VARIETY_REGULAR,
            exchange="NFO",
            tradingsymbol=clean,
            transaction_type=transaction,
            quantity=trade.quantity,
            product=kite_manager.kite.PRODUCT_MIS,
            order_type=kite_manager.kite.ORDER_TYPE_SLM,
            trigger_price=sl_trigger_price,
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
    """Exit active trade."""
    trade = state.active_trade
    if not trade:
        return

    # ── Cancel exchange SL-M before placing exit order ────────────
    # CRITICAL: if SL-M is still open at exchange and we also place an
    # exit order, both could fill → double-exit (short-sell problem).
    _cancel_sl_order(trade)

    exit_direction = Direction.SHORT if trade.direction == "long" else Direction.LONG

    if not state.is_paper_mode:
        try:
            transaction = (
                kite_manager.kite.TRANSACTION_TYPE_SELL
                if trade.direction == "long"
                else kite_manager.kite.TRANSACTION_TYPE_BUY
            )
            kite_manager.kite.place_order(
                variety=kite_manager.kite.VARIETY_REGULAR,
                exchange="NFO",
                tradingsymbol=trade.instrument.replace("NFO:", ""),
                transaction_type=transaction,
                quantity=trade.quantity,
                product=kite_manager.kite.PRODUCT_MIS,
                order_type=kite_manager.kite.ORDER_TYPE_MARKET,
            )
        except Exception as e:
            print(f"❌ Exit order failed: {e}")

    # Calculate P&L
    if trade.direction == "long":
        pnl = (current_price - trade.entry_price) * trade.quantity
    else:
        pnl = (trade.entry_price - current_price) * trade.quantity

    trade.exit_price = current_price
    trade.exit_time = datetime.now().isoformat()
    trade.exit_reason = reason
    trade.pnl = round(pnl, 2)
    trade.status = OrderStatus.EXITED

    state.total_pnl += pnl
    state.active_trade = None

    mode = "📝 PAPER" if trade.paper else "🟢 LIVE"
    emoji = "🟢" if pnl >= 0 else "🔴"
    print(f"{emoji} [{mode}] EXIT {trade.direction.upper()} @ ₹{current_price} "
          f"| P&L: ₹{pnl:+.2f} | Reason: {reason}")

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
        # ── Sync trailing SL to exchange OUTSIDE the lock (API call safe here) ──
        # tick_guard sets pending_sl_exchange_update=True when SL trails;
        # we pick it up here every 5 min — no API calls in the tick thread.
        _sync_trailing_sl_to_exchange()
        return

    # 3. No active trade — evaluate selected strategy
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

    # 4. Check safety before actually placing orders
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


# LOT_SIZE defined above as env-var default 65; 75 is the current Nifty lot size.
# Override via LOT_SIZE env var if needed.


def _resolve_quantity(nifty_price: float) -> int:
    """Return qty to trade based on qty_mode.

    manual  → return state.manual_qty as-is
    capital → estimate option premium, calc how many lots ₹capital buys
               qty = floor(capital / (est_premium × LOT_SIZE)) × LOT_SIZE
    """
    if state.qty_mode == "manual":
        return state.manual_qty

    # Capital mode: estimate ATM option premium ≈ 0.35% of Nifty spot
    # (rough but good enough — actual premium lookup needs live option chain)
    est_premium = round(nifty_price * 0.0035, 1)  # e.g. 23500 × 0.35% ≈ ₹82
    cost_per_lot = est_premium * LOT_SIZE
    lots = max(1, int(state.capital / cost_per_lot))
    qty  = lots * LOT_SIZE
    print(f"📐 Capital mode: ₹{state.capital:,.0f} ÷ "
          f"(₹{est_premium} × {LOT_SIZE}) = {lots} lots → {qty} units")
    return qty


def _enter_trade(direction: Direction, price: float):
    """Open a new trade."""
    try:
        symbol, _token = _get_option_symbol(price, direction)
    except RuntimeError as e:
        print(f"❌ Cannot enter trade: {e}")
        state.last_signal_reason = f"❌ Instrument lookup failed: {e}"
        return

    # ── Resolve trade settings from runtime state (overrideable from UI) ──
    sl_pts  = state.sl_points
    trail   = state.trailing_sl_points
    rr      = state.rr_ratio
    qty     = _resolve_quantity(price)

    if direction == Direction.LONG:
        sl     = price - sl_pts
        target = price + sl_pts * rr
    else:
        sl     = price + sl_pts
        target = price - sl_pts * rr

    order_id = _place_order(symbol, direction, qty, price)
    if not order_id:
        return

    trade = Trade(
        id=f"T-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        timestamp=datetime.now().isoformat(),
        direction=direction.value,
        instrument=symbol,
        entry_price=price,
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
    print(f"🚀 [{mode}] ENTRY {direction.value.upper()} {qty}x {symbol} "
          f"@ ₹{price} | SL: ₹{sl:.0f} (−{sl_pts}pts) | Target: ₹{target:.0f} (R:R 1:{rr})")

    # ── Place SL-M backstop at exchange immediately after entry ───
    # Crash protection: if app dies, exchange still holds this order.
    # We estimate the option trigger WITHOUT an API call (delta approximation)
    # so the tick thread is never blocked.
    # Tick guard handles all active / trailing SL management.
    sl_trigger = _estimate_option_sl_trigger(direction.value, state.sl_points)
    sl_order_id = _place_sl_order(trade, sl_trigger)
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
            state.pending_sl_exchange_update = True   # ← candle loop will sync
            _save_state_snapshot()
    else:
        new_sl = state.lowest_price_since_entry + state.trailing_sl_points
        if new_sl < trade.stop_loss:
            trade.stop_loss = round(new_sl, 2)
            state.pending_sl_exchange_update = True   # ← candle loop will sync
            _save_state_snapshot()

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

def tick_guard(tick: dict) -> None:
    """Real-time SL / target / trailing-SL protection on every Kite tick.

    Entry decisions stay on 5-min candles (need closed candle data).
    Exit decisions run here — every ~1s tick — so we never overshoot SL
    by a whole candle.

    This runs in the KiteTicker background thread so we use a lock
    to avoid racing with the 5-min candle loop.
    """
    if not state.is_running or state.kill_switch:
        return
    if not state.active_trade:
        return   # nothing to protect

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
            "entry_price": t.entry_price, "quantity": t.quantity,
            "stop_loss": t.stop_loss, "target": t.target,
            "exit_price": t.exit_price, "exit_time": t.exit_time,
            "exit_reason": t.exit_reason, "pnl": t.pnl,
            "status": t.status, "order_id": t.order_id,
            "paper": t.paper,
        })
    TRADE_LOG_FILE.write_text(json.dumps({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "total_pnl": round(state.total_pnl, 2),
        "orders_placed": state.orders_placed,
        "paper_mode": state.is_paper_mode,
        "trades": trades,
    }, indent=2))


# ── Public API ────────────────────────────────────────────────

def get_trader_status() -> dict:
    """Get current auto-trader state for dashboard."""
    active = state.active_trade
    return {
        "is_running": state.is_running,
        "is_paper_mode": state.is_paper_mode,
        "kill_switch": state.kill_switch,
        "active_trade": {
            "id": active.id,
            "direction": active.direction,
            "instrument": active.instrument,
            "entry_price": active.entry_price,
            "stop_loss": active.stop_loss,
        "exchange_sl_pending": state.pending_sl_exchange_update,
            "target": active.target,
            "quantity": active.quantity,
            "pnl_unrealized": 0,  # updated by caller with live price
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
    }


def configure_auto_trader(
    sl_points:          float | None = None,
    trailing_sl_points: float | None = None,
    rr_ratio:           float | None = None,
    qty_mode:           str   | None = None,   # 'manual' | 'capital'
    manual_qty:         int   | None = None,
    capital:            float | None = None,
) -> dict:
    """Update runtime trade settings without restarting."""
    if sl_points          is not None: state.sl_points          = sl_points
    if trailing_sl_points is not None: state.trailing_sl_points = trailing_sl_points
    if rr_ratio           is not None: state.rr_ratio           = rr_ratio
    if qty_mode           is not None: state.qty_mode           = qty_mode
    if manual_qty         is not None: state.manual_qty         = manual_qty
    if capital            is not None: state.capital            = capital
    _save_state_snapshot()
    return {
        "sl_points":          state.sl_points,
        "trailing_sl_points": state.trailing_sl_points,
        "rr_ratio":           state.rr_ratio,
        "qty_mode":           state.qty_mode,
        "manual_qty":         state.manual_qty,
        "capital":            state.capital,
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

"""Crude Oil Auto-Trader Engine — MCX Crude Oil Options.

Mirrors auto_trader.py architecture but MCX-specific:
- MCX exchange (not NFO)
- 100 barrel lots (not 65 units)
- 9:00 AM open, 11:25 PM exit (not 3:15 PM)
- ORB + Supertrend strategy (not Nifty smart router)
- Paper mode ON by default

Set CRUDE_LIVE=true in .env to enable real orders.
"""

import os
import json
import threading
from datetime import datetime, time as dt_time
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum

import pandas as pd
from dotenv import load_dotenv

from kite_integration import kite_manager
from crude_data import (
    MCX_CRUDE_LOT_SIZE,
    get_crude_spot,
    get_crude_atm_option,
    get_crude_option_ltp,
    fetch_crude_intraday_data,
    estimate_crude_premium,
)
from crude_strategy import evaluate_crude_best
from strategy import Direction

load_dotenv()

# ── Config ────────────────────────────────────────────────────────
CRUDE_LIVE          = os.getenv("CRUDE_LIVE", "false").lower() == "true"
CRUDE_SL_POINTS     = float(os.getenv("CRUDE_SL_POINTS",     "50"))   # ₹50/bbl SL
CRUDE_TRAIL_POINTS  = float(os.getenv("CRUDE_TRAIL_POINTS",  "25"))   # ₹25 trail
CRUDE_RR_RATIO      = float(os.getenv("CRUDE_RR_RATIO",       "2.0")) # 1:2
CRUDE_CAPITAL       = float(os.getenv("CRUDE_CAPITAL",     "50000"))  # ₹
CRUDE_MAX_LOSS      = float(os.getenv("CRUDE_MAX_LOSS",      "3000"))  # ₹/day
CRUDE_MAX_TRADES    = int(os.getenv("CRUDE_MAX_TRADES",          "4"))
CRUDE_EXIT_TIME     = dt_time(23, 25)  # 11:25 PM
CRUDE_SNAP_FILE     = Path(__file__).parent / ".crude_snapshot.json"
CRUDE_LOG_FILE      = Path(__file__).parent / "crude_trade_log.json"


class CrudeOrderStatus(str, Enum):
    PENDING   = "pending"
    PLACED    = "placed"
    FILLED    = "filled"
    EXITED    = "exited"
    REJECTED  = "rejected"


@dataclass
class CrudeTrade:
    """A single Crude Oil trade record."""
    id:            str
    timestamp:     str
    direction:     str           # 'long' | 'short'
    instrument:    str           # MCX:CRUDEOILAPR...CE/PE
    entry_price:   float         # MCX Crude spot at entry
    entry_premium: float         # Option LTP at entry
    quantity:      int           # units (lots × 100)
    stop_loss:     float         # Crude spot SL level
    target:        float | None  = None
    exit_price:    float | None  = None
    exit_premium:  float | None  = None
    exit_time:     str  | None   = None
    exit_reason:   str  | None   = None
    pnl:           float         = 0.0
    status:        str           = CrudeOrderStatus.PENDING
    order_id:      str  | None   = None
    paper:         bool          = True


@dataclass
class CrudeTraderState:
    is_running:     bool              = False
    is_paper_mode:  bool              = not CRUDE_LIVE
    kill_switch:    bool              = False
    active_trade:   CrudeTrade | None = None
    trades_today:   list              = field(default_factory=list)
    total_pnl:      float             = 0.0
    orders_placed:  int               = 0

    # ── Runtime-tunable params (overrideable from UI) ─────────────
    sl_points:      float = CRUDE_SL_POINTS
    trail_points:   float = CRUDE_TRAIL_POINTS
    rr_ratio:       float = CRUDE_RR_RATIO
    capital:        float = CRUDE_CAPITAL
    strike_offset:  int   = 0

    # ── Live price tracking ───────────────────────────────────────
    last_crude_price:  float = 0.0
    last_option_ltp:   float = 0.0
    last_signal_reason: str  = ""
    last_block_reason:  str | None = None

    # ── Trailing trackers ─────────────────────────────────────────
    highest_since_entry: float = 0.0
    lowest_since_entry:  float = float('inf')


state = CrudeTraderState()
_tick_lock = threading.Lock()


# ── Snapshot ──────────────────────────────────────────────────────

def _save_snapshot():
    if not state.active_trade:
        CRUDE_SNAP_FILE.unlink(missing_ok=True)
        return
    t = state.active_trade
    data = {
        'id': t.id, 'timestamp': t.timestamp,
        'direction': t.direction, 'instrument': t.instrument,
        'entry_price': t.entry_price, 'entry_premium': t.entry_premium,
        'quantity': t.quantity, 'stop_loss': t.stop_loss,
        'target': t.target, 'paper': t.paper, 'status': t.status,
        'is_running': state.is_running,
    }
    CRUDE_SNAP_FILE.write_text(json.dumps(data, indent=2))


def _recover_snapshot():
    """On startup, recover any interrupted Crude trade from snapshot."""
    if not CRUDE_SNAP_FILE.exists():
        return
    try:
        data = json.loads(CRUDE_SNAP_FILE.read_text())
        trade = CrudeTrade(**{k: v for k, v in data.items() if k != 'is_running'})
        state.active_trade = trade
        print(f"🛢️  [Recovery] Crude trade restored: {trade.instrument} {trade.direction}")
    except Exception as e:
        print(f"⚠️  Crude snapshot recovery failed: {e}")


_recover_snapshot()


# ── Trade log ─────────────────────────────────────────────────────

def _save_log():
    trades = []
    for t in state.trades_today:
        trades.append({
            'id': t.id, 'timestamp': t.timestamp, 'direction': t.direction,
            'instrument': t.instrument, 'entry_price': t.entry_price,
            'entry_premium': t.entry_premium, 'quantity': t.quantity,
            'stop_loss': t.stop_loss, 'target': t.target,
            'exit_price': t.exit_price, 'exit_premium': t.exit_premium,
            'exit_time': t.exit_time, 'exit_reason': t.exit_reason,
            'pnl': t.pnl, 'status': t.status, 'paper': t.paper,
        })
    CRUDE_LOG_FILE.write_text(json.dumps(trades, indent=2))


# ── Order placement ───────────────────────────────────────────────

def _limit_price_for(symbol: str, side: str) -> float | None:
    """Fetch LTP for an MCX option and add slippage for reliable fills.

    MCX blocks MARKET orders on options — must use LIMIT with a price
    within the exchange's market-protection band (~2% of LTP).
    BUY  → LTP + 1%  (aggressive, ensures fill)
    SELL → LTP - 1%  (still fills quickly, better price)
    """
    try:
        ltp = get_crude_option_ltp(symbol)
        if not isinstance(ltp, (int, float)) or ltp <= 0:
            return None
        slippage = max(1.0, round(ltp * 0.01, 1))  # 1% or min ₹1
        return round(ltp + slippage, 1) if side == "BUY" else round(ltp - slippage, 1)
    except Exception:
        return None


def _place_order(symbol: str, direction: Direction, qty: int, price: float) -> str | None:
    clean = symbol.replace("MCX:", "")
    mode  = "📝 PAPER" if state.is_paper_mode else "🟢 LIVE"
    tx    = "BUY"  # always buy options (CE or PE)

    if state.is_paper_mode:
        print(f"🛢️  [{mode}] {tx} {qty} × {clean} @ ₹{price:.0f} ({direction.value})")
        return f"PAPER-CRUDE-{datetime.now().strftime('%H%M%S')}"

    # MCX options require LIMIT orders (MARKET is blocked by exchange)
    limit_px = _limit_price_for(symbol, "BUY")
    if limit_px is None:
        state.last_block_reason = "Could not fetch option LTP for LIMIT price"
        return None

    print(f"🛢️  [{mode}] {tx} {qty} × {clean} LIMIT ₹{limit_px:.1f} ({direction.value})")
    try:
        oid = kite_manager.kite.place_order(
            variety=kite_manager.kite.VARIETY_REGULAR,
            exchange="MCX",
            tradingsymbol=clean,
            transaction_type=kite_manager.kite.TRANSACTION_TYPE_BUY,
            quantity=qty,
            product=kite_manager.kite.PRODUCT_MIS,
            order_type=kite_manager.kite.ORDER_TYPE_LIMIT,
            price=limit_px,
            validity="DAY",
        )
        print(f"✅ Crude entry order placed: {oid}")
        return str(oid)
    except Exception as e:
        print(f"❌ Crude order failed: {e}")
        state.last_block_reason = f"Order failed: {e}"
        return None


def _resolve_qty(spot: float, real_premium: float | None = None) -> int:
    premium = real_premium if real_premium and real_premium > 0 else estimate_crude_premium(spot)
    cost_per_lot = premium * MCX_CRUDE_LOT_SIZE
    if cost_per_lot <= 0:
        return MCX_CRUDE_LOT_SIZE
    lots = max(1, int(state.capital / cost_per_lot))
    qty  = lots * MCX_CRUDE_LOT_SIZE
    print(f"📐 Crude qty: ₹{state.capital:,.0f} ÷ ₹{cost_per_lot:.0f}/lot = {lots} lots → {qty} units")
    return qty


# ── Enter / Exit ──────────────────────────────────────────────────

def _enter_trade(direction: Direction, price: float):
    try:
        symbol, _token = get_crude_atm_option(price, direction.value, state.strike_offset)
    except RuntimeError as e:
        print(f"❌ Crude instrument lookup failed: {e}")
        state.last_block_reason = str(e)
        return

    real_ltp = get_crude_option_ltp(symbol)
    qty      = _resolve_qty(price, real_ltp)
    sl_pts   = state.sl_points
    trail    = state.trail_points
    rr       = state.rr_ratio

    sl     = price - sl_pts if direction == Direction.LONG else price + sl_pts
    target = price + sl_pts * rr if direction == Direction.LONG else price - sl_pts * rr

    order_id = _place_order(symbol, direction, qty, price)
    if not order_id:
        return

    ep = real_ltp if isinstance(real_ltp, (int, float)) and real_ltp > 0 else estimate_crude_premium(price)

    trade = CrudeTrade(
        id=f"CRUDE-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        timestamp=datetime.now().isoformat(),
        direction=direction.value,
        instrument=symbol,
        entry_price=price,
        entry_premium=ep,
        quantity=qty,
        stop_loss=sl,
        target=target,
        status=CrudeOrderStatus.FILLED,
        order_id=order_id,
        paper=state.is_paper_mode,
    )

    state.active_trade          = trade
    state.orders_placed        += 1
    state.highest_since_entry   = price
    state.lowest_since_entry    = price
    state.last_option_ltp       = ep
    state.last_signal_reason    = f"Entered {direction.value.upper()} {symbol}"

    _save_snapshot()
    print(f"🛢️  Trade opened: {direction.value.upper()} {symbol} @ ₹{price:.0f} | SL ₹{sl:.0f} | Tgt ₹{target:.0f}")


def _exit_position(reason: str, price: float):
    trade = state.active_trade
    if not trade:
        return

    exit_ltp  = get_crude_option_ltp(trade.instrument)
    exit_prem = exit_ltp if isinstance(exit_ltp, (int, float)) and exit_ltp > 0 else trade.entry_premium

    if not state.is_paper_mode:
        clean    = trade.instrument.replace("MCX:", "")
        # MCX options: LIMIT order required (MARKET is exchange-blocked)
        limit_px = _limit_price_for(trade.instrument, "SELL")
        try:
            kite_manager.kite.place_order(
                variety=kite_manager.kite.VARIETY_REGULAR,
                exchange="MCX",
                tradingsymbol=clean,
                transaction_type=kite_manager.kite.TRANSACTION_TYPE_SELL,
                quantity=trade.quantity,
                product=kite_manager.kite.PRODUCT_MIS,
                order_type=(
                    kite_manager.kite.ORDER_TYPE_LIMIT if limit_px
                    else kite_manager.kite.ORDER_TYPE_MARKET
                ),
                price=limit_px,
                validity="DAY",
            )
        except Exception as e:
            print(f"❌ Crude exit order failed: {e}")

    pnl = (exit_prem - trade.entry_premium) * trade.quantity
    trade.exit_price   = price
    trade.exit_premium = exit_prem
    trade.exit_time    = datetime.now().isoformat()
    trade.exit_reason  = reason
    trade.pnl          = round(pnl, 2)
    trade.status       = CrudeOrderStatus.EXITED

    state.total_pnl   += pnl
    state.active_trade = None

    emoji = "🟢" if pnl >= 0 else "🔴"
    mode  = "📝 PAPER" if trade.paper else "🟢 LIVE"
    print(f"{emoji} [{mode}] Crude EXIT | {reason} | P&L ₹{pnl:+.0f}")

    state.trades_today.append(trade)
    _save_log()
    CRUDE_SNAP_FILE.unlink(missing_ok=True)


# ── Trade management (per tick + per candle) ──────────────────────

def _manage_trade(price: float, source: str = "candle"):
    """Check SL / target / trailing — same logic as Nifty auto_trader."""
    trade = state.active_trade
    if not trade:
        return

    sl   = trade.stop_loss
    tgt  = trade.target
    d    = trade.direction

    # ── Time exit ────────────────────────────────────────────────
    if datetime.now().time() >= CRUDE_EXIT_TIME:
        _exit_position(f"⏰ Time exit ({CRUDE_EXIT_TIME})", price)
        return

    # ── SL breach ────────────────────────────────────────────────
    if d == 'long'  and price <= sl:
        _exit_position(f"🛑 SL hit [{source}] @ ₹{price:.0f}", price)
        return
    if d == 'short' and price >= sl:
        _exit_position(f"🛑 SL hit [{source}] @ ₹{price:.0f}", price)
        return

    # ── Target hit ────────────────────────────────────────────────
    if tgt:
        if d == 'long'  and price >= tgt:
            _exit_position(f"🎯 Target hit [{source}] @ ₹{price:.0f}", price)
            return
        if d == 'short' and price <= tgt:
            _exit_position(f"🎯 Target hit [{source}] @ ₹{price:.0f}", price)
            return

    # ── Trailing SL (only when is_running) ───────────────────────
    if not state.is_running:
        return
    trail = state.trail_points
    if d == 'long':
        state.highest_since_entry = max(state.highest_since_entry, price)
        new_sl = state.highest_since_entry - trail
        if new_sl > trade.stop_loss:
            trade.stop_loss = round(new_sl, 2)
            _save_snapshot()
    else:
        state.lowest_since_entry = min(state.lowest_since_entry, price)
        new_sl = state.lowest_since_entry + trail
        if new_sl < trade.stop_loss:
            trade.stop_loss = round(new_sl, 2)
            _save_snapshot()


# ── Candle-close evaluation ───────────────────────────────────────

def evaluate_and_act_crude(df: pd.DataFrame, price: float):
    """Called on every 5-min candle close. Evaluates entry or manages trade."""
    if not state.is_running or state.kill_switch:
        return

    # ── Time guard ───────────────────────────────────────────────
    now_t = datetime.now().time()
    if now_t >= CRUDE_EXIT_TIME:
        if state.active_trade:
            _exit_position(f"⏰ Time exit {CRUDE_EXIT_TIME}", price)
        return

    # ── Manage existing trade ─────────────────────────────────────
    if state.active_trade:
        _manage_trade(price, source="candle")
        ltp = get_crude_option_ltp(state.active_trade.instrument)
        if isinstance(ltp, (int, float)) and ltp > 0:
            state.last_option_ltp = ltp
        return

    # ── Safety limits ─────────────────────────────────────────────
    if state.total_pnl <= -state.capital * 0.06:   # 6% of capital max loss
        state.last_block_reason = f"Max loss hit (₹{state.total_pnl:.0f})"
        return
    if state.orders_placed >= CRUDE_MAX_TRADES:
        state.last_block_reason = f"Max {CRUDE_MAX_TRADES} trades today"
        return

    # ── Evaluate strategy ─────────────────────────────────────────
    signal = evaluate_crude_best(df)
    state.last_signal_reason = signal.reason
    state.last_block_reason  = None if signal.should_enter else signal.reason

    if signal.should_enter and signal.direction:
        _enter_trade(signal.direction, price)


# ── Tick guard (real-time SL/target on every WebSocket tick) ──────

def crude_tick_guard(tick: dict):
    """Called on every Kite WebSocket tick for MCX Crude Oil spot.

    Only runs SL / target / time-exit protection.
    Entry decisions stay on 5-min closed candles.
    """
    if state.kill_switch:
        return
    price = tick.get("last_price")
    if price and price > 0:
        state.last_crude_price = float(price)
    if not state.active_trade:
        return

    acquired = _tick_lock.acquire(blocking=False)
    if not acquired:
        return
    try:
        if not state.active_trade:
            return
        _manage_trade(float(price), source="tick")
    finally:
        _tick_lock.release()


# ── Public API ────────────────────────────────────────────────────

def start_crude_trader():
    if state.is_running:
        return {'success': False, 'error': 'Already running'}
    if not kite_manager.is_authenticated and not state.is_paper_mode:
        return {'success': False, 'error': 'Not authenticated'}
    state.is_running   = True
    state.kill_switch  = False
    mode = "LIVE" if not state.is_paper_mode else "PAPER"
    print(f"🛢️  Crude auto-trader STARTED [{mode}]")
    return {'success': True, 'mode': mode}


def stop_crude_trader():
    state.is_running = False
    print("🛢️  Crude auto-trader STOPPED")
    return {'success': True}


def kill_crude_trader():
    state.is_running  = False
    state.kill_switch = True
    if state.active_trade:
        price = state.last_crude_price or state.active_trade.entry_price
        _exit_position("🚨 Kill switch", price)
    print("🛢️  Crude auto-trader KILLED")
    return {'success': True}


def get_crude_status() -> dict:
    """Return full status dict — consumed by /api/crude/status endpoint."""
    at = state.active_trade
    trade_dict = None
    if at:
        ltp = state.last_option_ltp
        ep  = at.entry_premium or 0
        pnl = round((ltp - ep) * at.quantity, 2) if ltp > 0 else None
        trade_dict = {
            'id': at.id, 'timestamp': at.timestamp,
            'direction': at.direction, 'instrument': at.instrument,
            'entry_price': at.entry_price, 'entry_premium': at.entry_premium,
            'quantity': at.quantity, 'stop_loss': at.stop_loss,
            'target': at.target, 'paper': at.paper, 'status': at.status,
            'pnl_unrealized': pnl,
            'last_ltp': ltp if ltp > 0 else None,
        }
    return {
        'is_running':    state.is_running,
        'is_paper_mode': state.is_paper_mode,
        'kill_switch':   state.kill_switch,
        'orders_placed': state.orders_placed,
        'total_pnl':     round(state.total_pnl, 2),
        'crude_price':   round(state.last_crude_price, 2) if state.last_crude_price else None,
        'last_option_ltp': state.last_option_ltp or None,
        'last_signal':   state.last_signal_reason,
        'block_reason':  state.last_block_reason,
        'active_trade':  trade_dict,
        'trades_today':  len(state.trades_today),
        'sl_points':     state.sl_points,
        'trail_points':  state.trail_points,
        'rr_ratio':      state.rr_ratio,
        'capital':       state.capital,
    }
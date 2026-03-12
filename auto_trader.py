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

load_dotenv()

# ── Configuration ────────────────────────────────────────────────
LIVE_TRADING = os.getenv("LIVE_TRADING", "false").lower() == "true"
MAX_LOSS_PER_DAY = float(os.getenv("MAX_LOSS_PER_DAY", "5000"))  # ₹5000
MAX_ORDERS_PER_DAY = int(os.getenv("MAX_ORDERS_PER_DAY", "6"))
DEFAULT_QUANTITY = int(os.getenv("DEFAULT_QUANTITY", "50"))  # Nifty lot size
SL_POINTS = float(os.getenv("SL_POINTS", "30"))  # Fixed SL in points
TRAILING_SL_POINTS = float(os.getenv("TRAILING_SL_POINTS", "15"))  # Trail by 15pts
EXIT_TIME = dt_time(15, 15)  # 3:15 PM IST — auto-exit all positions
TRADE_LOG_FILE = Path(__file__).parent / "trade_log.json"


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
    order_id: str | None = None  # Zerodha order ID
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
    highest_price_since_entry: float = 0.0  # for trailing SL
    lowest_price_since_entry: float = float("inf")
    last_evaluation: str = ""
    last_signal_reason: str = ""
    kill_switch: bool = False  # Emergency stop


# ── Singleton State ─────────────────────────────────────────────
state = TraderState()


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


# ── Order Placement ───────────────────────────────────────────

def _get_option_symbol(nifty_price: float, direction: Direction) -> str:
    """Calculate ATM option symbol for Nifty.

    Nifty options have 50-point strike intervals.
    BUY CE for long, BUY PE for short.
    """
    # Round to nearest 50
    atm_strike = round(nifty_price / 50) * 50
    option_type = "CE" if direction == Direction.LONG else "PE"

    # Format: NIFTY{YYMDD}{STRIKE}{CE/PE}
    expiry = _get_nearest_expiry()
    return f"NFO:NIFTY{expiry}{atm_strike}{option_type}"


def _get_nearest_expiry() -> str:
    """Get nearest weekly expiry in YYMDD format."""
    from datetime import timedelta
    today = datetime.now()
    # Nifty weekly expiry is Thursday
    days_until_thursday = (3 - today.weekday()) % 7
    if days_until_thursday == 0 and today.hour >= 15:
        days_until_thursday = 7
    expiry_date = today + timedelta(days=days_until_thursday)
    return expiry_date.strftime("%y%m%d")


def _place_order(symbol: str, direction: Direction,
                 quantity: int, price: float) -> str | None:
    """Place order via Zerodha or simulate in paper mode."""
    if state.is_paper_mode:
        order_id = f"PAPER-{datetime.now().strftime('%H%M%S')}"
        print(f"📝 [PAPER] {direction.value.upper()} {quantity}x {symbol} @ ₹{price}")
        return order_id

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
            tradingsymbol=symbol.replace("NFO:", ""),
            transaction_type=transaction,
            quantity=quantity,
            product=kite_manager.kite.PRODUCT_MIS,  # Intraday
            order_type=kite_manager.kite.ORDER_TYPE_MARKET,
        )
        print(f"✅ [LIVE] Order placed: {direction.value.upper()} {quantity}x {symbol} | ID: {order_id}")
        return str(order_id)
    except Exception as e:
        print(f"❌ [LIVE] Order failed: {e}")
        return None


def _exit_position(reason: str, current_price: float):
    """Exit active trade."""
    trade = state.active_trade
    if not trade:
        return

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
    if state.active_trade:
        _manage_active_trade(current_price)
        return

    # 3. No active trade — evaluate for new entry
    safe, safety_msg = _check_safety()
    if not safe:
        state.last_signal_reason = safety_msg
        return

    signal = evaluate_vwap_breakout(df)
    state.last_signal_reason = signal.reason

    if not signal.should_enter or signal.direction is None:
        return

    # All conditions met — enter trade!
    _enter_trade(signal.direction, current_price)


def _enter_trade(direction: Direction, price: float):
    """Open a new trade."""
    symbol = _get_option_symbol(price, direction)

    # Calculate SL
    if direction == Direction.LONG:
        sl = price - SL_POINTS
        target = price + SL_POINTS * 2  # 1:2 risk-reward
    else:
        sl = price + SL_POINTS
        target = price - SL_POINTS * 2

    order_id = _place_order(symbol, direction, DEFAULT_QUANTITY, price)
    if not order_id:
        return

    trade = Trade(
        id=f"T-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        timestamp=datetime.now().isoformat(),
        direction=direction.value,
        instrument=symbol,
        entry_price=price,
        quantity=DEFAULT_QUANTITY,
        stop_loss=sl,
        target=target,
        status=OrderStatus.FILLED,
        order_id=order_id,
        paper=state.is_paper_mode,
    )

    state.active_trade = trade
    state.trades_today.append(trade)
    state.orders_placed += 1
    state.highest_price_since_entry = price
    state.lowest_price_since_entry = price

    mode = "📝 PAPER" if trade.paper else "🟢 LIVE"
    print(f"🚀 [{mode}] ENTRY {direction.value.upper()} {DEFAULT_QUANTITY}x {symbol} "
          f"@ ₹{price} | SL: ₹{sl} | Target: ₹{target}")
    _save_trade_log()


def _manage_active_trade(current_price: float):
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

    # Trailing stop-loss
    if is_long:
        new_sl = state.highest_price_since_entry - TRAILING_SL_POINTS
        if new_sl > trade.stop_loss:
            trade.stop_loss = round(new_sl, 2)
    else:
        new_sl = state.lowest_price_since_entry + TRAILING_SL_POINTS
        if new_sl < trade.stop_loss:
            trade.stop_loss = round(new_sl, 2)

    # Check stop-loss hit
    if is_long and current_price <= trade.stop_loss:
        _exit_position(f"Stop-loss hit (SL=₹{trade.stop_loss})", current_price)
        return
    if not is_long and current_price >= trade.stop_loss:
        _exit_position(f"Stop-loss hit (SL=₹{trade.stop_loss})", current_price)
        return

    # Check target hit
    if trade.target:
        if is_long and current_price >= trade.target:
            _exit_position(f"Target hit (₹{trade.target})", current_price)
            return
        if not is_long and current_price <= trade.target:
            _exit_position(f"Target hit (₹{trade.target})", current_price)
            return


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
        "exit_time": EXIT_TIME.strftime("%H:%M"),
        "sl_points": SL_POINTS,
        "trailing_sl_points": TRAILING_SL_POINTS,
    }


def start_auto_trader():
    """Start the auto-trader loop."""
    if state.is_running:
        return {"status": "already_running"}

    state.is_running = True
    state.kill_switch = False
    mode = "📝 PAPER" if state.is_paper_mode else "🟢 LIVE"
    print(f"\n🚀 Auto-Trader STARTED [{mode} MODE]")
    print(f"   Max loss: ₹{MAX_LOSS_PER_DAY} | Max orders: {MAX_ORDERS_PER_DAY}")
    print(f"   SL: {SL_POINTS}pts | Trail: {TRAILING_SL_POINTS}pts")
    print(f"   Auto-exit: {EXIT_TIME.strftime('%H:%M')} IST\n")
    return {"status": "started", "mode": mode}


def stop_auto_trader():
    """Stop the auto-trader and exit any active position."""
    state.is_running = False
    if state.active_trade:
        # Get latest price for exit
        tick = kite_manager.latest_tick
        price = tick["last_price"] if tick else state.active_trade.entry_price
        _exit_position("Manual stop — auto-trader stopped", price)

    print("🛑 Auto-Trader STOPPED")
    return {"status": "stopped"}


def activate_kill_switch():
    """Emergency stop — exit all positions, block new trades."""
    state.kill_switch = True
    state.is_running = False
    if state.active_trade:
        tick = kite_manager.latest_tick
        price = tick["last_price"] if tick else state.active_trade.entry_price
        _exit_position("🚨 KILL SWITCH — emergency exit", price)

    print("🚨 KILL SWITCH ACTIVATED — all trading halted!")
    return {"status": "killed"}

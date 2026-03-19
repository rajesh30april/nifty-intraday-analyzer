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
    MCX_CRUDE_MINI_LOT_SIZE,
    get_crude_lot_size,
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

    # ── Trail mode: 'fixed' | 'atr' | 'supertrend' ───────────────
    trail_mode:      str   = 'fixed'  # default: fixed points
    atr_multiplier:  float = 1.5      # used when trail_mode='atr'

    # ── Live price tracking ───────────────────────────────────────
    last_crude_price:   float = 0.0
    last_option_ltp:    float = 0.0
    last_signal_reason: str   = ""
    last_block_reason:  str | None = None

    # ── Cached indicator values (updated on each candle close) ────
    last_atr:      float = 0.0   # latest ATR(14) of crude futures
    last_st_line:  float = 0.0   # latest Supertrend line value

    # ── Entry SL reference (original, never changes) ─────────────
    entry_crude_sl: float = 0.0   # crude spot SL at the moment of entry

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
        # ── extra state for banner ────────────────────────────────
        'entry_crude_sl':      state.entry_crude_sl,
        'highest_since_entry': state.highest_since_entry,
        'lowest_since_entry':  state.lowest_since_entry,
    }
    CRUDE_SNAP_FILE.write_text(json.dumps(data, indent=2))


def _recover_snapshot():
    """On startup, recover any interrupted Crude trade from snapshot."""
    if not CRUDE_SNAP_FILE.exists():
        return
    try:
        data  = json.loads(CRUDE_SNAP_FILE.read_text())
        _SNAP_EXTRA = {'is_running', 'entry_crude_sl', 'highest_since_entry', 'lowest_since_entry'}
        trade = CrudeTrade(**{k: v for k, v in data.items() if k not in _SNAP_EXTRA})
        state.active_trade        = trade
        state.entry_crude_sl      = data.get('entry_crude_sl', trade.stop_loss)  # fallback to current SL
        state.highest_since_entry = data.get('highest_since_entry', trade.entry_price)
        state.lowest_since_entry  = data.get('lowest_since_entry',  trade.entry_price)
        print(f"🛢️  [Recovery] Crude trade restored: {trade.instrument} {trade.direction} | orig SL ₹{state.entry_crude_sl}")
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


def _fetch_available_margin() -> float | None:
    """Pull actual available funds from Zerodha.

    MCX (commodity) orders are funded from the equity segment when the
    commodity segment is not separately activated. We therefore check
    commodity first; if disabled / zero, fall back to equity net.
    """
    try:
        m = kite_manager.kite.margins()
        commodity = m.get('commodity', {})
        equity    = m.get('equity',    {})

        if commodity.get('enabled') and float(commodity.get('net', 0)) > 0:
            avail = float(commodity['net'])
            print(f"💰 Margin source: COMMODITY  net=₹{avail:,.2f}")
        else:
            # Commodity not funded separately — equity live_balance funds MCX
            avail = float(equity.get('net', equity.get('available', {}).get('live_balance', 0)))
            print(f"💰 Margin source: EQUITY (commodity disabled)  net=₹{avail:,.2f}")
        return avail if avail > 0 else None
    except Exception as e:
        print(f"⚠️  Margin fetch failed: {e}")
        return None


# MCX Crude Oil OPTIONS — lot sizing note:
# Buyers of MCX commodity options pay ONLY the option premium upfront
# (just like NSE equity option buyers). SPAN + Exposure margin applies
# only to SELLERS and futures traders.
#
# Correct formula: cost_per_lot = option_premium × lot_size_barrels
# e.g. premium ₹1,029 × 10 barrels (mini) = ₹10,290 per lot
#      premium ₹1,029 × 100 barrels (full) = ₹1,02,900 per lot
#
# The original order rejections were caused by the code accidentally
# selecting the FULL CRUDEOIL contract (100 bbl) instead of mini.
# That is now fixed by capital threshold → always use mini when < ₹1.2L.
MCX_OPTION_MARGIN_RATE = 0.15   # kept for _crude_margin_info() display only — NOT used for lot sizing


def _fetch_margin_per_lot(symbol: str, qty: int = 1) -> float | None:
    """Ask Zerodha exactly what margin 1 lot of this option costs.

    Uses kite.order_margins() — the most accurate source.
    Falls back to None so callers can use the rule-of-thumb estimate.
    """
    if not kite_manager.is_authenticated:
        return None
    clean = symbol.replace("MCX:", "")
    try:
        result = kite_manager.kite.order_margins([
            {
                "exchange":        "MCX",
                "tradingsymbol":   clean,
                "transaction_type": "BUY",
                "variety":         "regular",
                "product":         "MIS",
                "order_type":      "LIMIT",
                "quantity":        qty,
                "price":           0,
            }
        ])
        if result and isinstance(result, list) and result[0]:
            total = float(result[0].get("total", 0) or 0)
            print(f"📋 Zerodha margin check: ₹{total:,.0f} for {qty} lot(s) of {clean}")
            return total if total > 0 else None
    except Exception as e:
        print(f"⚠️  order_margins() failed ({e}) — using rule-of-thumb")
    return None


def _resolve_qty(spot: float, real_premium: float | None = None,
                 lot_size: int = MCX_CRUDE_LOT_SIZE,
                 symbol: str = "") -> int:
    """Return order quantity in LOTS (what the exchange expects).

    MCX quantity unit = LOTS, NOT barrels.
      1 lot CRUDEOIL  = 100 barrels  → qty=1 buys 100 barrels
      1 lot CRUDEOILM = 10  barrels  → qty=1 buys 10  barrels

    MCX option BUYERS pay only the option premium (just like NSE equity
    option buyers). We size based on premium × lot_size.
    Zerodha's order_margins() API is tried first for the exact figure;
    if unavailable we fall back to premium × barrels.
    Returns 0 if capital is insufficient — caller must block the trade.
    """
    available = state.capital * 0.9   # keep 10% buffer

    # ── Step 1: try Zerodha's exact required margin for 1 lot ─────
    cost_per_lot = _fetch_margin_per_lot(symbol) if symbol else None
    if cost_per_lot and cost_per_lot > 0:
        print(f"📐 Margin from Zerodha API: ₹{cost_per_lot:,.0f}/lot")
    else:
        # ── Step 2: fallback — premium × barrels (correct for buyers) ─
        premium      = real_premium if real_premium and real_premium > 0 else estimate_crude_premium(spot)
        cost_per_lot = premium * lot_size
        print(f"📐 Margin estimate (premium): "
              f"₹{premium:.1f} × {lot_size}bbl = ₹{cost_per_lot:,.0f}/lot")

    if not cost_per_lot or cost_per_lot <= 0:
        return 0

    lots = int(available / cost_per_lot)
    print(f"📐 Qty: ₹{state.capital:,.0f} × 90% = ₹{available:,.0f} "
          f"÷ ₹{cost_per_lot:,.0f}/lot = {lots} lots "
          f"(lot_sz={lot_size}bbl, total ₹{lots * cost_per_lot:,.0f})")
    return lots


# ── Enter / Exit ──────────────────────────────────────────────────

def _enter_trade(direction: Direction, price: float):
    try:
        symbol, _token, lot_size = get_crude_atm_option(
            price, direction.value, state.strike_offset, capital=state.capital
        )
    except RuntimeError as e:
        print(f"❌ Crude instrument lookup failed: {e}")
        state.last_block_reason = str(e)
        return

    # ── Sync capital from Zerodha before every trade ─────────────
    live_margin = _fetch_available_margin()
    if live_margin is not None:
        if abs(live_margin - state.capital) > 100:   # only log meaningful changes
            print(f"💰 Capital synced: ₹{state.capital:,.0f} → ₹{live_margin:,.0f} (live Zerodha balance)")
        state.capital = live_margin

    real_ltp = get_crude_option_ltp(symbol)
    qty      = _resolve_qty(price, real_ltp, lot_size=lot_size, symbol=symbol)

    # ── Capital guard — block before hitting exchange ──────────────
    if qty == 0:
        premium      = real_ltp or estimate_crude_premium(price)
        needed       = round(premium * lot_size, 0)
        is_mini      = lot_size == MCX_CRUDE_MINI_LOT_SIZE
        state.last_block_reason = (
            f"⛔ Need ₹{needed:,.0f} for 1 {'mini' if is_mini else 'full'} lot "
            f"(₹{premium:.0f} prem × {lot_size}bbl) but only "
            f"₹{state.capital:,.0f} available."
        )
        print(f"🚫 {state.last_block_reason}")
        return
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
    state.entry_crude_sl        = sl        # original SL — never mutated, used for UI
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

    mode = state.trail_mode

    # ── Mode: Supertrend line IS the dynamic SL ───────────────────
    if mode == 'supertrend' and state.last_st_line > 0:
        if d == 'long' and state.last_st_line > trade.stop_loss:
            old = trade.stop_loss
            trade.stop_loss = round(state.last_st_line, 2)
            _save_snapshot()
            print(f"📈 [ST trail] SL ₹{old:.0f} → ₹{trade.stop_loss:.0f} (ST={state.last_st_line:.0f})")
        elif d == 'short' and state.last_st_line < trade.stop_loss:
            old = trade.stop_loss
            trade.stop_loss = round(state.last_st_line, 2)
            _save_snapshot()
            print(f"📉 [ST trail] SL ₹{old:.0f} → ₹{trade.stop_loss:.0f} (ST={state.last_st_line:.0f})")
        return

    # ── Mode: ATR-based trail distance ───────────────────────────
    if mode == 'atr' and state.last_atr > 0:
        trail = state.last_atr * state.atr_multiplier
    else:
        trail = state.trail_points   # fixed fallback

    if d == 'long':
        state.highest_since_entry = max(state.highest_since_entry, price)
        new_sl = state.highest_since_entry - trail
        if new_sl > trade.stop_loss:
            old = trade.stop_loss
            trade.stop_loss = round(new_sl, 2)
            _save_snapshot()
            tag = f"ATR×{state.atr_multiplier}={trail:.0f}" if mode == 'atr' else f"fixed={trail:.0f}"
            print(f"📈 [{tag}] SL ₹{old:.0f} → ₹{trade.stop_loss:.0f}")
    else:
        state.lowest_since_entry = min(state.lowest_since_entry, price)
        new_sl = state.lowest_since_entry + trail
        if new_sl < trade.stop_loss:
            old = trade.stop_loss
            trade.stop_loss = round(new_sl, 2)
            _save_snapshot()
            tag = f"ATR×{state.atr_multiplier}={trail:.0f}" if mode == 'atr' else f"fixed={trail:.0f}"
            print(f"📉 [{tag}] SL ₹{old:.0f} → ₹{trade.stop_loss:.0f}")


# ── Candle-close evaluation ───────────────────────────────────────

def _cache_indicators(df: pd.DataFrame) -> None:
    """Cache ATR(14) and Supertrend line from latest candle into state.
    Called every candle so _manage_trade always has fresh values.
    """
    import indicators as ind
    try:
        atr_s = ind.atr(df['high'], df['low'], df['close'], 14)
        state.last_atr = float(atr_s.iloc[-1]) if not pd.isna(atr_s.iloc[-1]) else 0.0
    except Exception:
        pass
    try:
        st = ind.supertrend(df['high'], df['low'], df['close'],
                            CRUDE_ST_PERIOD, CRUDE_ST_MULTIPLIER)
        state.last_st_line = float(st['supertrend'].iloc[-1])
    except Exception:
        pass


def evaluate_and_act_crude(df: pd.DataFrame, price: float):
    """Called on every 5-min candle close. Evaluates entry or manages trade."""
    if not state.is_running or state.kill_switch:
        return

    # ── Cache ATR + ST so _manage_trade can use them ─────────────
    _cache_indicators(df)

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


# ATM crude option delta ≈ 0.45 (close enough for SL/target premium display)
_CRUDE_DELTA = 0.45


def _estimate_sl_premium(trade: CrudeTrade) -> float | None:
    """Approx option premium if crude hits the current SL level."""
    if not trade or not trade.entry_premium:
        return None
    sl_pts    = abs(trade.entry_price - trade.stop_loss)
    estimated = trade.entry_premium - sl_pts * _CRUDE_DELTA
    return round(max(estimated, 0.5), 1)


def _estimate_target_premium(trade: CrudeTrade) -> float | None:
    """Approx option premium if crude hits the target level."""
    if not trade or not trade.entry_premium or not trade.target:
        return None
    tgt_pts   = abs(trade.entry_price - trade.target)
    estimated = trade.entry_premium + tgt_pts * _CRUDE_DELTA
    return round(estimated, 1)


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
            # ── extra fields for the Nifty-style position banner ──
            'trailing_sl':    round(at.stop_loss, 2),             # current (moving) SL
            'original_sl':    round(state.entry_crude_sl, 2),     # SL at entry
            'sl_premium':     _estimate_sl_premium(at),           # approx option prem at SL
            'target_premium': _estimate_target_premium(at),       # approx option prem at target
            'lots':           at.quantity,                        # MCX = quantity in lots
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
        'trail_mode':    state.trail_mode,
        'atr_multiplier': state.atr_multiplier,
        'last_atr':      round(state.last_atr, 2) if state.last_atr else None,
        'last_st_line':  round(state.last_st_line, 2) if state.last_st_line else None,
        'exit_time':     CRUDE_EXIT_TIME.strftime('%H:%M'),
        # Margin info — helps diagnose order rejections
        'margin_info': _crude_margin_info(),
    }


def _crude_margin_info() -> dict:
    """Return estimated margin requirements for 1 lot each of full and mini contracts.

    Crude MCX options: SPAN + Exposure ≈ 15% of contract value.
    Full lot = 100 barrels, Mini = 10 barrels.
    """
    price = state.last_crude_price or 0
    if not price:
        return {}
    full_margin = round(price * MCX_CRUDE_LOT_SIZE      * MCX_OPTION_MARGIN_RATE)
    mini_margin = round(price * MCX_CRUDE_MINI_LOT_SIZE * MCX_OPTION_MARGIN_RATE)
    capital     = state.capital
    full_lots   = int(capital * 0.9 / full_margin) if full_margin else 0
    mini_lots   = int(capital * 0.9 / mini_margin) if mini_margin else 0
    return {
        'full_margin_per_lot': full_margin,
        'mini_margin_per_lot': mini_margin,
        'affordable_full_lots': full_lots,
        'affordable_mini_lots': mini_lots,
        'will_use_mini':        capital < 120_000,
    }
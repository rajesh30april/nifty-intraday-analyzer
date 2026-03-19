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
from datetime import datetime, time as dt_time, timedelta
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

# Pre-flight margin safety buffer (₹).
# WHY: Zerodha re-prices margin at the live LTP when the order hits the
# exchange, which is 2-5 seconds AFTER our order_margins() pre-flight
# check. A single tick in the option LTP can move margin by ₹100-1000.
# Observed gap: ₹868.50 (required=31459, available=30591).
# ₹2 500 covers ~₹25/tick movement on a 100-barrel mini lot, which is
# comfortable for normal crude intraday volatility.
MARGIN_SAFETY_BUFFER = float(os.getenv("MARGIN_SAFETY_BUFFER", "2500"))
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
    quantity:      int           # in LOTS (e.g. 2)
    stop_loss:     float         # Crude spot SL level (reference)
    lot_size:      int           = 10    # barrels per lot (10 mini, 100 full) — default mini
    sl_premium:    float         = 0.0   # Option premium SL — PRIMARY exit trigger
    tgt_premium:   float | None  = None  # Option premium target
    peak_ltp:      float         = 0.0   # Highest option LTP seen — used for trailing
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
    trade_date:     str               = ""  # ISO date of last reset (YYYY-MM-DD IST)

    # ── Runtime-tunable params (overrideable from UI) ─────────────────
    sl_points:      float = CRUDE_SL_POINTS
    trail_points:   float = CRUDE_TRAIL_POINTS
    rr_ratio:       float = CRUDE_RR_RATIO
    capital:        float = CRUDE_CAPITAL
    strike_offset:  int   = 0
    max_trades:     int   = CRUDE_MAX_TRADES   # UI-tunable, default from env

    # ── Trail mode: 'fixed' | 'atr' | 'supertrend' ───────────────
    trail_mode:      str   = 'fixed'  # default: fixed points
    atr_multiplier:  float = 1.5      # used when trail_mode='atr'

    # ── Live price tracking ───────────────────────────────────────
    last_crude_price:   float = 0.0
    last_option_ltp:    float = 0.0
    last_signal_reason: str   = ""
    last_block_reason:  str | None = None
    last_option_eval:   str   = ""   # options quality gate summary

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
    import dataclasses
    # Serialize ALL trade fields so new fields survive restarts
    data = dataclasses.asdict(state.active_trade)
    # Enum → string
    data['status'] = state.active_trade.status.value if hasattr(state.active_trade.status, 'value') else str(state.active_trade.status)
    # Extra state needed for recovery
    data['is_running']           = state.is_running
    data['entry_crude_sl']       = state.entry_crude_sl
    data['highest_since_entry']  = state.highest_since_entry
    data['lowest_since_entry']   = state.lowest_since_entry
    CRUDE_SNAP_FILE.write_text(json.dumps(data, indent=2))


def _recover_snapshot():
    """On startup, recover any interrupted Crude trade from snapshot."""
    if not CRUDE_SNAP_FILE.exists():
        return
    try:
        data  = json.loads(CRUDE_SNAP_FILE.read_text())
        _SNAP_EXTRA = {'is_running', 'entry_crude_sl', 'highest_since_entry', 'lowest_since_entry'}
        trade_data  = {k: v for k, v in data.items() if k not in _SNAP_EXTRA}
        # Backfill / sanitize fields added in newer versions — replace None too
        ep = trade_data.get('entry_premium') or 0
        def _fill(key, default):
            if not trade_data.get(key):
                trade_data[key] = default
        _fill('lot_size',   MCX_CRUDE_MINI_LOT_SIZE)
        _fill('sl_premium', round(ep * 0.98, 1))
        _fill('peak_ltp',   ep)
        # tgt_premium: None is valid (no target set)
        trade = CrudeTrade(**trade_data)
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
        err_str = str(e)
        print(f"❌ Crude order failed: {err_str}")

        # Parse Zerodha's "Insufficient funds" rejection
        # e.g. "Insufficient funds. Required margin is 31459.50 but available margin is 30591.00."
        reason = err_str
        try:
            import re
            m = re.search(
                r"Required margin is ([\d,.]+) but available margin is ([\d,.]+)",
                err_str, re.IGNORECASE,
            )
            if m:
                required_m  = float(m.group(1).rstrip(".").replace(",", ""))
                available_m = float(m.group(2).rstrip(".").replace(",", ""))
                topup       = max(0.0, required_m - available_m + MARGIN_SAFETY_BUFFER)
                reason = (
                    f"⛔ Zerodha margin rejection: "
                    f"need ₹{required_m:,.0f} but only ₹{available_m:,.0f} free. "
                    f"Top up ₹{topup:,.0f} (includes ₹{MARGIN_SAFETY_BUFFER:,.0f} safety buffer) "
                    f"or switch to MINI lot (CRUDEOILM, ~10× smaller margin)."
                )
        except Exception:
            pass

        state.last_block_reason = reason
        return None


def _fetch_available_margin() -> tuple[float, float, float] | tuple[None, None, None]:
    """Return (free_margin, total_net, utilised) from Zerodha.

    WHY NOT equity.net?
    ─────────────────────────────────────────────────────────────────
    equity.net = opening_balance + intraday_payin + credited P&L
                 BUT it does NOT subtract margin already locked in
                 open positions (option premiums paid upfront).

    Zerodha stores the LOCKED amount in utilised.debits (negative).
    Free margin = sum of equity.available sub-fields, which excludes
    anything already committed.

    Verified against live data:
      net=38329   available={opening=12591, intraday_payin=18000}  → 30591 free
      utilised.debits = -7738  →  38329 + (-7738) = 30591  ✓

    We use the sum-of-available approach as primary (most explicit)
    and cross-check with net+debits. Returns the LOWER of the two
    as the safest value so we never oversize.
    ─────────────────────────────────────────────────────────────────
    Returns (free_margin, total_net, utilised_amount).
    Returns (None, None, None) on API failure.
    """
    try:
        m         = kite_manager.kite.margins()
        commodity = m.get('commodity', {})
        equity    = m.get('equity',    {})

        # ── Prefer commodity segment if actually funded ────────────
        if commodity.get('enabled'):
            c_avail   = commodity.get('available', {})
            c_free    = sum([
                float(c_avail.get('cash', 0)             or 0),
                float(c_avail.get('opening_balance', 0)  or 0),
                float(c_avail.get('intraday_payin', 0)   or 0),
                float(c_avail.get('adhoc_margin', 0)     or 0),
                float(c_avail.get('collateral', 0)       or 0),
            ])
            c_net    = float(commodity.get('net', 0) or 0)
            c_debits = float(commodity.get('utilised', {}).get('debits', 0) or 0)
            c_free2  = c_net + c_debits           # debits is negative
            free     = min(c_free, c_free2) if c_free > 0 else c_free2
            used     = abs(c_debits)
            if free > 0:
                print(f"💰 Margin [COMMODITY]  free=₹{free:,.0f}  net=₹{c_net:,.0f}  used=₹{used:,.0f}")
                return free, c_net, used

        # ── Equity segment (MCX funded from equity when commodity=off) ─
        e_avail   = equity.get('available', {})
        e_free    = sum([
            float(e_avail.get('cash', 0)             or 0),
            float(e_avail.get('opening_balance', 0)  or 0),
            float(e_avail.get('intraday_payin', 0)   or 0),
            float(e_avail.get('adhoc_margin', 0)     or 0),
            float(e_avail.get('collateral', 0)       or 0),
        ])
        e_net    = float(equity.get('net', 0) or 0)
        e_debits = float(equity.get('utilised', {}).get('debits', 0) or 0)
        e_free2  = e_net + e_debits           # debits is negative → subtracts utilized
        free     = min(e_free, e_free2) if e_free > 0 else e_free2
        used     = abs(e_debits)

        print(f"💰 Margin [EQUITY]  free=₹{free:,.0f}  net=₹{e_net:,.0f}  used=₹{used:,.0f}  "
              f"(opening=₹{e_avail.get('opening_balance',0):,.0f}  "
              f"intraday=₹{e_avail.get('intraday_payin',0):,.0f})")
        return (free, e_net, used) if free > 0 else (None, None, None)
    except Exception as e:
        print(f"⚠️  Margin fetch failed: {e}")
        return None, None, None


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


def _query_zerodha_margin(symbol: str, lots: int,
                          ltp: float | None = None) -> float | None:
    """Call Zerodha order_margins() for exactly `lots` lots.

    IMPORTANT: Zerodha returns zeros when price=0 for commodity options.
    We must pass the actual LTP so the `option_premium` field is populated.
    If ltp is not provided, we fetch it live.

    Returns total required margin in ₹, or None on API failure.
    """
    if not kite_manager.is_authenticated or lots <= 0:
        return None
    clean = symbol.replace("MCX:", "")

    # Zerodha REQUIRES a real price for commodity options — price=0 → zeros
    price = ltp
    if not price or price <= 0:
        price = get_crude_option_ltp(symbol)
    if not price or price <= 0:
        print(f"⚠️  Cannot query Zerodha margin — no LTP for {clean}")
        return None

    try:
        result = kite_manager.kite.order_margins([{
            "exchange":          "MCX",
            "tradingsymbol":     clean,
            "transaction_type":  "BUY",
            "variety":           "regular",
            "product":           "MIS",
            "order_type":        "LIMIT",
            "quantity":          lots,
            "price":             round(price, 1),
        }])
        if result and isinstance(result, list) and result[0]:
            total = float(result[0].get("total", 0) or 0)
            if total > 0:
                print(f"📋 Zerodha margin ({lots}L {clean} @ ₹{price:.1f}): ₹{total:,.0f}")
                return total
            # option_premium might be the right field for buyers
            prem_field = float(result[0].get("option_premium", 0) or 0)
            if prem_field > 0:
                print(f"📋 Zerodha margin [prem] ({lots}L {clean}): ₹{prem_field:,.0f}")
                return prem_field
    except Exception as e:
        print(f"⚠️  order_margins({lots}L {clean}) failed: {e}")
    return None


def _validate_and_size(symbol: str, desired_lots: int, available: float,
                       ltp: float | None = None) -> tuple[int, float]:
    """Ask Zerodha for the EXACT margin for `desired_lots`, walk down if needed.

    This is the pre-flight gatekeeper — called RIGHT BEFORE we hit the exchange.
    Loops lots = desired → 1, asking Zerodha each time.
    Returns (approved_lots, required_margin).
    approved_lots == 0  →  block the trade entirely.

    Why walk down instead of just math?  Zerodha margin for N lots is
    NOT always exactly N × (margin for 1 lot).  Edge fees, rounding, and
    MIS intraday levies can push it above the simple multiple.

    Buffer: we compare against (available - MARGIN_SAFETY_BUFFER) rather
    than raw `available`.  Zerodha re-prices at the LIVE LTP when the
    order actually hits the exchange — which is 2-5 sec after our
    order_margins() call — and a single option tick can move required
    margin by ₹100-1000.  The buffer absorbs that drift.
    """
    if not kite_manager.is_authenticated:
        # Paper mode or no auth — just return desired_lots as-is
        return desired_lots, 0.0

    usable = available - MARGIN_SAFETY_BUFFER   # conservative ceiling
    if usable <= 0:
        print(f"⚠️  Available ₹{available:,.0f} ≤ safety buffer ₹{MARGIN_SAFETY_BUFFER:,.0f} — blocking")
        return 0, 0.0

    for lots in range(desired_lots, 0, -1):
        required = _query_zerodha_margin(symbol, lots, ltp=ltp)
        if required is None:
            # API unavailable — block the trade: better to miss than to get rejected
            print(f"⚠️  Margin API unavailable for {symbol} — blocking trade to avoid rejection")
            return 0, 0.0
        if required <= usable:
            print(
                f"✅ Pre-flight OK: {lots} lot(s) ₹{required:,.0f} ≤ ₹{usable:,.0f} "
                f"(usable after ₹{MARGIN_SAFETY_BUFFER:,.0f} buffer)"
            )
            return lots, required
        print(f"⚠️  {lots}L needs ₹{required:,.0f} but usable ₹{usable:,.0f} — trying {lots-1}L")

    return 0, 0.0  # even 1 lot doesn't fit


def _resolve_qty(spot: float, real_premium: float | None = None,
                 lot_size: int = MCX_CRUDE_LOT_SIZE,
                 symbol: str = "") -> int:
    """Estimate order quantity in LOTS from available capital.

    This is just the INITIAL ESTIMATE for sizing — the real gatekeeper
    is _validate_and_size() which calls Zerodha for the exact number.
    We keep this fast (no exchange call) so it can run without auth.
    """
    available = state.capital   # use full available balance; _validate_and_size guards the rest

    # Try to get per-lot margin from Zerodha (fast — 1-lot check)
    # Pass LTP so Zerodha doesn't return zeros for commodity options
    cost_per_lot = _query_zerodha_margin(symbol, 1, ltp=real_premium) if symbol else None
    if cost_per_lot and cost_per_lot > 0:
        print(f"📐 Zerodha 1-lot cost: ₹{cost_per_lot:,.0f}")
    else:
        # Fallback: premium × barrels (underestimates — that's OK, pre-flight fixes it)
        premium      = real_premium if real_premium and real_premium > 0 else estimate_crude_premium(spot)
        cost_per_lot = premium * lot_size
        print(f"📐 Estimate 1-lot cost: ₹{premium:.1f} × {lot_size}bbl = ₹{cost_per_lot:,.0f}")

    if not cost_per_lot or cost_per_lot <= 0:
        return 0

    lots = int(available / cost_per_lot)
    print(f"📐 Initial size: ₹{available:,.0f} ÷ ₹{cost_per_lot:,.0f}/lot = {lots} lots")
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
    free, net, used = _fetch_available_margin()
    if free is not None:
        if abs(free - state.capital) > 100:
            print(f"💰 Capital synced: ₹{state.capital:,.0f} → ₹{free:,.0f} "
                  f"(net=₹{net:,.0f}, utilised=₹{used:,.0f})")
        state.capital = free   # always use FREE margin, not net

    real_ltp     = get_crude_option_ltp(symbol)
    available    = state.capital          # full live balance after sync
    desired_lots = _resolve_qty(price, real_ltp, lot_size=lot_size, symbol=symbol)

    # ── Pre-flight: ask Zerodha for EXACT margin for our lot count ─
    # This is the real gatekeeper — walks down lots until Zerodha says OK.
    # Pass real_ltp so Zerodha's commodity option pricing is correct (price=0 → zeros).
    qty, required_margin = _validate_and_size(
        symbol, desired_lots, available, ltp=real_ltp
    )

    if qty == 0:
        # Even 1 lot rejected — pull the exact 1-lot cost for the error message
        one_lot_cost = _query_zerodha_margin(symbol, 1) or round(
            (real_ltp or estimate_crude_premium(price)) * lot_size, 0
        )
        is_mini = lot_size == MCX_CRUDE_MINI_LOT_SIZE
        usable  = available - MARGIN_SAFETY_BUFFER
        topup   = max(0.0, one_lot_cost - usable)
        state.last_block_reason = (
            f"⛔ Insufficient margin. "
            f"1 {'mini' if is_mini else 'full'} lot ({symbol.replace('MCX:','')}) "
            f"costs ₹{one_lot_cost:,.0f} but usable balance is ₹{usable:,.0f} "
            f"(₹{available:,.0f} free − ₹{MARGIN_SAFETY_BUFFER:,.0f} safety buffer). "
            f"Top up ₹{topup:,.0f} or set MARGIN_SAFETY_BUFFER lower."
        )
        print(f"🚫 {state.last_block_reason}")
        return

    if qty < desired_lots:
        print(f"⚠️  Lot count reduced {desired_lots} → {qty} to fit margin (₹{required_margin:,.0f} ≤ ₹{available:,.0f})")

    sl_pts = state.sl_points
    rr     = state.rr_ratio

    sl     = price - sl_pts if direction == Direction.LONG else price + sl_pts
    target = price + sl_pts * rr if direction == Direction.LONG else price - sl_pts * rr

    order_id = _place_order(symbol, direction, qty, price)
    if not order_id:
        return

    ep = real_ltp if isinstance(real_ltp, (int, float)) and real_ltp > 0 else estimate_crude_premium(price)

    # ── Premium-based SL and target ──────────────────────────────
    # For option buyers: SL premium = entry - sl_pts (trail below peak)
    #                    Tgt premium = entry + (sl_pts × rr)
    # Both derived directly from the option LTP — no delta estimation needed.
    sl_pts_in_prem  = state.trail_points              # trail distance in ₹ prem
    crude_sl_pts    = abs(price - sl)                 # crude pts at risk
    # Use simple ratio: sl_prem_distance ≈ crude_sl_pts × (ep / price) × sensitivity
    # But keep it simple — just use trail_points directly in premium space
    prem_sl_gap     = round(crude_sl_pts * _CRUDE_DELTA, 1)  # δ-adjusted
    sl_prem         = round(ep - prem_sl_gap, 1)             # option LTP floor
    tgt_prem        = round(ep + prem_sl_gap * state.rr_ratio, 1) if target else None

    print(f"📐 Entry prem ₹{ep:.1f} | SL prem ₹{sl_prem:.1f} "
          f"| Tgt prem {'₹'+str(tgt_prem) if tgt_prem else '—'}")

    trade = CrudeTrade(
        id=f"CRUDE-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        timestamp=datetime.now().isoformat(),
        direction=direction.value,
        instrument=symbol,
        entry_price=price,
        entry_premium=ep,
        quantity=qty,
        lot_size=lot_size,
        stop_loss=sl,
        sl_premium=sl_prem,
        tgt_premium=tgt_prem,
        peak_ltp=ep,
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

    # ── Subscribe option to WebSocket for ~1s real-time exit checks ──
    # Without this, exits rely on 5s REST poll fallback.
    if kite_manager.subscribe_crude_option(_token):
        print(f"📡 Real-time exit active (~1s tick) for {symbol}")
    else:
        print(f"⚠️  WebSocket not streaming — exits via 5s REST poll")

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
        if limit_px is None:
            # Last-resort fallback: use last known LTP with 2% buffer below
            limit_px = round(exit_prem * 0.98, 1) if exit_prem > 0 else None
            print(f"⚠️  LTP fetch failed for exit — using fallback LIMIT ₹{limit_px}")
        try:
            oid = kite_manager.kite.place_order(
                variety=kite_manager.kite.VARIETY_REGULAR,
                exchange="MCX",
                tradingsymbol=clean,
                transaction_type=kite_manager.kite.TRANSACTION_TYPE_SELL,
                quantity=trade.quantity,
                product=kite_manager.kite.PRODUCT_MIS,
                order_type=(
                    kite_manager.kite.ORDER_TYPE_LIMIT
                    if limit_px
                    else kite_manager.kite.ORDER_TYPE_MARKET
                ),
                price=limit_px,
                validity="DAY",
            )
            print(f"✅ Exit order placed: {oid} | {clean} SELL {trade.quantity}L @ ₹{limit_px}")
        except Exception as e:
            print(f"❌ Crude exit order failed: {e} | Instrument: {clean} | Qty: {trade.quantity}L")
            # Log the failure but still clear the trade locally
            # The position needs to be closed manually in Zerodha if this fails

    # P&L = premium Δ × lots × barrels_per_lot
    pnl = (exit_prem - trade.entry_premium) * trade.quantity * trade.lot_size
    trade.exit_price   = price
    trade.exit_premium = exit_prem
    trade.exit_time    = datetime.now().isoformat()
    trade.exit_reason  = reason
    trade.pnl          = round(pnl, 2)
    trade.status       = CrudeOrderStatus.EXITED

    state.total_pnl   += pnl
    state.active_trade = None

    # ── Unsubscribe crude option from WebSocket ───────────────────
    kite_manager.unsubscribe_crude_option()

    emoji = "🟢" if pnl >= 0 else "🔴"
    mode  = "📝 PAPER" if trade.paper else "🟢 LIVE"
    print(f"{emoji} [{mode}] Crude EXIT | {reason} | P&L ₹{pnl:+.0f}")

    state.trades_today.append(trade)
    _save_log()
    CRUDE_SNAP_FILE.unlink(missing_ok=True)


# ── Trade management ──────────────────────────────────────────────
#
# Options are tracked on PREMIUM, not spot price.
# _manage_trade_by_premium() is the PRIMARY exit+trail function.
# _manage_trade() keeps spot-price time/safety exits as backup.

def _manage_trade_by_premium(ltp: float, source: str = "ltp_poll") -> None:
    """PRIMARY: Check SL / target / trail against the option LTP.

    Called every 15 s by the LTP refresh loop AND on every candle close.
    Options traders think in premium space — this is the correct place to
    check exits and trail the stop loss.
    """
    trade = state.active_trade
    if not trade or ltp <= 0:
        return

    # ── Time exit ────────────────────────────────────────────────
    if datetime.now().time() >= CRUDE_EXIT_TIME:
        _exit_position(f"⏰ Time exit ({CRUDE_EXIT_TIME})", state.last_crude_price)
        return

    # ── Update peak LTP (option premium only goes up on profitable side)
    if ltp > trade.peak_ltp:
        trade.peak_ltp = ltp

    sl_prem  = trade.sl_premium
    tgt_prem = trade.tgt_premium

    # ── SL breach — option premium dropped below sl_premium ──────
    if ltp <= sl_prem:
        crude = state.last_crude_price
        _exit_position(f"🛑 SL hit [{source}] option ₹{ltp:.1f} ≤ ₹{sl_prem:.1f}", crude)
        return

    # ── Target hit — option premium reached tgt_premium ──────────
    if tgt_prem and ltp >= tgt_prem:
        crude = state.last_crude_price
        _exit_position(f"🎯 Target hit [{source}] option ₹{ltp:.1f} ≥ ₹{tgt_prem:.1f}", crude)
        return

    # ── Trailing SL (only when trader is running) ─────────────────
    if not state.is_running:
        return

    mode = state.trail_mode

    # Supertrend: use ST line as proxy for trail amount in premium space
    if mode == 'supertrend' and state.last_st_line > 0 and state.last_crude_price > 0:
        # Convert ST-to-crude distance to premium distance via δ
        st_crude_dist = abs(state.last_crude_price - state.last_st_line)
        trail_prem    = round(st_crude_dist * _CRUDE_DELTA, 1)
    elif mode == 'atr' and state.last_atr > 0:
        trail_prem = round(state.last_atr * state.atr_multiplier * _CRUDE_DELTA, 1)
    else:
        trail_prem = state.trail_points   # fixed: treat as direct premium points

    # New SL = peak LTP − trail distance  (works for both long/short option buyers
    # because we always BUY options — premium rises when trade is winning)
    new_sl_prem = round(trade.peak_ltp - trail_prem, 1)
    if new_sl_prem > sl_prem:           # only ever move SL UP (lock in profits)
        old = trade.sl_premium
        trade.sl_premium = new_sl_prem
        _save_snapshot()
        tag = f"{mode} trail={trail_prem:.1f}"
        print(f"📈 [{tag}] SL prem ₹{old:.1f} → ₹{new_sl_prem:.1f} (peak=₹{trade.peak_ltp:.1f})")


def _manage_trade(price: float, source: str = "candle") -> None:
    """SECONDARY: Time and safety exits on crude spot price.

    Crude-price spot checks are a belt-and-suspenders backup to
    _manage_trade_by_premium(). They handle edge cases like IV collapse
    where the option may be nearly worthless but spot didn't hit SL.
    """
    trade = state.active_trade
    if not trade:
        return

    # ── Time exit ────────────────────────────────────────────────
    if datetime.now().time() >= CRUDE_EXIT_TIME:
        _exit_position(f"⏰ Time exit ({CRUDE_EXIT_TIME})", price)
        return

    # ── Spot-price SL + target as safety nets ────────────────────
    d   = trade.direction
    sl  = trade.stop_loss
    tgt = trade.target
    if d == 'long'  and price <= sl:
        _exit_position(f"🛑 Spot SL [{source}] ₹{price:.0f} ≤ ₹{sl:.0f}", price)
        return
    if d == 'short' and price >= sl:
        _exit_position(f"🛑 Spot SL [{source}] ₹{price:.0f} ≥ ₹{sl:.0f}", price)
        return
    if tgt:
        if d == 'long'  and price >= tgt:
            _exit_position(f"🎯 Spot target [{source}] ₹{price:.0f} ≥ ₹{tgt:.0f}", price)
            return
        if d == 'short' and price <= tgt:
            _exit_position(f"🎯 Spot target [{source}] ₹{price:.0f} ≤ ₹{tgt:.0f}", price)
            return


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


def _reset_daily_counters_if_new_day() -> bool:
    """Reset orders_placed + trades_today if the IST calendar date has changed.

    MCX trades across midnight (session closes at 11:25 PM IST).
    We use the IST date of the MCX OPEN (9:00 AM) as the session marker:
    - If current IST time is before 9:00 AM, the 'session date' is yesterday.
    - At or after 9:00 AM the session date is today.

    This means the counter resets once per day at 09:00 AM IST,
    never in the middle of an evening session.
    Returns True if a reset happened (for logging).
    """
    import pytz
    IST      = pytz.timezone("Asia/Kolkata")
    now_ist  = datetime.now(IST)
    # Session date = today if past 09:00, else yesterday
    if now_ist.hour < 9:
        session_date = (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        session_date = now_ist.strftime("%Y-%m-%d")

    if state.trade_date == session_date:
        return False   # same session, nothing to do

    # New session detected — reset daily counters
    old_date   = state.trade_date or "(none)"
    state.trade_date    = session_date
    state.orders_placed = 0
    state.trades_today  = []
    state.total_pnl     = 0.0   # reset daily P&L too
    state.last_block_reason = None
    print(f"🗓️  New session {session_date} (was {old_date}) — daily counters reset")
    return True


def evaluate_and_act_crude(df: pd.DataFrame, price: float):
    """Called on every 5-min candle close. Evaluates entry or manages trade."""
    if not state.is_running or state.kill_switch:
        return

    # ── Daily counter reset (runs silently if same session) ────────────
    _reset_daily_counters_if_new_day()

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
        # Refresh option LTP first so premium checks are current
        ltp = get_crude_option_ltp(state.active_trade.instrument)
        if isinstance(ltp, (int, float)) and ltp > 0:
            state.last_option_ltp = ltp
            _manage_trade_by_premium(ltp, source="candle")
        # Belt-and-suspenders: spot price safety exit
        if state.active_trade:  # may have been exited above
            _manage_trade(price, source="candle")
        return

    # ── Safety limits ─────────────────────────────────────────────
    if state.total_pnl <= -state.capital * 0.06:   # 6% of capital max loss
        state.last_block_reason = f"Max loss hit (₹{state.total_pnl:.0f})"
        return
    if state.orders_placed >= state.max_trades:
        state.last_block_reason = f"Max {state.max_trades} trades/day reached"
        return

    # ── Layer 1: directional signal (consensus from ≥2 strategies) ──
    signal = evaluate_crude_best(df)
    state.last_signal_reason = signal.reason
    state.last_block_reason  = None if signal.should_enter else signal.reason

    if not (signal.should_enter and signal.direction):
        return

    # ── Layer 2: options quality gate ────────────────────────────
    # Directional signal is necessary but not sufficient.
    # Options buyers also need: right DTE, manageable IV, real trend (ADX),
    # squeeze release timing, and OI chain support for the direction.
    try:
        from crude_option_evaluator import evaluate_option_quality
        opt_eval = evaluate_option_quality(df, signal.direction, price)
        state.last_option_eval = opt_eval.summary   # expose for UI
        print(f"  📊 Option quality: {opt_eval.summary}")
        if opt_eval.verdict == "SKIP":
            state.last_block_reason = f"Option gate SKIP ({opt_eval.summary})"
            return
        if opt_eval.verdict == "WAIT":
            # Allow WAIT only if directional consensus is strong (both ORB + ST or Squeeze)
            strong_consensus = (
                any(n in signal.reason for n in ("Squeeze", "ORB"))
                and any(n in signal.reason for n in ("SuperTrend", "VWAP"))
            )
            if not strong_consensus:
                state.last_block_reason = f"Option gate WAIT ({opt_eval.summary})"
                return
    except Exception as e:
        print(f"  ⚠️  Option evaluator error (proceeding): {e}")

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


def add_lots_to_trade(extra_lots: int) -> dict:
    """Add extra_lots to the current active trade (scale-in / pyramid).

    Places a new BUY order for the same instrument and updates the
    active trade's quantity + recalculates weighted average entry premium.
    Returns {success, message, new_qty, avg_premium, order_id}.
    """
    if extra_lots < 1:
        return {"success": False, "error": "extra_lots must be ≥ 1"}

    trade = state.active_trade
    if not trade:
        return {"success": False, "error": "No active trade to add lots to"}

    symbol   = trade.instrument
    clean    = symbol.replace("MCX:", "")
    lot_size = get_crude_lot_size(clean)

    # ── Live LTP for the order price ──────────────────────────────
    current_ltp = get_crude_option_ltp(symbol)
    if not current_ltp or current_ltp <= 0:
        return {"success": False, "error": "Could not fetch current option LTP"}

    # ── Place order ───────────────────────────────────────────────
    direction = Direction.LONG if trade.direction == 'long' else Direction.SHORT

    if state.is_paper_mode:
        order_id = f"PAPER-ADD-{datetime.now().strftime('%H%M%S')}"
        print(f"📝 [PAPER] ADD {extra_lots} lots × {clean} @ ₹{current_ltp:.1f}")
    else:
        limit_px = _limit_price_for(symbol, "BUY")
        if limit_px is None:
            return {"success": False, "error": "Could not get LIMIT price for add-lots order"}
        try:
            order_id = str(kite_manager.kite.place_order(
                variety   = kite_manager.kite.VARIETY_REGULAR,
                exchange  = "MCX",
                tradingsymbol = clean,
                transaction_type = kite_manager.kite.TRANSACTION_TYPE_BUY,
                quantity  = extra_lots,
                product   = kite_manager.kite.PRODUCT_MIS,
                order_type = kite_manager.kite.ORDER_TYPE_LIMIT,
                price     = limit_px,
                validity  = "DAY",
            ))
            print(f"✅ Add-lots order placed: {order_id} — {extra_lots} lots @ ₹{limit_px}")
        except Exception as e:
            msg = str(e)
            print(f"❌ Add-lots order failed: {msg}")
            return {"success": False, "error": msg}

    # ── Update trade state (weighted average) ─────────────────────
    old_qty   = trade.quantity
    new_qty   = old_qty + extra_lots
    old_prem  = trade.entry_premium or current_ltp
    avg_prem  = round(
        (old_prem * old_qty + current_ltp * extra_lots) / new_qty, 2
    )

    trade.quantity      = new_qty
    trade.entry_premium = avg_prem
    state.orders_placed += 1
    _save_snapshot()

    msg = (f"➕ Added {extra_lots} lot(s) to {direction.value.upper()} "
           f"{clean} — now {new_qty} lots @ avg ₹{avg_prem:.1f}")
    print(f"🛢️  {msg}")

    return {
        "success":     True,
        "message":     msg,
        "new_qty":     new_qty,
        "avg_premium": avg_prem,
        "fill_price":  current_ltp,
        "order_id":    order_id,
        "paper":       state.is_paper_mode,
    }


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
        ltp      = state.last_option_ltp
        ep       = at.entry_premium or 0
        lot_sz   = getattr(at, 'lot_size', get_crude_lot_size(at.instrument))
        # P&L = premium Δ × lots × barrels_per_lot
        pnl = round((ltp - ep) * at.quantity * lot_sz, 2) if ltp > 0 else None
        trade_dict = {
            'id': at.id, 'timestamp': at.timestamp,
            'direction': at.direction, 'instrument': at.instrument,
            'entry_price': at.entry_price, 'entry_premium': at.entry_premium,
            'quantity': at.quantity, 'stop_loss': at.stop_loss,
            'target': at.target, 'paper': at.paper, 'status': at.status,
            'pnl_unrealized': pnl,
            'last_ltp': ltp if ltp > 0 else None,
            # ── extra fields for the Nifty-style position banner ──
            'trailing_sl':    round(at.stop_loss, 2),
            'original_sl':    round(state.entry_crude_sl, 2),
            # Use stored premium SL/target — no delta estimation needed
            'sl_premium':     round(at.sl_premium, 1) if at.sl_premium else None,
            'target_premium': round(at.tgt_premium, 1) if at.tgt_premium else None,
            'peak_ltp':       round(at.peak_ltp, 1) if at.peak_ltp else None,
            'lot_size':       lot_sz,
            'lots':           at.quantity,
        }
    return {
        'is_running':    state.is_running,
        'is_paper_mode': state.is_paper_mode,
        'kill_switch':   state.kill_switch,
        'orders_placed': state.orders_placed,
        'trade_date':    state.trade_date,
        'total_pnl':     round(state.total_pnl, 2),
        'crude_price':   round(state.last_crude_price, 2) if state.last_crude_price else None,
        'last_option_ltp': state.last_option_ltp or None,
        'last_signal':     state.last_signal_reason,
        'block_reason':    state.last_block_reason,
        'option_eval':     state.last_option_eval,
        'active_trade':  trade_dict,
        'trades_today':  len(state.trades_today),
        'sl_points':     state.sl_points,
        'trail_points':  state.trail_points,
        'rr_ratio':      state.rr_ratio,
        'capital':       state.capital,
        'max_trades':    state.max_trades,
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
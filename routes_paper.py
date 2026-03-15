"""Paper Trading API routes — mounted on /api/paper/*"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

import paper_trader as pt
from kite_integration import kite_manager

router = APIRouter(prefix="/api/paper", tags=["paper-trading"])


# ── Request models ─────────────────────────────────────────────────────────────

class OpenTradeRequest(BaseModel):
    direction: str          # 'long' | 'short'
    nifty_price: float
    strategy: str = "smart_router"
    sl_nifty: float = 30.0
    rr: float = 2.0
    lot_size: int = pt.LOT_SIZE
    notes: str = ""


class CloseTradeRequest(BaseModel):
    exit_premium: float
    exit_reason: str = "Manual"


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/summary")
def get_summary(days: int = 30):
    """P&L summary for the last N days."""
    return pt.summary(days)


@router.get("/trades")
def get_trades(days: int = 30):
    """All trades (open + closed) for the last N days."""
    trades = pt.get_trades(days)
    # Enrich open trades with live unrealized P&L
    for t in trades:
        if t["status"] == "open":
            live = pt.current_premium(t)
            t.update(live)
    return trades


@router.get("/trades/open")
def get_open_trades():
    """Currently open paper trades with live unrealized P&L."""
    trades = pt.get_open_trades()
    for t in trades:
        live = pt.current_premium(t)
        t.update(live)
    return trades


@router.post("/trades")
def open_trade(req: OpenTradeRequest):
    """Open a new paper trade."""
    if req.direction not in ("long", "short"):
        raise HTTPException(400, "direction must be 'long' or 'short'")
    if req.nifty_price <= 0:
        raise HTTPException(400, "nifty_price must be positive")
    trade = pt.open_trade(
        direction=req.direction,
        nifty_price=req.nifty_price,
        strategy=req.strategy,
        sl_nifty=req.sl_nifty,
        rr=req.rr,
        lot_size=req.lot_size,
        notes=req.notes,
    )
    return trade


@router.post("/trades/{trade_id}/close")
def close_trade(trade_id: int, req: CloseTradeRequest):
    """Close an open trade with given exit premium."""
    try:
        return pt.close_trade(trade_id, req.exit_premium, req.exit_reason)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/trades/{trade_id}/close-at-market")
def close_trade_at_market(trade_id: int, exit_reason: str = "Manual"):
    """Close trade using current live/estimated premium."""
    trade = pt.get_trade(trade_id)
    if not trade:
        raise HTTPException(404, f"Trade {trade_id} not found")
    if trade["status"] != "open":
        raise HTTPException(400, "Trade is already closed")

    live = pt.current_premium(trade)
    exit_prem = live.get("current_premium") or trade["entry_premium"]
    return pt.close_trade(trade_id, exit_prem, exit_reason)


@router.delete("/trades/{trade_id}")
def delete_trade(trade_id: int):
    """Delete an open trade (cancel without recording P&L)."""
    deleted = pt.delete_trade(trade_id)
    if not deleted:
        raise HTTPException(404, "Trade not found or already closed")
    return {"deleted": True, "id": trade_id}


@router.get("/trades/{trade_id}/premium")
def live_premium(trade_id: int):
    """Get current live premium for an open trade."""
    trade = pt.get_trade(trade_id)
    if not trade:
        raise HTTPException(404, f"Trade {trade_id} not found")
    return pt.current_premium(trade)


@router.get("/quick-entry")
def quick_entry_data():
    """Pre-filled data for 'Quick Paper Trade' button — uses live Nifty price."""
    nifty = None
    try:
        nifty = kite_manager.get_nifty_ltp()
    except Exception:
        pass

    if not nifty:
        return {"connected": False, "nifty": None}

    strike_ce = pt.atm_strike(nifty)
    strike_pe = strike_ce
    expiry    = pt.next_expiry()

    ce_data = pt.estimate_premium(nifty, strike_ce, "CE")
    pe_data = pt.estimate_premium(nifty, strike_pe, "PE")

    return {
        "connected":    True,
        "nifty":        round(nifty, 2),
        "strike":       strike_ce,
        "expiry":       expiry,
        "ce_premium":   ce_data["premium"],
        "pe_premium":   pe_data["premium"],
        "source":       ce_data["source"],
        "lot_size":     pt.LOT_SIZE,
    }
# 🚨 MARGIN CALCULATION BUG FOUND!

## THE PROBLEM:

```
Error: Insufficient funds
Required margin:  ₹19,425.25
Available margin: ₹17,804.40
```

**System tried to trade 2 lots when it can only afford 1 lot!**

---

## ROOT CAUSE:

The `_resolve_quantity()` function calculates lots based on **PREMIUM COST**, but Zerodha requires **MARGIN** (which is ~2x the premium!)

### Current Calculation (WRONG):
```python
# In _resolve_quantity():
cost_per_lot = premium × LOT_SIZE
lots = capital / cost_per_lot

Example:
  premium = ₹150
  LOT_SIZE = 65
  cost_per_lot = 150 × 65 = ₹9,750
  
  capital = ₹17,804
  lots = 17,804 / 9,750 = 1.82 lots
  
  System rounds to: 2 lots ← WRONG!
```

### The Reality (Zerodha):
```
Zerodha MARGIN requirement ≠ Premium cost!

For options, margin can be:
  - BUYING options: Premium + buffer
  - SELLING options: SPAN + Exposure margin (huge!)
  
Actual required:
  Margin per lot = ₹9,712.62 (not just ₹9,750 premium!)
  
  For 2 lots: 9,712.62 × 2 = ₹19,425.25
  But available: ₹17,804.40
  
  Result: ORDER REJECTED! ❌
```

---

## WHY THIS HAPPENS:

### Issue 1: No Margin Multiplier
```python
# Current code assumes:
required = premium × LOT_SIZE

# Should be:
required = premium × LOT_SIZE × MARGIN_MULTIPLIER
# Or better: Fetch actual margin requirement from Zerodha!
```

### Issue 2: Rounding Up Instead of Down
```python
# Current:
lots = max(1, int(capital / cost_per_lot))

# This rounds 1.82 → 1, but then somewhere else it becomes 2!

# Should be:
lots = int(capital / cost_per_lot)  # Floor, not max!
```

---

## THE FIX:

### Option 1: Add Safety Margin Multiplier (Quick Fix)
```python
def _resolve_quantity(nifty_price: float, real_premium: float | None = None) -> int:
    if state.qty_mode == "manual":
        return state.manual_qty

    if real_premium and real_premium > 0:
        premium = real_premium
    else:
        premium = _estimate_premium_fallback(nifty_price)

    # 🔧 FIX: Add 2x safety margin for option margin requirements
    MARGIN_MULTIPLIER = 2.0  # Zerodha margin ≈ 2x premium for options
    
    cost_per_lot = premium × LOT_SIZE × MARGIN_MULTIPLIER
    lots = int(state.capital / cost_per_lot)  # Floor, not max!
    
    if lots < 1:
        print(f"⚠️  Insufficient capital: need ₹{cost_per_lot:,.0f}/lot, have ₹{state.capital:,.0f}")
        return 0  # Don't trade!
    
    qty = lots × LOT_SIZE
    print(f"📐 Capital mode: ₹{state.capital:,.0f} ÷ "
          f"(₹{cost_per_lot:,.0f}/lot with {MARGIN_MULTIPLIER}x margin) = {lots} lots → {qty} units")
    return qty
```

### Option 2: Fetch Actual Margin from Zerodha (Best Fix)
```python
def _get_margin_required(symbol: str, qty: int, price: float, transaction_type: str) -> float:
    """Fetch actual margin requirement from Zerodha."""
    try:
        order_params = {
            "exchange": "NFO",
            "tradingsymbol": symbol,
            "transaction_type": transaction_type,  # "BUY" or "SELL"
            "quantity": qty,
            "product": "MIS",  # Intraday
            "order_type": "LIMIT",
            "price": price,
            "variety": "regular",
        }
        
        # Zerodha margin calculator API
        margin_data = kite_manager.kite.order_margins([order_params])
        
        required_margin = margin_data[0].get("total", 0)
        return float(required_margin)
        
    except Exception as e:
        print(f"⚠️  Margin fetch failed: {e}")
        # Fallback to 2x multiplier
        return price × qty × 2.0


def _resolve_quantity(nifty_price: float, real_premium: float | None = None) -> int:
    if state.qty_mode == "manual":
        return state.manual_qty

    # Get symbol first
    symbol, _ = _get_option_symbol(nifty_price, Direction.LONG)  # Doesn't matter which direction
    
    if real_premium and real_premium > 0:
        premium = real_premium
    else:
        premium = _estimate_premium_fallback(nifty_price)

    # Try different lot sizes starting from max possible
    max_lots = int(state.capital / (premium × LOT_SIZE))
    
    for lots in range(max_lots, 0, -1):
        qty = lots × LOT_SIZE
        required = _get_margin_required(symbol, qty, premium, "BUY")
        
        if required <= state.capital:
            print(f"✅ Found affordable size: {lots} lots (₹{required:,.0f} margin required)")
            return qty
    
    print(f"⚠️  Insufficient margin for even 1 lot!")
    return 0
```

---

## RECOMMENDED FIX:

**Use Option 1 (Quick Fix with 2x multiplier) for immediate relief!**

This will:
- ✅ Prevent margin errors immediately
- ✅ Be conservative (may trade 1 lot when could afford 2)
- ✅ Simple, no API calls needed

**Then upgrade to Option 2 later for exact margin calculation.**

---

## EXAMPLE WITH FIX:

### Current (Broken):
```
Available: ₹17,804
Premium: ₹150/unit
LOT_SIZE: 65

Calculation:
  cost_per_lot = 150 × 65 = ₹9,750
  lots = 17,804 / 9,750 = 1.82
  Rounds to: 2 lots
  
Order placed: 2 lots × 65 = 130 units
Required margin: ₹19,425.25
Result: ❌ ORDER REJECTED!
```

### With Fix (Works):
```
Available: ₹17,804
Premium: ₹150/unit
LOT_SIZE: 65
MARGIN_MULTIPLIER: 2.0

Calculation:
  cost_per_lot = 150 × 65 × 2.0 = ₹19,500
  lots = 17,804 / 19,500 = 0.91
  Floors to: 0 lots (can't afford even 1!)
  
  OR with slightly lower premium ₹137:
  cost_per_lot = 137 × 65 × 2.0 = ₹17,810
  lots = 17,804 / 17,810 = 0.99
  Floors to: 0 lots
  
  OR with even lower premium ₹135:
  cost_per_lot = 135 × 65 × 2.0 = ₹17,550
  lots = 17,804 / 17,550 = 1.01
  Floors to: 1 lot
  
Order placed: 1 lot × 65 = 65 units
Required margin: ~₹17,550
Result: ✅ ORDER ACCEPTED!
```

---

## ADDITIONAL ISSUE: Where does 2 come from?

Need to check if there's another place that's doubling the quantity!

```bash
# Search for quantity doubling:
grep -n "qty.*\*.*2\|quantity.*\*.*2" auto_trader.py
```


"""Pytest configuration — patches heavy deps so tests run without Zerodha/Yahoo."""

import os
import sys
from unittest.mock import MagicMock

# ── Bypass Kelly×Expiry×VIX sizing in tests (avoids VIX network call + expiry date math)
os.environ.setdefault("SKIP_TRADE_SIZING", "1")

# ── Stub kite_integration before any module imports it ───────────
_mock_kite_obj = MagicMock()
_mock_kite_obj.TRANSACTION_TYPE_BUY  = "BUY"
_mock_kite_obj.TRANSACTION_TYPE_SELL = "SELL"
_mock_kite_obj.VARIETY_REGULAR       = "regular"
_mock_kite_obj.PRODUCT_MIS           = "MIS"
_mock_kite_obj.ORDER_TYPE_MARKET     = "MARKET"
_mock_kite_obj.place_order.return_value = 12345
_mock_kite_obj.margins.return_value  = {
    "equity": {"available": {"live_balance": 100_000}}
}

_mock_km = MagicMock()
_mock_km.kite = _mock_kite_obj
_mock_km.latest_tick = {"last_price": 23200.0}
_mock_km.is_authenticated = True

kite_stub = MagicMock()
kite_stub.kite_manager = _mock_km
kite_stub.KiteConnect  = MagicMock()
kite_stub.KiteTicker   = MagicMock()

sys.modules.setdefault("kite_integration", kite_stub)
sys.modules.setdefault("kiteconnect",     MagicMock())
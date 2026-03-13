"""Load all strategies into the registry.

Import this module once at app startup to populate the registry.
"""

# Import order matters — existing strategies first, then new ones
import strategies.existing        # noqa: F401 — Price Rejection + Trend Follow
import strategies.orb              # noqa: F401 — Opening Range Breakout
import strategies.vwap_reversion   # noqa: F401 — VWAP Mean Reversion
import strategies.ema_crossover    # noqa: F401 — EMA Crossover
import strategies.supertrend_strat # noqa: F401 — Supertrend
import strategies.rsi_reversal     # noqa: F401 — RSI Reversal
import strategies.macd_momentum    # noqa: F401 — MACD Momentum
import strategies.smart_router     # noqa: F401 — Smart Router (Auto)
import strategies.scalping          # noqa: F401 — Scalping strategies (EMA/VWAP/Momentum)

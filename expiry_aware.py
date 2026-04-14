"""Expiry-Aware Options Logic for Nifty Weekly Options.

Nifty weekly options expire every Thursday. Theta (time decay) is NOT
linear — it EXPLODES in the last 2 days. Buying OTM options on Wednesday
or Thursday is basically burning money.

This module provides:
  1. ExpiryContext — current expiry state (days to expiry, theta risk)
  2. should_buy_option() — gate: is it safe to buy (not sell) options today?
  3. strike_selection_hint() — recommended delta band given DTE
  4. size_scale_for_dte() — reduce size as expiry approaches (gamma/theta risk)

Key insight (Nifty weekly options behaviour):
  DTE 5-4 (Mon-Tue): Normal theta, full strategies enabled
  DTE 3   (Wed):     Elevated theta — prefer ATM or slight ITM; smaller OTM
  DTE 2   (Thu AM):  Gamma spike — ONLY ATM ±50 pts; halve OTM size
  DTE 1   (Thu PM):  Afternoon expiry — NO new buys after 13:00; only close
  DTE 0   (holiday): Should not occur, but guard against it.

References:
  - NSE Nifty 50 expiry = every Thursday (monthly = last Thursday)
  - India VIX jumps 10-20% on Mon after big weekend news
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

# ── Expiry resolution ─────────────────────────────────────────────────────────

def _next_thursday(from_date: date | None = None) -> date:
    """Return the upcoming Thursday (or today if today IS Thursday)."""
    d = from_date or date.today()
    # weekday(): Mon=0 ... Thu=3 ... Sun=6
    days_until_thu = (3 - d.weekday()) % 7
    return d + timedelta(days=days_until_thu)


def _days_to_expiry(from_date: date | None = None) -> int:
    """Calendar days from today to the next Thursday expiry."""
    d   = from_date or date.today()
    exp = _next_thursday(d)
    return (exp - d).days


# ── Core dataclass ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExpiryContext:
    """Full expiry context for the current trading session.

    Attributes:
        expiry_date:      Next weekly expiry (Thursday).
        dte:              Calendar days to expiry (0 = expiry day).
        is_expiry_day:    True if today is Thursday.
        theta_risk:       'low' | 'medium' | 'high' | 'extreme'.
        buy_allowed:      Should we buy options at all right now?
        size_scale:        Multiply base lot size by this (0.0 – 1.0).
        recommended_delta: Delta band for strike selection e.g. (0.30, 0.45).
        warning:          Human-readable caution message.
    """
    expiry_date: date
    dte: int
    is_expiry_day: bool
    theta_risk: str              # 'low' | 'medium' | 'high' | 'extreme'
    buy_allowed: bool            # False = only close positions, no new buys
    size_scale: float            # 0.0 – 1.0
    recommended_delta: tuple[float, float]  # (min_delta, max_delta)
    warning: str


def get_expiry_context(
    now: datetime | None = None,
    close_buys_after_hour: int = 13,
) -> ExpiryContext:
    """Build ExpiryContext for the current moment.

    Args:
        now:                   Override datetime (useful in backtests).
        close_buys_after_hour: On expiry day, stop new buys after this hour.

    Returns:
        ExpiryContext with all derived fields.
    """
    dt       = now or datetime.now()
    today    = dt.date()
    exp_date = _next_thursday(today)
    dte      = _days_to_expiry(today)
    is_exp   = today == exp_date

    # ── Thursday (expiry day) ─────────────────────────────────────────────────
    if is_exp:
        if dt.hour >= close_buys_after_hour:
            return ExpiryContext(
                expiry_date=exp_date, dte=0, is_expiry_day=True,
                theta_risk="extreme", buy_allowed=False, size_scale=0.0,
                recommended_delta=(0.45, 0.55),  # ATM only
                warning=(
                    f"🚨 EXPIRY DAY — after {close_buys_after_hour}:00. "
                    "NO new option buys! Only close open positions. "
                    "Theta is in free-fall."
                ),
            )
        else:
            return ExpiryContext(
                expiry_date=exp_date, dte=0, is_expiry_day=True,
                theta_risk="extreme", buy_allowed=True, size_scale=0.40,
                recommended_delta=(0.40, 0.55),  # ATM ±50 pts only
                warning=(
                    "⚠️ EXPIRY MORNING — gamma risk is extreme. "
                    "Trade ATM only. Use 40% of normal size. "
                    f"All buys close after {close_buys_after_hour}:00."
                ),
            )

    # ── Wednesday (DTE=1 after market closes Tue, but calendar DTE=1 = Wed) ──
    if dte == 1:
        return ExpiryContext(
            expiry_date=exp_date, dte=1, is_expiry_day=False,
            theta_risk="high", buy_allowed=True, size_scale=0.65,
            recommended_delta=(0.35, 0.50),   # near ATM, avoid deep OTM
            warning=(
                "⚠️ 1 day to expiry (Wednesday). Theta is elevated — "
                "prefer ATM or slight ITM strikes. Avoid OTM buys. "
                "Use 65% of normal size."
            ),
        )

    # ── DTE 2 — Tuesday (mild caution zone) ──────────────────────────────────
    if dte == 2:
        return ExpiryContext(
            expiry_date=exp_date, dte=2, is_expiry_day=False,
            theta_risk="medium", buy_allowed=True, size_scale=0.85,
            recommended_delta=(0.30, 0.50),
            warning=(
                "ℹ️ 2 days to expiry. Theta picking up — "
                "slightly prefer near-ATM. 85% of normal size is fine."
            ),
        )

    # ── DTE 3+ — Monday / beginning of week — normal trading ─────────────────
    return ExpiryContext(
        expiry_date=exp_date, dte=dte, is_expiry_day=False,
        theta_risk="low", buy_allowed=True, size_scale=1.0,
        recommended_delta=(0.25, 0.50),   # comfortable OTM to ATM range
        warning="",  # no special warning needed
    )


def expiry_lot_scale(consecutive_losses: int = 0) -> float:
    """Convenience: just the size_scale from expiry context.

    Combine with Kelly multiplier:
        final_lots = base_lots * kelly_mult * expiry_lot_scale()
    """
    ctx = get_expiry_context()
    return ctx.size_scale


def should_buy_option(now: datetime | None = None) -> bool:
    """Quick gate: is it safe to open a new option buy right now?"""
    return get_expiry_context(now).buy_allowed


def strike_delta_range(now: datetime | None = None) -> tuple[float, float]:
    """Recommended delta band for strike selection given DTE."""
    return get_expiry_context(now).recommended_delta


# ── CLI demo ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from datetime import datetime as _dt

    test_cases = [
        ("Monday 10:30",   _dt(2026, 4, 13, 10, 30)),  # Mon
        ("Tuesday 10:30",  _dt(2026, 4, 14, 10, 30)),  # Tue
        ("Wednesday 10:30", _dt(2026, 4, 15, 10, 30)), # Wed  DTE=1
        ("Thursday 09:30", _dt(2026, 4, 16,  9, 30)),  # Thu morning
        ("Thursday 13:30", _dt(2026, 4, 16, 13, 30)),  # Thu afternoon — block buys
    ]

    print("\n🐶 Expiry-Aware Context — Nifty Weekly Options")
    print("=" * 72)
    for label, dt in test_cases:
        ctx = get_expiry_context(dt)
        buy_icon = "✅" if ctx.buy_allowed else "🚫"
        print(f"\n📅 {label}")
        print(f"   DTE={ctx.dte} | theta={ctx.theta_risk} | {buy_icon} buy_allowed={ctx.buy_allowed}")
        print(f"   size_scale={ctx.size_scale:.2f} | delta_range={ctx.recommended_delta}")
        if ctx.warning:
            print(f"   {ctx.warning}")
    print("\n" + "=" * 72)

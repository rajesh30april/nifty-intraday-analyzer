"""Nifty Option Quality Info — Advisory mode (non-blocking).

Shows option quality metrics before entry but NEVER blocks trades.
User sees warnings/info, decides whether to proceed.

Metrics displayed:
  - HV Rank (0-100): Where is current IV vs 90-day range?
  - DTE (days to expiry): Theta decay risk
  - ADX: Trend strength (< 22 = ranging, theta bleed risk)
  - Premium cost: Absolute ₹ amount per unit
  - Score (0-10): Composite quality rating

Unlike Crude's option_evaluator, this is purely informational.
"""

from __future__ import annotations

import pandas as pd
from dataclasses import dataclass
from datetime import date, datetime

import indicators as ind


@dataclass
class OptionInfo:
    """Advisory info about the option entry opportunity."""
    hv_rank:     float      # 0-100 percentile
    hv_current:  float      # annualised %
    dte:         int        # days to expiry
    adx_value:   float      # trend strength
    premium:     float      # option LTP (₹ per unit)
    score:       float      # 0-10 composite
    verdict:     str        # BUY / CAUTION / RISKY
    warnings:    list[str]  # human-readable warnings
    summary:     str        # one-line summary


def _get_hv_rank(df: pd.DataFrame) -> tuple[float, float]:
    """Calculate HV rank and current HV (annualised %).
    
    Returns (hv_rank, hv_current) or (0.0, 0.0) if insufficient data.
    """
    try:
        close = df['close']
        if len(close) < 91:
            return 0.0, 0.0
        
        returns = close.pct_change().dropna()
        hv_90d  = returns.rolling(90).std() * (252 ** 0.5) * 100  # annualised %
        hv_90d  = hv_90d.dropna()
        
        if len(hv_90d) < 2:
            return 0.0, 0.0
        
        current = float(hv_90d.iloc[-1])
        hv_min  = float(hv_90d.min())
        hv_max  = float(hv_90d.max())
        
        if hv_max == hv_min:
            return 50.0, current
        
        rank = ((current - hv_min) / (hv_max - hv_min)) * 100
        return round(rank, 1), round(current, 1)
    except Exception:
        return 0.0, 0.0


def _get_dte() -> int:
    """Return days to next weekly Nifty expiry (Thursday).
    
    Nifty has weekly expiries every Thursday.
    """
    today = date.today()
    weekday = today.weekday()  # 0=Mon, 3=Thu, 6=Sun
    
    # Days until next Thursday
    if weekday < 3:  # Mon-Wed
        days = 3 - weekday
    else:  # Thu-Sun (today is Thu or later, next expiry is next week)
        days = 7 - (weekday - 3)
    
    return days


def _get_adx(df: pd.DataFrame) -> float:
    """Return current ADX(14) value."""
    try:
        if len(df) < 30:
            return 0.0
        adx_s = ind.adx(df['high'], df['low'], df['close'], 14)
        return round(float(adx_s.iloc[-1]), 1) if not pd.isna(adx_s.iloc[-1]) else 0.0
    except Exception:
        return 0.0


def get_nifty_option_info(
    df: pd.DataFrame,
    premium: float,  # real LTP or estimate
) -> OptionInfo:
    """Calculate advisory option quality info (non-blocking).
    
    Args:
        df: 5-min OHLCV DataFrame
        premium: Option LTP in ₹ per unit (e.g. 245.50)
    
    Returns:
        OptionInfo with metrics and warnings (but never blocks)
    """
    hv_rank, hv_val = _get_hv_rank(df)
    dte             = _get_dte()
    adx_val         = _get_adx(df)
    
    warnings = []
    score    = 10.0  # start perfect, deduct points
    
    # ── HV Rank (deduct 0-3 pts) ──────────────────────────────────
    if hv_rank > 85:
        score -= 3.0
        warnings.append(f"⚠️ HV rank {hv_rank:.0f}% (expensive, IV crush risk)")
    elif hv_rank > 70:
        score -= 1.5
        warnings.append(f"⚠️ HV rank {hv_rank:.0f}% (above average)")
    elif hv_rank < 30:
        warnings.append(f"✅ HV rank {hv_rank:.0f}% (cheap options)")
    
    # ── DTE (deduct 0-2 pts) ──────────────────────────────────────
    if dte < 2:
        score -= 2.0
        warnings.append(f"⚠️ DTE {dte}d (theta cliff)")
    elif dte == 2:
        score -= 1.0
        warnings.append(f"⚠️ DTE {dte}d (rapid theta decay)")
    elif dte <= 4:
        warnings.append(f"✅ DTE {dte}d (ideal for intraday)")
    else:
        score -= 0.5
        warnings.append(f"ℹ️ DTE {dte}d (theta slower)")
    
    # ── ADX (deduct 0-2 pts) ──────────────────────────────────────
    if adx_val < 20:
        score -= 2.0
        warnings.append(f"⚠️ ADX {adx_val:.0f} (ranging, theta bleed risk)")
    elif adx_val < 25:
        score -= 1.0
        warnings.append(f"⚠️ ADX {adx_val:.0f} (weak trend)")
    else:
        warnings.append(f"✅ ADX {adx_val:.0f} (trending)")
    
    # ── Premium absolute cost (deduct 0-2 pts) ────────────────────
    if premium > 300:
        score -= 2.0
        warnings.append(f"⚠️ Premium ₹{premium:.0f} (very expensive)")
    elif premium > 250:
        score -= 1.0
        warnings.append(f"⚠️ Premium ₹{premium:.0f} (expensive)")
    elif premium < 150:
        warnings.append(f"✅ Premium ₹{premium:.0f} (affordable)")
    
    # ── Verdict ──────────────────────────────────────────────────
    score = max(0.0, round(score, 1))
    
    if score >= 7.0:
        verdict = "BUY"
        emoji   = "✅"
    elif score >= 5.0:
        verdict = "CAUTION"
        emoji   = "⚠️"
    else:
        verdict = "RISKY"
        emoji   = "🚨"
    
    summary = (
        f"{emoji} {verdict} — Score {score:.1f}/10 | "
        f"HV {hv_val:.0f}%@rank{hv_rank:.0f} | ADX {adx_val:.0f} | "
        f"DTE {dte}d | Prem ₹{premium:.0f}"
    )
    
    return OptionInfo(
        hv_rank=hv_rank,
        hv_current=hv_val,
        dte=dte,
        adx_value=adx_val,
        premium=premium,
        score=score,
        verdict=verdict,
        warnings=warnings,
        summary=summary,
    )

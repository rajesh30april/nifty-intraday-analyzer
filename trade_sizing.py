"""Unified Trade Sizing Engine — Kelly × Expiry × VIX.

Combines three independent risk dimensions into a single lot multiplier:

    final_mult = kelly_mult × expiry_scale × vix_scale

Where:
    kelly_mult:    Based on strategy win rate + payoff ratio (calibration.json)
    expiry_scale:  Based on DTE — reduces size near weekly Thursday expiry
    vix_scale:     Based on India VIX / IV Rank — shrinks when premiums are costly

Each factor is independently bounded to [0, 1.5] and the product is
clamped to a sensible range so you can't accidentally 0× or 10× your lots.

Usage in auto_trader.py:
    from trade_sizing import compute_lot_multiplier

    mult = compute_lot_multiplier(
        strategy_id="candlestick_patterns",
        consecutive_losses=1,
        today_pnl=-800.0,
        max_daily_loss=3000.0,
    )
    lots = max(1, round(base_lots * mult))

Author: Code Puppy 🐶
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from kelly_sizing import get_kelly_result, KellyResult
from expiry_aware import get_expiry_context, ExpiryContext
from vix_monitor import fetch_vix_context, VixContext

logger = logging.getLogger(__name__)

# ── Hard floor/ceiling for combined multiplier ────────────────────────────────
_COMBINED_MIN = 0.20   # Never trade less than 20% of base lots
_COMBINED_MAX = 1.50   # Never trade more than 150% of base lots


@dataclass(frozen=True)
class SizingDecision:
    """Full sizing decision — transparent breakdown for UI display."""
    strategy_id: str

    # Component details
    kelly: KellyResult
    expiry: ExpiryContext
    vix: VixContext

    # Multipliers before combination
    kelly_mult: float
    expiry_scale: float
    vix_scale: float

    # Final decision
    combined_mult: float    # kelly × expiry × vix, clamped
    buy_allowed: bool       # False = expiry hard block

    # Summary
    summary: str


def compute_lot_multiplier(
    strategy_id: str,
    consecutive_losses: int = 0,
    today_pnl: float = 0.0,
    max_daily_loss: float = 3000.0,
    skip_vix_fetch: bool = False,
) -> float:
    """Compute the final lot multiplier for a trade.

    Args:
        strategy_id:        ID from calibration.json / strategy registry.
        consecutive_losses: Current consecutive loss streak.
        today_pnl:          Today's running P&L (negative = loss).
        max_daily_loss:     Hard daily loss limit (positive number).
        skip_vix_fetch:     Set True in backtests to skip live VIX fetch.

    Returns:
        Float multiplier to apply to base lot count.
        Returns 0.0 if expiry hard-blocks new buys.
    """
    decision = compute_sizing_decision(
        strategy_id, consecutive_losses, today_pnl, max_daily_loss, skip_vix_fetch
    )
    return decision.combined_mult


def compute_sizing_decision(
    strategy_id: str,
    consecutive_losses: int = 0,
    today_pnl: float = 0.0,
    max_daily_loss: float = 3000.0,
    skip_vix_fetch: bool = False,
) -> SizingDecision:
    """Full sizing decision with all details.

    Returns:
        SizingDecision with component breakdown + final multiplier.
    """
    # ── 1. Kelly (strategy quality + drawdown state) ──────────────────────────
    kelly_result = get_kelly_result(
        strategy_id, consecutive_losses, today_pnl, max_daily_loss
    )
    k_mult = kelly_result.lot_multiplier

    # ── 2. Expiry (DTE — Theta / Gamma risk) ─────────────────────────────────
    exp_ctx = get_expiry_context()

    # Hard block: no new buys on expiry afternoon
    if not exp_ctx.buy_allowed:
        return SizingDecision(
            strategy_id=strategy_id,
            kelly=kelly_result, expiry=exp_ctx,
            vix=_dummy_vix(),
            kelly_mult=k_mult, expiry_scale=0.0, vix_scale=1.0,
            combined_mult=0.0, buy_allowed=False,
            summary=(
                f"🚫 NO BUY — Expiry hard block ({exp_ctx.warning})"
            ),
        )

    e_scale = exp_ctx.size_scale

    # ── 3. VIX / IV Rank (premium cost) ──────────────────────────────────────
    if skip_vix_fetch:
        vix_ctx   = _dummy_vix()
        v_scale   = 1.0
    else:
        try:
            vix_ctx = fetch_vix_context()
            v_scale = vix_ctx.size_scale
        except Exception as ex:
            logger.warning("VIX fetch failed (%s) — using 1.0x scale", ex)
            vix_ctx = _dummy_vix()
            v_scale = 1.0

    # ── 4. Combine ────────────────────────────────────────────────────────────
    combined = k_mult * e_scale * v_scale
    combined = round(max(_COMBINED_MIN, min(combined, _COMBINED_MAX)), 2)

    summary = (
        f"{strategy_id}: Kelly={k_mult:.2f}x "
        f"× Expiry(DTE={exp_ctx.dte})={e_scale:.2f} "
        f"× VIX(IVR={vix_ctx.iv_rank:.0f}%)={v_scale:.2f} "
        f"→ {combined:.2f}x"
    )
    if exp_ctx.warning:
        summary += f" | {exp_ctx.warning}"

    return SizingDecision(
        strategy_id=strategy_id,
        kelly=kelly_result,
        expiry=exp_ctx,
        vix=vix_ctx,
        kelly_mult=k_mult,
        expiry_scale=e_scale,
        vix_scale=v_scale,
        combined_mult=combined,
        buy_allowed=True,
        summary=summary,
    )


def _dummy_vix() -> VixContext:
    """Neutral VIX context for when live data isn't available."""
    from vix_monitor import VixContext as _VixContext
    return _VixContext(
        vix_current=14.5, vix_52w_high=22.0, vix_52w_low=10.5,
        iv_rank=35.0, iv_percentile=40.0, regime="normal",
        buy_quality="ok", sell_quality="ok", size_scale=1.0,
        guidance="Using default VIX (data unavailable).", data_stale=True,
    )


# ── CLI demo ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    strategies = [
        "candlestick_patterns",
        "chart_patterns",
        "ocf",
        "orb",
        "gap_and_go",
        "supertrend",
        "volume_spike",
        "camarilla",
    ]

    print("\n🐶 Unified Trade Sizing — Today's Multipliers")
    print("=" * 72)
    print(f"  {'Strategy':<25} {'Kelly':>7} {'Expiry':>7} {'VIX':>6} {'FINAL':>7}")
    print("  " + "-" * 60)

    for sid in strategies:
        d = compute_sizing_decision(sid, consecutive_losses=0, skip_vix_fetch=False)
        ba = "✅" if d.buy_allowed else "🚫"
        print(
            f"  {sid:<25} {d.kelly_mult:>6.2f}x "
            f"{d.expiry_scale:>6.2f}x "
            f"{d.vix_scale:>5.2f}x "
            f"{ba} {d.combined_mult:>5.2f}x"
        )

    print("\n  With 2 consecutive losses:")
    print("  " + "-" * 60)
    for sid in ["candlestick_patterns", "supertrend"]:
        d = compute_sizing_decision(sid, consecutive_losses=2, skip_vix_fetch=False)
        ba = "✅" if d.buy_allowed else "🚫"
        print(
            f"  {sid:<25} {d.kelly_mult:>6.2f}x "
            f"{d.expiry_scale:>6.2f}x "
            f"{d.vix_scale:>5.2f}x "
            f"{ba} {d.combined_mult:>5.2f}x"
        )

    print("=" * 72)
    print(f"\n  ℹ️  Expiry warning: {compute_sizing_decision('ocf', skip_vix_fetch=True).expiry.warning or 'None'}")

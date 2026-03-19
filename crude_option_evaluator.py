"""MCX Crude Oil option quality evaluator — Layer 2 gate before entry.

Answers a different question from crude_strategy.py:
  crude_strategy  → "Should I go LONG or SHORT on crude?"
  crude_option_evaluator → "Is THIS option worth buying RIGHT NOW?"

Scoring layers (0–10 composite):
  DTE    — days-to-expiry theta risk         (0-2 pts, hard-block < 3d)
  HV     — historical-vol rank               (0-2 pts)
  ADX    — trend strength                    (0-2 pts)
  Squeeze— BB squeeze release                (0-1 pt)
  Chain  — PCR alignment + OI wall clearance (0-2 pts)
  Bonus  — sweet-spot DTE 7-14d             (+0.5 pt)

Verdict:
  ≥ 7.0  → BUY   (enter)
  5–6.9  → WAIT  (marginal — skip unless all hard conditions met)
  < 5.0  → SKIP  (block trade)
  DTE<3  → SKIP  (hard block, theta cliff)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from strategy import Direction
import indicators as ind

# ── Scoring thresholds ────────────────────────────────────────────
DTE_HARD_MIN   = 3     # below this → hard SKIP (theta cliff)
DTE_SWEET_MIN  = 7     # sweet spot start
DTE_SWEET_MAX  = 21    # sweet spot end
HV_RANK_MAX    = 70    # HV rank above this = IV expensive for buyers
ADX_TREND_MIN  = 22    # below = chop, option bleeds
PCR_BULL_MIN   = 0.9   # PCR >= this for longs (put sellers supporting floor)
PCR_BEAR_MAX   = 1.2   # PCR <= this for shorts
OI_WALL_PCT    = 0.015 # OI wall within 1.5% of price = significant friction


@dataclass
class OptionCondition:
    """Single scored condition in the options quality check."""
    name:      str
    met:       bool
    score:     float      # points earned
    max_score: float      # max possible
    detail:    str


@dataclass
class OptionEvalResult:
    """Full result from evaluate_option_quality()."""
    score:       float                       # 0–10
    max_score:   float
    verdict:     str                         # BUY / WAIT / SKIP
    conditions:  list[OptionCondition]       = field(default_factory=list)
    summary:     str                         = ""
    pcr:         Optional[float]             = None
    oi_wall:     Optional[float]             = None   # nearest OI wall level
    max_pain:    Optional[float]             = None
    dte:         int                         = 0
    hv_rank:     float                       = 0.0    # 0–100
    hv_current:  float                       = 0.0    # annualised %
    adx_value:   float                       = 0.0
    hard_blocked: bool                       = False
    block_reason: str                        = ""

    @property
    def score_of_10(self) -> float:
        """Normalised 0–10 score."""
        return round(self.score / self.max_score * 10, 1) if self.max_score else 0.0


# ── Utility helpers ───────────────────────────────────────────────

def _session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP reset to today's 09:00 IST session open."""
    today = pd.Timestamp.now(tz='Asia/Kolkata').date()
    sess  = df[df.index.date == today]  # type: ignore
    if sess.empty:
        sess = df  # fallback: use all available
    tp      = (sess['high'] + sess['low'] + sess['close']) / 3
    cumvol  = sess['volume'].cumsum().replace(0, 1)
    vwap_s  = (tp * sess['volume']).cumsum() / cumvol
    # Re-index to full df so callers can use .iloc[-1]
    return vwap_s.reindex(df.index, method='ffill')


def _hv_rank(
    close: pd.Series,
    window: int = 20,
    history: int = 252,
) -> tuple[float, float]:
    """Return (hv_annualised_pct, hv_rank_0_to_100).

    HV is computed on 5-min bars → annualise with √(252 sessions × 75 bars/session).
    Rank = percentile of current HV within its own recent history.
    Higher rank = more expensive vol environment for option buyers.
    """
    if len(close) < window + 2:
        return 0.0, 50.0   # neutral default
    log_ret    = np.log(close / close.shift(1)).dropna()
    rolling_hv = log_ret.rolling(window).std() * math.sqrt(252 * 75)
    current_hv = float(rolling_hv.iloc[-1])
    hist        = rolling_hv.dropna().iloc[-history:]
    if hist.empty or hist.std() == 0:
        # All identical values (e.g. flat test data) — rank is undefined;
        # treat as lowest possible (cheapest vol = best for buyers)
        return round(current_hv * 100, 1), 0.0
    rank = float((hist < current_hv).sum()) / len(hist) * 100
    return round(current_hv * 100, 1), round(rank, 1)


def _dte_from_instruments(direction: Direction) -> int:
    """Resolve DTE for the front-month crude option closest expiry.

    Mirrors the logic in get_crude_atm_option() but only returns DTE.
    Returns 999 on failure (so DTE check doesn't hard-block when offline).
    """
    try:
        from crude_data import _get_mcx_instruments, MCX_CRUDE_MINI_NAME, MCX_CRUDE_NAME
        opt_type   = 'CE' if direction == Direction.LONG else 'PE'
        today      = date.today()
        instruments = _get_mcx_instruments()
        opts = [
            i for i in instruments
            if i.get('name') in (MCX_CRUDE_MINI_NAME, MCX_CRUDE_NAME)
            and i.get('instrument_type') == opt_type
            and i.get('expiry') and i['expiry'] >= today
        ]
        if not opts:
            return 999
        opts.sort(key=lambda x: x['expiry'])
        return (opts[0]['expiry'] - today).days
    except Exception:
        return 999


# ── Layer evaluators ──────────────────────────────────────────────

def _eval_dte(direction: Direction) -> OptionCondition:
    dte  = _dte_from_instruments(direction)
    ok   = dte >= DTE_HARD_MIN
    sweet = DTE_SWEET_MIN <= dte <= DTE_SWEET_MAX
    score = 2.0 if (ok and sweet) else (1.0 if ok else 0.0)
    return OptionCondition(
        name="DTE",
        met=ok,
        score=score,
        max_score=2.0,
        detail=(
            f"{dte}d to expiry — {'SWEET SPOT' if sweet else 'OK' if ok else '⛔ THETA CLIFF'}"
            f" (need ≥{DTE_HARD_MIN}d, ideal {DTE_SWEET_MIN}-{DTE_SWEET_MAX}d)"
        ),
    ), dte


def _eval_hv_rank(
    close: pd.Series,
) -> tuple[OptionCondition, float, float]:
    hv, rank = _hv_rank(close)
    ok    = rank < HV_RANK_MAX
    score = 2.0 if rank < 40 else (1.0 if rank < HV_RANK_MAX else 0.0)
    return OptionCondition(
        name="HV Rank",
        met=ok,
        score=score,
        max_score=2.0,
        detail=(
            f"HV {hv:.1f}% @ rank {rank:.0f}th pctile — "
            f"{'cheap vol ✅' if rank < 40 else 'fair vol' if ok else '⚠️ expensive IV'}"
        ),
    ), hv, rank


def _eval_adx(
    high: pd.Series, low: pd.Series, close: pd.Series, direction: Direction,
) -> tuple[OptionCondition, float]:
    adx_df = ind.adx(high, low, close, 14)
    adx_v   = float(adx_df['adx'].iloc[-1])
    plus_di = float(adx_df['plus_di'].iloc[-1])
    minus_di = float(adx_df['minus_di'].iloc[-1])
    trending  = adx_v >= ADX_TREND_MIN
    di_ok = (
        (direction == Direction.LONG  and plus_di > minus_di) or
        (direction == Direction.SHORT and minus_di > plus_di)
    )
    ok    = trending and di_ok
    score = 2.0 if (adx_v >= 30 and di_ok) else (1.5 if (adx_v >= 25 and di_ok) else (1.0 if trending else 0.0))
    return OptionCondition(
        name="ADX Trend",
        met=ok,
        score=score,
        max_score=2.0,
        detail=(
            f"ADX {adx_v:.1f} +DI {plus_di:.1f} -DI {minus_di:.1f} — "
            f"{'STRONG trend ✅' if adx_v >= 30 else 'OK trend' if trending else '⚠️ weak/ranging'}"
            f"{' | DI ✅' if di_ok else ' | ⚠️ DI against direction'}"
        ),
    ), adx_v


def _eval_squeeze(
    high: pd.Series, low: pd.Series, close: pd.Series, direction: Direction,
) -> OptionCondition:
    """TTM Squeeze: prefer entries when squeeze just released.

    Squeeze ON = low vol compression, option premium cheap but can bleed theta.
    Squeeze OFF (just released) = energy explosion — ideal timing for buyers.
    """
    if len(close) < 22:
        return OptionCondition("BB Squeeze", True, 1.0, 1.0, "Insufficient data — neutral")

    sq = ind.bb_squeeze(high, low, close)
    sq_now  = bool(sq['squeeze_on'].iloc[-1])
    sq_prev = bool(sq['squeeze_on'].iloc[-2])
    mom     = float(sq['momentum'].iloc[-1])

    # Released = was ON, now OFF
    released = sq_prev and not sq_now
    # Momentum direction matches trade direction
    mom_ok = (
        (direction == Direction.LONG and mom > 0)
        or (direction == Direction.SHORT and mom < 0)
    )

    if released and mom_ok:
        return OptionCondition("BB Squeeze", True, 1.0, 1.0,
                               f"Squeeze RELEASED 🚀 momentum={'↑' if mom>0 else '↓'} {mom:.2f}")
    if not sq_now and mom_ok:
        return OptionCondition("BB Squeeze", True, 0.5, 1.0,
                               f"No squeeze, momentum aligned {'↑' if mom>0 else '↓'} {mom:.2f}")
    if sq_now:
        return OptionCondition("BB Squeeze", False, 0.0, 1.0,
                               f"Squeeze ACTIVE ⏳ — vol compression, theta risk")
    return OptionCondition("BB Squeeze", False, 0.2, 1.0,
                           f"Squeeze off but momentum {'against' if not mom_ok else 'neutral'} direction")


# ── Options chain: PCR + OI wall + max pain ───────────────────────

def _fetch_option_chain(
    spot: float,
    direction: Direction,
    n_strikes: int = 7,
) -> dict | None:
    """Fetch OI and LTP for n_strikes CE + PE around ATM from Kite.

    Returns dict or None if unauthenticated / API failure.
    Structure: {strike: {ce_oi, pe_oi, ce_ltp, pe_ltp}}
    """
    try:
        from kite_integration import kite_manager
        if not kite_manager.is_authenticated:
            return None
        from crude_data import (
            _get_mcx_instruments, MCX_CRUDE_MINI_NAME, MCX_CRUDE_NAME,
            MCX_CRUDE_STRIKE_STEP,
        )
        today      = date.today()
        instruments = _get_mcx_instruments()
        # Pick front-month expiry matching CRUDEOILM (mini) preferred
        for name in (MCX_CRUDE_MINI_NAME, MCX_CRUDE_NAME):
            opts = [
                i for i in instruments
                if i.get('name') == name
                and i.get('instrument_type') in ('CE', 'PE')
                and i.get('expiry') and i['expiry'] >= today
            ]
            if opts:
                opts.sort(key=lambda x: x['expiry'])
                front_expiry = opts[0]['expiry']
                break
        else:
            return None

        atm    = round(spot / MCX_CRUDE_STRIKE_STEP) * MCX_CRUDE_STRIKE_STEP
        strikes = [
            atm + k * MCX_CRUDE_STRIKE_STEP
            for k in range(-n_strikes, n_strikes + 1)
        ]
        # Build symbol list for both CE and PE
        symbols: list[str] = []
        sym_map: dict[str, tuple[float, str]] = {}  # symbol → (strike, type)
        for i in instruments:
            if (i.get('name') == name
                    and i.get('expiry') == front_expiry
                    and i.get('instrument_type') in ('CE', 'PE')
                    and i.get('strike') in strikes):
                sym = f"MCX:{i['tradingsymbol']}"
                symbols.append(sym)
                sym_map[sym] = (float(i['strike']), i['instrument_type'])

        if not symbols:
            return None

        # Kite quote — returns last_price, oi, volume per symbol
        quotes = kite_manager.kite.quote(symbols)

        chain: dict[float, dict] = {}
        for sym, (strike, opt_type) in sym_map.items():
            q = quotes.get(sym, {})
            if strike not in chain:
                chain[strike] = {'ce_oi': 0, 'pe_oi': 0, 'ce_ltp': 0.0, 'pe_ltp': 0.0}
            oi  = q.get('oi', 0) or 0
            ltp = q.get('last_price', 0.0) or 0.0
            if opt_type == 'CE':
                chain[strike]['ce_oi']  = oi
                chain[strike]['ce_ltp'] = ltp
            else:
                chain[strike]['pe_oi']  = oi
                chain[strike]['pe_ltp'] = ltp
        return chain if chain else None
    except Exception as e:
        print(f"⚠️  Option chain fetch failed: {e}")
        return None


def _max_pain(chain: dict) -> float | None:
    """Compute max pain strike from OI chain.

    Max pain = strike where total option seller loss is MINIMUM
    (i.e., where option buyers lose the most).
    """
    strikes = sorted(chain.keys())
    if not strikes:
        return None
    min_loss  = float('inf')
    max_pain_strike = None
    for settlement in strikes:
        total_loss = 0.0
        for k, v in chain.items():
            ce_loss = max(0.0, settlement - k) * v['ce_oi']
            pe_loss = max(0.0, k - settlement) * v['pe_oi']
            total_loss += ce_loss + pe_loss
        if total_loss < min_loss:
            min_loss   = total_loss
            max_pain_strike = settlement
    return max_pain_strike


def _eval_chain(
    spot: float, direction: Direction, chain: dict,
) -> tuple[list[OptionCondition], float | None, float | None, float | None]:
    """Return (conditions, pcr, oi_wall_level, max_pain) from the chain."""
    conds: list[OptionCondition] = []

    total_ce_oi = sum(v['ce_oi'] for v in chain.values())
    total_pe_oi = sum(v['pe_oi'] for v in chain.values())
    pcr = (total_pe_oi / total_ce_oi) if total_ce_oi > 0 else None

    # PCR alignment
    if pcr is not None:
        pcr_long_ok  = direction == Direction.LONG  and pcr >= PCR_BULL_MIN
        pcr_short_ok = direction == Direction.SHORT and pcr <= PCR_BEAR_MAX
        pcr_ok = pcr_long_ok or pcr_short_ok
        conds.append(OptionCondition(
            name="PCR",
            met=pcr_ok,
            score=1.0 if pcr_ok else 0.0,
            max_score=1.0,
            detail=(
                f"PCR {pcr:.2f} — "
                f"{'bullish support ✅' if pcr >= 1.2 else 'neutral' if pcr_ok else '⚠️ PCR against direction'}"
            ),
        ))
    else:
        conds.append(OptionCondition("PCR", True, 0.5, 1.0, "PCR unavailable — neutral"))

    # OI wall: highest OI strike ABOVE spot for longs (resistance),
    #          highest OI strike BELOW spot for shorts (support)
    strikes = sorted(chain.keys())
    if direction == Direction.LONG:
        above = {k: v for k, v in chain.items() if k > spot}
        wall_strike = max(above, key=lambda k: above[k]['ce_oi']) if above else None
    else:
        below = {k: v for k, v in chain.items() if k < spot}
        wall_strike = max(below, key=lambda k: below[k]['pe_oi']) if below else None

    wall_dist_pct = abs(wall_strike - spot) / spot if wall_strike else 1.0
    wall_clear = wall_dist_pct > OI_WALL_PCT
    conds.append(OptionCondition(
        name="OI Wall",
        met=wall_clear,
        score=1.0 if wall_clear else 0.0,
        max_score=1.0,
        detail=(
            f"Nearest {'resistance' if direction==Direction.LONG else 'support'} OI wall: "
            f"₹{wall_strike:.0f} ({wall_dist_pct*100:.1f}% away) — "
            f"{'clear ✅' if wall_clear else f'⚠️ strong friction < {OI_WALL_PCT*100:.1f}%'}"
        ) if wall_strike else "OI wall not found — neutral",
    ))

    max_pain_lvl = _max_pain(chain)
    return conds, pcr, wall_strike, max_pain_lvl


# ── Master evaluator ──────────────────────────────────────────────

def evaluate_option_quality(
    df: pd.DataFrame,
    direction: Direction,
    spot: float,
) -> OptionEvalResult:
    """Score this option entry opportunity on a 0-10 composite scale.

    Args:
        df:        5-min OHLCV DataFrame (multi-day, indexed by datetime)
        direction: Direction.LONG (buy CE) or Direction.SHORT (buy PE)
        spot:      Current MCX crude spot/futures price

    Returns:
        OptionEvalResult with score, verdict, and full condition breakdown.
    """
    close = df['close']
    high  = df['high']
    low   = df['low']

    conditions: list[OptionCondition] = []
    max_possible = 0.0

    # ── Layer 1: DTE — hard block below 3 days ────────────────────
    dte_cond, dte = _eval_dte(direction)
    conditions.append(dte_cond)
    max_possible += dte_cond.max_score
    if not dte_cond.met:   # theta cliff hard block
        return OptionEvalResult(
            score=0.0, max_score=9.5, verdict="SKIP",
            conditions=conditions,
            summary=f"⛔ SKIP — {dte_cond.detail}",
            dte=dte,
            hard_blocked=True,
            block_reason=dte_cond.detail,
        )

    # ── Layer 2: HV Rank — option buying cost ─────────────────────
    hv_cond, hv, hv_rank = _eval_hv_rank(close)
    conditions.append(hv_cond)
    max_possible += hv_cond.max_score

    # ── Layer 3: ADX trend strength ───────────────────────────────
    adx_cond, adx_v = _eval_adx(high, low, close, direction)
    conditions.append(adx_cond)
    max_possible += adx_cond.max_score

    # ── Layer 4: BB Squeeze ───────────────────────────────────────
    sq_cond = _eval_squeeze(high, low, close, direction)
    conditions.append(sq_cond)
    max_possible += sq_cond.max_score

    # ── Layer 5: Options chain (PCR + OI wall) ────────────────────
    chain    = _fetch_option_chain(spot, direction)
    pcr_val  = None
    oi_wall  = None
    max_pain = None
    if chain:
        chain_conds, pcr_val, oi_wall, max_pain = _eval_chain(spot, direction, chain)
    else:
        # Offline/API fail — give neutral half-score for chain conditions
        chain_conds = [
            OptionCondition("PCR",     True, 0.5, 1.0, "Chain unavailable — neutral"),
            OptionCondition("OI Wall", True, 0.5, 1.0, "Chain unavailable — neutral"),
        ]
    conditions.extend(chain_conds)
    max_possible += sum(c.max_score for c in chain_conds)

    # ── Bonus: DTE sweet spot ─────────────────────────────────────
    max_possible += 0.5
    if DTE_SWEET_MIN <= dte <= DTE_SWEET_MAX:
        conditions.append(OptionCondition(
            "DTE Bonus", True, 0.5, 0.5,
            f"Ideal DTE window ({DTE_SWEET_MIN}-{DTE_SWEET_MAX}d) +0.5 bonus",
        ))
    else:
        conditions.append(OptionCondition(
            "DTE Bonus", False, 0.0, 0.5,
            f"{dte}d outside sweet spot {DTE_SWEET_MIN}-{DTE_SWEET_MAX}d",
        ))

    raw_score   = sum(c.score for c in conditions)
    normalised  = round(raw_score / max_possible * 10, 1) if max_possible else 0.0

    # ── Verdict ───────────────────────────────────────────────────
    hard_fails  = [c for c in conditions if not c.met and c.max_score >= 2.0]
    if normalised >= 7.0 and not hard_fails:
        verdict = "BUY"
    elif normalised >= 5.0:
        verdict = "WAIT"
    else:
        verdict = "SKIP"

    extras = []
    if pcr_val  is not None: extras.append(f"PCR {pcr_val:.2f}")
    if oi_wall  is not None: extras.append(f"OI wall ₹{oi_wall:.0f}")
    if max_pain is not None: extras.append(f"max pain ₹{max_pain:.0f}")
    extras_str = " | ".join(extras)

    emoji   = {"BUY": "✅", "WAIT": "⏳", "SKIP": "⛔"}.get(verdict, "")
    summary = (
        f"{emoji} {verdict} — Score {normalised}/10 "
        f"| HV {hv:.0f}%@rank{hv_rank:.0f} "
        f"| ADX {adx_v:.0f} "
        f"| DTE {dte}d"
        + (f" | {extras_str}" if extras_str else "")
    )

    return OptionEvalResult(
        score=normalised,
        max_score=10.0,
        verdict=verdict,
        conditions=conditions,
        summary=summary,
        pcr=pcr_val,
        oi_wall=oi_wall,
        max_pain=max_pain,
        dte=dte,
        hv_rank=hv_rank,
        hv_current=hv,
        adx_value=adx_v,
    )
"""Pattern Detector Validation Script.

Builds synthetic price data that CLEARLY has each pattern
and checks if the detector fires correctly.
Also runs on pure random noise to measure false positive rate.
"""
import numpy as np
import pandas as pd
from typing import Callable, Optional

# ── colour helpers ─────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


# ══════════════════════════════════════════════════════════════════
# SYNTHETIC DATA BUILDERS
# ══════════════════════════════════════════════════════════════════

def _make_df(closes: list[float], noise: float = 0.5) -> pd.DataFrame:
    """Build OHLCV DataFrame from a list of close prices."""
    rng = np.random.default_rng(42)
    n   = len(closes)
    closes = np.array(closes, dtype=float)
    highs  = closes + abs(rng.normal(noise, noise / 2, n))
    lows   = closes - abs(rng.normal(noise, noise / 2, n))
    opens  = np.roll(closes, 1);  opens[0] = closes[0]
    volume = rng.integers(50_000, 200_000, n).astype(float)
    # Boost last-candle volume (simulates breakout)
    volume[-1] *= 2.0
    idx = pd.date_range("2026-03-26 09:15", periods=n, freq="5min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows,
         "close": closes, "volume": volume},
        index=idx,
    )


def _make_double_top(base=23000.0) -> pd.DataFrame:
    """Two peaks at ~23200, neckline ~22900, then breakdown."""
    c = (
        [base] * 5 +                             # flat start
        list(np.linspace(base, base+200, 8)) +   # rally to peak 1
        list(np.linspace(base+200, base-100, 6)) + # pull to neckline
        list(np.linspace(base-100, base+195, 8)) + # rally to peak 2
        list(np.linspace(base+195, base-120, 8))   # breakdown
    )
    return _make_df(c, noise=8)


def _make_double_bottom(base=23000.0) -> pd.DataFrame:
    c = (
        [base] * 5 +
        list(np.linspace(base, base-200, 8)) +
        list(np.linspace(base-200, base+100, 6)) +
        list(np.linspace(base+100, base-195, 8)) +
        list(np.linspace(base-195, base+120, 8))
    )
    return _make_df(c, noise=8)


def _make_head_and_shoulders(base=23000.0) -> pd.DataFrame:
    c = (
        [base] * 5 +
        list(np.linspace(base, base+150, 6)) +  # left shoulder
        list(np.linspace(base+150, base-50, 5)) +
        list(np.linspace(base-50, base+300, 7)) + # head
        list(np.linspace(base+300, base-50, 6)) +
        list(np.linspace(base-50, base+140, 5)) + # right shoulder
        list(np.linspace(base+140, base-80, 6))   # breakdown
    )
    return _make_df(c, noise=10)


def _make_inverse_hs(base=23000.0) -> pd.DataFrame:
    c = (
        [base] * 5 +
        list(np.linspace(base, base-150, 6)) +
        list(np.linspace(base-150, base+50, 5)) +
        list(np.linspace(base+50, base-300, 7)) +
        list(np.linspace(base-300, base+50, 6)) +
        list(np.linspace(base+50, base-140, 5)) +
        list(np.linspace(base-140, base+80, 6))
    )
    return _make_df(c, noise=10)


def _make_triple_top(base=23000.0) -> pd.DataFrame:
    pk = base + 200
    c = (
        [base] * 4 +
        list(np.linspace(base, pk, 5)) +
        list(np.linspace(pk, base-50, 5)) +
        list(np.linspace(base-50, pk-10, 5)) +
        list(np.linspace(pk-10, base-50, 5)) +
        list(np.linspace(base-50, pk+5, 5)) +
        list(np.linspace(pk+5, base-100, 6))
    )
    return _make_df(c, noise=8)


def _make_triple_bottom(base=23000.0) -> pd.DataFrame:
    tr = base - 200
    c = (
        [base] * 4 +
        list(np.linspace(base, tr, 5)) +
        list(np.linspace(tr, base+50, 5)) +
        list(np.linspace(base+50, tr+10, 5)) +
        list(np.linspace(tr+10, base+50, 5)) +
        list(np.linspace(base+50, tr-5, 5)) +
        list(np.linspace(tr-5, base+100, 6))
    )
    return _make_df(c, noise=8)


def _make_rising_wedge(base=23000.0) -> pd.DataFrame:
    """Proper rising wedge: highs slope +4, lows slope +8 → they converge."""
    n = 20
    highs  = [base + 80 + i * 4 for i in range(n)]   # slow rise
    lows   = [base      + i * 8 for i in range(n)]   # fast rise (lows catching up)
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    idx = pd.date_range("2026-03-26 09:15", periods=n, freq="5min")
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows,
         "close": closes, "volume": [100_000.0] * n},
        index=idx,
    )


def _make_falling_wedge(base=23200.0) -> pd.DataFrame:
    n = 20
    highs  = [base - i * 12 for i in range(n)]
    lows   = [base - i * 8  for i in range(n)]
    lows   = [max(l, h + 5) for l, h in zip(lows, highs)]
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    idx = pd.date_range("2026-03-26 09:15", periods=n, freq="5min")
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows,
         "close": closes, "volume": [100_000.0] * n},
        index=idx,
    )


def _make_sym_triangle(base=23000.0) -> pd.DataFrame:
    n = 20
    highs  = [base + (10 - i * 0.8) for i in range(n)]
    lows   = [base - (10 - i * 0.8) for i in range(n)]
    closes = [base + (1 if i % 2 == 0 else -1) for i in range(n)]
    # Breakout on last candle
    closes[-1] = highs[-1] + 5
    idx = pd.date_range("2026-03-26 09:15", periods=n, freq="5min")
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows,
         "close": closes, "volume": [100_000.0] * (n - 1) + [300_000.0]},
        index=idx,
    )


def _make_channel(base=23000.0, rising=True) -> pd.DataFrame:
    n = 20
    if rising:
        highs  = [base + 80 + i * 10 for i in range(n)]
        lows   = [base       + i * 10 for i in range(n)]
    else:
        highs  = [base + 80 - i * 10 for i in range(n)]
        lows   = [base       - i * 10 for i in range(n)]
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    idx = pd.date_range("2026-03-26 09:15", periods=n, freq="5min")
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows,
         "close": closes, "volume": [100_000.0] * n},
        index=idx,
    )


def _make_noise(base=23000.0, n=35) -> pd.DataFrame:
    rng = np.random.default_rng(99)
    closes = base + rng.normal(0, 15, n).cumsum()
    return _make_df(closes, noise=5)


# ══════════════════════════════════════════════════════════════════
# TEST RUNNER
# ══════════════════════════════════════════════════════════════════

results = []

def run_test(
    label: str,
    detector: Callable,
    df: pd.DataFrame,
    expect_pattern: bool,
    expected_bias: Optional[str] = None,
):
    """Run one detector on one dataset and report pass/fail."""
    try:
        high   = df["high"]
        low    = df["low"]
        close  = df["close"]
        volume = df["volume"]

        # Some detectors need full df, most need high/low/close/volume
        import inspect
        sig = inspect.signature(detector)
        params = list(sig.parameters.keys())
        if "df" in params or "open_p" in params:
            result = detector(df)
        else:
            result = detector(high, low, close, volume)

        found    = result is not None
        passed   = found == expect_pattern
        bias_ok  = (
            expected_bias is None or
            not found or
            result.bias == expected_bias or
            result.bias == "neutral"   # neutral is acceptable for auto-bias
        )
        ok = passed and bias_ok

        conf_str = f"{result.confidence*100:.0f}%" if found else "—"
        bias_str = result.bias if found else "—"
        name_str = result.name if found else "(not detected)"

        icon   = f"{GREEN}✅{RESET}" if ok else f"{RED}❌{RESET}"
        expect = "FIRE" if expect_pattern else "SKIP"
        got    = "FIRE" if found else "SKIP"

        if not bias_ok:
            extra = f"  {YELLOW}⚠ wrong bias: got {bias_str}, expected {expected_bias}{RESET}"
        else:
            extra = ""

        print(f"  {icon} {label:<35} expect={expect} got={got}  conf={conf_str}  ({name_str}){extra}")
        results.append((label, ok))

    except Exception as e:
        print(f"  {RED}💥 {label:<35} CRASHED: {e}{RESET}")
        results.append((label, False))


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from pattern_detector import (
        detect_double_top, detect_double_bottom,
        detect_ascending_triangle, detect_descending_triangle,
        detect_flag, detect_rsi_divergence,
        detect_bullish_engulfing, detect_bearish_engulfing,
        detect_hammer, detect_shooting_star,
    )
    from patterns_advanced import (
        detect_triple_top, detect_triple_bottom,
        detect_head_and_shoulders, detect_inverse_head_and_shoulders,
        detect_rising_wedge, detect_falling_wedge,
        detect_channel, detect_symmetrical_triangle,
    )

    noise_df = _make_noise()

    print(f"\n{BOLD}{CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print(f"{BOLD}{CYAN}  PATTERN DETECTOR VALIDATION  🐶{RESET}")
    print(f"{BOLD}{CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}\n")

    # ── Double Top / Bottom ─────────────────────────────────────────
    print(f"{BOLD}📊 Double Top / Bottom{RESET}")
    run_test("Double Top   → should FIRE",
             detect_double_top,    _make_double_top(),   True,  "bearish")
    run_test("Double Bottom → should FIRE",
             detect_double_bottom, _make_double_bottom(), True, "bullish")
    run_test("Double Top   on noise → NO FIRE",
             detect_double_top,    noise_df,              False)
    run_test("Double Bottom on noise → NO FIRE",
             detect_double_bottom, noise_df,              False)

    # ── Triple Top / Bottom ─────────────────────────────────────────
    print(f"\n{BOLD}🔱 Triple Top / Bottom{RESET}")
    run_test("Triple Top   → should FIRE",
             detect_triple_top,    _make_triple_top(),    True,  "bearish")
    run_test("Triple Bottom → should FIRE",
             detect_triple_bottom, _make_triple_bottom(), True,  "bullish")
    run_test("Triple Top   on noise → NO FIRE",
             detect_triple_top,    noise_df,              False)
    run_test("Triple Bottom on noise → NO FIRE",
             detect_triple_bottom, noise_df,              False)

    # ── Head & Shoulders ────────────────────────────────────────────
    print(f"\n{BOLD}👤 Head & Shoulders{RESET}")
    run_test("H&S          → should FIRE",
             detect_head_and_shoulders,         _make_head_and_shoulders(), True, "bearish")
    run_test("Inverse H&S  → should FIRE",
             detect_inverse_head_and_shoulders, _make_inverse_hs(),         True, "bullish")
    run_test("H&S          on noise → NO FIRE",
             detect_head_and_shoulders,         noise_df,                   False)
    run_test("Inverse H&S  on noise → NO FIRE",
             detect_inverse_head_and_shoulders, noise_df,                   False)

    # ── Wedges ──────────────────────────────────────────────────────
    print(f"\n{BOLD}📐 Wedges{RESET}")
    run_test("Rising Wedge  → should FIRE",
             detect_rising_wedge,  _make_rising_wedge(),  True, "bearish")
    run_test("Falling Wedge → should FIRE",
             detect_falling_wedge, _make_falling_wedge(), True, "bullish")
    run_test("Rising Wedge  on noise → NO FIRE",
             detect_rising_wedge,  noise_df,              False)
    run_test("Falling Wedge on noise → NO FIRE",
             detect_falling_wedge, noise_df,              False)

    # ── Channel ─────────────────────────────────────────────────────
    print(f"\n{BOLD}📊 Channel{RESET}")
    run_test("Rising Channel  → should FIRE",
             detect_channel, _make_channel(rising=True),  True, "bullish")
    run_test("Falling Channel → should FIRE",
             detect_channel, _make_channel(rising=False), True, "bearish")
    run_test("Channel on noise → NO FIRE",
             detect_channel, noise_df,                    False)

    # ── Symmetrical Triangle ────────────────────────────────────────
    print(f"\n{BOLD}🔺 Symmetrical Triangle{RESET}")
    run_test("Sym Triangle  → should FIRE",
             detect_symmetrical_triangle, _make_sym_triangle(), True)
    run_test("Sym Triangle  on noise → NO FIRE",
             detect_symmetrical_triangle, noise_df,             False)

    # ── Summary ─────────────────────────────────────────────────────
    passed = sum(1 for _, ok in results if ok)
    total  = len(results)
    pct    = passed / total * 100

    color  = GREEN if pct >= 80 else YELLOW if pct >= 60 else RED
    print(f"\n{BOLD}{CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print(f"{BOLD}  Result: {color}{passed}/{total} passed ({pct:.0f}%){RESET}")
    if pct == 100:
        print(f"  {GREEN}🎉 Perfect! All patterns working correctly.{RESET}")
    elif pct >= 80:
        print(f"  {YELLOW}⚠️  Mostly good. Check failed cases above.{RESET}")
    else:
        print(f"  {RED}🚨 Issues found! Patterns need fixing.{RESET}")
    print(f"{BOLD}{CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}\n")

"""Backtesting engine for VWAP Breakout strategy on Nifty 50.

Fetches historical data from Yahoo Finance and simulates trades
using the same strategy logic as the live auto-trader.

Data available from Yahoo:
- 5m candles: last 60 days (with period='60d')
- 15m candles: last 60 days
- 1h candles: last 730 days (2 years)
- Daily candles: unlimited

We use 5m candles for accurate intraday backtesting.
"""

import pandas as pd
import numpy as np
from datetime import datetime, time as dt_time, timedelta
from dataclasses import dataclass, field

from data_fetcher import fetch_intraday_data
from strategy import evaluate_vwap_breakout, Direction
from strategy_router import route_strategy
import strategies.loader  # noqa: F401 — ensure all strategies registered
from strategies.registry import get as get_strategy


# ── Configuration ──────────────────────────────────────────────

DEFAULT_SL_POINTS = 30.0
DEFAULT_TRAILING_SL = 15.0
DEFAULT_RR_RATIO = 2.0  # 1:2 risk-reward
ENTRY_START = dt_time(9, 18)   # No trades in first 3 min
EXIT_TIME = dt_time(15, 15)    # Force exit at 3:15 PM
QUANTITY = 50                  # Nifty lot size


@dataclass
class BacktestTrade:
    """A single backtested trade."""
    date: str
    direction: str
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    stop_loss: float
    target: float
    pnl_points: float
    pnl_rupees: float
    exit_reason: str  # 'SL', 'Target', 'Time Exit', 'Trailing SL'
    conditions_met: list[str] = field(default_factory=list)


@dataclass
class BacktestResult:
    """Backtest summary."""
    total_trades: int = 0
    winners: int = 0
    losers: int = 0
    win_rate: float = 0.0
    total_pnl_points: float = 0.0
    total_pnl_rupees: float = 0.0
    max_win: float = 0.0
    max_loss: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    sharpe_approx: float = 0.0
    days_tested: int = 0
    data_source: str = ""
    period: str = ""
    trades: list[BacktestTrade] = field(default_factory=list)
    daily_pnl: dict = field(default_factory=dict)  # date -> pnl


def _fetch_data(data_source: str, interval: str, period: str) -> tuple[pd.DataFrame, str]:
    """Fetch data from the specified source. Returns (df, source_label)."""
    if data_source == "truedata":
        from truedata_fetcher import fetch_historical_data, TrueDataCredentialError
        df = fetch_historical_data(interval=interval, period=period)
        return df, "TrueData"

    elif data_source == "zerodha":
        from kite_integration import kite_manager
        if not kite_manager.is_authenticated:
            raise ValueError("Zerodha not logged in. Please login via the Auto-Trader tab first.")
        # Convert period to days
        period_days = {
            "5d": 5, "30d": 30, "60d": 60, "90d": 60,  # Zerodha max is 60 days
            "6mo": 60, "1y": 60,
        }
        days = period_days.get(period, 30)
        # Convert interval to Zerodha format
        interval_map = {"1m": "minute", "3m": "3minute", "5m": "5minute",
                        "15m": "15minute", "30m": "30minute", "1h": "60minute"}
        kite_interval = interval_map.get(interval, "5minute")
        raw = kite_manager.get_historical_data(interval=kite_interval, days=days)
        if not raw:
            raise ValueError("Zerodha returned no data")
        df = pd.DataFrame(raw)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df.columns = [c.lower() for c in df.columns]
        df = df[[c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]]
        return df, "Zerodha Kite"

    else:  # default: yahoo
        df = fetch_intraday_data(interval=interval, period=period)
        return df, "Yahoo Finance"


def run_backtest(
    period: str = "60d",
    interval: str = "5m",
    sl_points: float = DEFAULT_SL_POINTS,
    trailing_sl: float = DEFAULT_TRAILING_SL,
    rr_ratio: float = DEFAULT_RR_RATIO,
    max_trades_per_day: int = 3,
    use_router: bool = True,
    strategy_id: str = "smart_router",
    data_source: str = "yahoo",
) -> BacktestResult:
    """Run backtest on historical data.

    Args:
        period: Lookback period ('5d','30d','60d','6mo','1y','2y','5y').
        interval: Candle interval ('5m', '15m').
        sl_points: Stop-loss in points.
        trailing_sl: Trailing SL in points.
        rr_ratio: Risk-reward ratio.
        max_trades_per_day: Max trades allowed per day.
        data_source: 'yahoo' | 'zerodha' | 'truedata'

    Returns:
        BacktestResult with all trades and stats.
    """
    print(f"\n🔬 Fetching {period} of {interval} data from {data_source}...")
    df, source_label = _fetch_data(data_source, interval, period)

    if df is None or df.empty:
        raise ValueError("No data fetched for backtesting")

    print(f"✅ Got {len(df)} candles from {df.index[0]} to {df.index[-1]}")

    # Group by trading day
    trading_days = df.groupby(df.index.date)
    result = BacktestResult(
        data_source=source_label,
        period=period,
        days_tested=len(trading_days),
    )

    for day, day_df in trading_days:
        _backtest_day(
            day_df, str(day), result,
            full_df=df,
            sl_points=sl_points,
            trailing_sl=trailing_sl,
            rr_ratio=rr_ratio,
            max_trades=max_trades_per_day,
            use_router=use_router,
            strategy_id=strategy_id,
        )

    # Calculate summary stats
    _calculate_stats(result)
    return result


def _backtest_day(
    df: pd.DataFrame,
    date_str: str,
    result: BacktestResult,
    full_df: pd.DataFrame,
    sl_points: float,
    trailing_sl: float,
    rr_ratio: float,
    max_trades: int,
    use_router: bool = True,
    strategy_id: str = "smart_router",
):
    """Backtest a single trading day."""
    trades_today = 0
    in_trade = False
    entry_price = 0.0
    stop_loss = 0.0
    target = 0.0
    direction = None
    entry_time = ""
    highest = 0.0
    lowest = float("inf")
    conditions_met = []

    # Walk through each candle — start from 1 (skip the open candle itself)
    # Signal evaluation uses full_df lookback so no warmup skip needed here
    for i in range(1, len(df)):
        candle_time = df.index[i].time()
        candle = df.iloc[i]
        price = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])

        # Skip if before entry start
        if candle_time < ENTRY_START:
            continue

        # Force exit at EXIT_TIME
        if candle_time >= EXIT_TIME and in_trade:
            pnl_pts = _calc_pnl(direction, entry_price, price)
            trade = _make_trade(
                date_str, direction, entry_time,
                df.index[i].strftime("%H:%M"),
                entry_price, price, stop_loss, target,
                pnl_pts, "Time Exit", conditions_met,
            )
            result.trades.append(trade)
            in_trade = False
            continue

        if candle_time >= EXIT_TIME:
            continue

        # If in trade — manage SL / target
        if in_trade:
            # Trailing SL
            if direction == "long":
                highest = max(highest, high)
                new_sl = highest - trailing_sl
                if new_sl > stop_loss:
                    stop_loss = new_sl
                # Check SL hit
                if low <= stop_loss:
                    exit_price = stop_loss
                    pnl_pts = _calc_pnl(direction, entry_price, exit_price)
                    reason = "Trailing SL" if new_sl > entry_price - sl_points else "SL"
                    trade = _make_trade(
                        date_str, direction, entry_time,
                        df.index[i].strftime("%H:%M"),
                        entry_price, exit_price, stop_loss, target,
                        pnl_pts, reason, conditions_met,
                    )
                    result.trades.append(trade)
                    in_trade = False
                    continue
                # Check target hit
                if high >= target:
                    pnl_pts = _calc_pnl(direction, entry_price, target)
                    trade = _make_trade(
                        date_str, direction, entry_time,
                        df.index[i].strftime("%H:%M"),
                        entry_price, target, stop_loss, target,
                        pnl_pts, "Target", conditions_met,
                    )
                    result.trades.append(trade)
                    in_trade = False
                    continue
            else:  # short
                lowest = min(lowest, low)
                new_sl = lowest + trailing_sl
                if new_sl < stop_loss:
                    stop_loss = new_sl
                if high >= stop_loss:
                    exit_price = stop_loss
                    pnl_pts = _calc_pnl(direction, entry_price, exit_price)
                    reason = "Trailing SL" if new_sl < entry_price + sl_points else "SL"
                    trade = _make_trade(
                        date_str, direction, entry_time,
                        df.index[i].strftime("%H:%M"),
                        entry_price, exit_price, stop_loss, target,
                        pnl_pts, reason, conditions_met,
                    )
                    result.trades.append(trade)
                    in_trade = False
                    continue
                if low <= target:
                    pnl_pts = _calc_pnl(direction, entry_price, target)
                    trade = _make_trade(
                        date_str, direction, entry_time,
                        df.index[i].strftime("%H:%M"),
                        entry_price, target, stop_loss, target,
                        pnl_pts, "Target", conditions_met,
                    )
                    result.trades.append(trade)
                    in_trade = False
                    continue
            continue

        # Not in trade — evaluate strategy for entry
        if trades_today >= max_trades:
            continue

        # Use full_df up to current candle's timestamp for prev day context
        current_ts = df.index[i]
        lookback_df = full_df[full_df.index <= current_ts]

        # Use specified strategy from registry
        strat_info = get_strategy(strategy_id)
        if strat_info:
            signal = strat_info.evaluate(lookback_df)
        elif use_router:
            router_result = route_strategy(lookback_df)
            signal = router_result.signal
        else:
            signal = evaluate_vwap_breakout(lookback_df)

        if not signal.should_enter or signal.direction is None:
            continue

        # Entry!
        in_trade = True
        trades_today += 1
        direction = signal.direction.value
        entry_price = price
        entry_time = df.index[i].strftime("%H:%M")
        highest = high
        lowest = low
        conditions_met = [c.name for c in signal.conditions if c.met]

        if direction == "long":
            stop_loss = entry_price - sl_points
            target = entry_price + (sl_points * rr_ratio)
        else:
            stop_loss = entry_price + sl_points
            target = entry_price - (sl_points * rr_ratio)

    # If still in trade at end of day (shouldn't happen with EXIT_TIME)
    if in_trade:
        last_price = float(df["close"].iloc[-1])
        pnl_pts = _calc_pnl(direction, entry_price, last_price)
        trade = _make_trade(
            date_str, direction, entry_time,
            df.index[-1].strftime("%H:%M"),
            entry_price, last_price, stop_loss, target,
            pnl_pts, "Day End", conditions_met,
        )
        result.trades.append(trade)


def _calc_pnl(direction: str, entry: float, exit_val: float) -> float:
    """Calculate P&L in points."""
    if direction == "long":
        return round(exit_val - entry, 2)
    return round(entry - exit_val, 2)


def _make_trade(
    date: str, direction: str, entry_time: str, exit_time: str,
    entry_price: float, exit_price: float, sl: float, target: float,
    pnl_pts: float, reason: str, conditions: list[str],
) -> BacktestTrade:
    return BacktestTrade(
        date=date,
        direction=direction,
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=round(entry_price, 2),
        exit_price=round(exit_price, 2),
        stop_loss=round(sl, 2),
        target=round(target, 2),
        pnl_points=pnl_pts,
        pnl_rupees=round(pnl_pts * QUANTITY, 2),
        exit_reason=reason,
        conditions_met=conditions,
    )


def _calculate_stats(result: BacktestResult):
    """Calculate summary statistics."""
    if not result.trades:
        return

    pnls = [t.pnl_points for t in result.trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    result.total_trades = len(pnls)
    result.winners = len(wins)
    result.losers = len(losses)
    result.win_rate = round(len(wins) / len(pnls) * 100, 1) if pnls else 0
    result.total_pnl_points = round(sum(pnls), 2)
    result.total_pnl_rupees = round(sum(pnls) * QUANTITY, 2)
    result.max_win = round(max(wins), 2) if wins else 0
    result.max_loss = round(min(losses), 2) if losses else 0
    result.avg_win = round(sum(wins) / len(wins), 2) if wins else 0
    result.avg_loss = round(sum(losses) / len(losses), 2) if losses else 0

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0.01
    result.profit_factor = round(gross_profit / gross_loss, 2)

    # Max drawdown
    cumulative = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumulative)
    drawdown = peak - cumulative
    result.max_drawdown = round(float(np.max(drawdown)), 2) if len(drawdown) else 0

    # Approximate Sharpe (daily)
    if len(pnls) > 1:
        result.sharpe_approx = round(
            float(np.mean(pnls) / np.std(pnls) * np.sqrt(252)), 2
        )

    # Daily P&L breakdown
    for t in result.trades:
        result.daily_pnl[t.date] = result.daily_pnl.get(t.date, 0) + t.pnl_points
    result.daily_pnl = {
        k: round(v, 2) for k, v in sorted(result.daily_pnl.items())
    }


# ── Day Replay ────────────────────────────────────────────────

@dataclass
class ReplayFrame:
    """State of a single candle during day replay."""
    time: str
    open: float
    high: float
    low: float
    close: float
    # Strategy evaluation
    strategy_name: str
    strategy_emoji: str
    signal_fires: bool
    direction: str          # 'long' | 'short' | ''
    confidence: float
    regime: str
    # Trade state
    trade_state: str        # 'idle' | 'entry' | 'in_trade' | 'exit'
    idle_reason: str        # 'max_trades' | 'no_signal' | 'time_filter' | 'in_trade' | ''
    entry_price: float
    stop_loss: float
    target: float
    unrealized_pts: float
    cumul_pts: float
    exit_reason: str


def replay_day(
    date_str: str,
    period: str = "60d",
    strategy_id: str = "smart_router",
    sl_points: float = DEFAULT_SL_POINTS,
    trailing_sl: float = DEFAULT_TRAILING_SL,
    rr_ratio: float = DEFAULT_RR_RATIO,
    max_trades: int = 3,
    data_source: str = "yahoo",
) -> dict:
    """Replay a single trading day candle-by-candle.

    Returns a dict with:
        frames: list[ReplayFrame]  — one per 5-min candle
        trades: list[BacktestTrade]
        summary: dict
        available_dates: list[str]
    """
    full_df, source_label = _fetch_data(data_source, "5m", period)
    full_df.index = pd.DatetimeIndex(full_df.index)

    # All available trading dates
    available_dates = sorted({str(ts.date()) for ts in full_df.index})

    target_date = pd.Timestamp(date_str).date()
    day_df = full_df[full_df.index.date == target_date]

    if day_df.empty:
        return {
            "error": f"No data found for {date_str}. Available: {available_dates[-5:]}",
            "available_dates": available_dates,
        }

    frames: list[ReplayFrame] = []
    trades: list[BacktestTrade] = []
    cumul_pts = 0.0

    # Trade tracking
    in_trade = False
    entry_price = 0.0
    stop_loss_lvl = 0.0
    target_lvl = 0.0
    direction = ""
    entry_time = ""
    highest = 0.0
    lowest = float("inf")
    trades_today = 0
    conditions_met: list[str] = []

    for i in range(len(day_df)):
        candle_time = day_df.index[i].time()
        candle = day_df.iloc[i]
        price = float(candle["close"])
        high  = float(candle["high"])
        low   = float(candle["low"])
        open_ = float(candle["open"])

        time_str  = day_df.index[i].strftime("%H:%M")
        trade_state = "idle"
        exit_reason = ""
        unrealized  = 0.0

        # ── Evaluate strategy for this candle ─────────────────
        current_ts  = day_df.index[i]
        lookback_df = full_df[full_df.index <= current_ts]

        strat_info = get_strategy(strategy_id)
        regime_str = "unknown"
        strat_name = strategy_id
        strat_emoji = "🧠"
        confidence  = 0.0
        sig_fires   = False
        sig_dir     = ""

        try:
            if strat_info:
                sig = strat_info.evaluate(lookback_df)
                strat_name  = strat_info.name
                strat_emoji = strat_info.emoji
                confidence  = sig.confidence
                sig_fires   = sig.should_enter
                sig_dir     = sig.direction.value if sig.direction else ""

            # Regime from meta router (best effort)
            if strategy_id == "smart_router":
                from strategy_meta_router import evaluate_all
                meta = evaluate_all(lookback_df)
                regime_str  = meta.regime
                strat_name  = meta.selected_strategy or strat_name
                strat_emoji = meta.selected_emoji or strat_emoji
                confidence  = meta.signal.confidence
                sig_fires   = meta.signal.should_enter
                sig_dir     = meta.signal.direction.value if meta.signal.direction else ""
            else:
                from market_regime import detect_regime
                regime_str = detect_regime(lookback_df).value
        except Exception:
            pass

        # ── Trade management ──────────────────────────────────
        if in_trade:
            trade_state = "in_trade"

            if direction == "long":
                highest = max(highest, high)
                new_sl  = highest - trailing_sl
                if new_sl > stop_loss_lvl:
                    stop_loss_lvl = new_sl
                unrealized = price - entry_price

                exited = False
                if candle_time >= EXIT_TIME:
                    exit_p = price; rsn = "Time Exit"; exited = True
                elif low <= stop_loss_lvl:
                    exit_p = stop_loss_lvl
                    rsn = "Trailing SL" if stop_loss_lvl > entry_price - sl_points else "SL"
                    exited = True
                elif high >= target_lvl:
                    exit_p = target_lvl; rsn = "Target"; exited = True

                if exited:
                    pnl = round(exit_p - entry_price, 2)
                    trades.append(_make_trade(
                        date_str, direction, entry_time, time_str,
                        entry_price, exit_p, stop_loss_lvl, target_lvl,
                        pnl, rsn, conditions_met,
                    ))
                    cumul_pts  += pnl
                    unrealized  = pnl
                    trade_state = "exit"
                    exit_reason = rsn
                    in_trade    = False

            else:  # short
                lowest  = min(lowest, low)
                new_sl  = lowest + trailing_sl
                if new_sl < stop_loss_lvl:
                    stop_loss_lvl = new_sl
                unrealized = entry_price - price

                exited = False
                if candle_time >= EXIT_TIME:
                    exit_p = price; rsn = "Time Exit"; exited = True
                elif high >= stop_loss_lvl:
                    exit_p = stop_loss_lvl
                    rsn = "Trailing SL" if stop_loss_lvl < entry_price + sl_points else "SL"
                    exited = True
                elif low <= target_lvl:
                    exit_p = target_lvl; rsn = "Target"; exited = True

                if exited:
                    pnl = round(entry_price - exit_p, 2)
                    trades.append(_make_trade(
                        date_str, direction, entry_time, time_str,
                        entry_price, exit_p, stop_loss_lvl, target_lvl,
                        pnl, rsn, conditions_met,
                    ))
                    cumul_pts  += pnl
                    unrealized  = pnl
                    trade_state = "exit"
                    exit_reason = rsn
                    in_trade    = False

        # ── Entry check ───────────────────────────────────────
        elif (
            not in_trade
            and trades_today < max_trades
            and candle_time >= ENTRY_START
            and candle_time < EXIT_TIME
            and sig_fires and sig_dir
        ):
            in_trade      = True
            trade_state   = "entry"
            trades_today += 1
            direction     = sig_dir
            entry_price   = price
            entry_time    = time_str
            highest       = high
            lowest        = low
            conditions_met = []
            unrealized    = 0.0

            if direction == "long":
                stop_loss_lvl = entry_price - sl_points
                target_lvl    = entry_price + sl_points * rr_ratio
            else:
                stop_loss_lvl = entry_price + sl_points
                target_lvl    = entry_price - sl_points * rr_ratio

        # ── Classify why we're idle (transparent UX) ─────────
        if trade_state == "idle":
            if in_trade:
                idle_reason = "in_trade"          # shouldn't happen, guard
            elif trades_today >= max_trades:
                idle_reason = "max_trades"
            elif candle_time < ENTRY_START or candle_time >= EXIT_TIME:
                idle_reason = "time_filter"
            elif not sig_fires:
                idle_reason = "no_signal"
            else:
                idle_reason = "no_signal"          # signal fired but something blocked
        else:
            idle_reason = ""

        frames.append(ReplayFrame(
            time=time_str,
            open=round(open_, 2),
            high=round(high, 2),
            low=round(low, 2),
            close=round(price, 2),
            strategy_name=strat_name,
            strategy_emoji=strat_emoji,
            signal_fires=sig_fires,
            direction=sig_dir,
            confidence=round(confidence, 1),
            regime=regime_str,
            trade_state=trade_state,
            idle_reason=idle_reason,
            entry_price=round(entry_price, 2) if in_trade or trade_state in ("entry", "exit") else 0.0,
            stop_loss=round(stop_loss_lvl, 2) if in_trade or trade_state in ("entry", "exit") else 0.0,
            target=round(target_lvl, 2) if in_trade or trade_state in ("entry", "exit") else 0.0,
            unrealized_pts=round(unrealized, 2),
            cumul_pts=round(cumul_pts, 2),
            exit_reason=exit_reason,
        ))

    # Summary
    wins   = [t.pnl_points for t in trades if t.pnl_points > 0]
    losses = [t.pnl_points for t in trades if t.pnl_points <= 0]
    return {
        "date": date_str,
        "available_dates": available_dates,
        "frames": [vars(f) for f in frames],
        "trades": [vars(t) if not hasattr(t, '__dataclass_fields__') else
                   {k: getattr(t, k) for k in t.__dataclass_fields__} for t in trades],
        "summary": {
            "total_trades": len(trades),
            "winners": len(wins),
            "losers": len(losses),
            "total_pnl_pts": round(sum(t.pnl_points for t in trades), 2),
            "total_pnl_rupees": round(sum(t.pnl_points for t in trades) * QUANTITY, 2),
        },
        "source": source_label,
    }


def print_backtest_report(result: BacktestResult):
    """Print a clean backtest report to console."""
    print("\n" + "=" * 60)
    print("BACKTEST REPORT - VWAP Breakout Strategy")
    print("=" * 60)
    print(f"Data: {result.data_source} | Period: {result.period}")
    print(f"Days tested: {result.days_tested}")
    print(f"Lot size: {QUANTITY} (Nifty)")
    print("-" * 60)

    print(f"\nPerformance Summary:")
    print(f"  Total trades: {result.total_trades}")
    print(f"  Winners: {result.winners} | Losers: {result.losers}")
    print(f"  Win rate: {result.win_rate}%")
    print(f"  Total P&L: {result.total_pnl_points:+.1f} pts (Rs {result.total_pnl_rupees:+,.0f})")
    print(f"  Profit factor: {result.profit_factor}")
    print(f"  Max win: +{result.max_win} pts | Max loss: {result.max_loss} pts")
    print(f"  Avg win: +{result.avg_win} pts | Avg loss: {result.avg_loss} pts")
    print(f"  Max drawdown: {result.max_drawdown} pts")
    print(f"  Sharpe (approx): {result.sharpe_approx}")

    print(f"\nDaily P&L:")
    for date, pnl in result.daily_pnl.items():
        marker = " WIN" if pnl >= 0 else "LOSS"
        print(f"  [{marker}] {date}: {pnl:+.1f} pts (Rs {pnl * QUANTITY:+,.0f})")

    print(f"\nTrade Log:")
    for t in result.trades:
        marker = "W" if t.pnl_points >= 0 else "L"
        print(
            f"  [{marker}] {t.date} {t.entry_time}-{t.exit_time} "
            f"{t.direction.upper():5s} "
            f"{t.entry_price} -> {t.exit_price} "
            f"({t.exit_reason:12s}) "
            f"{t.pnl_points:+.1f} pts Rs{t.pnl_rupees:+,.0f}"
        )

    print("=" * 60)

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

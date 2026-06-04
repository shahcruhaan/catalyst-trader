"""
src/backtester.py — Historical Backtesting Engine

Simulates the AI Catalyst Momentum Breakout strategy on historical data.

Usage (from command line):
    python main.py backtest --start 2024-01-01 --end 2024-12-31
    python main.py backtest --start 2023-01-01 --end 2024-12-31 --tickers NVDA,AMD,MSFT
    python main.py backtest --start 2024-01-01 --end 2024-12-31 --skip-catalyst

IMPORTANT LIMITATIONS — READ BEFORE INTERPRETING RESULTS:

  1. CATALYST DATA (most important)
     This strategy requires a tradeable catalyst (score >= 8) for every entry.
     The backtester reads data/catalyst_overrides.csv and only uses entries
     where the 'date' field falls within the backtest window and is still
     fresh (<= 10 days old at signal time).

     Without dated historical catalysts, the backtest will produce 0 trades.

     Two options:
       a. Add past catalyst entries to data/catalyst_overrides.csv with
          historical dates (e.g. date=2024-03-15 for an earnings beat then).
       b. Run with --skip-catalyst to test the technical conditions only.
          This tells you how often the 6 technical conditions aligned, but
          does not reflect real tradeable setups (which all required catalysts).

  2. SURVIVORSHIP BIAS
     The universe you backtest matters enormously. If you use today's large-cap
     winners (NVDA, MSFT, AAPL), you are looking at stocks that survived and
     thrived. Real trading in 2020-2024 would have included stocks that later
     declined or were delisted. To reduce this bias, use a period-appropriate
     universe rather than today's winners.

  3. EARLY CLOSINGS
     The market calendar does not model early-close days (Black Friday, Dec 24).
     Signals generated on these days use data that was available, and entries
     execute at the next open — so the error is small in practice.

  4. EXECUTION ASSUMPTIONS
     - Entry at next day's open + 10bps slippage (default)
     - Exit at next day's open after exit signal - 10bps slippage
     - Transaction cost: 5bps on both entry and exit
     - Fractional shares allowed (unrealistic for some brokers)
     - No partial position sizing based on intraday volatility

Backtest flow per trading day T:
  1. Execute pending buy orders at T's open
  2. Execute pending exit orders at T's open
  3. Mark all positions to T's close, update peak prices
  4. Record equity curve value
  5. Check exit conditions using data up to T
  6. Generate new buy signals using data up to T
  7. Queue any new actions for T+1

Lookahead bias protection:
  - All indicator calculations use df[df.index.date <= T] slices
  - Entry executes at T+1 open (never at signal-day close or open)
  - Exit executes at T+1 open after the signal fires on T
  - Catalyst availability checked with strict date <= signal_date logic
"""

import csv
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import mean, stdev
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

from src.indicators import calculate_all_indicators
from src.market_calendar import is_trading_day
from src.utils import setup_logger, ensure_dir

logger = setup_logger(__name__)

_DEFAULT_RESULTS_PATH = "reports/backtest_results.csv"
_DEFAULT_EQUITY_PATH = "reports/backtest_equity_curve.csv"


# ============================================================
# Data structures
# ============================================================

@dataclass
class BacktestTrade:
    """One completed trade (entry + exit)."""
    ticker: str
    entry_date: date
    exit_date: date
    entry_price: float       # effective entry price after slippage
    exit_price: float        # effective exit price after slippage
    shares: float
    notional: float          # dollars invested at entry
    gross_pnl: float         # pnl before fees
    net_pnl: float           # pnl after all fees
    pnl_pct: float           # net_pnl / notional (as decimal, e.g. 0.12 = +12%)
    holding_days: int
    exit_reason: str
    entry_fee: float
    exit_fee: float


@dataclass
class BacktestPosition:
    """A currently-open position during the simulation."""
    ticker: str
    entry_date: date
    entry_price: float       # effective entry after slippage
    shares: float
    notional: float          # dollars spent at entry (excluding fees)
    entry_fee: float
    peak_price: float
    holding_days: int = 0


@dataclass
class PendingOrder:
    """A buy order queued for execution at the next trading day's open."""
    ticker: str
    signal_date: date
    notional_estimate: float   # dollar amount to spend (recalculated at execution)


@dataclass
class BacktestResult:
    """Complete results from one backtest run."""
    trades: List[BacktestTrade]
    equity_curve: List[float]   # portfolio value per trading day
    equity_dates: List[date]
    metrics: dict
    initial_cash: float
    final_value: float
    start_date: date
    end_date: date
    universe: List[str]
    spy_return: float
    skip_catalyst: bool
    warnings: List[str]


# ============================================================
# Historical data download
# ============================================================

def download_backtest_data(
    tickers: List[str],
    start_date: date,
    end_date: date,
    warmup_days: int = 100,
) -> Dict[str, pd.DataFrame]:
    """
    Download OHLCV data for a specific date range for backtesting.

    Downloads from (start_date - warmup_days) to end_date so that
    indicators requiring 50-90 days of history work correctly at the
    start of the backtest period.

    Args:
        tickers:     Stock symbols to download
        start_date:  First day of the backtest period
        end_date:    Last day of the backtest period
        warmup_days: Calendar days before start_date to download (default 100)

    Returns:
        Dict mapping uppercase ticker -> cleaned OHLCV DataFrame.
        Always includes "SPY". Tickers with no data are omitted.
    """
    fetch_start = start_date - timedelta(days=warmup_days)
    fetch_end = end_date + timedelta(days=1)  # yfinance end is exclusive

    clean = [t.strip().upper() for t in tickers if t and str(t).strip()]
    if not clean:
        return {}

    result: Dict[str, pd.DataFrame] = {}

    # SPY first — required for all signal calculations (retry up to 3x)
    spy_df = None
    for attempt in range(3):
        spy_df = _download_range("SPY", fetch_start, fetch_end)
        if spy_df is not None:
            break
        if attempt < 2:
            import time
            logger.info(f"SPY download failed, retrying in 10s... (attempt {attempt + 1}/3)")
            time.sleep(10)
    if spy_df is None:
        logger.error("Could not download SPY data.")
        return {}
    result["SPY"] = spy_df

    logger.info(f"Downloading backtest data: {fetch_start} to {end_date}")
    logger.info(f"  Tickers: {len(clean)} + SPY  |  Warmup: {warmup_days} calendar days")

    for i, ticker in enumerate(clean, 1):
        df = _download_range(ticker, fetch_start, fetch_end)
        if df is not None:
            result[ticker] = df
        if i % 10 == 0 or i == len(clean):
            logger.info(f"  [{i}/{len(clean)}] downloaded...")

    loaded = len(result) - 1  # exclude SPY from count
    logger.info(f"  {loaded}/{len(clean)} tickers loaded successfully")
    return result


def _download_range(ticker: str, start: date, end: date) -> Optional[pd.DataFrame]:
    """Download OHLCV data for one ticker over a specific date range."""
    try:
        raw = yf.download(
            tickers=ticker,
            start=str(start),
            end=str(end),
            auto_adjust=True,
            progress=False,
            actions=False,
        )
    except Exception as e:
        logger.debug(f"Download failed for {ticker}: {e}")
        return None

    if raw is None or raw.empty:
        return None

    # Flatten MultiIndex columns (some yfinance versions return these)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [col[0] if isinstance(col, tuple) else col for col in raw.columns]

    raw.columns = [str(c).strip().capitalize() for c in raw.columns]

    standard_cols = ["Open", "High", "Low", "Close", "Volume"]
    available = [c for c in standard_cols if c in raw.columns]
    if "Close" not in available:
        return None

    df = raw[available].copy()
    df = df.dropna(how="all")
    df = df[df["Close"] > 0]

    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index)
        except Exception:
            return None

    df = df.sort_index()
    return df if len(df) >= 5 else None


def _slice_up_to(df: pd.DataFrame, signal_date: date) -> pd.DataFrame:
    """
    Return a DataFrame slice containing only rows up to and including signal_date.

    This is the primary lookahead-bias protection. By passing df.loc[:signal_date]
    to all indicator functions, we guarantee that no future data is used.
    """
    if df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return df.iloc[:0]  # return empty frame with same columns
    mask = df.index.normalize() <= pd.Timestamp(signal_date)
    return df.loc[mask]


# ============================================================
# Catalyst filtering (backtest-aware)
# ============================================================

def is_catalyst_available_on_date(
    catalysts: Dict[str, List[dict]],
    ticker: str,
    signal_date: date,
    config,
) -> Tuple[bool, str, int]:
    """
    Check whether a tradeable catalyst is available for ticker on signal_date.

    Rules (prevents lookahead bias):
      - catalyst.date <= signal_date   (catalyst was known at signal time)
      - signal_date - catalyst.date <= max_age_days  (catalyst still fresh)
      - catalyst.catalyst_score >= min_catalyst_score
      - catalyst.tradeable == True

    Special case: catalysts with no 'date' field are treated as available
    throughout the entire backtest period. This is the user's explicit choice
    (they added a catalyst without dating it). Use dated entries for accuracy.

    Args:
        catalysts:   Output of load_catalysts()
        ticker:      Stock symbol
        signal_date: The day we are evaluating signals
        config:      AppConfig (for min_catalyst_score, catalyst_max_age_days)

    Returns:
        (is_available, headline, score)
    """
    entries = catalysts.get(ticker.upper(), [])
    min_score = getattr(config, "min_catalyst_score", 8)
    max_age = getattr(config, "catalyst_max_age_days", 10)

    for entry in entries:
        if not entry.get("tradeable", False):
            continue
        score = int(entry.get("catalyst_score", 0) or 0)
        if score < min_score:
            continue

        cat_date_str = str(entry.get("date", "")).strip()

        if not cat_date_str or cat_date_str in ("", "nan", "None"):
            # Undated catalyst — available throughout
            return True, entry.get("headline", ""), score

        try:
            cat_date = datetime.strptime(cat_date_str[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue

        # Lookahead bias guard
        if cat_date > signal_date:
            continue

        # Freshness check
        if (signal_date - cat_date).days > max_age:
            continue

        return True, entry.get("headline", ""), score

    return False, "", 0


# ============================================================
# Per-day signal and exit logic
# ============================================================

def _get_buy_signal(
    ticker: str,
    ticker_df: pd.DataFrame,
    spy_df: pd.DataFrame,
    signal_date: date,
    config,
    catalysts: Dict,
    skip_catalyst: bool,
) -> Optional[dict]:
    """
    Evaluate whether ticker has a valid buy signal on signal_date.

    Uses only price data available on or before signal_date.
    Returns a signal dict on success, or None if conditions not met.
    """
    stock_slice = _slice_up_to(ticker_df, signal_date)
    spy_slice = _slice_up_to(spy_df, signal_date)

    if stock_slice.empty or len(stock_slice) < 55:
        return None
    if spy_slice.empty:
        return None

    indicators = calculate_all_indicators(stock_slice, spy_slice, config)

    if not indicators.get("enough_data"):
        return None
    if indicators.get("error"):
        return None

    min_vol_ratio = getattr(config, "min_volume_ratio", 2.5)
    min_20d_return = getattr(config, "min_20d_return", 0.10)
    min_rs_pct = getattr(config, "min_rs_pct", 0.08)

    passes = (
        indicators.get("above_20d_high", False)
        and indicators.get("above_50d_ma", False)
        and (indicators.get("return_20d") or 0.0) >= min_20d_return
        and (indicators.get("volume_ratio") or 0.0) >= min_vol_ratio
        and (indicators.get("relative_strength_20d") or 0.0) >= min_rs_pct
    )

    if not passes:
        return None

    if skip_catalyst:
        cat_headline = "(--skip-catalyst)"
        cat_score = 0
    else:
        ok, cat_headline, cat_score = is_catalyst_available_on_date(
            catalysts, ticker, signal_date, config
        )
        if not ok:
            return None

    return {
        "ticker": ticker,
        "signal_date": signal_date,
        "close": indicators.get("close"),
        "volume_ratio": indicators.get("volume_ratio"),
        "return_20d": indicators.get("return_20d"),
        "rs_20d": indicators.get("relative_strength_20d"),
        "catalyst_headline": cat_headline,
        "catalyst_score": cat_score,
    }


def _check_exit_signal(
    pos: BacktestPosition,
    ticker_df: pd.DataFrame,
    spy_df: pd.DataFrame,
    current_date: date,
    config,
) -> Optional[str]:
    """
    Check whether a held position should be exited on current_date.

    Checks (in priority order):
      1. Hard stop loss (price fell 8% from entry)
      2. Breakeven stop (once peak >= +20%, price < entry)
      3. Time limit (held >= max_holding_days)
      4. Below 10-day MA
      5. RS weakness: underperformed SPY 5 consecutive days
      6. Volume stall: 5 consecutive low-volume days

    Returns the exit reason string, or None if no exit triggered.
    """
    stock_slice = _slice_up_to(ticker_df, current_date)
    if stock_slice.empty:
        return None

    try:
        current_close = float(stock_slice["Close"].iloc[-1])
    except (IndexError, KeyError):
        return None

    stop_loss_pct = getattr(config, "stop_loss_pct", 0.08)
    breakeven_trigger = getattr(config, "breakeven_trigger_pct", 0.20)
    max_holding_days = getattr(config, "max_holding_days", 20)

    gain_pct = (current_close / pos.entry_price) - 1.0
    peak_gain_pct = (pos.peak_price / pos.entry_price) - 1.0

    # Hard stop loss
    if gain_pct <= -stop_loss_pct:
        return f"stop_loss_{stop_loss_pct:.0%}"

    # Breakeven stop
    if peak_gain_pct >= breakeven_trigger and current_close < pos.entry_price:
        return "breakeven_stop"

    # Time limit
    if pos.holding_days >= max_holding_days:
        return f"time_limit_{max_holding_days}d"

    # MA-based exits
    spy_slice = _slice_up_to(spy_df, current_date)
    if not spy_slice.empty:
        indicators = calculate_all_indicators(stock_slice, spy_slice, config)
        if indicators.get("below_10d_ma"):
            return "below_10d_ma"
        if indicators.get("rs_weakness_5d"):
            return "rs_weakness_5d"
        if indicators.get("volume_stall_5d"):
            return "volume_stall_5d"

    return None


# ============================================================
# Main backtest engine
# ============================================================

class BacktestEngine:
    """
    Simulates the AI Catalyst Momentum Breakout strategy on historical data.

    Entry rules:
      - All 6 conditions fire on signal_date
      - Order executes at next trading day's open + slippage

    Exit rules (evaluated daily, exit at next open after trigger):
      - Hard stop: current_close fell >= 8% from entry_price
      - Breakeven stop: once peak_gain >= +20%, current_close < entry_price
      - Time limit: held for max_holding_days trading days
      - Below 10-day MA
      - RS weakness: underperformed SPY 5 consecutive days
      - Volume stall: 5 consecutive low-volume days
    """

    def run(
        self,
        config,
        start_date: date,
        end_date: date,
        universe: List[str],
        catalysts: Dict,
        skip_catalyst: bool = False,
        price_data: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> BacktestResult:
        """
        Run the historical backtest.

        Args:
            config:        AppConfig
            start_date:    First day of the backtest window
            end_date:      Last day of the backtest window
            universe:      Tickers to scan each day
            catalysts:     Catalyst data from load_catalysts()
            skip_catalyst: If True, bypass catalyst filter (technical only)
            price_data:    Pre-downloaded data (downloads if None)

        Returns:
            BacktestResult with all trades, equity curve, and metrics
        """
        initial_cash = float(getattr(config, "starting_cash", 100_000))
        max_positions = int(getattr(config, "max_positions", 8))
        target_size = float(getattr(config, "target_position_size", 0.18))
        slippage_bps = float(getattr(config, "slippage_bps", 10))
        tc_bps = float(getattr(config, "transaction_cost_bps", 5))

        warnings: List[str] = []

        # ---- Download data ----
        if price_data is None:
            price_data = download_backtest_data(universe, start_date, end_date)

        if "SPY" not in price_data:
            raise RuntimeError("SPY data unavailable — backtest cannot proceed.")

        spy_df = price_data["SPY"]
        available = [t for t in universe if t in price_data]

        if not available:
            raise RuntimeError(
                "No price data for any ticker. Check your internet connection."
            )

        missing = set(universe) - set(available)
        if missing:
            sample = ", ".join(sorted(missing)[:5])
            more = f" (+{len(missing)-5} more)" if len(missing) > 5 else ""
            warnings.append(f"No data for: {sample}{more}")

        # ---- Build list of trading days ----
        trading_days: List[date] = []
        d = start_date
        while d <= end_date:
            if is_trading_day(d):
                trading_days.append(d)
            d += timedelta(days=1)

        if not trading_days:
            raise RuntimeError(
                f"No trading days found between {start_date} and {end_date}."
            )

        logger.info(
            f"Backtesting {start_date} — {end_date}  "
            f"({len(trading_days)} trading days, {len(available)} tickers)"
        )

        # ---- Simulation state ----
        cash = initial_cash
        positions: Dict[str, BacktestPosition] = {}
        pending_buys: List[PendingOrder] = []
        pending_exits: Dict[str, str] = {}   # ticker -> exit_reason
        completed_trades: List[BacktestTrade] = []
        equity_curve: List[float] = []

        # ---- Day-by-day loop ----
        for today in trading_days:
            ts_today = pd.Timestamp(today)

            # 1. Execute pending BUY orders at today's open
            executed_buys = []
            for order in pending_buys:
                ticker = order.ticker
                if ticker not in price_data or ticker in positions:
                    executed_buys.append(order)
                    continue
                if len(positions) >= max_positions:
                    executed_buys.append(order)
                    continue

                df = price_data[ticker]
                day_rows = df[df.index.normalize() == ts_today]
                if day_rows.empty or "Open" not in day_rows.columns:
                    executed_buys.append(order)
                    continue

                open_px = float(day_rows["Open"].iloc[0])
                if open_px <= 0:
                    executed_buys.append(order)
                    continue

                eff_entry = open_px * (1.0 + slippage_bps / 10000.0)

                # Recalculate notional based on current portfolio value
                port_val = _estimate_portfolio_value(cash, positions, price_data, ts_today)
                notional = min(port_val * target_size, cash * 0.99)
                if notional < 100:
                    executed_buys.append(order)
                    continue

                shares = notional / eff_entry
                entry_fee = notional * tc_bps / 10000.0
                total_out = notional + entry_fee

                if total_out > cash:
                    executed_buys.append(order)
                    continue

                cash -= total_out
                positions[ticker] = BacktestPosition(
                    ticker=ticker,
                    entry_date=today,
                    entry_price=eff_entry,
                    shares=shares,
                    notional=notional,
                    entry_fee=entry_fee,
                    peak_price=eff_entry,
                )
                executed_buys.append(order)
                logger.debug(
                    f"  BUY  {ticker:<6} @ ${eff_entry:.2f}  "
                    f"{shares:.2f} sh  ${notional:.0f}"
                )

            pending_buys.clear()

            # 2. Execute pending EXIT orders at today's open
            for ticker, reason in list(pending_exits.items()):
                if ticker not in positions:
                    continue
                pos = positions[ticker]
                if ticker not in price_data:
                    continue

                df = price_data[ticker]
                day_rows = df[df.index.normalize() == ts_today]
                if day_rows.empty or "Open" not in day_rows.columns:
                    continue

                open_px = float(day_rows["Open"].iloc[0])
                if open_px <= 0:
                    continue

                eff_exit = open_px * (1.0 - slippage_bps / 10000.0)
                gross = pos.shares * eff_exit
                exit_fee = gross * tc_bps / 10000.0
                net = gross - exit_fee
                net_pnl = net - pos.notional - pos.entry_fee
                pnl_pct = net_pnl / pos.notional if pos.notional > 0 else 0.0

                cash += net
                del positions[ticker]
                del pending_exits[ticker]

                completed_trades.append(BacktestTrade(
                    ticker=ticker,
                    entry_date=pos.entry_date,
                    exit_date=today,
                    entry_price=pos.entry_price,
                    exit_price=eff_exit,
                    shares=pos.shares,
                    notional=pos.notional,
                    gross_pnl=pos.shares * eff_exit - pos.notional,
                    net_pnl=net_pnl,
                    pnl_pct=pnl_pct,
                    holding_days=pos.holding_days,
                    exit_reason=reason,
                    entry_fee=pos.entry_fee,
                    exit_fee=exit_fee,
                ))
                logger.debug(
                    f"  SELL {ticker:<6} @ ${eff_exit:.2f}  "
                    f"pnl={pnl_pct:+.1%}  ({reason})"
                )

            # 3. Mark positions to market, update peaks, increment holding days
            for ticker, pos in positions.items():
                if ticker not in price_data:
                    pos.holding_days += 1
                    continue
                df = price_data[ticker]
                day_rows = df[df.index.normalize() == ts_today]
                if not day_rows.empty and "Close" in day_rows.columns:
                    close = float(day_rows["Close"].iloc[0])
                    if close > pos.peak_price:
                        pos.peak_price = close
                pos.holding_days += 1

            # 4. Record equity
            port_val = _estimate_portfolio_value(cash, positions, price_data, ts_today)
            equity_curve.append(port_val)

            # 5. Check exits for held positions
            for ticker, pos in list(positions.items()):
                if ticker in pending_exits:
                    continue
                if ticker not in price_data:
                    continue
                reason = _check_exit_signal(
                    pos, price_data[ticker], spy_df, today, config
                )
                if reason:
                    pending_exits[ticker] = reason

            # 6. Generate buy signals → queue for tomorrow's open
            slots_open = max_positions - len(positions) - len(pending_buys)
            if slots_open > 0:
                for ticker in available:
                    if slots_open <= 0:
                        break
                    if ticker in positions or any(b.ticker == ticker for b in pending_buys):
                        continue
                    if ticker not in price_data:
                        continue
                    sig = _get_buy_signal(
                        ticker, price_data[ticker], spy_df,
                        today, config, catalysts, skip_catalyst
                    )
                    if sig:
                        pending_buys.append(PendingOrder(
                            ticker=ticker,
                            signal_date=today,
                            notional_estimate=0,  # recalculated at execution
                        ))
                        slots_open -= 1
                        logger.debug(f"  SIGNAL {ticker} on {today}")

        # ---- Close all remaining positions at the last day's close ----
        last_ts = pd.Timestamp(trading_days[-1])
        for ticker, pos in list(positions.items()):
            if ticker not in price_data:
                continue
            df = price_data[ticker]
            rows = df[df.index.normalize() == last_ts]
            if rows.empty or "Close" not in rows.columns:
                rows = df[df.index.normalize() <= last_ts]
            if rows.empty:
                continue

            close_px = float(rows["Close"].iloc[-1])
            eff_exit = close_px * (1.0 - slippage_bps / 10000.0)
            gross = pos.shares * eff_exit
            exit_fee = gross * tc_bps / 10000.0
            net = gross - exit_fee
            net_pnl = net - pos.notional - pos.entry_fee
            pnl_pct = net_pnl / pos.notional if pos.notional > 0 else 0.0

            cash += net
            completed_trades.append(BacktestTrade(
                ticker=ticker,
                entry_date=pos.entry_date,
                exit_date=trading_days[-1],
                entry_price=pos.entry_price,
                exit_price=eff_exit,
                shares=pos.shares,
                notional=pos.notional,
                gross_pnl=gross - pos.notional,
                net_pnl=net_pnl,
                pnl_pct=pnl_pct,
                holding_days=pos.holding_days,
                exit_reason="backtest_end",
                entry_fee=pos.entry_fee,
                exit_fee=exit_fee,
            ))

        final_value = cash
        if equity_curve:
            equity_curve[-1] = final_value

        # ---- SPY comparison ----
        spy_return = _compute_spy_return(spy_df, start_date, end_date)
        if spy_return is None:
            spy_return = 0.0
            warnings.append("Could not compute SPY return for comparison.")

        metrics = compute_metrics(
            completed_trades, equity_curve, trading_days, initial_cash, spy_return
        )

        return BacktestResult(
            trades=completed_trades,
            equity_curve=equity_curve,
            equity_dates=trading_days,
            metrics=metrics,
            initial_cash=initial_cash,
            final_value=final_value,
            start_date=start_date,
            end_date=end_date,
            universe=universe,
            spy_return=spy_return,
            skip_catalyst=skip_catalyst,
            warnings=warnings,
        )


# ============================================================
# Helpers
# ============================================================

def _estimate_portfolio_value(
    cash: float,
    positions: Dict[str, BacktestPosition],
    price_data: Dict[str, pd.DataFrame],
    ts: pd.Timestamp,
) -> float:
    """Estimate current portfolio value: cash + mark-to-market of all positions."""
    total = cash
    for ticker, pos in positions.items():
        if ticker not in price_data:
            total += pos.notional
            continue
        df = price_data[ticker]
        rows = df[df.index.normalize() == ts]
        if rows.empty or "Close" not in rows.columns:
            # Use most recent close before ts
            prior = df[df.index.normalize() <= ts]
            close = float(prior["Close"].iloc[-1]) if not prior.empty else pos.entry_price
        else:
            close = float(rows["Close"].iloc[0])
        total += pos.shares * close
    return total


def _compute_spy_return(
    spy_df: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> Optional[float]:
    """Compute SPY total return over the backtest period."""
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)

    start_rows = spy_df[spy_df.index.normalize() >= start_ts].head(1)
    end_rows = spy_df[spy_df.index.normalize() <= end_ts].tail(1)

    if start_rows.empty or end_rows.empty:
        return None

    spy_start = float(start_rows["Close"].iloc[0])
    spy_end = float(end_rows["Close"].iloc[0])

    if spy_start <= 0:
        return None

    return (spy_end / spy_start) - 1.0


# ============================================================
# Performance metrics
# ============================================================

def compute_metrics(
    trades: List[BacktestTrade],
    equity_curve: List[float],
    trading_days: List[date],
    initial_cash: float,
    spy_return: float,
) -> dict:
    """
    Compute all performance metrics from completed backtest results.

    Pure function — no side effects, no network calls.

    Args:
        trades:       Completed trades
        equity_curve: Portfolio value per trading day (same length as trading_days)
        trading_days: List of trading day dates
        initial_cash: Starting cash
        spy_return:   SPY total return over the same period (as decimal)

    Returns:
        Dict with all metrics.
    """
    if not equity_curve or initial_cash <= 0:
        return _empty_metrics(spy_return)

    n_days = len(equity_curve)
    n_years = n_days / 252.0
    final = equity_curve[-1]
    total_return = (final / initial_cash) - 1.0

    # Annualized return (CAGR)
    if n_years > 0 and final > 0:
        ann_return = (final / initial_cash) ** (1.0 / n_years) - 1.0
    else:
        ann_return = 0.0

    # Max drawdown
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd

    # Sharpe ratio (annualized)
    sharpe = 0.0
    if n_days > 2:
        daily_rets = [
            equity_curve[i] / equity_curve[i - 1] - 1.0
            for i in range(1, n_days)
            if equity_curve[i - 1] > 0
        ]
        if len(daily_rets) > 1:
            mu = mean(daily_rets)
            sigma = stdev(daily_rets)
            if sigma > 0:
                sharpe = mu / sigma * math.sqrt(252)

    # Trade statistics
    n_trades = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]

    win_rate = len(wins) / n_trades if n_trades > 0 else 0.0
    avg_win = mean(t.pnl_pct for t in wins) if wins else 0.0
    avg_loss = mean(t.pnl_pct for t in losses) if losses else 0.0

    gross_profit = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    avg_hold = mean(t.holding_days for t in trades) if trades else 0.0
    total_fees = sum(t.entry_fee + t.exit_fee for t in trades)

    best = max(trades, key=lambda t: t.pnl_pct) if trades else None
    worst = min(trades, key=lambda t: t.pnl_pct) if trades else None

    # Exposure: total holding-days / total trading days
    # (capped at 1.0; can exceed if multiple simultaneous positions)
    total_held_days = sum(t.holding_days for t in trades)
    exposure = min(total_held_days / n_days, 1.0) if n_days > 0 else 0.0

    # Exit reason breakdown
    exit_counts: dict = {}
    for t in trades:
        exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1

    # SPY annualized
    if n_years > 0 and (1 + spy_return) > 0:
        spy_ann = (1 + spy_return) ** (1.0 / n_years) - 1.0
    else:
        spy_ann = 0.0

    alpha = ann_return - spy_ann

    return {
        "total_return": total_return,
        "annualized_return": ann_return,
        "max_drawdown": max_dd,
        "sharpe_ratio": sharpe,
        "total_trades": n_trades,
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "profit_factor": profit_factor,
        "avg_holding_days": avg_hold,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "total_fees": total_fees,
        "best_trade_ticker": best.ticker if best else "",
        "best_trade_pct": best.pnl_pct if best else 0.0,
        "best_trade_days": best.holding_days if best else 0,
        "worst_trade_ticker": worst.ticker if worst else "",
        "worst_trade_pct": worst.pnl_pct if worst else 0.0,
        "worst_trade_days": worst.holding_days if worst else 0,
        "exposure_pct": exposure,
        "total_trading_days": n_days,
        "spy_return": spy_return,
        "spy_annualized": spy_ann,
        "alpha": alpha,
        "exit_reasons": exit_counts,
    }


def _empty_metrics(spy_return: float = 0.0) -> dict:
    return {
        "total_return": 0.0, "annualized_return": 0.0,
        "max_drawdown": 0.0, "sharpe_ratio": 0.0,
        "total_trades": 0, "win_count": 0, "loss_count": 0,
        "win_rate": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
        "profit_factor": 0.0, "avg_holding_days": 0.0,
        "gross_profit": 0.0, "gross_loss": 0.0, "total_fees": 0.0,
        "best_trade_ticker": "", "best_trade_pct": 0.0, "best_trade_days": 0,
        "worst_trade_ticker": "", "worst_trade_pct": 0.0, "worst_trade_days": 0,
        "exposure_pct": 0.0, "total_trading_days": 0,
        "spy_return": spy_return, "spy_annualized": 0.0, "alpha": 0.0,
        "exit_reasons": {},
    }


# ============================================================
# Saving results
# ============================================================

def save_backtest_results(
    result: BacktestResult,
    results_path: str = _DEFAULT_RESULTS_PATH,
    equity_path: str = _DEFAULT_EQUITY_PATH,
) -> None:
    """
    Save backtest results to two CSV files.

    Files:
      reports/backtest_results.csv      — one row per completed trade
      reports/backtest_equity_curve.csv — daily portfolio value and return

    Args:
        result:       BacktestResult from BacktestEngine.run()
        results_path: Output path for trade-level CSV
        equity_path:  Output path for equity curve CSV
    """
    ensure_dir("reports")

    # ---- Trade-level results ----
    with open(results_path, "w", newline="") as f:
        fieldnames = [
            "entry_date", "exit_date", "ticker", "holding_days",
            "entry_price", "exit_price", "shares", "notional",
            "net_pnl", "pnl_pct", "exit_reason", "entry_fee", "exit_fee",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in sorted(result.trades, key=lambda x: x.entry_date):
            writer.writerow({
                "entry_date": str(t.entry_date),
                "exit_date": str(t.exit_date),
                "ticker": t.ticker,
                "holding_days": t.holding_days,
                "entry_price": round(t.entry_price, 4),
                "exit_price": round(t.exit_price, 4),
                "shares": round(t.shares, 4),
                "notional": round(t.notional, 2),
                "net_pnl": round(t.net_pnl, 2),
                "pnl_pct": round(t.pnl_pct * 100, 4),
                "exit_reason": t.exit_reason,
                "entry_fee": round(t.entry_fee, 2),
                "exit_fee": round(t.exit_fee, 2),
            })

    # ---- Equity curve ----
    with open(equity_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["date", "portfolio_value", "daily_return_pct"]
        )
        writer.writeheader()
        prev = result.initial_cash
        for d, v in zip(result.equity_dates, result.equity_curve):
            daily_ret = (v / prev - 1.0) * 100 if prev > 0 else 0.0
            writer.writerow({
                "date": str(d),
                "portfolio_value": round(v, 2),
                "daily_return_pct": round(daily_ret, 4),
            })
            prev = v

    logger.info(
        f"Saved {len(result.trades)} trades → {results_path}"
    )
    logger.info(
        f"Saved equity curve ({len(result.equity_curve)} days) → {equity_path}"
    )

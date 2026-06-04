"""
src/indicators.py — Technical indicator calculations

This module calculates every technical measurement the strategy needs.

All functions operate on a pandas DataFrame with columns:
  Open, High, Low, Close, Volume
and a DatetimeIndex sorted ascending (oldest row first, newest row last).

These are pure calculations — no API calls, no file reading/writing.
They can be tested independently with synthetic data.

Strategy indicators used:
  - Moving averages (50-day for trend, 10-day for exit)
  - 20-day return (momentum strength)
  - Volume ratio vs 20-day average (unusual activity)
  - Relative strength vs SPY (market outperformance)
  - 20-day high breakout (price momentum)
  - Exit signals: below 10-day MA, RS weakness streak, volume stall
"""

import logging
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# Moving averages
# ============================================================

def moving_average(series: pd.Series, period: int) -> pd.Series:
    """
    Calculate a Simple Moving Average (SMA).

    The SMA is the plain average of the last N closing prices.
    For example, the 50-day MA is the average of the last 50 closing prices.

    Args:
        series: A pandas Series of prices (e.g. df['Close'])
        period: Number of days to average. E.g. 50 for 50-day MA.

    Returns:
        A pandas Series of the same length.
        The first (period-1) values will be NaN because there
        isn't enough history yet to compute the average.

    Example:
        ma50 = moving_average(df['Close'], 50)
        latest_ma50 = ma50.iloc[-1]   # Most recent 50-day MA value
    """
    return series.rolling(window=period, min_periods=period).mean()


def latest_ma(df: pd.DataFrame, period: int) -> Optional[float]:
    """
    Get the most recent moving average value for the Close price.

    Args:
        df:     OHLCV DataFrame
        period: MA period (e.g. 50 for the 50-day moving average)

    Returns:
        The most recent MA value, or None if not enough data exists.
    """
    if df is None or len(df) < period:
        return None

    ma_series = moving_average(df["Close"], period)
    valid = ma_series.dropna()

    if valid.empty:
        return None

    return float(valid.iloc[-1])


def latest_close(df: pd.DataFrame) -> Optional[float]:
    """
    Get the most recent closing price from a DataFrame.

    Returns:
        Latest close price as a float, or None if the DataFrame is empty.
    """
    if df is None or df.empty:
        return None
    try:
        return float(df["Close"].dropna().iloc[-1])
    except (IndexError, KeyError, ValueError):
        return None


# ============================================================
# Breakout detection
# ============================================================

def is_above_20d_high(df: pd.DataFrame, lookback: int = 20) -> bool:
    """
    Check if today's closing price is above the highest close
    of the previous N trading days (not counting today).

    This is the "price breakout" signal.

    Why it matters:
        When a stock breaks above its recent high, it means buyers
        are more aggressive than they have been in weeks.
        Combined with high volume, this often signals institutional buying.

    Args:
        df:       OHLCV DataFrame (newest row = today)
        lookback: Number of prior days to look at (default 20)

    Returns:
        True if today's close is strictly above the prior N-day high.
        False if not enough data or no breakout.

    Example:
        if is_above_20d_high(nvda_df):
            print("NVDA is making a 20-day high breakout!")
    """
    if df is None or len(df) < lookback + 1:
        return False

    close = df["Close"].dropna()
    if len(close) < lookback + 1:
        return False

    today_close = float(close.iloc[-1])
    # Look at the N days before today (not including today)
    prior_high = float(close.iloc[-(lookback + 1):-1].max())

    return today_close > prior_high


def is_above_ma(df: pd.DataFrame, period: int = 50) -> bool:
    """
    Check if today's closing price is above the N-day moving average.

    This is the "trend filter". We only trade stocks that are in an uptrend.
    A stock above its 50-day MA is generally in a longer-term uptrend.

    Args:
        df:     OHLCV DataFrame
        period: MA period (default 50 for the 50-day MA)

    Returns:
        True if latest close > MA value.
        False if not enough data or close is below MA.
    """
    if df is None or len(df) < period:
        return False

    ma_val = latest_ma(df, period)
    close = latest_close(df)

    if ma_val is None or close is None:
        return False

    return close > ma_val


# ============================================================
# Return / momentum
# ============================================================

def return_nd(df: pd.DataFrame, n: int = 20) -> Optional[float]:
    """
    Calculate the N-day return: how much the stock went up or down
    over the last N trading days.

    Formula:  (price today / price N days ago) - 1

    Args:
        df: OHLCV DataFrame
        n:  Number of trading days to look back (default 20)

    Returns:
        A decimal return value.
        0.15  = +15% return (stock went up 15% in 20 days)
        -0.08 = -8% return  (stock went down 8% in 20 days)
        None  = not enough data

    The strategy requires a minimum 20-day return of +10% (0.10).
    """
    if df is None:
        return None

    close = df["Close"].dropna()

    if len(close) < n + 1:
        return None

    today = float(close.iloc[-1])
    n_days_ago = float(close.iloc[-(n + 1)])

    if n_days_ago <= 0:
        return None

    return (today / n_days_ago) - 1.0


# ============================================================
# Volume indicators
# ============================================================

def volume_ratio(df: pd.DataFrame, period: int = 20) -> Optional[float]:
    """
    Calculate today's volume as a multiple of the average daily volume
    over the past N days (not counting today).

    Example:
        If the 20-day average volume is 10 million shares,
        and today's volume is 35 million shares,
        then the volume ratio = 35M / 10M = 3.5x

    Why it matters:
        Unusually high volume (2.5x or more) often means institutional
        investors are actively buying or selling the stock.
        This is one of the most important signals for this strategy.

    Args:
        df:     OHLCV DataFrame
        period: Look-back period for the average (default 20)

    Returns:
        Volume ratio as a float (e.g. 3.5 means 3.5× average).
        None if not enough data or average volume is zero.
    """
    if df is None or "Volume" not in df.columns:
        return None

    vol = df["Volume"].dropna()

    if len(vol) < period + 1:
        return None

    today_vol = float(vol.iloc[-1])
    # Average of the N days BEFORE today
    avg_vol = float(vol.iloc[-(period + 1):-1].mean())

    if avg_vol <= 0:
        return None

    return today_vol / avg_vol


def avg_volume_n(df: pd.DataFrame, n: int = 20) -> Optional[float]:
    """
    Average daily share volume over the past N trading days (not counting today).

    Used for liquidity filtering: stocks with average volume below 1M shares/day
    are too illiquid to trade safely.

    Args:
        df: OHLCV DataFrame
        n:  Number of days (default 20)

    Returns:
        Average share volume, or None if not enough data.
    """
    if df is None or "Volume" not in df.columns:
        return None

    vol = df["Volume"].dropna()

    if len(vol) < n + 1:
        return None

    return float(vol.iloc[-(n + 1):-1].mean())


def avg_dollar_volume_n(df: pd.DataFrame, n: int = 20) -> Optional[float]:
    """
    Average daily dollar volume over the past N trading days.

    Dollar volume = Close price × Share volume.
    It measures how many dollars of stock traded hands each day.

    Example: NVDA at $900 × 40M shares = $36B in dollar volume per day.

    Used for liquidity filtering: stocks with average dollar volume
    below $50M/day are too illiquid for the strategy.

    Args:
        df: OHLCV DataFrame
        n:  Number of days (default 20)

    Returns:
        Average dollar volume, or None if not enough data.
    """
    if df is None or "Volume" not in df.columns or "Close" not in df.columns:
        return None

    valid = df[["Close", "Volume"]].dropna()

    if len(valid) < n + 1:
        return None

    dollar_vol = valid["Close"] * valid["Volume"]
    return float(dollar_vol.iloc[-(n + 1):-1].mean())


# ============================================================
# Relative strength vs SPY
# ============================================================

def relative_strength_vs_spy(
    stock_df: pd.DataFrame,
    spy_df: pd.DataFrame,
    period: int = 20,
) -> Optional[float]:
    """
    Calculate how much the stock has outperformed (or underperformed)
    the S&P 500 (SPY) over the past N trading days.

    Formula:
        RS = stock_20d_return - spy_20d_return

    Example:
        If NVDA went up 25% and SPY went up 5% over 20 days,
        then RS = 25% - 5% = +20%

    Why it matters:
        If a stock is outperforming the entire market, something
        specific is driving it. This is exactly what the strategy looks for.

    The strategy requires RS > 8% (0.08) over 20 days.

    Args:
        stock_df: OHLCV DataFrame for the stock being evaluated
        spy_df:   OHLCV DataFrame for SPY (market benchmark)
        period:   Look-back period in trading days (default 20)

    Returns:
        Relative strength as a decimal. E.g. 0.12 = stock beat SPY by 12%.
        Negative means stock underperformed SPY.
        None if there is not enough data for either the stock or SPY.
    """
    stock_ret = return_nd(stock_df, n=period)
    spy_ret = return_nd(spy_df, n=period)

    if stock_ret is None or spy_ret is None:
        return None

    return stock_ret - spy_ret


# ============================================================
# Exit signal indicators
# ============================================================

def is_below_exit_ma(df: pd.DataFrame, period: int = 10) -> bool:
    """
    Check if today's close is below the fast exit moving average (10-day MA).

    This is one of the sell signals. When a stock that was strong
    falls below its 10-day MA, momentum may be breaking down.

    Args:
        df:     OHLCV DataFrame
        period: Exit MA period (default 10)

    Returns:
        True if close < MA (potential exit signal).
        False otherwise.
    """
    if df is None or len(df) < period:
        return False

    ma_val = latest_ma(df, period)
    close = latest_close(df)

    if ma_val is None or close is None:
        return False

    return close < ma_val


def check_rs_weakness_streak(
    stock_df: pd.DataFrame,
    spy_df: pd.DataFrame,
    n_days: int = 5,
) -> bool:
    """
    Check if the stock has underperformed SPY on every single one
    of the last N trading days.

    If a previously strong stock starts underperforming the market
    for 5 straight days, its momentum may be fading.

    Args:
        stock_df: OHLCV DataFrame for the stock
        spy_df:   OHLCV DataFrame for SPY
        n_days:   Number of consecutive days to check (default 5)

    Returns:
        True if the stock underperformed SPY on every one of the last N days.
        False otherwise.
    """
    if stock_df is None or spy_df is None:
        return False

    if len(stock_df) < n_days + 2 or len(spy_df) < n_days + 2:
        return False

    # Calculate daily returns (percent change day-over-day)
    stock_daily = stock_df["Close"].pct_change().dropna()
    spy_daily = spy_df["Close"].pct_change().dropna()

    if len(stock_daily) < n_days or len(spy_daily) < n_days:
        return False

    # Take only the last N days
    stock_last = stock_daily.iloc[-n_days:].values
    spy_last = spy_daily.iloc[-n_days:].values

    # True if stock underperformed SPY on EVERY one of the last N days
    underperformed = stock_last < spy_last
    return bool(np.all(underperformed))


def check_volume_stall(
    df: pd.DataFrame,
    n_days: int = 5,
    vol_threshold: float = 0.7,
) -> bool:
    """
    Check if trading volume has dried up significantly for the last N days.

    "Dried up" means each day's volume is below (vol_threshold × 20-day avg).
    Default threshold is 0.7, meaning volume dropped below 70% of average.

    When a stock's price stalls AND volume drops off, it often means
    buyers are losing interest — a sign the move may be over.

    Args:
        df:            OHLCV DataFrame
        n_days:        Number of consecutive low-volume days to check
        vol_threshold: Fraction of average volume to consider "low"

    Returns:
        True if volume has been low for all of the last N days.
        False otherwise.
    """
    if df is None or "Volume" not in df.columns:
        return False

    # Need 20 days for average + n_days of recent data
    if len(df) < 20 + n_days:
        return False

    vol = df["Volume"].dropna()

    # Calculate 20-day average using the period BEFORE the recent N days
    avg_vol = float(vol.iloc[-(20 + n_days):-n_days].mean())

    if avg_vol <= 0:
        return False

    # Check if the last N days all have volume below the threshold
    recent_vol = vol.iloc[-n_days:]
    low_volume_days = recent_vol < (avg_vol * vol_threshold)

    return bool(low_volume_days.all())


# ============================================================
# Volatility indicators (ATR and ADX)
# ============================================================

def calculate_atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """
    Calculate the Average True Range (ATR) over the last `period` days.

    ATR measures a stock's typical daily price range, accounting for gaps.
    A stock with ATR = $3 on a $50 price has a daily range of ~6%.

    Used for adaptive stop-loss sizing: a high-ATR stock needs a wider stop
    so normal daily swings don't trigger it prematurely.
    """
    if df is None or len(df) < period + 1:
        return None
    try:
        high = df["High"]
        low = df["Low"]
        prev_close = df["Close"].shift(1)
        tr = pd.concat([
            (high - low).rename("hl"),
            (high - prev_close).abs().rename("hpc"),
            (low - prev_close).abs().rename("lpc"),
        ], axis=1).max(axis=1)
        atr_val = tr.rolling(period, min_periods=period).mean().iloc[-1]
        return float(atr_val) if pd.notna(atr_val) else None
    except Exception:
        return None


def calculate_adx(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """
    Calculate the Average Directional Index (ADX) over the last `period` days.

    ADX measures trend strength on a 0-100 scale:
      < 20  — no trend / choppy (avoid entering)
      20-25 — weak trend forming
      > 25  — confirmed trend (our minimum threshold)
      > 40  — strong trend

    ADX says nothing about direction — a falling stock can have a high ADX.
    We use ADX alongside the above-50MA filter which confirms direction.
    """
    if df is None or len(df) < period * 2 + 5:
        return None
    try:
        high = df["High"]
        low = df["Low"]

        # Directional movement
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

        # True Range
        prev_close = df["Close"].shift(1)
        tr = pd.concat([
            (high - low).rename("hl"),
            (high - prev_close).abs().rename("hpc"),
            (low - prev_close).abs().rename("lpc"),
        ], axis=1).max(axis=1)

        # Wilder smoothing (approximated by EWM with alpha=1/period)
        alpha = 1.0 / period
        atr_s = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_s
        minus_di = 100 * minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_s
        di_sum = (plus_di + minus_di).replace(0, np.nan)
        dx = (100 * (plus_di - minus_di).abs() / di_sum).fillna(0)
        adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        val = adx.iloc[-1]
        return float(val) if pd.notna(val) else None
    except Exception:
        return None


# ============================================================
# Combined indicator calculator
# ============================================================

def calculate_all_indicators(
    stock_df: pd.DataFrame,
    spy_df: pd.DataFrame,
    config=None,
) -> Dict[str, Any]:
    """
    Calculate ALL indicators for one stock and return them as a dictionary.

    This is the main function called by the signal engine.
    It runs every indicator the strategy needs in one place.

    Args:
        stock_df: OHLCV DataFrame for the stock (newest row = today)
        spy_df:   OHLCV DataFrame for SPY (market benchmark)
        config:   AppConfig object (optional — uses default values if None)

    Returns:
        A dictionary with all computed values and boolean flags.

        Key indicators:
          "close"                 Latest closing price
          "volume_today"          Today's trading volume
          "avg_volume_20d"        20-day average share volume
          "volume_ratio"          Today's volume / 20-day avg (e.g. 3.5 = 3.5x avg)
          "avg_dollar_volume_20d" 20-day average dollar volume
          "return_20d"            20-day price return (e.g. 0.15 = +15%)
          "ma_50"                 50-day moving average price
          "ma_10"                 10-day moving average price
          "ma_20"                 20-day moving average price
          "above_20d_high"        True if making a 20-day high breakout
          "above_50d_ma"          True if price is above 50-day MA (uptrend)
          "relative_strength_20d" How much stock beat SPY over 20 days
          "below_10d_ma"          True if price dropped below 10-day MA (exit signal)
          "rs_weakness_5d"        True if underperformed SPY 5 straight days (exit)
          "volume_stall_5d"       True if volume dried up for 5 days (exit)
          "enough_data"           True if at least 55 rows of history exist
          "error"                 None if successful, error message string if failed
    """
    # Initialize the result with default/empty values
    result: Dict[str, Any] = {
        "close": None,
        "volume_today": None,
        "avg_volume_20d": None,
        "volume_ratio": None,
        "avg_dollar_volume_20d": None,
        "return_20d": None,
        "ma_50": None,
        "ma_10": None,
        "ma_20": None,
        "above_20d_high": False,
        "above_50d_ma": False,
        "relative_strength_20d": None,
        "below_10d_ma": False,
        "rs_weakness_5d": False,
        "volume_stall_5d": False,
        "return_5d": None,
        "return_10d": None,
        "pct_above_20d_ma": None,
        "atr_14": None,
        "adx_14": None,
        "pct_below_52w_high": None,
        "volume_elevated_prev_2d": None,
        "pre_breakout_tightness": None,
        "enough_data": False,
        "error": None,
    }

    # Validate inputs before calculating
    if stock_df is None or stock_df.empty:
        result["error"] = "No price data available for this stock"
        return result

    if spy_df is None or spy_df.empty:
        result["error"] = "No SPY benchmark data available"
        return result

    try:
        # ---- Price levels ----
        result["close"] = latest_close(stock_df)

        # ---- Volume ----
        if "Volume" in stock_df.columns and not stock_df["Volume"].empty:
            result["volume_today"] = float(stock_df["Volume"].dropna().iloc[-1])

        result["avg_volume_20d"] = avg_volume_n(stock_df, 20)
        result["volume_ratio"] = volume_ratio(stock_df, 20)
        result["avg_dollar_volume_20d"] = avg_dollar_volume_n(stock_df, 20)

        # ---- Momentum ----
        result["return_20d"] = return_nd(stock_df, 20)
        result["return_10d"] = return_nd(stock_df, 10)
        result["return_5d"] = return_nd(stock_df, 5)

        # ---- Moving averages ----
        result["ma_50"] = latest_ma(stock_df, 50)
        result["ma_10"] = latest_ma(stock_df, 10)
        result["ma_20"] = latest_ma(stock_df, 20)

        # ---- Extension above 20d MA ----
        ma_20_val = result["ma_20"]
        close_val = result["close"]
        result["pct_above_20d_ma"] = (
            (close_val / ma_20_val) - 1.0
            if (ma_20_val and close_val and ma_20_val > 0)
            else None
        )

        # ---- Breakout signals ----
        result["above_20d_high"] = is_above_20d_high(stock_df, 20)
        result["above_50d_ma"] = is_above_ma(stock_df, 50)

        # ---- Relative strength ----
        result["relative_strength_20d"] = relative_strength_vs_spy(stock_df, spy_df, 20)

        # ---- Exit signals ----
        result["below_10d_ma"] = is_below_exit_ma(stock_df, 10)
        result["rs_weakness_5d"] = check_rs_weakness_streak(stock_df, spy_df, 5)
        result["volume_stall_5d"] = check_volume_stall(stock_df, 5)

        # ---- Volatility / trend strength ----
        result["atr_14"] = calculate_atr(stock_df, 14)
        result["adx_14"] = calculate_adx(stock_df, 14)

        # ---- 52-week high proximity ----
        lookback_52w = min(252, len(stock_df))
        high_52w = stock_df["High"].iloc[-lookback_52w:].max() if lookback_52w > 0 else None
        close_val = result["close"]
        if high_52w and high_52w > 0 and close_val:
            result["pct_below_52w_high"] = (high_52w - close_val) / high_52w
        else:
            result["pct_below_52w_high"] = None

        # ---- Multi-day volume accumulation ----
        avg_vol_20 = result.get("avg_volume_20d")
        if avg_vol_20 and avg_vol_20 > 0 and len(stock_df) >= 3 and "Volume" in stock_df.columns:
            prior_2 = stock_df["Volume"].iloc[-3:-1].dropna()
            result["volume_elevated_prev_2d"] = bool((prior_2 >= 1.5 * avg_vol_20).any())
        else:
            result["volume_elevated_prev_2d"] = None

        # ---- Pre-breakout tightness (consolidation before breakout) ----
        if len(stock_df) >= 7 and close_val and close_val > 0:
            consol = stock_df.iloc[-6:-1]
            h = consol["High"].max()
            l = consol["Low"].min()
            result["pre_breakout_tightness"] = (h - l) / close_val
        else:
            result["pre_breakout_tightness"] = None

        # ---- Data quality flag ----
        # We need at least 55 rows to compute the 50-day MA reliably
        result["enough_data"] = len(stock_df) >= 55

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Error calculating indicators: {e}", exc_info=True)

    return result

"""
src/data_loader.py — Downloads price and volume data from Yahoo Finance

This module is the only place in the project that fetches market data.
All other modules call functions here to get prices.

Data source: Yahoo Finance via the free 'yfinance' library.
No API key required. No paid subscription needed.

Main functions:
  download_price_data(tickers)  — Download OHLCV data for a list of tickers
  download_single(ticker)       — Download data for one ticker
  download_spy_data()           — Download SPY (market benchmark) data
  get_latest_price(ticker)      — Get just the most recent price
  get_market_cap_approx(ticker) — Get approximate market capitalization
"""

import logging
from typing import Dict, List, Optional
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


# ============================================================
# Main download functions
# ============================================================

def download_price_data(
    tickers: List[str],
    period: str = "90d",
    interval: str = "1d",
) -> Dict[str, pd.DataFrame]:
    """
    Download daily OHLCV (Open, High, Low, Close, Volume) data
    for a list of stock tickers.

    Args:
        tickers:  List of stock symbols, e.g. ['NVDA', 'MSFT', 'AAPL']
        period:   How far back to download data.
                  '60d'  = last 60 calendar days
                  '90d'  = last 90 calendar days (default, gives ~60 trading days)
                  '6mo'  = 6 months
                  '1y'   = 1 year
        interval: Frequency of bars. '1d' = daily (one bar per trading day).

    Returns:
        A dictionary: { 'NVDA': DataFrame, 'MSFT': DataFrame, ... }

        Each DataFrame has columns: Open, High, Low, Close, Volume
        with the date as the index (oldest date first).

        Tickers with no available data are skipped (not in the result dict).

    Example:
        data = download_price_data(['NVDA', 'MSFT'])
        nvda_df = data['NVDA']
        latest_close = nvda_df['Close'].iloc[-1]
    """
    if not tickers:
        return {}

    # Clean up ticker list: strip whitespace, uppercase
    clean_tickers = [t.strip().upper() for t in tickers if t and str(t).strip()]
    if not clean_tickers:
        return {}

    result: Dict[str, pd.DataFrame] = {}

    # Download one ticker at a time.
    # This is slightly slower than batching but is more reliable —
    # batch downloads from yfinance can have MultiIndex complexity
    # that varies across yfinance versions.
    logger.debug(f"Downloading price data for {len(clean_tickers)} tickers (period={period})")

    for ticker in clean_tickers:
        df = _download_one(ticker, period=period, interval=interval)
        if df is not None:
            result[ticker] = df

    logger.debug(f"Successfully loaded {len(result)}/{len(clean_tickers)} tickers")
    return result


def download_single(ticker: str, period: str = "90d") -> Optional[pd.DataFrame]:
    """
    Download data for a single ticker.

    Args:
        ticker: Stock symbol, e.g. 'NVDA'
        period: How far back. See download_price_data() for options.

    Returns:
        DataFrame with OHLCV data, or None if data is unavailable.
    """
    return _download_one(ticker.strip().upper(), period=period)


def download_spy_data(period: str = "90d") -> Optional[pd.DataFrame]:
    """
    Download SPY (S&P 500 ETF) data.

    SPY is used as the market benchmark for relative strength calculations.
    When we say "this stock beat the market by 10%", we mean it beat SPY.

    Returns:
        DataFrame with SPY OHLCV data, or None if unavailable.
    """
    return _download_one("SPY", period=period)


def get_latest_price(ticker: str) -> Optional[float]:
    """
    Get the most recent closing price for a ticker.

    Downloads only the last 5 days of data to be fast.

    Returns:
        Most recent closing price as a float, or None if unavailable.

    Example:
        price = get_latest_price('NVDA')
        if price:
            print(f"NVDA is at ${price:.2f}")
    """
    df = _download_one(ticker.strip().upper(), period="5d")
    if df is None or df.empty:
        return None
    try:
        return float(df["Close"].dropna().iloc[-1])
    except (IndexError, KeyError, ValueError):
        return None


def download_batch(
    tickers: List[str],
    period: str = "90d",
) -> Dict[str, pd.DataFrame]:
    """
    Download OHLCV data for many tickers at once using yfinance's batch mode.

    This is significantly faster than calling download_single() in a loop
    when you need data for 50–150 tickers at once (as the discovery engine does).

    Falls back to individual downloads automatically if the batch fails.

    Args:
        tickers: List of stock symbols, e.g. ['NVDA', 'MSFT', 'AAPL', ...]
        period:  Look-back period. '90d' is the default.

    Returns:
        Dict mapping ticker -> DataFrame, same as download_price_data().

    Example:
        data = download_batch(['NVDA', 'AMD', 'MSFT', 'AAPL'], period='90d')
        for ticker, df in data.items():
            print(f"{ticker}: {len(df)} rows, latest close ${df['Close'].iloc[-1]:.2f}")
    """
    if not tickers:
        return {}

    clean = [t.strip().upper() for t in tickers if t and str(t).strip()]
    if not clean:
        return {}

    # For a single ticker, the single-download path is simpler and more reliable
    if len(clean) == 1:
        result = {}
        df = download_single(clean[0], period=period)
        if df is not None:
            result[clean[0]] = df
        return result

    logger.debug(f"Batch downloading {len(clean)} tickers (period={period})")

    try:
        # yfinance supports downloading multiple tickers in one request.
        # group_by='ticker' organizes columns as (ticker, price_type),
        # which lets us extract each stock's data cleanly with raw[ticker].
        raw = yf.download(
            tickers=clean,
            period=period,
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            actions=False,
        )

        if raw is None or raw.empty:
            raise ValueError("Empty result from yfinance batch download")

        result: Dict[str, pd.DataFrame] = {}

        if isinstance(raw.columns, pd.MultiIndex):
            # Expected format: MultiIndex with (ticker, price_type) at each level
            available = raw.columns.get_level_values(0).unique().tolist()

            for ticker in clean:
                if ticker in available:
                    try:
                        ticker_df = raw[ticker].copy()
                        ticker_df = _clean_ohlcv(ticker_df, ticker)
                        if ticker_df is not None:
                            result[ticker] = ticker_df
                    except Exception as e:
                        logger.debug(f"Could not parse {ticker} from batch result: {e}")
        else:
            # Flat structure — unexpected for multi-ticker, fall through
            raise ValueError("Unexpected flat column structure in multi-ticker download")

        # If batch worked well enough, return what we have and fill gaps individually
        if len(result) >= max(1, len(clean) // 2):
            # Fill any missing tickers one at a time
            missing = [t for t in clean if t not in result]
            if missing:
                logger.debug(f"Filling {len(missing)} missing tickers individually")
                for ticker in missing:
                    df = _download_one(ticker, period=period)
                    if df is not None:
                        result[ticker] = df

            logger.debug(f"Batch complete: {len(result)}/{len(clean)} tickers loaded")
            return result

        # Batch got too few results — fall back entirely
        raise ValueError(f"Batch only got {len(result)}/{len(clean)} tickers")

    except Exception as e:
        logger.debug(f"Batch download failed ({e}), falling back to individual downloads")
        return download_price_data(clean, period=period)


def get_market_cap_approx(ticker: str) -> Optional[float]:
    """
    Get the approximate market capitalization for a ticker.

    Uses yfinance's 'info' dictionary, which can sometimes be slow
    or unavailable. Returns None if data cannot be fetched.

    Market cap is in dollars. E.g. 1_000_000_000 = $1 Billion.

    NOTE: yfinance's market cap data can be delayed or missing for some stocks.
    This is used for filtering, so if we can't get it, we note it as unknown.

    Returns:
        Market cap in dollars as a float, or None if unavailable.
    """
    try:
        info = yf.Ticker(ticker.upper()).fast_info
        # fast_info is faster than .info for basic data
        mc = getattr(info, "market_cap", None)
        if mc and mc > 0:
            return float(mc)
    except Exception:
        pass

    # Fallback: try the regular .info dict
    try:
        info = yf.Ticker(ticker.upper()).info
        mc = info.get("marketCap")
        if mc and mc > 0:
            return float(mc)
    except Exception:
        pass

    return None


# ============================================================
# Internal helpers
# ============================================================

def _download_one(ticker: str, period: str = "90d", interval: str = "1d") -> Optional[pd.DataFrame]:
    """
    Internal: download OHLCV data for a single ticker from Yahoo Finance.

    Args:
        ticker:   Uppercase stock symbol.
        period:   Look-back period string.
        interval: Bar frequency ('1d' for daily).

    Returns:
        Cleaned DataFrame or None.
    """
    try:
        raw = yf.download(
            tickers=ticker,
            period=period,
            interval=interval,
            auto_adjust=True,   # Automatically adjust for splits and dividends
            progress=False,     # Suppress the yfinance progress bar
            actions=False,      # Don't include dividend/split columns
        )
    except Exception as e:
        logger.debug(f"yfinance download failed for {ticker}: {e}")
        return None

    return _clean_ohlcv(raw, ticker)


def _clean_ohlcv(df: pd.DataFrame, ticker: str = "") -> Optional[pd.DataFrame]:
    """
    Internal: clean and validate a raw yfinance DataFrame.

    Steps:
    1. Flatten MultiIndex columns if present (yfinance sometimes returns them)
    2. Keep only the standard OHLCV columns
    3. Drop rows with all-NaN values
    4. Remove rows with zero or negative close price
    5. Sort by date (oldest first)
    6. Return None if the result is too short to be useful

    Args:
        df:     Raw DataFrame from yf.download()
        ticker: Ticker name (used for log messages)

    Returns:
        Cleaned DataFrame with columns [Open, High, Low, Close, Volume],
        or None if the data is not usable.
    """
    if df is None or df.empty:
        logger.debug(f"No data returned for {ticker}")
        return None

    # Flatten MultiIndex columns if present
    # In some yfinance versions, columns may be like ('Close', 'NVDA')
    if isinstance(df.columns, pd.MultiIndex):
        # Take the first level (e.g. 'Close', 'Open', etc.)
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

    # Normalize column names: strip whitespace and capitalize first letter
    df.columns = [str(c).strip().capitalize() for c in df.columns]

    # Keep only standard OHLCV columns that exist in this DataFrame
    standard_cols = ["Open", "High", "Low", "Close", "Volume"]
    available_cols = [c for c in standard_cols if c in df.columns]

    if "Close" not in available_cols:
        logger.debug(f"No Close column found for {ticker}")
        return None

    df = df[available_cols].copy()

    # Drop rows where all values are NaN
    df = df.dropna(how="all")

    # Remove rows with zero or negative close price (data errors)
    df = df[df["Close"] > 0]

    # Ensure the index is a proper DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index)
        except Exception:
            logger.debug(f"Could not parse dates for {ticker}")
            return None

    # Sort ascending (oldest date first)
    df = df.sort_index()

    # Need at least 2 rows to be useful
    if len(df) < 2:
        logger.debug(f"Not enough rows for {ticker}: {len(df)}")
        return None

    return df

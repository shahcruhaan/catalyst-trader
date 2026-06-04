"""
src/signal_engine.py — Buy and Exit Signal Generation

The signal engine is the core decision-making module.
It evaluates every stock in the discovered universe and decides:
  - BUY: Should we enter this position? (all 6 conditions must pass)
  - EXIT: Should we exit this existing position? (any 1 of 5 conditions triggers)
  - REVIEW: Stock looks interesting but has no catalyst yet

BUY conditions (all 6 must be true):
  1. Price breakout: above 20-day high
  2. Trend: above 50-day MA
  3. Momentum: 20-day return >= 10%
  4. Volume: today's volume >= 2.5x the 20-day average
  5. Relative strength: beats SPY by >= 8% over 20 days
  6. Catalyst: fresh catalyst with score >= 8 and tradeable = True

EXIT conditions (any 1 triggers a sell):
  1. Stop loss: price dropped 8% or more from entry
  2. Trend break: price fell below 10-day MA
  3. RS weakness: underperformed SPY 5 straight trading days
  4. Volume stall: volume dried up for 5 consecutive days
  5. Time limit: held for >= 20 trading days

Winner management (adjusts stop or triggers partial sell):
  - After +20% gain: move stop to breakeven (entry price)
  - After +40% gain: trigger a 1/3 partial sell recommendation
  - After +75% gain: trail on 20-day MA (stronger protection for big winners)
"""

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.utils import setup_logger, today_str, ensure_dir
from src.indicators import calculate_all_indicators
from src.catalyst_scorer import get_tradeable_catalyst

logger = setup_logger(__name__)


# ============================================================
# Record builders
# ============================================================

def _make_buy_signal(
    ticker: str,
    indicators: dict,
    catalyst: dict,
    score: float = 0.0,
) -> dict:
    """Build a buy signal record."""
    return {
        "ticker": ticker,
        "signal_type": "BUY",
        "signal_date": today_str(),
        "close": indicators.get("close"),
        "volume_ratio": indicators.get("volume_ratio"),
        "return_20d": indicators.get("return_20d"),
        "relative_strength_20d": indicators.get("relative_strength_20d"),
        "above_20d_high": indicators.get("above_20d_high"),
        "above_50d_ma": indicators.get("above_50d_ma"),
        "catalyst_score": catalyst.get("catalyst_score"),
        "catalyst_type": catalyst.get("catalyst_type"),
        "catalyst_headline": catalyst.get("headline"),
        "catalyst_date": catalyst.get("date"),
        "rank_score": score,
        # Private: retained for ranking and options overlay, stripped before CSV export
        "_indicators": indicators,
        "_catalyst": catalyst,
    }


def _make_exit_signal(
    ticker: str,
    reason: str,
    indicators: dict,
    position: dict,
    partial: bool = False,
    partial_fraction: float = 1.0,
) -> dict:
    """Build an exit signal record."""
    entry_price = position.get("entry_price") or 0.0
    current_price = indicators.get("close") or 0.0
    gain_pct = ((current_price / entry_price) - 1.0) if entry_price > 0 else 0.0

    return {
        "ticker": ticker,
        "signal_type": "EXIT_PARTIAL" if partial else "EXIT",
        "signal_date": today_str(),
        "reason": reason,
        "entry_price": entry_price,
        "current_price": current_price,
        "gain_pct": gain_pct,
        "partial_fraction": partial_fraction if partial else 1.0,
        "holding_days": position.get("holding_days", 0),
    }


def _make_review_record(ticker: str, indicators: dict) -> dict:
    """Build a needs-catalyst-review record."""
    return {
        "ticker": ticker,
        "scan_date": today_str(),
        "close": indicators.get("close"),
        "volume_ratio": indicators.get("volume_ratio"),
        "return_20d": indicators.get("return_20d"),
        "relative_strength_20d": indicators.get("relative_strength_20d"),
        "above_20d_high": indicators.get("above_20d_high"),
        "above_50d_ma": indicators.get("above_50d_ma"),
        "note": "Passes technical filters — needs catalyst review",
    }


# ============================================================
# Alpha filter helpers
# ============================================================

_SECTOR_ETFS = {
    "Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}


def _get_sector_etf_trend(sector: str, etf_cache: dict) -> bool:
    """
    Returns True if the sector's ETF is above its 50-day MA (healthy sector trend).
    Returns True (allow trade) if the ETF is unknown or data is unavailable.
    """
    etf = _SECTOR_ETFS.get(sector)
    if not etf:
        return True  # Unknown sector — don't block

    if etf in etf_cache:
        return etf_cache[etf]

    try:
        import yfinance as yf
        df = yf.download(etf, period="90d", progress=False, auto_adjust=True)
        if df is None or df.empty or len(df) < 50:
            etf_cache[etf] = True
            return True
        close = df["Close"].dropna()
        ma50 = close.rolling(50).mean().iloc[-1]
        last = float(close.iloc[-1])
        result = float(last) > float(ma50)
        etf_cache[etf] = result
        return result
    except Exception:
        etf_cache[etf] = True
        return True


def _quick_composite_score(sig: dict) -> float:
    """
    Fast composite score for ranking buy signals when applying the daily entry cap.
    Higher is better.
    """
    try:
        cat = float(sig.get("catalyst_score") or 8)
        vol = float(sig.get("volume_ratio") or 1)
        rs = float(sig.get("relative_strength_20d") or 0)
        ret = float(sig.get("return_20d") or 0)
        return cat * 2 + vol * 0.5 + rs * 10 + ret * 5
    except Exception:
        return 0.0


def _is_near_earnings(ticker: str, days: int = 5) -> bool:
    """
    Returns True if the next earnings date is within `days` calendar days.

    Uses yfinance calendar data. Defaults to False (allow trade) if the
    data is unavailable or the request fails — we never block on uncertainty.
    """
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return False
        if hasattr(cal, "empty") and cal.empty:
            return False

        today = date.today()
        cutoff = today + timedelta(days=days)

        # yfinance returns a dict in newer versions
        if isinstance(cal, dict):
            earn_raw = cal.get("Earnings Date")
            if earn_raw is None:
                return False
            candidates = list(earn_raw) if hasattr(earn_raw, "__iter__") and not isinstance(earn_raw, str) else [earn_raw]
        else:
            # DataFrame format (older yfinance)
            try:
                candidates = list(cal.loc["Earnings Date"].dropna())
            except (KeyError, AttributeError):
                return False

        for d in candidates:
            try:
                earn_date = d.date() if hasattr(d, "date") else d
                if isinstance(earn_date, date) and today <= earn_date <= cutoff:
                    logger.debug(f"{ticker}: earnings on {earn_date} — within {days}d window")
                    return True
            except Exception:
                pass
        return False
    except Exception:
        return False  # never block on failure — default to allow


def _get_sector(ticker: str, cache: dict) -> str:
    """
    Return the GICS sector for a ticker using yfinance.

    Results are stored in `cache` to avoid redundant API calls within a scan.
    Returns 'Unknown' if the lookup fails.
    """
    if ticker in cache:
        return cache[ticker]
    try:
        import yfinance as yf
        sector = yf.Ticker(ticker).info.get("sector") or "Unknown"
        cache[ticker] = str(sector)
        return cache[ticker]
    except Exception:
        cache[ticker] = "Unknown"
        return "Unknown"


# ============================================================
# Buy signal evaluation
# ============================================================

def evaluate_buy_signal(
    ticker: str,
    indicators: dict,
    catalysts: dict,
    config,
) -> Tuple[Optional[dict], Optional[dict]]:
    """
    Evaluate whether a ticker qualifies for a BUY signal.

    Returns a tuple: (buy_signal, review_record).
    Exactly one will be non-None, or both will be None.

      buy_signal    — all 6 conditions passed
      review_record — 5 technical conditions passed, but no valid catalyst
      (None, None)  — failed a technical condition
    """
    min_vol_ratio = getattr(config, "min_volume_ratio", 2.5)
    min_return_20d = getattr(config, "min_20d_return", 0.10)
    min_rs = getattr(config, "min_relative_strength_20d", 0.08)
    min_catalyst_score = getattr(config, "min_catalyst_score", 8)
    max_extension = getattr(config, "max_extension_pct", 0.30)
    earnings_days = getattr(config, "earnings_avoidance_days", 5)
    max_below_52w = getattr(config, "max_pct_below_52w_high", 0.15)
    require_multi_vol = getattr(config, "require_multi_day_volume", True)
    min_adx = getattr(config, "min_adx", 25)
    max_tightness = getattr(config, "max_pre_breakout_range_pct", 0.08)

    if not indicators.get("enough_data"):
        logger.debug(f"{ticker}: not enough price history, skipping")
        return None, None

    if indicators.get("error"):
        logger.debug(f"{ticker}: indicator error — {indicators['error']}")
        return None, None

    close = indicators.get("close")
    if close is None or close <= 0:
        return None, None

    # ---- Check all 5 core technical conditions ----
    cond_breakout = bool(indicators.get("above_20d_high", False))
    cond_trend = bool(indicators.get("above_50d_ma", False))

    vol_ratio = indicators.get("volume_ratio")
    cond_volume = vol_ratio is not None and vol_ratio >= min_vol_ratio

    ret_20d = indicators.get("return_20d")
    cond_momentum = ret_20d is not None and ret_20d >= min_return_20d

    rs = indicators.get("relative_strength_20d")
    cond_rs = rs is not None and rs >= min_rs

    if not (cond_breakout and cond_trend and cond_volume and cond_momentum and cond_rs):
        return None, None

    # ---- Feature 3: Multi-timeframe momentum confirmation ----
    # 5d and 10d returns must both be non-negative — stock still accelerating.
    # If data is missing (None), we give the benefit of the doubt.
    ret_5d = indicators.get("return_5d")
    ret_10d = indicators.get("return_10d")
    if ret_5d is not None and ret_5d < 0:
        logger.debug(f"{ticker}: 5d return {ret_5d:.1%} is negative — fading momentum, skip")
        return None, None
    if ret_10d is not None and ret_10d < 0:
        logger.debug(f"{ticker}: 10d return {ret_10d:.1%} is negative — fading momentum, skip")
        return None, None

    # ---- Feature 6: Overbought / extension filter ----
    pct_above = indicators.get("pct_above_20d_ma")
    if pct_above is not None and pct_above >= max_extension:
        logger.debug(f"{ticker}: {pct_above:.1%} above 20d MA (limit {max_extension:.0%}) — skip")
        return None, None

    # ---- Feature 2: 52-week high proximity ----
    # Only enter stocks within max_below_52w of their 52-week high.
    pct_below = indicators.get("pct_below_52w_high")
    if pct_below is not None and pct_below > max_below_52w:
        logger.debug(f"{ticker}: {pct_below:.1%} below 52w high (limit {max_below_52w:.0%}) — overhead supply, skip")
        return None, None

    # ---- Feature 3: Multi-day volume accumulation ----
    # Require elevated volume on at least 1 of the prior 2 days.
    if require_multi_vol:
        vol_prev = indicators.get("volume_elevated_prev_2d")
        if vol_prev is not None and not vol_prev:
            logger.debug(f"{ticker}: no elevated volume in prior 2 days — single-day spike, skip")
            return None, None

    # ---- Feature 5: ADX trend strength ----
    if min_adx > 0:
        adx = indicators.get("adx_14")
        if adx is not None and adx < min_adx:
            logger.debug(f"{ticker}: ADX {adx:.1f} < {min_adx} — weak trend, skip")
            return None, None

    # ---- Feature 6b: Pre-breakout tightness ----
    if max_tightness > 0:
        tightness = indicators.get("pre_breakout_tightness")
        if tightness is not None and tightness > max_tightness:
            logger.debug(f"{ticker}: pre-breakout range {tightness:.1%} > {max_tightness:.0%} — choppy, skip")
            return None, None

    # ---- Check catalyst condition ----
    catalyst = get_tradeable_catalyst(ticker, catalysts, min_score=min_catalyst_score)

    if catalyst is None:
        logger.debug(f"{ticker}: all technical conditions pass, no tradeable catalyst — queued for review")
        return None, _make_review_record(ticker, indicators)

    # ---- Feature 2: Earnings date avoidance ----
    # This is a network call, so we defer it until after all other checks pass.
    if earnings_days > 0 and _is_near_earnings(ticker, days=earnings_days):
        logger.info(
            f"{ticker}: earnings within {earnings_days} days — skipping to avoid binary event risk"
        )
        return None, None

    # ---- All conditions passed — BUY signal ----
    logger.info(
        f"{ticker}: BUY  close={close:.2f}  volR={vol_ratio:.1f}x  "
        f"ret={ret_20d:.1%}  rs={rs:.1%}  cat={catalyst.get('catalyst_score')}"
    )
    return _make_buy_signal(ticker, indicators, catalyst), None


# ============================================================
# Exit signal evaluation
# ============================================================

def evaluate_exit_signal(
    ticker: str,
    position: dict,
    indicators: dict,
    config,
) -> List[dict]:
    """
    Check all exit conditions for a held position.

    Position dict should contain:
        entry_price:    float — price at which we bought
        holding_days:   int   — number of trading days held
        shares:         int   — number of shares held
        partial_taken:  bool  — whether a partial sell has already happened

    Returns a list of exit signal dicts (usually 0 or 1 entries).
    Stops at the first triggered condition.
    """
    stop_loss_pct = getattr(config, "stop_loss_pct", 0.08)
    atr_multiplier = getattr(config, "atr_stop_multiplier", 1.5)
    atr_max_stop = getattr(config, "atr_max_stop_pct", 0.12)
    breakeven_trigger = getattr(config, "breakeven_trigger_pct", 0.20)
    partial_trigger = getattr(config, "partial_take_profit_trigger_pct", 0.40)
    big_winner_trigger = getattr(config, "big_winner_trigger_pct", 0.75)
    max_holding_days = getattr(config, "max_holding_days", 20)

    entry_price = position.get("entry_price") or 0.0
    holding_days = position.get("holding_days") or 0
    partial_taken = position.get("partial_taken", False)
    current_price = indicators.get("close") or 0.0

    # Feature 1: ATR-based adaptive stop loss
    # Compute effective_stop_pct from ATR; fall back to fixed stop_loss_pct.
    # Clamped to [stop_loss_pct/2, atr_max_stop] so it's never trivially tight
    # or dangerously wide.
    atr_14 = indicators.get("atr_14")
    if atr_14 and atr_14 > 0 and current_price > 0:
        atr_stop_pct = atr_multiplier * atr_14 / current_price
        effective_stop_pct = max(min(atr_stop_pct, atr_max_stop), stop_loss_pct * 0.5)
    else:
        effective_stop_pct = stop_loss_pct

    if entry_price <= 0 or current_price <= 0:
        return []

    gain_pct = (current_price / entry_price) - 1.0

    # ---- Winner management: big winner (>= +75%) ----
    if gain_pct >= big_winner_trigger:
        ma_20 = indicators.get("ma_20")
        if ma_20 and current_price < ma_20:
            return [_make_exit_signal(ticker, "big_winner_20d_ma_trail", indicators, position)]

    # ---- Winner management: partial take-profit (>= +40%) ----
    elif gain_pct >= partial_trigger:
        if not partial_taken:
            return [_make_exit_signal(
                ticker, "partial_take_profit_40pct", indicators, position,
                partial=True, partial_fraction=1/3,
            )]
        # After partial was taken, trail the rest on 10-day MA
        ma_10 = indicators.get("ma_10")
        if ma_10 and current_price < ma_10:
            return [_make_exit_signal(ticker, "trail_10d_ma_after_partial", indicators, position)]

    # ---- Winner management: breakeven stop (>= +20%) ----
    elif gain_pct >= breakeven_trigger:
        # Stop has been moved to entry price — sell if we drop below it
        if current_price < entry_price:
            return [_make_exit_signal(ticker, "breakeven_stop", indicators, position)]
        # Also trail on 10-day MA from this point
        if indicators.get("below_10d_ma", False):
            return [_make_exit_signal(ticker, "below_10d_ma", indicators, position)]

    else:
        # ---- ATR-adaptive stop loss ----
        stop_price = entry_price * (1.0 - effective_stop_pct)
        if current_price <= stop_price:
            return [_make_exit_signal(
                ticker, f"stop_loss_{effective_stop_pct:.0%}", indicators, position
            )]

    # ---- Condition 2: below 10-day MA ----
    if indicators.get("below_10d_ma", False):
        return [_make_exit_signal(ticker, "below_10d_ma", indicators, position)]

    # ---- Condition 3: RS weakness streak ----
    if indicators.get("rs_weakness_5d", False):
        return [_make_exit_signal(ticker, "rs_weakness_5d", indicators, position)]

    # ---- Condition 4: volume stall ----
    if indicators.get("volume_stall_5d", False):
        return [_make_exit_signal(ticker, "volume_stall_5d", indicators, position)]

    # ---- Condition 5: time limit ----
    if holding_days >= max_holding_days:
        return [_make_exit_signal(
            ticker, f"max_holding_days_{max_holding_days}", indicators, position
        )]

    return []


# ============================================================
# Main scan function
# ============================================================

def run_signal_scan(
    universe: List[str],
    price_data: Dict[str, pd.DataFrame],
    spy_df: pd.DataFrame,
    catalysts: dict,
    config,
    held_positions: Optional[Dict[str, dict]] = None,
) -> dict:
    """
    Run the full signal scan over the discovered universe.

    Args:
        universe:        List of ticker symbols to evaluate
        price_data:      Dict of {ticker: OHLCV DataFrame}
        spy_df:          SPY OHLCV DataFrame (benchmark)
        catalysts:       Dict from load_catalysts()
        config:          AppConfig object
        held_positions:  Dict of {ticker: position_dict} for exit checking

    Returns:
        Dict with:
            buy_signals:     List of buy signal dicts
            exit_signals:    List of exit signal dicts
            needs_review:    List of review record dicts
            scan_date:       Today's date string
            tickers_scanned: Count of tickers evaluated
            errors:          Count of tickers with data errors
    """
    if held_positions is None:
        held_positions = {}

    buy_signals = []
    exit_signals = []
    needs_review = []
    error_count = 0

    # ---- Market regime filter ----
    # Skip new BUY entries when SPY is below its 50-day MA (bear market / correction).
    # EXIT signals are never blocked — protecting open positions always takes priority.
    market_uptrend = True
    if spy_df is not None and not spy_df.empty and len(spy_df) >= 50:
        try:
            spy_close = spy_df["Close"].iloc[-1]
            spy_ma50 = spy_df["Close"].rolling(50).mean().iloc[-1]
            market_uptrend = float(spy_close) > float(spy_ma50)
        except Exception:
            market_uptrend = True  # default to allowing buys if we can't calculate

    if not market_uptrend:
        logger.warning(
            "MARKET REGIME FILTER: SPY is below its 50-day MA — "
            "skipping all new BUY signals until market recovers. "
            "EXIT checks will still run."
        )

    logger.info(f"Signal scan: evaluating {len(universe)} tickers")
    if held_positions:
        logger.info(f"  Checking exits for {len(held_positions)} held positions")

    for ticker in universe:
        stock_df = price_data.get(ticker)

        if stock_df is None or stock_df.empty:
            logger.debug(f"{ticker}: no price data available")
            error_count += 1
            continue

        indicators = calculate_all_indicators(stock_df, spy_df, config)

        if indicators.get("error"):
            error_count += 1

        # Exit check for held positions
        if ticker in held_positions:
            exits = evaluate_exit_signal(ticker, held_positions[ticker], indicators, config)
            exit_signals.extend(exits)

        # Buy check only for stocks we don't currently hold
        # and only when the market is in an uptrend (regime filter)
        if ticker not in held_positions and market_uptrend:
            buy_signal, review_record = evaluate_buy_signal(
                ticker, indicators, catalysts, config
            )
            if buy_signal:
                buy_signals.append(buy_signal)
            elif review_record:
                needs_review.append(review_record)

    # ---- Sector filters (concentration cap + ETF trend) ----
    sector_max = getattr(config, "sector_max_positions", 2)
    check_etf = getattr(config, "require_sector_etf_uptrend", True)

    if buy_signals and (sector_max > 0 or check_etf):
        sector_cache: dict = {}
        etf_cache: dict = {}

        # Count held positions per sector
        sector_counts: Dict[str, int] = {}
        for held_ticker in held_positions:
            s = _get_sector(held_ticker, sector_cache)
            if s != "Unknown":
                sector_counts[s] = sector_counts.get(s, 0) + 1

        filtered_buys = []
        for sig in buy_signals:
            sig_ticker = sig.get("ticker", "")
            s = _get_sector(sig_ticker, sector_cache)

            # Feature 4: Sector concentration cap
            if sector_max > 0 and s != "Unknown" and sector_counts.get(s, 0) >= sector_max:
                logger.info(
                    f"{sig_ticker}: sector '{s}' already has "
                    f"{sector_counts[s]}/{sector_max} positions — skipping"
                )
                continue

            # Feature 7: Sector ETF trend confirmation
            if check_etf and not _get_sector_etf_trend(s, etf_cache):
                etf_sym = _SECTOR_ETFS.get(s, "?")
                logger.info(
                    f"{sig_ticker}: sector ETF {etf_sym} ({s}) is below its 50MA — skip"
                )
                continue

            filtered_buys.append(sig)
            if s != "Unknown":
                sector_counts[s] = sector_counts.get(s, 0) + 1

        removed = len(buy_signals) - len(filtered_buys)
        if removed:
            logger.info(f"Sector filters removed {removed} buy signal(s)")
        buy_signals = filtered_buys

    # ---- Feature 4: Daily entry cap (top N by composite score) ----
    max_entries = getattr(config, "max_daily_entries", 3)
    if max_entries > 0 and len(buy_signals) > max_entries:
        buy_signals.sort(key=_quick_composite_score, reverse=True)
        logger.info(
            f"Daily entry cap: keeping top {max_entries} of {len(buy_signals)} signals "
            f"(removed {len(buy_signals) - max_entries} lower-ranked)"
        )
        buy_signals = buy_signals[:max_entries]

    logger.info(
        f"Scan complete: {len(buy_signals)} buy, {len(exit_signals)} exit, "
        f"{len(needs_review)} need review, {error_count} data errors"
    )

    return {
        "buy_signals": buy_signals,
        "exit_signals": exit_signals,
        "needs_review": needs_review,
        "scan_date": today_str(),
        "tickers_scanned": len(universe),
        "errors": error_count,
        "market_uptrend": market_uptrend,
    }


# ============================================================
# Report writers
# ============================================================

def save_needs_review(
    needs_review: List[dict],
    path: str = "reports/needs_catalyst_review.csv",
) -> None:
    """
    Save stocks that passed technical filters but lack a catalyst.

    These are candidates for your manual catalyst research.
    Add their catalyst to data/catalyst_overrides.csv to include
    them in the next scan.
    """
    ensure_dir("reports")
    columns = [
        "ticker", "scan_date", "close", "volume_ratio", "return_20d",
        "relative_strength_20d", "above_20d_high", "above_50d_ma", "note",
    ]

    if not needs_review:
        pd.DataFrame(columns=columns).to_csv(path, index=False)
        logger.info("No stocks need catalyst review today.")
        return

    rows = []
    for rec in needs_review:
        rows.append({
            "ticker": rec.get("ticker", ""),
            "scan_date": rec.get("scan_date", today_str()),
            "close": _fmt(rec.get("close"), 2),
            "volume_ratio": _fmt(rec.get("volume_ratio"), 2),
            "return_20d": _fmt_pct(rec.get("return_20d")),
            "relative_strength_20d": _fmt_pct(rec.get("relative_strength_20d")),
            "above_20d_high": rec.get("above_20d_high", False),
            "above_50d_ma": rec.get("above_50d_ma", False),
            "note": rec.get("note", ""),
        })

    pd.DataFrame(rows).to_csv(path, index=False)
    logger.info(f"Saved {len(rows)} needs-review stocks → {path}")


def save_signals_csv(
    buy_signals: List[dict],
    exit_signals: List[dict],
    path: str = "reports/paper_signals.csv",
) -> None:
    """
    Save all buy and exit signals to paper_signals.csv.
    """
    ensure_dir("reports")
    columns = [
        "signal_type", "signal_date", "ticker", "close", "volume_ratio",
        "return_20d", "relative_strength_20d", "above_20d_high", "above_50d_ma",
        "catalyst_score", "catalyst_type", "catalyst_headline", "rank_score", "reason",
    ]

    rows = []

    for sig in buy_signals:
        rows.append({
            "signal_type": "BUY",
            "signal_date": sig.get("signal_date", today_str()),
            "ticker": sig.get("ticker", ""),
            "close": _fmt(sig.get("close"), 2),
            "volume_ratio": _fmt(sig.get("volume_ratio"), 2),
            "return_20d": _fmt_pct(sig.get("return_20d")),
            "relative_strength_20d": _fmt_pct(sig.get("relative_strength_20d")),
            "above_20d_high": sig.get("above_20d_high", False),
            "above_50d_ma": sig.get("above_50d_ma", False),
            "catalyst_score": sig.get("catalyst_score", ""),
            "catalyst_type": sig.get("catalyst_type", ""),
            "catalyst_headline": sig.get("catalyst_headline", ""),
            "rank_score": _fmt(sig.get("rank_score"), 4),
            "reason": "",
        })

    for sig in exit_signals:
        rows.append({
            "signal_type": sig.get("signal_type", "EXIT"),
            "signal_date": sig.get("signal_date", today_str()),
            "ticker": sig.get("ticker", ""),
            "close": _fmt(sig.get("current_price"), 2),
            "volume_ratio": "",
            "return_20d": _fmt_pct(sig.get("gain_pct")),
            "relative_strength_20d": "",
            "above_20d_high": "",
            "above_50d_ma": "",
            "catalyst_score": "",
            "catalyst_type": "",
            "catalyst_headline": "",
            "rank_score": "",
            "reason": sig.get("reason", ""),
        })

    if rows:
        pd.DataFrame(rows).to_csv(path, index=False)
        logger.info(f"Saved {len(rows)} signals → {path}")
    else:
        pd.DataFrame(columns=columns).to_csv(path, index=False)
        logger.info(f"No signals today. Empty file → {path}")


# ============================================================
# Private helpers
# ============================================================

def _fmt(value, decimals: int = 2) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return ""


def _fmt_pct(value) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return ""

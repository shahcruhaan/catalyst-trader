"""
src/catalyst_scorer.py — Catalyst Loading and Scoring

A "catalyst" is a real business event that explains why a stock is moving.
Examples: earnings beat, FDA approval, large contract win, new product launch.

This module reads data/catalyst_overrides.csv — a file you edit manually to
add catalyst data for stocks you want the bot to consider trading.

In v1, catalysts are entirely manual. You research the news, decide the score,
and add a row. The bot reads what you wrote.

Future versions (Stage 9) will call Claude/OpenAI here instead.

The bot WILL NOT trade a stock unless:
  1. A catalyst entry exists for that ticker within the freshness window
  2. The catalyst_score is >= min_catalyst_score (default 8)
  3. tradeable is set to "true"

Stocks that pass price/volume/momentum filters but have no valid catalyst
are placed in reports/needs_catalyst_review.csv for your manual review.

Catalyst CSV format (data/catalyst_overrides.csv):
  date, ticker, catalyst_type, headline, summary,
  catalyst_score, tradeable, confidence

  date:           YYYY-MM-DD — when you scored this catalyst
  ticker:         Stock symbol (e.g. NVDA)
  catalyst_type:  earnings / guidance / contract / FDA / analyst / sector / other
  headline:       One-line news headline
  summary:        Brief description of the business impact
  catalyst_score: 1–10 (10 = strongest possible catalyst)
  tradeable:      true or false
  confidence:     low / medium / high

Scoring guidelines:
  9–10: Exceptional — quantified beat + raised guidance + institutional confirmation
  7–8:  Strong — clear positive surprise with meaningful business impact
  5–6:  Moderate — positive but mixed or uncertain business impact
  3–4:  Weak — vague, speculative, or already priced in
  1–2:  Very weak — not actionable

LLM-ready placeholder:
  The function llm_score_catalyst() is a clean interface ready for
  a Claude or OpenAI call in a future stage.
"""

import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from src.utils import setup_logger, load_csv_safe, normalize_ticker, today_str, ensure_dir

logger = setup_logger(__name__)

# How many calendar days old a catalyst can be and still be considered "fresh".
# A catalyst scored today is fresh. One scored 10 days ago is stale.
DEFAULT_MAX_CATALYST_AGE_DAYS = 10


# ============================================================
# Loading
# ============================================================

def load_catalysts(
    path: str = "data/catalyst_overrides.csv",
) -> Dict[str, List[dict]]:
    """
    Load all catalyst data from the CSV file.

    Returns a dictionary mapping uppercase ticker symbols to a list of
    catalyst rows for that ticker:
        {
            "NVDA": [
                {
                    "date": "2026-06-02",
                    "ticker": "NVDA",
                    "catalyst_type": "earnings",
                    "headline": "Nvidia beats and raises guidance",
                    "summary": "Strong AI chip demand...",
                    "catalyst_score": 9,
                    "tradeable": True,
                    "confidence": "high",
                },
                ...
            ],
            "META": [...],
        }

    If the file does not exist or cannot be read, returns an empty dict.

    Args:
        path: Path to catalyst_overrides.csv (default: "data/catalyst_overrides.csv")

    Returns:
        Dict of {ticker: [list of catalyst dicts]}
    """
    if not os.path.exists(path):
        logger.warning(f"Catalyst file not found: {path}")
        logger.info("  To add catalysts, edit data/catalyst_overrides.csv")
        return {}

    df = load_csv_safe(path)
    if df is None or df.empty:
        logger.warning(f"Catalyst file is empty: {path}")
        return {}

    required_columns = ["date", "ticker", "catalyst_score", "tradeable"]
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        logger.error(
            f"catalyst_overrides.csv is missing columns: {missing}\n"
            "  Expected columns: date, ticker, catalyst_type, headline, "
            "summary, catalyst_score, tradeable, confidence"
        )
        return {}

    catalysts: Dict[str, List[dict]] = {}

    for _, row in df.iterrows():
        try:
            ticker = normalize_ticker(str(row.get("ticker", "") or ""))
            if not ticker:
                continue

            # Parse catalyst_score safely
            score_raw = row.get("catalyst_score", 0)
            try:
                score = int(float(score_raw))
            except (ValueError, TypeError):
                score = 0

            # Parse tradeable: accepts "true", "True", True, 1
            tradeable_raw = str(row.get("tradeable", "false")).strip().lower()
            tradeable = tradeable_raw in ("true", "1", "yes")

            entry = {
                "date": str(row.get("date", "")).strip(),
                "ticker": ticker,
                "catalyst_type": str(row.get("catalyst_type", "other")).strip(),
                "headline": str(row.get("headline", "")).strip(),
                "summary": str(row.get("summary", "")).strip(),
                "catalyst_score": score,
                "tradeable": tradeable,
                "confidence": str(row.get("confidence", "medium")).strip().lower(),
            }

            if ticker not in catalysts:
                catalysts[ticker] = []
            catalysts[ticker].append(entry)

        except Exception as e:
            logger.debug(f"Skipping malformed catalyst row: {e}")
            continue

    logger.debug(
        f"Loaded catalysts for {len(catalysts)} tickers from {path}"
    )
    return catalysts


# ============================================================
# Freshness check
# ============================================================

def is_catalyst_fresh(
    catalyst_date_str: str,
    max_days_old: int = DEFAULT_MAX_CATALYST_AGE_DAYS,
) -> bool:
    """
    Check whether a catalyst is recent enough to use.

    A catalyst scored today (date == today) is definitely fresh.
    A catalyst scored more than max_days_old calendar days ago is stale.

    Why this matters:
        An earnings beat from last quarter is no longer a fresh catalyst.
        The market has already reacted. We want catalysts from the last
        ~10 days that are still driving current price action.

    Args:
        catalyst_date_str: Date string in YYYY-MM-DD format (from the CSV)
        max_days_old:       Maximum age in calendar days

    Returns:
        True if the catalyst is fresh, False if stale or unparseable.
    """
    if not catalyst_date_str or catalyst_date_str.strip() in ("", "nan", "None"):
        return False
    try:
        catalyst_date = datetime.strptime(catalyst_date_str[:10], "%Y-%m-%d").date()
        today = datetime.now().date()
        age_days = (today - catalyst_date).days
        return 0 <= age_days <= max_days_old
    except (ValueError, TypeError):
        logger.debug(f"Could not parse catalyst date: {catalyst_date_str!r}")
        return False


# ============================================================
# Best catalyst lookup
# ============================================================

def get_best_catalyst(
    ticker: str,
    catalysts: Dict[str, List[dict]],
    max_days_old: int = DEFAULT_MAX_CATALYST_AGE_DAYS,
    min_score: int = 0,
) -> Optional[dict]:
    """
    Find the best catalyst for a ticker from the loaded catalyst data.

    "Best" means:
      1. Must be fresh (within max_days_old days)
      2. Must have catalyst_score >= min_score
      3. Must have tradeable = True (unless min_score == 0, for review purposes)
      4. Among qualifying catalysts, pick the one with the highest score
         (most recent if tied)

    Args:
        ticker:      Uppercase stock symbol
        catalysts:   Dict from load_catalysts()
        max_days_old: Maximum age in calendar days
        min_score:   Minimum score to qualify (0 = include all)

    Returns:
        Best catalyst dict, or None if no qualifying catalyst is found.
    """
    sym = normalize_ticker(ticker)
    ticker_catalysts = catalysts.get(sym, [])

    if not ticker_catalysts:
        return None

    qualifying = []
    for cat in ticker_catalysts:
        # Must be fresh
        if not is_catalyst_fresh(cat["date"], max_days_old):
            continue
        # Must meet minimum score
        if cat["catalyst_score"] < min_score:
            continue
        qualifying.append(cat)

    if not qualifying:
        return None

    # Sort by score descending, then by date descending (most recent first)
    qualifying.sort(
        key=lambda c: (c["catalyst_score"], c["date"]),
        reverse=True,
    )
    return qualifying[0]


def get_tradeable_catalyst(
    ticker: str,
    catalysts: Dict[str, List[dict]],
    min_score: int = 8,
    max_days_old: int = DEFAULT_MAX_CATALYST_AGE_DAYS,
) -> Optional[dict]:
    """
    Find a tradeable catalyst for a ticker — one that is:
      - Fresh (within max_days_old days)
      - Has catalyst_score >= min_score
      - Has tradeable = True

    This is the function used by the signal engine to decide if a stock
    is eligible for a buy signal.

    Args:
        ticker:      Uppercase stock symbol
        catalysts:   Dict from load_catalysts()
        min_score:   Minimum catalyst score (default 8)
        max_days_old: Maximum age in calendar days

    Returns:
        Best tradeable catalyst dict, or None if none qualifies.
    """
    sym = normalize_ticker(ticker)
    ticker_catalysts = catalysts.get(sym, [])

    if not ticker_catalysts:
        return None

    qualifying = []
    for cat in ticker_catalysts:
        if not is_catalyst_fresh(cat["date"], max_days_old):
            continue
        if cat["catalyst_score"] < min_score:
            continue
        if not cat["tradeable"]:
            continue
        qualifying.append(cat)

    if not qualifying:
        return None

    qualifying.sort(
        key=lambda c: (c["catalyst_score"], c["date"]),
        reverse=True,
    )
    return qualifying[0]


# ============================================================
# Catalyst summary helpers
# ============================================================

def summarize_catalyst(catalyst: Optional[dict]) -> str:
    """
    Create a short human-readable summary of a catalyst.

    Args:
        catalyst: A catalyst dict from get_best_catalyst()

    Returns:
        A one-line string describing the catalyst.
        "No catalyst" if the input is None.

    Examples:
        "earnings [score: 9/10] — Nvidia beats and raises guidance"
        "No catalyst"
    """
    if catalyst is None:
        return "No catalyst"
    score = catalyst.get("catalyst_score", 0)
    ctype = catalyst.get("catalyst_type", "unknown")
    headline = catalyst.get("headline", "")
    tradeable = catalyst.get("tradeable", False)
    t_flag = "" if tradeable else " [NOT TRADEABLE]"
    return f"{ctype} [score: {score}/10]{t_flag} — {headline}"


def get_catalyst_count_summary(
    catalysts: Dict[str, List[dict]],
    max_days_old: int = DEFAULT_MAX_CATALYST_AGE_DAYS,
) -> dict:
    """
    Return a summary of how many catalysts are loaded and how many are fresh.

    Used by the paper-scan command for status display.

    Returns:
        Dict with keys: total_tickers, fresh_tickers, tradeable_tickers, ticker_list
    """
    total = len(catalysts)
    fresh_tickers = []
    tradeable_tickers = []

    for ticker, entries in catalysts.items():
        has_fresh = any(is_catalyst_fresh(e["date"], max_days_old) for e in entries)
        has_tradeable = any(
            is_catalyst_fresh(e["date"], max_days_old)
            and e["tradeable"]
            and e["catalyst_score"] >= 8
            for e in entries
        )
        if has_fresh:
            fresh_tickers.append(ticker)
        if has_tradeable:
            tradeable_tickers.append(ticker)

    return {
        "total_tickers": total,
        "fresh_tickers": len(fresh_tickers),
        "tradeable_tickers": len(tradeable_tickers),
        "tradeable_ticker_list": sorted(tradeable_tickers),
    }


# ============================================================
# LLM-ready placeholder (for future Stage 9 upgrade)
# ============================================================

def llm_score_catalyst(
    ticker: str,
    headline: str,
    summary: str,
    stock_price: Optional[float] = None,
) -> Optional[dict]:
    """
    PLACEHOLDER — Future integration point for Claude/OpenAI catalyst scoring.

    In a future stage, this function will send the catalyst details to an
    LLM API and receive a structured JSON score back.

    For now, it always returns None (not implemented).
    The manual CSV workflow in catalyst_overrides.csv is used instead.

    Expected future output format:
        {
            "ticker": "XYZ",
            "catalyst_score": 8,
            "tradeable": True,
            "one_sentence_reason": "...",
            "bullish_mechanism": "...",
            "risk": "...",
            "catalyst_type": "earnings",
            "confidence": "high",
        }

    Args:
        ticker:      Stock symbol
        headline:    News headline to score
        summary:     Brief news summary
        stock_price: Current stock price (helps contextualize impact)

    Returns:
        None (placeholder — not yet implemented)
    """
    # This function is intentionally not implemented in v1.
    # To enable LLM scoring in a future stage:
    #   1. Install the anthropic or openai package
    #   2. Add your API key to .env
    #   3. Replace 'return None' with a real API call
    logger.debug(f"llm_score_catalyst called for {ticker} — not yet implemented (v1)")
    return None


# ============================================================
# Stage 9: Automated catalyst loading and persistence
# ============================================================

_AUTO_SCORES_PATH = "reports/automated_catalyst_scores.csv"


def load_auto_catalysts(
    path: str = _AUTO_SCORES_PATH,
) -> Dict[str, List[dict]]:
    """
    Load automatically-scored catalysts from reports/automated_catalyst_scores.csv.

    These are generated by the heuristic scorer (or LLM if enabled) during
    the daily score_catalysts pipeline step.

    Returns the same {ticker: [list of catalyst dicts]} format as
    load_catalysts() so the two can be merged transparently.

    Only includes rows where final_decision == "trade" — rejected or
    needs_review rows are excluded from the trading pipeline.

    Args:
        path: Path to automated_catalyst_scores.csv

    Returns:
        Dict of {ticker: [list of catalyst dicts]} or {} if file missing.
    """
    if not os.path.exists(path):
        logger.debug(f"No auto-scored catalysts yet: {path}")
        return {}

    df = load_csv_safe(path)
    if df is None or df.empty:
        return {}

    required = ["date", "ticker", "catalyst_score", "tradeable"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.warning(f"automated_catalyst_scores.csv missing columns: {missing}")
        return {}

    catalysts: Dict[str, List[dict]] = {}

    for _, row in df.iterrows():
        try:
            ticker = normalize_ticker(str(row.get("ticker", "") or ""))
            if not ticker:
                continue

            # Only include rows approved for trading
            final_decision = str(row.get("final_decision", "trade")).strip().lower()
            if final_decision in ("reject", "needs_review"):
                continue

            try:
                score = int(float(row.get("catalyst_score", 0)))
            except (ValueError, TypeError):
                score = 0

            tradeable_raw = str(row.get("tradeable", "false")).strip().lower()
            tradeable = tradeable_raw in ("true", "1", "yes")

            # Use source_headlines_used as the headline field if available
            headline = (
                str(row.get("source_headlines_used", "") or "").split(" | ")[0].strip()
                or str(row.get("headline", "") or "").strip()
            )

            entry = {
                "date": str(row.get("date", "")).strip(),
                "ticker": ticker,
                "catalyst_type": str(row.get("catalyst_type", "automated")).strip(),
                "headline": headline[:200],
                "summary": str(row.get("one_sentence_reason", "")).strip(),
                "catalyst_score": score,
                "tradeable": tradeable,
                "confidence": str(row.get("confidence", "medium")).strip().lower(),
                "score_method": str(row.get("score_method", "heuristic")).strip(),
            }

            if ticker not in catalysts:
                catalysts[ticker] = []
            catalysts[ticker].append(entry)

        except Exception as e:
            logger.debug(f"Skipping malformed auto-catalyst row: {e}")
            continue

    logger.debug(
        f"Loaded auto-scored catalysts for {len(catalysts)} tickers from {path}"
    )
    return catalysts


def load_all_catalysts(config=None) -> Dict[str, List[dict]]:
    """
    Load all catalysts: manual overrides merged with automated scores.

    Priority order (manual always wins):
      1. Manual overrides (data/catalyst_overrides.csv) — take complete priority.
         If a ticker appears in manual overrides, only its manual entry is used.
      2. Automated scores (reports/automated_catalyst_scores.csv) — used for
         any ticker that has NO manual override.

    This guarantees that human-curated catalyst data is never silently
    overridden by the automated scoring system.

    Args:
        config: AppConfig object (used to resolve file paths)

    Returns:
        Merged {ticker: [list of catalyst dicts]}
    """
    manual_path = "data/catalyst_overrides.csv"
    auto_path = _AUTO_SCORES_PATH

    if config is not None:
        manual_path = getattr(config, "manual_catalyst_path", manual_path)
        auto_path = getattr(config, "auto_catalyst_scores_path", auto_path)

    manual = load_catalysts(manual_path)
    auto = load_auto_catalysts(auto_path)

    # Start with auto, then let manual overwrite (manual takes priority per ticker)
    merged: Dict[str, List[dict]] = dict(auto)
    for ticker, entries in manual.items():
        merged[ticker] = entries

    auto_only = sum(1 for t in merged if t not in manual)
    logger.debug(
        f"load_all_catalysts: {len(manual)} manual, {auto_only} auto-only, "
        f"{len(merged)} total tickers"
    )
    return merged


def save_automated_scores(
    scores: Dict[str, dict],
    needs_review: List[str],
    config=None,
    date_str: str = "",
) -> None:
    """
    Save automated catalyst scores to:
      reports/automated_catalyst_scores.csv  — all scored tickers
      reports/needs_catalyst_review.csv      — tickers needing manual review

    This file is read by load_auto_catalysts() and merged with manual overrides
    so the signal engine can trade on auto-scored catalysts without manual input.

    Args:
        scores:      {ticker: score_result_dict} from heuristic_scorer
        needs_review: List of tickers that need manual review
        config:      AppConfig (for file paths)
        date_str:    Date string to stamp rows (default: today)
    """
    ensure_dir("reports")
    scores_path = _AUTO_SCORES_PATH
    review_path = "reports/needs_catalyst_review.csv"

    if config is not None:
        scores_path = getattr(config, "auto_catalyst_scores_path", scores_path)
        review_path = getattr(config, "needs_review_path", review_path)

    if not date_str:
        date_str = today_str()

    rows = []
    for ticker, result in scores.items():
        score = result.get("catalyst_score", 0)
        tradeable = result.get("tradeable", False)
        confidence = result.get("confidence", "low")
        neg_flags = result.get("negative_flags", [])

        if tradeable and score >= 8 and confidence in ("medium", "high"):
            final_decision = "trade"
            rejection_reason = ""
        elif neg_flags:
            final_decision = "reject"
            rejection_reason = f"negative flags: {', '.join(neg_flags[:2])}"
        elif score < 8:
            final_decision = "reject"
            rejection_reason = f"score {score} < 8 minimum"
        elif confidence == "low":
            final_decision = "needs_review"
            rejection_reason = "low confidence automated catalyst score"
        else:
            final_decision = "reject"
            rejection_reason = "catalyst criteria not met"

        rows.append({
            "date": date_str,
            "ticker": ticker,
            "catalyst_score": score,
            "tradeable": str(tradeable).lower(),
            "confidence": confidence,
            "catalyst_type": result.get("catalyst_type", "unknown"),
            "one_sentence_reason": result.get("one_sentence_reason", "")[:200],
            "bullish_mechanism": result.get("bullish_mechanism", "")[:200],
            "risk": result.get("risk", "")[:200],
            "matched_keywords": ",".join(result.get("matched_keywords", [])[:5]),
            "negative_flags": ",".join(neg_flags[:3]),
            "hype_flags": ",".join(result.get("hype_flags", [])[:3]),
            "source_headlines_used": " | ".join(
                result.get("headlines_used", [])[:3]
            )[:300],
            "score_method": result.get("score_method", "heuristic"),
            "final_decision": final_decision,
            "rejection_reason": rejection_reason,
        })

    if rows:
        import pandas as pd
        df = pd.DataFrame(rows)
        df.to_csv(scores_path, index=False)
        tradeable_count = sum(1 for r in rows if r["final_decision"] == "trade")
        logger.info(
            f"save_automated_scores: {len(rows)} tickers scored, "
            f"{tradeable_count} tradeable → {scores_path}"
        )

    if needs_review:
        import pandas as pd
        review_df = pd.DataFrame([
            {"date": date_str, "ticker": t, "reason": "no usable automated catalyst found"}
            for t in needs_review
        ])
        review_df.to_csv(review_path, index=False)
        logger.info(
            f"save_automated_scores: {len(needs_review)} needs-review tickers → {review_path}"
        )

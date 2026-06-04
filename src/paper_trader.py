"""
src/paper_trader.py — Paper Trading Workflow Orchestration

Stage 9 update: fully automated pipeline.

New in Stage 9:
  run_pull_news(config)
      Pulls recent news headlines for all tickers in the discovered universe.
      Uses AlpacaNewsProvider → YahooFinanceNewsProvider → ManualCSV fallback.
      Saves to reports/news_cache.csv.

  run_score_catalysts(config)
      Loads reports/news_cache.csv and scores every ticker with the
      heuristic catalyst scorer.  Saves to:
        reports/automated_catalyst_scores.csv (all scores)
        reports/needs_catalyst_review.csv     (tickers needing manual review)

  run_paper_scan(config) — UPDATED
      Now calls load_all_catalysts(config) instead of load_catalysts().
      This merges manual overrides (highest priority) with automated scores
      so the bot can trade on auto-scored catalysts without manual input.
      Also saves reports/tomorrow_orders.csv and reports/order_approval.csv
      when require_human_review_before_orders is enabled.

Existing functions (unchanged in behavior):
  run_paper_report(config)
  run_reconcile_with_portfolio(config)
  check_positions_for_exits(state, config)
  run_intraday_exit_check_and_execute(config)
"""

import os
from datetime import datetime

import pandas as pd

from src.broker_alpaca import AlpacaPaperBroker
from src.utils import setup_logger, today_str, ensure_dir, load_csv_safe
from src.portfolio import (
    update_peak_price,
    compute_portfolio_summary,
    format_positions_table,
    load_recent_orders,
)
from src.execution_engine import (
    load_state,
    save_state,
    reconcile_state,
    _DEFAULT_STATE_PATH,
    _DEFAULT_ORDER_LOG,
)

logger = setup_logger(__name__)


# ============================================================
# Stage 9: Automated news pulling
# ============================================================

def run_pull_news(config) -> dict:
    """
    Pull recent news headlines for every ticker in today's discovered universe.

    Provider chain: Alpaca → Yahoo Finance → ManualCSV fallback.
    All results are saved to reports/news_cache.csv (overwrites previous cache).

    Called by the scheduler at 16:30 ET (after discover_universe at 16:20).

    Args:
        config: AppConfig object

    Returns:
        {
            "news_pulled":       int — total news items saved
            "tickers_covered":   int — tickers that got at least one article
            "no_news_tickers":   int — tickers with no news from any provider
            "error":             str or None — set if the whole pull failed
        }
    """
    from src.universe_discovery import load_todays_universe, is_todays_universe_fresh
    from src.news_discovery import NewsDiscovery
    from src.utils import log_alert

    universe_path = getattr(
        config, "discovered_universe_path", "reports/daily_discovered_universe.csv"
    )
    alerts_path = getattr(config, "alerts_log_path", "reports/alerts_log.csv")

    if not is_todays_universe_fresh(universe_path):
        logger.warning("pull_news: no fresh universe found — news may not cover today's tickers")

    universe_df = load_todays_universe(universe_path)
    if universe_df is None or universe_df.empty:
        return {
            "news_pulled": 0,
            "tickers_covered": 0,
            "no_news_tickers": 0,
            "error": "No discovered universe — run discover-universe first",
        }

    ticker_col = "ticker" if "ticker" in universe_df.columns else universe_df.columns[0]
    tickers = list(universe_df[ticker_col].dropna().unique())

    logger.info(f"pull_news: pulling news for {len(tickers)} tickers")

    nd = NewsDiscovery(config)
    all_news, no_news_tickers = nd.get_news_for_discovered_universe(tickers)
    count_saved = nd.cache_news_to_csv(all_news, append=False)

    covered = len(tickers) - len(no_news_tickers)

    if no_news_tickers:
        log_alert(
            "news_provider_partial",
            "WARNING",
            f"{len(no_news_tickers)} tickers got no news from any provider",
            action_required="Check reports/needs_catalyst_review.csv",
            alerts_path=alerts_path,
        )

    log_alert(
        "news_pulled",
        "INFO",
        f"Pulled {count_saved} articles for {covered}/{len(tickers)} tickers",
        alerts_path=alerts_path,
    )

    logger.info(
        f"pull_news: {count_saved} articles saved, "
        f"{covered}/{len(tickers)} tickers covered, "
        f"{len(no_news_tickers)} with no news"
    )

    return {
        "news_pulled": count_saved,
        "tickers_covered": covered,
        "no_news_tickers": len(no_news_tickers),
        "error": None,
    }


# ============================================================
# Stage 9: Automated catalyst scoring
# ============================================================

def run_score_catalysts(config) -> dict:
    """
    Load cached news and score every ticker with the heuristic catalyst scorer.

    Steps:
      1. Load today's news from reports/news_cache.csv
      2. Group news by ticker
      3. Format each ticker's news for the scorer
      4. Score all tickers with score_all_tickers()
      5. Determine tradeable vs. needs_review classification
      6. Save reports/automated_catalyst_scores.csv and needs_catalyst_review.csv

    Called by the scheduler at 16:45 ET (after pull_news at 16:30).

    Args:
        config: AppConfig object

    Returns:
        {
            "scored":       int — number of tickers scored
            "tradeable":    int — tickers with score >= 8 and tradeable=True
            "needs_review": int — tickers that need manual review
            "error":        str or None — set if the whole step failed
        }
    """
    from src.news_discovery import load_cached_news, format_news_for_catalyst_scorer
    from src.heuristic_scorer import score_all_tickers, score_with_llm_if_enabled
    from src.catalyst_scorer import save_automated_scores
    from src.utils import log_alert

    cache_path = getattr(config, "news_cache_path", "reports/news_cache.csv")
    max_age = getattr(config, "news_max_news_age_days", 7)
    alerts_path = getattr(config, "alerts_log_path", "reports/alerts_log.csv")

    all_news = load_cached_news(cache_path, max_age_days=max_age)

    if not all_news:
        return {
            "scored": 0,
            "tradeable": 0,
            "needs_review": 0,
            "error": "No news in cache — run pull-news first",
        }

    # Group news by ticker
    tickers_with_news: set = set()
    for item in all_news:
        t = str(item.get("ticker", "") or "").upper()
        if t:
            tickers_with_news.add(t)

    news_by_ticker = {}
    for ticker in tickers_with_news:
        ticker_news = [
            i for i in all_news
            if str(i.get("ticker", "") or "").upper() == ticker
        ]
        news_by_ticker[ticker] = format_news_for_catalyst_scorer(ticker, ticker_news)

    scores = score_all_tickers(news_by_ticker, max_news_age_days=max_age)

    # ---- LLM re-scoring for borderline tickers ----
    # If use_llm_if_available is True and an API key is present, re-score tickers
    # whose heuristic score is 5-8 with the LLM for a more accurate read.
    # Scores of 9-10 are already clear buys; scores < 5 are clear rejects.
    use_llm = getattr(config, "use_llm_if_available", False)
    llm_upgraded = 0
    if use_llm:
        for ticker, result in scores.items():
            heuristic_score = result.get("catalyst_score", 0)
            if 5 <= heuristic_score <= 8:
                ticker_news = news_by_ticker.get(ticker, {})
                headlines = ticker_news.get("headlines", [])
                summaries = ticker_news.get("summaries", [])
                if not headlines:
                    continue
                llm_result = score_with_llm_if_enabled(
                    ticker, headlines, summaries=summaries, config=config
                )
                if llm_result and "catalyst_score" in llm_result:
                    scores[ticker] = llm_result
                    llm_upgraded += 1
                    logger.info(
                        f"LLM re-scored {ticker}: "
                        f"{heuristic_score} → {llm_result['catalyst_score']} "
                        f"(method={llm_result.get('score_method', 'llm')})"
                    )
        if llm_upgraded:
            logger.info(f"LLM re-scoring: upgraded {llm_upgraded} borderline tickers")

    # Tickers that scored too low or have low confidence go to needs_review
    needs_review = [
        t for t, r in scores.items()
        if (
            r.get("catalyst_score", 0) < 8
            or r.get("confidence") == "low"
            or not r.get("tradeable", False)
        )
        and r.get("catalyst_score", 0) > 0  # exclude no-news tickers (they go in review too)
    ]
    # Also flag tickers with no news at all
    no_news_in_scores = [t for t, r in scores.items() if r.get("catalyst_score", 0) == 0]
    needs_review = list(set(needs_review + no_news_in_scores))

    save_automated_scores(scores, needs_review, config, today_str())

    tradeable_count = sum(
        1 for r in scores.values()
        if r.get("tradeable") and r.get("catalyst_score", 0) >= 8
    )

    log_alert(
        "catalysts_scored",
        "INFO",
        f"Scored {len(scores)} tickers: {tradeable_count} tradeable, {len(needs_review)} needs review",
        alerts_path=alerts_path,
    )

    logger.info(
        f"score_catalysts: {len(scores)} tickers scored, "
        f"{tradeable_count} tradeable, {len(needs_review)} needs review"
    )

    return {
        "scored": len(scores),
        "tradeable": tradeable_count,
        "needs_review": len(needs_review),
        "error": None,
    }


# ============================================================
# Improved reconcile (Stage 6)
# ============================================================

def run_reconcile_with_portfolio(config) -> dict:
    """
    Sync paper_state.json with Alpaca — then update peak_price.

    Extends the base reconcile_state() with:
      1. Update peak_price for all positions using current_price
      2. Save the fully updated state to disk

    Args:
        config: AppConfig object — must pass require_paper_mode()

    Returns:
        Updated state dict (saved to disk at state_file_path).
    """
    config.require_paper_mode()
    broker = AlpacaPaperBroker.from_config(config)

    state_path = getattr(config, "state_file_path", _DEFAULT_STATE_PATH)
    state = load_state(state_path)

    state = reconcile_state(broker, config, state)
    state["positions"] = update_peak_price(state["positions"])

    save_state(state, state_path)
    logger.info(
        f"Portfolio reconcile complete: peak_price updated for "
        f"{len(state['positions'])} position(s)"
    )

    # ---- Drawdown circuit breaker ----
    # Auto-pause if portfolio drops more than max_drawdown_pct from its peak.
    # Default: pause if portfolio falls 20% from its all-time high during the run.
    try:
        from src.automation_state import (
            load_automation_state, save_automation_state, set_trading_paused
        )
        auto = load_automation_state()
        current_value = float(state.get("portfolio_value", 0))
        peak_value = float(auto.get("peak_portfolio_value", 0))
        max_dd = float(getattr(config, "max_drawdown_pct", 0.20))

        if current_value > 0:
            # Update peak if portfolio has grown
            if current_value > peak_value:
                auto["peak_portfolio_value"] = current_value
                save_automation_state(auto)
            elif peak_value > 0:
                drawdown = (peak_value - current_value) / peak_value
                if drawdown >= max_dd:
                    reason = (
                        f"Portfolio drawdown {drawdown:.1%} from peak ${peak_value:,.0f} "
                        f"to ${current_value:,.0f} — exceeds {max_dd:.0%} limit"
                    )
                    logger.critical(f"DRAWDOWN CIRCUIT BREAKER TRIGGERED: {reason}")
                    set_trading_paused(True, reason=reason, critical=True)
    except Exception as e:
        logger.warning(f"Drawdown check failed (non-critical): {e}")

    return state


# ============================================================
# Paper report
# ============================================================

def run_paper_report(config) -> dict:
    """
    Generate a full paper trading portfolio report.

    Fetches live account and position data from Alpaca, merges with
    local paper_state.json, and returns a structured dict for display.

    Args:
        config: AppConfig object — must pass require_paper_mode()

    Returns:
        {
            "summary":       dict from compute_portfolio_summary()
            "positions":     list of position rows
            "recent_orders": list of last 10 orders
            "last_updated":  str — timestamp of last state save
            "account":       dict from broker.get_account()
        }
    """
    config.require_paper_mode()
    broker = AlpacaPaperBroker.from_config(config)

    state_path = getattr(config, "state_file_path", _DEFAULT_STATE_PATH)
    log_path = getattr(config, "order_log_path", _DEFAULT_ORDER_LOG)

    account = broker.get_account()
    alpaca_positions = broker.get_positions()

    state = load_state(state_path)

    state["portfolio_value"] = account["portfolio_value"]
    state["buying_power"] = account["buying_power"]
    state["cash"] = account["cash"]

    for ticker, alpaca_pos in alpaca_positions.items():
        if ticker not in state["positions"]:
            state["positions"][ticker] = {
                "ticker": ticker,
                "entry_price": alpaca_pos["avg_entry_price"],
                "entry_date": "",
                "holding_days": 0,
                "partial_taken": False,
                "catalyst_score": "",
                "catalyst_headline": "",
                "peak_price": 0.0,
            }

        state["positions"][ticker].update({
            "current_price": alpaca_pos["current_price"],
            "market_value": alpaca_pos["market_value"],
            "unrealized_pl": alpaca_pos["unrealized_pl"],
            "unrealized_plpc": alpaca_pos["unrealized_plpc"],
            "avg_entry_price": alpaca_pos["avg_entry_price"],
            "shares": alpaca_pos["qty"],
        })

    state["positions"] = {
        t: p for t, p in state["positions"].items()
        if t in alpaca_positions
    }

    state["positions"] = update_peak_price(state["positions"])

    summary = compute_portfolio_summary(state, config)
    positions_table = format_positions_table(state, config)
    recent_orders = load_recent_orders(log_path, n=10)

    return {
        "summary": summary,
        "positions": positions_table,
        "recent_orders": recent_orders,
        "last_updated": state.get("last_updated", ""),
        "account": account,
    }


# ============================================================
# Hard-stop exit check (no network needed)
# ============================================================

def check_positions_for_exits(state: dict, config) -> list:
    """
    Check hard exit rules for all open positions using only state data.

    Pure function — no network calls, no price data download.
    Checks: hard stop loss, breakeven stop, time limit.

    MA-based exits (below 10d/20d MA, RS weakness, volume stall) are
    checked at paper-scan time when price history is available.

    Args:
        state:  Paper trading state dict (from load_state())
        config: AppConfig object

    Returns:
        List of exit signal dicts. Each dict:
        {ticker, signal_type, reason, entry_price, current_price,
         gain_pct, partial_fraction, holding_days}
    """
    stop_loss_pct = getattr(config, "stop_loss_pct", 0.08)
    breakeven_trigger = getattr(config, "breakeven_trigger_pct", 0.20)
    max_holding_days = getattr(config, "max_holding_days", 20)

    positions = state.get("positions", {})
    exits = []

    for ticker, pos in positions.items():
        entry_price = float(pos.get("entry_price") or 0)
        current_price = float(pos.get("current_price") or 0)
        holding_days = int(pos.get("holding_days") or 0)

        if entry_price <= 0 or current_price <= 0:
            continue

        gain_pct = (current_price / entry_price) - 1.0
        peak_price = float(pos.get("peak_price") or current_price)
        peak_gain_pct = (peak_price / entry_price) - 1.0 if entry_price > 0 else 0.0

        def _make_exit(reason):
            return {
                "ticker": ticker,
                "signal_type": "EXIT",
                "reason": reason,
                "entry_price": entry_price,
                "current_price": current_price,
                "gain_pct": gain_pct,
                "partial_fraction": 1.0,
                "holding_days": holding_days,
            }

        stop_price = entry_price * (1.0 - stop_loss_pct)
        if current_price <= stop_price:
            exits.append(_make_exit(f"stop_loss_{stop_loss_pct:.0%}"))
            continue

        if peak_gain_pct >= breakeven_trigger and current_price < entry_price:
            exits.append(_make_exit("breakeven_stop"))
            continue

        if holding_days >= max_holding_days:
            exits.append(_make_exit(f"max_holding_days_{max_holding_days}"))
            continue

    return exits


# ============================================================
# Paper scan pipeline (Stage 7 / Stage 9 updated)
# ============================================================

def run_paper_scan(config) -> dict:
    """
    Run the full paper-scan pipeline programmatically.

    Stage 9 update: uses load_all_catalysts(config) which merges
    manual overrides + automated heuristic scores. The bot no longer
    requires manual catalyst input for normal operation.

    Steps:
      1. Load today's discovered universe (auto-discover if stale)
      2. Download price data for all tickers + SPY
      3. Load ALL catalysts (manual overrides + auto-scored)
      4. Load held positions from paper_state.json
      5. Run signal engine (buy + exit signals)
      6. Rank buy signals
      7. Save paper_signals.csv + tomorrow_orders.csv + needs_catalyst_review.csv

    Args:
        config: AppConfig object

    Returns:
        {
            "tickers_scanned": int,
            "buy_signals":     int,
            "exit_signals":    int,
            "needs_review":    int,
            "errors":          int,
            "error":           str or None
        }
    """
    from src.universe_discovery import (
        load_todays_universe, is_todays_universe_fresh, run_discovery
    )
    from src.data_loader import download_price_data, download_spy_data
    from src.catalyst_scorer import load_all_catalysts  # Stage 9: merged catalysts
    from src.signal_engine import run_signal_scan, save_signals_csv, save_needs_review
    from src.ranking import rank_signals

    universe_path = getattr(
        config, "discovered_universe_path", "reports/daily_discovered_universe.csv"
    )
    state_path = getattr(config, "state_file_path", _DEFAULT_STATE_PATH)
    max_positions = getattr(config, "max_positions", 8)

    # Ensure universe is fresh
    if not is_todays_universe_fresh(universe_path):
        logger.info("paper_scan: no fresh universe — running discovery first")
        run_discovery(config)

    universe_df = load_todays_universe(universe_path)
    if universe_df is None or universe_df.empty:
        return {
            "tickers_scanned": 0, "buy_signals": 0, "exit_signals": 0,
            "needs_review": 0, "errors": 0,
            "error": "No universe available — run discover-universe first",
        }

    ticker_col = "ticker" if "ticker" in universe_df.columns else universe_df.columns[0]
    tickers = list(universe_df[ticker_col].dropna().unique())

    price_data = download_price_data(tickers, period="90d")
    spy_df = download_spy_data(period="90d")

    if spy_df is None or spy_df.empty:
        return {
            "tickers_scanned": len(tickers), "buy_signals": 0, "exit_signals": 0,
            "needs_review": 0, "errors": len(tickers),
            "error": "Could not download SPY benchmark data",
        }

    # Stage 9: load manual overrides merged with auto-scored catalysts
    catalysts = load_all_catalysts(config)

    state = load_state(state_path)
    held = {
        str(k).upper(): v
        for k, v in state.get("positions", {}).items()
        if isinstance(v, dict)
    }

    result = run_signal_scan(
        universe=tickers,
        price_data=price_data,
        spy_df=spy_df,
        catalysts=catalysts,
        config=config,
        held_positions=held,
    )

    ranked = rank_signals(
        result["buy_signals"],
        max_positions=max_positions,
        current_position_count=len(held),
    )

    save_signals_csv(ranked, result["exit_signals"])
    save_needs_review(result["needs_review"])

    # Stage 9: save tomorrow_orders.csv and order_approval.csv if review mode is on
    _save_tomorrow_orders(ranked, config)

    logger.info(
        f"paper_scan complete: {result['tickers_scanned']} scanned, "
        f"{len(ranked)} buy, {len(result['exit_signals'])} exit"
    )

    return {
        "tickers_scanned": result["tickers_scanned"],
        "buy_signals": len(ranked),
        "exit_signals": len(result["exit_signals"]),
        "needs_review": len(result["needs_review"]),
        "errors": result["errors"],
        "error": None,
    }


def _save_tomorrow_orders(ranked: list, config) -> None:
    """
    Save proposed buy orders to reports/tomorrow_orders.csv.

    This file shows exactly what the bot plans to execute at next open.
    When require_human_review_before_orders is True, also creates
    reports/order_approval.csv pre-filled with approved=false for each
    order so you can edit the file to approve them.

    Args:
        ranked: Ranked buy signals list (from rank_signals())
        config: AppConfig object
    """
    ensure_dir("reports")
    orders_path = "reports/tomorrow_orders.csv"
    approval_path = getattr(config, "order_approval_path", "reports/order_approval.csv")
    require_review = getattr(config, "require_human_review_before_orders", False)
    today = today_str()

    portfolio_value = getattr(config, "starting_cash", 100000)

    # Try to read current portfolio value from state
    try:
        state = load_state(getattr(config, "state_file_path", _DEFAULT_STATE_PATH))
        portfolio_value = state.get("portfolio_value", portfolio_value)
    except Exception:
        pass

    target_pct = getattr(config, "target_position_size", 0.18)
    notional_estimate = portfolio_value * target_pct

    if not ranked:
        # Write empty files so they exist
        pd.DataFrame(columns=[
            "date", "ticker", "side", "notional_estimate", "catalyst_score",
            "catalyst_type", "reason", "rank_score"
        ]).to_csv(orders_path, index=False)
        return

    rows = []
    for sig in ranked:
        rows.append({
            "date": today,
            "ticker": str(sig.get("ticker", "")),
            "side": "BUY",
            "notional_estimate": round(notional_estimate, 2),
            "catalyst_score": sig.get("catalyst_score", ""),
            "catalyst_type": sig.get("catalyst_type", ""),
            "reason": str(sig.get("catalyst_headline", "") or "auto-scored")[:150],
            "rank_score": round(float(sig.get("rank_score", 0)), 3),
        })

    pd.DataFrame(rows).to_csv(orders_path, index=False)
    logger.info(f"_save_tomorrow_orders: saved {len(rows)} proposed orders to {orders_path}")

    if require_review:
        approval_rows = []
        for sig in ranked:
            approval_rows.append({
                "date": today,
                "ticker": str(sig.get("ticker", "")),
                "side": "BUY",
                "notional_estimate": round(notional_estimate, 2),
                "approved": "false",
                "notes": "",
            })
        pd.DataFrame(approval_rows).to_csv(approval_path, index=False)
        logger.info(
            f"_save_tomorrow_orders: created order_approval.csv — "
            f"human review required before execution"
        )


# ============================================================
# Intraday exit check (called by scheduler at 15:45 ET)
# ============================================================

def run_intraday_exit_check_and_execute(config) -> dict:
    """
    Check hard exit rules from state and execute any triggered exits.

    Called at 15:45 ET (15 min before close).
    Uses only current_price from paper_state.json — no price download.

    Checks:
      - Hard stop loss (current_price <= entry * 0.92)
      - Breakeven stop (if peak_gain >= 20%, current_price < entry)
      - Time limit (holding_days >= max_holding_days)

    Args:
        config: AppConfig object

    Returns:
        {"exits_found": int, "submitted": int, "errors": int}
    """
    from src.execution_engine import execute_sell_signals

    config.require_paper_mode()
    broker = AlpacaPaperBroker.from_config(config)

    state_path = getattr(config, "state_file_path", _DEFAULT_STATE_PATH)
    state = load_state(state_path)

    exits = check_positions_for_exits(state, config)

    if not exits:
        logger.info("intraday_exit_check: no hard exits triggered")
        return {"exits_found": 0, "submitted": 0, "errors": 0}

    logger.info(f"intraday_exit_check: {len(exits)} exit(s) triggered — submitting via Alpaca")

    alpaca_positions = broker.get_positions()
    alpaca_open_orders = broker.get_open_orders()

    submitted = execute_sell_signals(
        exit_signals=exits,
        broker=broker,
        config=config,
        state=state,
        alpaca_positions=alpaca_positions,
        alpaca_open_orders=alpaca_open_orders,
    )

    save_state(state, state_path)

    errors = max(0, len(exits) - len(submitted))
    return {
        "exits_found": len(exits),
        "submitted": len(submitted),
        "errors": errors,
    }

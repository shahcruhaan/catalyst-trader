"""
src/scheduler.py — Automated Paper Trading Scheduler

Runs the complete daily trading cycle automatically.
All times are U.S. Eastern.

Stage 9 daily schedule (each task runs at most once per calendar day):
  09:32  paper_execute        — submit orders shortly after market open
  09:45  paper_reconcile      — sync with Alpaca after fills
  15:45  intraday_exit_check  — check hard stops before close
  16:10  paper_report         — generate end-of-day report
  16:20  discover_universe    — find stocks for tomorrow
  16:30  pull_news            — pull recent news for discovered universe
  16:45  score_catalysts      — auto-score catalysts with heuristic scorer
  17:00  paper_scan           — generate buy/exit signals for tomorrow

Stage 9 pipeline (run-once-full-cycle order):
  discover_universe → pull_news → score_catalysts → paper_scan
  → paper_execute → paper_reconcile → intraday_exit_check → paper_report

Automation safety:
  1. Never runs on weekends.
  2. Never runs on NYSE holidays (hardcoded + Alpaca calendar when available).
  3. Stops trading if paper mode safety check fails.
  4. Checks automation_state.json before executing orders.
  5. Pauses all trading on critical errors (writes pause_reason to state file).
  6. Logs all task results to reports/scheduler_log.csv.

scheduler_log.csv format:
  timestamp, date, task_name, status, duration_seconds, detail
"""

import concurrent.futures as _futures
import signal as _signal
import time as time_module
from datetime import date, datetime
from typing import Optional

from src.utils import setup_logger, append_to_csv, load_csv_safe, ensure_dir, now_str, today_str
from src.market_calendar import (
    is_trading_day, is_market_open, now_eastern, next_trading_day,
    get_market_open_time, market_status_summary,
)

logger = setup_logger(__name__)

_DEFAULT_SCHEDULER_LOG = "reports/scheduler_log.csv"

# ============================================================
# Daily task schedule — all times Eastern
# ============================================================

# (time_str, task_name)
# Each task runs at most once per calendar day (duplicate prevention).
DAILY_SCHEDULE = [
    ("09:32", "paper_execute"),           # Shortly after market open
    ("09:45", "paper_reconcile"),         # After fills confirmed
    ("15:45", "intraday_exit_check"),     # 15 min before close
    ("16:10", "paper_report"),            # 10 min after close
    ("16:20", "discover_universe"),       # 20 min after close
    ("16:30", "pull_news"),               # Pull news for discovered universe (Stage 9)
    ("16:45", "score_catalysts"),         # Auto-score catalysts (Stage 9)
    ("17:00", "paper_scan"),              # Generate signals (after scoring)
]

# Order for run-once-full-cycle (logical pipeline order)
FULL_CYCLE_ORDER = [
    "discover_universe",
    "pull_news",           # Stage 9
    "score_catalysts",     # Stage 9
    "paper_scan",
    "paper_execute",
    "paper_reconcile",
    "intraday_exit_check",
    "paper_report",
]

# Global flag for clean shutdown
_shutdown_requested = False

# Maximum seconds a single task may run before it is considered hung
TASK_TIMEOUT_SECONDS = 600  # 10 minutes


# ============================================================
# Scheduler logging
# ============================================================

def log_scheduler_event(
    task_name: str,
    status: str,
    duration_seconds: float = 0.0,
    detail: str = "",
    log_path: str = _DEFAULT_SCHEDULER_LOG,
    log_date: Optional[str] = None,
) -> None:
    """
    Append one row to reports/scheduler_log.csv.

    Args:
        task_name:        Name of the task (e.g. "paper_execute")
        status:           "SUCCESS", "ERROR", "SKIPPED", or "RUNNING"
        duration_seconds: How long the task took in seconds
        detail:           Extra info — error message, summary counts, etc.
        log_path:         Path to the log file (default: reports/scheduler_log.csv)
        log_date:         Date string to log (default: today)
    """
    ensure_dir("reports")
    row = {
        "timestamp": now_str(),
        "date": log_date or today_str(),
        "task_name": task_name,
        "status": status,
        "duration_seconds": round(duration_seconds, 2),
        "detail": str(detail)[:200],
    }
    append_to_csv(log_path, row)

    # Trim log to last 30 days so it never grows unbounded.
    # This keeps has_task_run_today() fast (reads small file every 60s).
    try:
        import pandas as pd
        from datetime import date as _date, timedelta as _timedelta
        df = pd.read_csv(log_path, dtype=str)
        if len(df) > 500:
            cutoff = str(_date.today() - _timedelta(days=30))
            df = df[df["date"].astype(str) >= cutoff]
            df.to_csv(log_path, index=False)
    except Exception:
        pass


def has_task_run_today(
    task_name: str,
    log_path: str = _DEFAULT_SCHEDULER_LOG,
    today: Optional[date] = None,
) -> bool:
    """
    Return True if the given task already completed successfully today.

    Reads scheduler_log.csv and looks for a SUCCESS row with today's date
    and the given task name.

    Args:
        task_name: Task name to check (e.g. "paper_execute")
        log_path:  Path to the scheduler log
        today:     Date to check (default: today in Eastern time)

    Returns:
        True if a SUCCESS row exists for today. False otherwise.
    """
    if today is None:
        today = now_eastern().date()

    today_str_val = str(today)

    df = load_csv_safe(log_path)
    if df is None or df.empty:
        return False

    if "date" not in df.columns or "task_name" not in df.columns or "status" not in df.columns:
        return False

    mask = (
        (df["date"].astype(str) == today_str_val)
        & (df["task_name"].astype(str) == task_name)
        & (df["status"].astype(str) == "SUCCESS")
    )
    return bool(mask.any())


# ============================================================
# Task execution
# ============================================================

def run_task(task_name: str, config) -> dict:
    """
    Execute one named task, log the result, and return a summary dict.

    All exceptions are caught, logged, and returned in the result dict
    so the scheduler loop can continue even if one task fails.

    Args:
        task_name: Name of the task to run
        config:    AppConfig object

    Returns:
        {
            "task_name": str,
            "status":    "SUCCESS" | "ERROR" | "SKIPPED",
            "duration":  float (seconds),
            "detail":    str (summary or error message),
        }
    """
    log_path = getattr(config, "scheduler_log_path", _DEFAULT_SCHEDULER_LOG)
    start = time_module.time()

    logger.info(f"Scheduler: starting  [{task_name}]")

    try:
        # Run the task in a thread so we can enforce a hard timeout.
        # If a yfinance or Alpaca API call hangs, the scheduler loop
        # must not be blocked indefinitely.
        executor = _futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_dispatch_task, task_name, config)
        try:
            detail = future.result(timeout=TASK_TIMEOUT_SECONDS)
        except _futures.TimeoutError:
            executor.shutdown(wait=False)
            raise RuntimeError(
                f"Task exceeded {TASK_TIMEOUT_SECONDS // 60}-minute timeout — "
                "likely a hung network call. Bot will continue with next task."
            )
        executor.shutdown(wait=False)
        duration = time_module.time() - start

        # Update automation state timestamp on success
        try:
            from src.automation_state import update_task_timestamp
            update_task_timestamp(task_name)
        except Exception:
            pass

        log_scheduler_event(task_name, "SUCCESS", duration, str(detail), log_path)
        logger.info(f"Scheduler: completed [{task_name}]  ({duration:.1f}s)  {detail}")
        return {
            "task_name": task_name,
            "status": "SUCCESS",
            "duration": duration,
            "detail": str(detail),
        }

    except Exception as e:
        duration = time_module.time() - start
        error_msg = str(e)
        log_scheduler_event(task_name, "ERROR", duration, error_msg, log_path)
        logger.error(f"Scheduler: ERROR     [{task_name}]  ({duration:.1f}s)  {error_msg}")

        # Record error in automation state
        try:
            from src.automation_state import record_error
            record_error(f"[{task_name}] {error_msg}", critical=False)
        except Exception:
            pass

        return {
            "task_name": task_name,
            "status": "ERROR",
            "duration": duration,
            "detail": error_msg,
        }


def _dispatch_task(task_name: str, config) -> str:
    """
    Run the correct function for the given task name.

    Returns a brief summary string for logging.
    Raises on any error (run_task() handles logging).
    """
    from src.universe_discovery import run_discovery
    from src.execution_engine import run_paper_execute
    from src.paper_trader import (
        run_reconcile_with_portfolio,
        run_paper_report,
        run_paper_scan,
        run_intraday_exit_check_and_execute,
        run_pull_news,
        run_score_catalysts,
    )

    if task_name == "discover_universe":
        universe_df = run_discovery(config)
        count = len(universe_df) if universe_df is not None else 0
        return f"{count} tickers discovered"

    elif task_name == "pull_news":
        result = run_pull_news(config)
        if result.get("error"):
            raise RuntimeError(result["error"])
        return (
            f"{result['news_pulled']} articles, "
            f"{result['tickers_covered']} tickers covered, "
            f"{result['no_news_tickers']} with no news"
        )

    elif task_name == "score_catalysts":
        result = run_score_catalysts(config)
        if result.get("error"):
            raise RuntimeError(result["error"])
        return (
            f"{result['scored']} scored, "
            f"{result['tradeable']} tradeable, "
            f"{result['needs_review']} needs review"
        )

    elif task_name == "paper_scan":
        result = run_paper_scan(config)
        if result.get("error"):
            raise RuntimeError(result["error"])
        return (
            f"{result['buy_signals']} buy, {result['exit_signals']} exit, "
            f"{result['tickers_scanned']} scanned"
        )

    elif task_name == "paper_execute":
        # Check automation enabled before placing orders
        try:
            from src.automation_state import is_automation_enabled
            if not is_automation_enabled():
                return "SKIPPED — automation is disabled or trading is paused"
        except Exception:
            pass

        # Check human review mode
        if getattr(config, "require_human_review_before_orders", False):
            if not _check_any_orders_approved(config):
                return (
                    "SKIPPED — human review required. "
                    "Approve orders in reports/order_approval.csv then re-run."
                )

        result = run_paper_execute(config)
        summary = (
            f"{result['buys_submitted']} buys, {result['sells_submitted']} sells "
            f"({result['buys_blocked'] + result['sells_blocked']} blocked)"
        )
        # Email alert when actual orders are placed
        trades = result["buys_submitted"] + result["sells_submitted"]
        if trades > 0:
            try:
                from src.utils import send_email_alert
                send_email_alert(
                    subject=f"Trade executed: {result['buys_submitted']} buy, {result['sells_submitted']} sell",
                    body=(
                        f"Paper trading orders submitted today.\n\n"
                        f"Buys submitted:  {result['buys_submitted']}\n"
                        f"Sells submitted: {result['sells_submitted']}\n"
                        f"Blocked:         {result['buys_blocked'] + result['sells_blocked']}\n\n"
                        f"Check your Alpaca paper trading account for fill details:\n"
                        f"https://app.alpaca.markets/paper/dashboard/overview"
                    ),
                )
            except Exception:
                pass
        return summary

    elif task_name == "paper_reconcile":
        state = run_reconcile_with_portfolio(config)
        n = len(state.get("positions", {}))
        pv = state.get("portfolio_value", 0)
        return f"{n} position(s)  portfolio=${pv:,.0f}"

    elif task_name == "intraday_exit_check":
        result = run_intraday_exit_check_and_execute(config)
        return (
            f"{result['exits_found']} exits found, "
            f"{result['submitted']} submitted"
        )

    elif task_name == "paper_report":
        report = run_paper_report(config)
        summary = report.get("summary", {})
        pv = summary.get("portfolio_value", 0)
        n = summary.get("position_count", 0)
        rp = summary.get("total_return_pct", 0)
        sign = "+" if rp >= 0 else ""
        result_str = f"portfolio=${pv:,.0f}  {n} position(s)  return={sign}{rp * 100:.2f}%"

        # Daily summary email
        try:
            from src.utils import send_email_alert
            positions = report.get("positions", [])
            pos_lines = "\n".join(
                f"  {p.get('ticker','?'):<6} entry=${p.get('entry_price',0):.2f}  "
                f"current=${p.get('current_price',0):.2f}  "
                f"gain={p.get('unrealized_plpc', 0):.1%}"
                for p in positions[:10]
            ) or "  No open positions."
            send_email_alert(
                subject=f"Daily report — portfolio ${pv:,.0f} ({sign}{rp * 100:.2f}%)",
                body=(
                    f"Daily paper trading summary.\n\n"
                    f"Portfolio value:  ${pv:,.2f}\n"
                    f"Total return:     {sign}{rp * 100:.2f}%\n"
                    f"Open positions:   {n}\n\n"
                    f"Positions:\n{pos_lines}\n\n"
                    f"Full details on Alpaca:\n"
                    f"https://app.alpaca.markets/paper/dashboard/overview"
                ),
            )
        except Exception:
            pass
        return result_str

    else:
        raise ValueError(f"Unknown task: {task_name!r}")


def _check_any_orders_approved(config) -> bool:
    """
    Return True if at least one order is approved for today in order_approval.csv.

    Used when require_human_review_before_orders is True.
    If the approval file does not exist, returns False (not approved).

    The order_approval.csv is created by run_paper_scan() and filled by the user:
      date,ticker,side,notional_estimate,approved,notes
      2026-06-03,NVDA,BUY,18000,true,looks good
    """
    approval_path = getattr(config, "order_approval_path", "reports/order_approval.csv")
    today = today_str()

    df = load_csv_safe(approval_path)
    if df is None or df.empty:
        return False

    required = ["date", "approved"]
    if not all(c in df.columns for c in required):
        return False

    mask = (
        (df["date"].astype(str) == today)
        & (df["approved"].astype(str).str.lower().isin(["true", "1", "yes"]))
    )
    return bool(mask.any())


# ============================================================
# Full cycle (run-once-full-cycle)
# ============================================================

def run_full_cycle(config, skip_tasks: Optional[list] = None) -> dict:
    """
    Run all tasks in logical pipeline order immediately.

    Respects duplicate prevention — skips tasks that already succeeded today.
    Does NOT check market hours. Useful for:
      - Testing the full pipeline end-to-end
      - Manual full-cycle runs outside scheduled hours
      - Recovery after the scheduler missed a window

    Args:
        config:      AppConfig object
        skip_tasks:  Optional list of task names to skip (e.g. ["paper_execute"])

    Returns:
        {
            "tasks_run":     int — tasks that ran successfully
            "tasks_skipped": int — tasks skipped (already ran or in skip_tasks)
            "tasks_failed":  int — tasks that errored
            "results":       list — one result dict per task
        }
    """
    log_path = getattr(config, "scheduler_log_path", _DEFAULT_SCHEDULER_LOG)
    today = now_eastern().date()
    skip_tasks = skip_tasks or []

    tasks_run = 0
    tasks_skipped = 0
    tasks_failed = 0
    results = []

    logger.info("run_full_cycle: starting all tasks")

    for task_name in FULL_CYCLE_ORDER:
        if task_name in skip_tasks:
            logger.info(f"run_full_cycle: SKIPPED [{task_name}] (in skip_tasks list)")
            results.append({
                "task_name": task_name,
                "status": "SKIPPED",
                "duration": 0.0,
                "detail": "explicitly skipped by caller",
            })
            tasks_skipped += 1
            continue

        if has_task_run_today(task_name, log_path, today):
            logger.info(f"run_full_cycle: SKIPPED [{task_name}] (already ran today)")
            log_scheduler_event(task_name, "SKIPPED", 0.0, "already ran today", log_path)
            results.append({
                "task_name": task_name,
                "status": "SKIPPED",
                "duration": 0.0,
                "detail": "already ran today",
            })
            tasks_skipped += 1
            continue

        result = run_task(task_name, config)
        results.append(result)

        if result["status"] == "SUCCESS":
            tasks_run += 1
        else:
            tasks_failed += 1
            logger.warning(
                f"run_full_cycle: [{task_name}] failed — continuing with remaining tasks"
            )

    logger.info(
        f"run_full_cycle complete: "
        f"{tasks_run} run, {tasks_skipped} skipped, {tasks_failed} failed"
    )

    return {
        "tasks_run": tasks_run,
        "tasks_skipped": tasks_skipped,
        "tasks_failed": tasks_failed,
        "results": results,
    }


# ============================================================
# Continuous bot loop (run-bot)
# ============================================================

def run_bot(config) -> None:
    """
    Start the fully automated paper trading bot.

    Runs indefinitely, checking the daily schedule every 60 seconds.
    Only executes tasks on confirmed U.S. market trading days.
    Handles Ctrl+C for clean shutdown.

    Task execution rules:
      1. Only runs on U.S. market trading days (Mon-Fri, not holidays)
      2. Each task runs at most once per calendar day (duplicate prevention)
      3. Checks automation_state.json before any order-placing task
      4. On non-trading days, sleeps 30 minutes between checks
      5. On errors, logs and continues — never crashes
      6. On paper mode failure, logs critical error and stops

    Args:
        config: AppConfig object — must pass require_paper_mode()
    """
    global _shutdown_requested
    _shutdown_requested = False

    # Handle SIGTERM gracefully (systemd sends this when stopping the service).
    # Without this handler, systemd kills the process instantly mid-task,
    # which can corrupt paper_state.json. With it, the bot finishes its current
    # task (or sleep cycle) then exits cleanly.
    def _sigterm_handler(signum, frame):
        global _shutdown_requested
        _shutdown_requested = True
        logger.info("run_bot: SIGTERM received — will stop after current task completes")
    try:
        _signal.signal(_signal.SIGTERM, _sigterm_handler)
    except (OSError, ValueError):
        pass  # Not in main thread (e.g. tests) — skip silently

    log_path = getattr(config, "scheduler_log_path", _DEFAULT_SCHEDULER_LOG)

    # Safety check before starting
    try:
        config.require_paper_mode()
    except RuntimeError as e:
        logger.critical(f"run_bot: cannot start — paper mode safety check failed: {e}")
        raise

    # Mark paper mode as verified in automation state
    try:
        from src.automation_state import mark_paper_mode_verified
        mark_paper_mode_verified(verified=True)
    except Exception:
        pass

    logger.info("=" * 60)
    logger.info("AUTOMATED PAPER TRADING BOT RUNNING — PAPER MODE ONLY")
    logger.info("Press Ctrl+C to stop gracefully.")
    logger.info("=" * 60)

    try:
        from src.utils import log_alert
        log_alert("bot_started", "INFO", "run-bot started — fully automated paper trading mode")
    except Exception:
        pass

    log_scheduler_event("bot_started", "SUCCESS", 0.0, "run-bot started", log_path)
    _print_bot_status(config)

    while not _shutdown_requested:
        try:
            now = now_eastern()
            today = now.date()

            # Write heartbeat every loop so the file age shows the bot is alive.
            # On the server: check with: cat reports/bot_heartbeat.txt
            try:
                with open("reports/bot_heartbeat.txt", "w") as _hb:
                    _hb.write(f"{now.strftime('%Y-%m-%d %H:%M:%S')} ET\n")
            except Exception:
                pass

            if not is_trading_day(today):
                _next = next_trading_day(today)
                logger.info(
                    f"  Not a trading day ({today}). "
                    f"Next: {_next}. Sleeping 30 minutes..."
                )
                _interruptible_sleep(1800)
                continue

            current_hhmm = now.strftime("%H:%M")

            for task_time_str, task_name in DAILY_SCHEDULE:
                if current_hhmm >= task_time_str:
                    if not has_task_run_today(task_name, log_path, today):
                        logger.info(
                            f"  [{current_hhmm} ET] Triggering [{task_name}] "
                            f"(scheduled after {task_time_str})"
                        )
                        run_task(task_name, config)

            _interruptible_sleep(60)

        except KeyboardInterrupt:
            break

        except Exception as e:
            logger.error(f"run_bot: unexpected error in main loop: {e}", exc_info=True)
            log_scheduler_event("bot_loop_error", "ERROR", 0.0, str(e), log_path)
            _interruptible_sleep(60)

    try:
        from src.utils import log_alert
        log_alert("bot_stopped", "INFO", "run-bot stopped cleanly")
    except Exception:
        pass

    log_scheduler_event("bot_stopped", "SUCCESS", 0.0, "run-bot stopped", log_path)
    logger.info("Bot stopped cleanly.")


def _interruptible_sleep(seconds: float) -> None:
    """Sleep in 1-second increments so Ctrl+C is responsive."""
    global _shutdown_requested
    elapsed = 0.0
    while elapsed < seconds and not _shutdown_requested:
        try:
            time_module.sleep(1)
            elapsed += 1
        except KeyboardInterrupt:
            _shutdown_requested = True
            break


def _print_bot_status(config) -> None:
    """Print the current market status and today's task schedule."""
    status = market_status_summary()
    log_path = getattr(config, "scheduler_log_path", _DEFAULT_SCHEDULER_LOG)
    today = now_eastern().date()

    print()
    print("  AUTOMATED PAPER TRADING BOT  —  PAPER MODE ONLY  (Stage 9)")
    print(f"  Current time: {status['now_eastern']}")
    print(f"  Market open:  {'YES' if status['is_open_now'] else 'NO'}")
    print(f"  Trading day:  {'YES' if status['is_trading_day'] else 'NO'}")
    if not status["is_trading_day"]:
        print(f"  Next trading day: {status['next_trading_day']}")

    # Show automation state
    try:
        from src.automation_state import get_automation_summary
        auto = get_automation_summary()
        print(f"  Automation:   {auto.get('status', 'UNKNOWN')}")
        if auto.get("trading_paused") or auto.get("critical_error"):
            print(f"  Pause reason: {auto.get('pause_reason', 'unknown')}")
    except Exception:
        pass

    print()
    print("  Stage 9 daily schedule (Eastern time):")
    for task_time_str, task_name in DAILY_SCHEDULE:
        already_ran = has_task_run_today(task_name, log_path, today)
        ran_str = " [done]" if already_ran else ""
        print(f"    {task_time_str}  {task_name}{ran_str}")
    print()
    print("  Press Ctrl+C to stop the bot.")
    print()

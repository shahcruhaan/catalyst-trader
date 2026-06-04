"""
src/automation_state.py — Automation State Tracking

Manages reports/automation_state.json — a small JSON file that tracks
whether the bot is currently enabled, when each task last ran, and
whether any critical error has paused automated trading.

Why this exists:
  When the bot runs fully automatically, you need a way to see at a glance
  whether it is healthy, when it last pulled news, whether trading is
  paused, and why. This file is the single source of truth for that.

State file: reports/automation_state.json

Key fields:
  automation_enabled       — True/False: whether the bot may place trades
  trading_paused           — True if automatically paused due to an error
  pause_reason             — why trading was paused
  critical_error           — True if a critical system error occurred
  last_discovery_date      — date of last successful universe discovery
  last_news_pull_date      — date of last successful news pull
  last_catalyst_score_date — date of last successful catalyst scoring
  last_scan_date           — date of last successful paper-scan
  last_execution_date      — date of last successful order execution
  last_reconcile_date      — date of last successful reconcile
  last_report_date         — date of last successful report generation
  last_error               — last error message
  paper_mode_verified      — True if paper mode was confirmed at last startup
  alpaca_connection_verified — True if Alpaca connection succeeded

Usage:
  from src.automation_state import (
      load_automation_state, save_automation_state,
      is_automation_enabled, set_automation_enabled,
      set_trading_paused, update_task_timestamp, record_error,
  )
"""

import json
import os

from src.utils import setup_logger, ensure_dir, today_str, now_str

logger = setup_logger(__name__)

_DEFAULT_STATE_PATH = "reports/automation_state.json"

_DEFAULT_STATE = {
    "automation_enabled": True,
    "last_discovery_date": "",
    "last_news_pull_date": "",
    "last_catalyst_score_date": "",
    "last_scan_date": "",
    "last_execution_date": "",
    "last_reconcile_date": "",
    "last_report_date": "",
    "last_error": "",
    "critical_error": False,
    "trading_paused": False,
    "pause_reason": "",
    "next_scheduled_task": "",
    "paper_mode_verified": False,
    "alpaca_connection_verified": False,
    "peak_portfolio_value": 0.0,   # For drawdown circuit breaker
    "last_updated": "",
}

# Map scheduler task names to their state field names
_TASK_TO_FIELD = {
    "discover_universe": "last_discovery_date",
    "pull_news": "last_news_pull_date",
    "score_catalysts": "last_catalyst_score_date",
    "paper_scan": "last_scan_date",
    "paper_execute": "last_execution_date",
    "paper_reconcile": "last_reconcile_date",
    "paper_report": "last_report_date",
    "intraday_exit_check": "",  # no separate timestamp needed
}


# ============================================================
# Load / save
# ============================================================

def load_automation_state(path: str = _DEFAULT_STATE_PATH) -> dict:
    """
    Load automation state from JSON file.

    Returns a fresh default state if the file does not exist.
    Merges with defaults so older state files that are missing
    new keys are handled safely.

    Never raises an exception.
    """
    if not os.path.exists(path):
        return dict(_DEFAULT_STATE)

    try:
        with open(path, "r") as f:
            data = json.load(f)
        # Merge loaded data over defaults so new keys are always present
        state = dict(_DEFAULT_STATE)
        state.update(data)
        return state
    except Exception as e:
        logger.warning(f"automation_state: could not load {path}: {e}")
        return dict(_DEFAULT_STATE)


def save_automation_state(state: dict, path: str = _DEFAULT_STATE_PATH) -> None:
    """
    Save automation state to JSON file.

    Creates reports/ directory if it does not exist.
    Always updates last_updated timestamp before saving.
    Never raises an exception — logs a warning on failure.
    """
    try:
        ensure_dir("reports")
        state["last_updated"] = now_str()
        with open(path, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"automation_state: could not save {path}: {e}")


# ============================================================
# Status queries
# ============================================================

def is_automation_enabled(path: str = _DEFAULT_STATE_PATH) -> bool:
    """
    Return True if the bot is allowed to place trades.

    Returns False if:
      - automation_enabled is False (manually disabled)
      - trading_paused is True (auto-paused by safety check)
      - critical_error is True (critical system failure)
    """
    state = load_automation_state(path)
    return (
        bool(state.get("automation_enabled", True))
        and not bool(state.get("trading_paused", False))
        and not bool(state.get("critical_error", False))
    )


def get_automation_summary(path: str = _DEFAULT_STATE_PATH) -> dict:
    """
    Return a summary dict suitable for the automation-status command.

    Includes all state fields plus a human-readable status string.
    """
    state = load_automation_state(path)
    enabled = is_automation_enabled(path)

    status_str = "RUNNING" if enabled else (
        "PAUSED (critical error)" if state.get("critical_error") else
        "PAUSED" if state.get("trading_paused") else
        "DISABLED"
    )

    state["status"] = status_str
    return state


# ============================================================
# State mutations
# ============================================================

def set_automation_enabled(
    enabled: bool,
    reason: str = "",
    path: str = _DEFAULT_STATE_PATH,
) -> None:
    """
    Enable or disable automation.

    When disabling: sets automation_enabled=False and records the reason.
    When enabling:  clears pause_reason and critical_error (assumes user
                    has verified the system is healthy before re-enabling).

    Args:
        enabled: True to enable, False to disable
        reason:  Why it was disabled (shown in automation-status)
        path:    State file path
    """
    state = load_automation_state(path)
    state["automation_enabled"] = enabled

    if enabled:
        state["trading_paused"] = False
        state["critical_error"] = False
        state["pause_reason"] = ""
        state["last_error"] = ""
        logger.info("automation_state: automation ENABLED")
    else:
        state["pause_reason"] = reason or "Manually disabled"
        logger.info(f"automation_state: automation DISABLED — {state['pause_reason']}")

    save_automation_state(state, path)


def set_trading_paused(
    paused: bool,
    reason: str = "",
    critical: bool = False,
    path: str = _DEFAULT_STATE_PATH,
) -> None:
    """
    Pause or unpause automated trading.

    Called automatically by safety checks when a critical condition
    is detected. The bot logs the reason and stops placing orders.

    To resume, the user must run: python main.py enable-automation

    Args:
        paused:   True to pause, False to unpause
        reason:   Why trading was paused (shown in automation-status)
        critical: If True, also sets critical_error=True (requires investigation)
        path:     State file path
    """
    state = load_automation_state(path)
    state["trading_paused"] = paused

    if paused:
        state["pause_reason"] = reason or "Trading paused"
        if critical:
            state["critical_error"] = True
        log_fn = logger.critical if critical else logger.warning
        log_fn(f"automation_state: trading PAUSED — {reason}")

        # Send email so the user knows immediately — they may not check the server
        try:
            from src.utils import send_email_alert
            severity_word = "CRITICAL" if critical else "WARNING"
            send_email_alert(
                subject=f"Trading PAUSED — {severity_word}",
                body=(
                    f"The paper trading bot has paused automated trading.\n\n"
                    f"Severity: {severity_word}\n"
                    f"Reason:   {reason or 'Trading paused'}\n\n"
                    f"To resume, SSH into the server and run:\n"
                    f"  python3 main.py enable-automation\n\n"
                    f"Check reports/automation_state.json and reports/alerts_log.csv for details."
                ),
            )
        except Exception:
            pass
    else:
        state["pause_reason"] = ""
        logger.info("automation_state: trading UNPAUSED")

    save_automation_state(state, path)


def update_task_timestamp(
    task_name: str,
    path: str = _DEFAULT_STATE_PATH,
) -> None:
    """
    Record today's date as the last successful run date for a task.

    Called by the scheduler after every successful task completion.
    Used by automation-status to show "last ran X days ago."

    Args:
        task_name: Scheduler task name (e.g. "pull_news", "paper_execute")
        path:      State file path
    """
    field = _TASK_TO_FIELD.get(task_name, "")
    if not field:
        return

    state = load_automation_state(path)
    state[field] = today_str()
    save_automation_state(state, path)


def record_error(
    error_message: str,
    critical: bool = False,
    path: str = _DEFAULT_STATE_PATH,
) -> None:
    """
    Record an error in the automation state.

    If critical=True, also sets trading_paused=True to prevent further
    automated orders until a human investigates.

    Args:
        error_message: Error description (truncated to 500 chars)
        critical:      If True, pause trading automatically
        path:          State file path
    """
    state = load_automation_state(path)
    state["last_error"] = str(error_message)[:500]

    if critical:
        state["critical_error"] = True
        state["trading_paused"] = True
        state["pause_reason"] = f"Critical error: {str(error_message)[:200]}"
        logger.critical(f"automation_state: CRITICAL ERROR recorded — trading paused: {error_message}")

    save_automation_state(state, path)


def mark_paper_mode_verified(
    verified: bool,
    alpaca_ok: bool = False,
    path: str = _DEFAULT_STATE_PATH,
) -> None:
    """
    Record whether paper mode and Alpaca connection were verified.

    Called during run-bot startup after safety checks pass.
    If paper mode cannot be verified, run-bot refuses to start.

    Args:
        verified:  True if ALPACA_PAPER=true and all config flags are correct
        alpaca_ok: True if the Alpaca API connection succeeded
        path:      State file path
    """
    state = load_automation_state(path)
    state["paper_mode_verified"] = verified
    state["alpaca_connection_verified"] = alpaca_ok
    save_automation_state(state, path)

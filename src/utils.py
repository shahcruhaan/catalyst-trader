"""
src/utils.py — Shared helper functions

Small utility functions used across the entire project.
Keeps other modules clean by centralizing common operations like:
  - Logging setup
  - Number formatting
  - Safe math (division without crashing)
  - Directory creation
  - CSV reading/writing
  - Ticker symbol validation
"""

import os
import csv
import logging
from datetime import datetime
from typing import Optional
import pandas as pd


# ============================================================
# Logging
# ============================================================

def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Create a named logger that prints messages to the terminal.

    Each message shows: timestamp [LEVEL] module_name: message

    Args:
        name:  The logger name, usually __name__ from the calling module.
        level: Minimum log level. Default INFO shows INFO, WARNING, ERROR.
               Use logging.DEBUG to see debug messages too.

    Returns:
        A configured logging.Logger instance.

    Usage:
        logger = setup_logger(__name__)
        logger.info("Starting scan...")
        logger.warning("No data found for AAPL")
        logger.error("Connection failed")
    """
    logger = logging.getLogger(name)

    # Only add a handler if the logger has none yet (prevents duplicate output)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(level)
    return logger


# ============================================================
# Directory helpers
# ============================================================

def ensure_dir(path: str) -> None:
    """
    Create a directory (and all parent directories) if it does not exist.
    Does nothing if the directory already exists.

    Example:
        ensure_dir("reports/archive")
        # Creates reports/ and reports/archive/ if needed
    """
    os.makedirs(path, exist_ok=True)


def ensure_reports_dir() -> None:
    """Make sure the reports/ folder exists. Called before writing any report."""
    ensure_dir("reports")


# ============================================================
# Safe math
# ============================================================

def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Divide two numbers safely. Returns 'default' if denominator is zero or None.

    Why: Dividing by zero in Python raises a ZeroDivisionError and crashes.
    This function prevents crashes when calculating ratios from market data,
    which sometimes has zero or missing values.

    Examples:
        safe_div(100, 20)     -> 5.0
        safe_div(100, 0)      -> 0.0  (default)
        safe_div(100, 0, -1)  -> -1.0 (custom default)
    """
    if not denominator:
        return default
    return numerator / denominator


# ============================================================
# Number formatting
# ============================================================

def format_currency(value: float) -> str:
    """
    Format a number as a dollar amount with commas and 2 decimal places.

    Examples:
        format_currency(12345.67)  -> "$12,345.67"
        format_currency(1000000)   -> "$1,000,000.00"
        format_currency(-500)      -> "-$500.00"
    """
    if value < 0:
        return f"-${abs(value):,.2f}"
    return f"${value:,.2f}"


def format_pct(value: float, decimals: int = 2) -> str:
    """
    Format a decimal as a percentage string.

    Examples:
        format_pct(0.1234)   -> "12.34%"
        format_pct(-0.05)    -> "-5.00%"
        format_pct(0.1, 1)   -> "10.0%"
    """
    return f"{value * 100:.{decimals}f}%"


def format_large_number(value: float) -> str:
    """
    Format a large number in human-readable form.

    Examples:
        format_large_number(1_500_000_000)  -> "$1.50B"
        format_large_number(75_000_000)     -> "$75.0M"
        format_large_number(50_000)         -> "$50,000"
    """
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value:,.0f}"


# ============================================================
# Date and time helpers
# ============================================================

def today_str() -> str:
    """Return today's date as YYYY-MM-DD string. Example: '2026-06-02'"""
    return datetime.now().strftime("%Y-%m-%d")


def now_str() -> str:
    """Return current datetime as YYYY-MM-DD HH:MM:SS string."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# CSV helpers
# ============================================================

def load_csv_safe(path: str) -> Optional[pd.DataFrame]:
    """
    Load a CSV file, returning None if it does not exist or cannot be read.

    Safer than pd.read_csv() because it doesn't crash on missing files.

    Args:
        path: File path to the CSV.

    Returns:
        DataFrame if the file exists and is readable, None otherwise.
    """
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
        return df
    except Exception:
        return None


def append_to_csv(path: str, row: dict) -> None:
    """
    Append a single row (as a dict) to a CSV file.
    Creates the file with headers if it does not yet exist.

    Args:
        path: File path to write to.
        row:  Dictionary mapping column name -> value.

    Example:
        append_to_csv("reports/orders_log.csv", {
            "timestamp": "2026-06-02 09:32:00",
            "ticker": "NVDA",
            "action": "BUY",
            "amount": 18000,
        })
    """
    ensure_reports_dir()
    file_exists = os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def load_blocked_symbols(path: str = "data/blocked_symbols.csv") -> set:
    """
    Load the blocked symbols list from CSV.

    Returns a Python set of uppercase ticker strings, e.g. {'SQQQ', 'TQQQ', ...}
    Returns an empty set if the file does not exist or cannot be read.

    Args:
        path: Path to blocked_symbols.csv

    Usage:
        blocked = load_blocked_symbols()
        if "SQQQ" in blocked:
            print("SQQQ is blocked")
    """
    blocked = set()
    if not os.path.exists(path):
        return blocked
    try:
        df = pd.read_csv(path)
        if "symbol" in df.columns:
            for symbol in df["symbol"].dropna():
                cleaned = str(symbol).strip().upper()
                if cleaned:
                    blocked.add(cleaned)
    except Exception:
        pass
    return blocked


# ============================================================
# Terminal output helpers
# ============================================================

def print_section(title: str, width: int = 60) -> None:
    """
    Print a formatted section header with a box.

    Example output:
        ============================================================
          My Section Title
        ============================================================
    """
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def print_check(label: str, passed: bool, detail: str = "", width: int = 44) -> None:
    """
    Print a single setup-check result line.

    Args:
        label:  Description of what was checked.
        passed: True = OK, False = FAIL
        detail: Extra info to show after the status.
        width:  Column width for alignment.

    Example output:
        Checking Python version...          [OK]  Python 3.11.5
        Checking .env file...               [FAIL] File not found
    """
    status = "[OK]  " if passed else "[FAIL]"
    label_padded = (label + "...").ljust(width)
    if detail:
        print(f"  {label_padded} {status} {detail}")
    else:
        print(f"  {label_padded} {status}")


# ============================================================
# Ticker symbol validation
# ============================================================

def is_likely_common_stock(symbol: str) -> bool:
    """
    Quick check: does this look like a normal U.S. common stock ticker?

    Rejects symbols that look like:
    - Warrants (often end in 'W' and are longer than 4 chars)
    - Units (often end in 'U')
    - Rights (often end in 'R' with extended names like 'XYZR')
    - Preferred shares (often have suffixes like '.PA', '/A', '-A')
    - Blank check companies with test suffixes

    This is a heuristic, not perfect. The discovery filters add more checks.

    Args:
        symbol: The ticker string to check.

    Returns:
        True if the symbol passes basic common-stock pattern checks.
    """
    if not symbol or not isinstance(symbol, str):
        return False

    s = symbol.strip().upper()

    # Must be between 1 and 5 characters
    if len(s) < 1 or len(s) > 5:
        return False

    # Must be all uppercase letters (no numbers, dots, slashes)
    if not s.isalpha():
        return False

    # Warrants often end in W (e.g. SPNVW) — flag if 5 chars ending in W
    if len(s) == 5 and s.endswith("W"):
        return False

    # Rights often end in R and are 5 chars
    if len(s) == 5 and s.endswith("R"):
        return False

    return True


def normalize_ticker(symbol: str) -> str:
    """Normalize a ticker to uppercase with no extra whitespace."""
    return str(symbol).strip().upper()


# ============================================================
# Alerts log
# ============================================================

def trim_csv_if_large(
    path: str,
    max_rows: int = 1000,
    days_to_keep: int = 90,
    date_col: str = "date",
) -> None:
    """
    Trim a CSV file to the most recent `days_to_keep` days when it exceeds
    `max_rows` rows. Keeps files from growing unbounded on a long-running server.

    Called after appending to orders_log.csv and alerts_log.csv.
    Silently does nothing if the file is small or the date column is missing.
    """
    try:
        if not os.path.exists(path):
            return
        df = pd.read_csv(path, dtype=str)
        if len(df) <= max_rows:
            return
        if date_col not in df.columns:
            return
        from datetime import date as _date, timedelta as _timedelta
        cutoff = str(_date.today() - _timedelta(days=days_to_keep))
        df = df[df[date_col].astype(str) >= cutoff]
        df.to_csv(path, index=False)
    except Exception:
        pass


def log_alert(
    event_type: str,
    severity: str,
    message: str,
    ticker: str = "",
    action_required: str = "",
    alerts_path: str = "reports/alerts_log.csv",
) -> None:
    """
    Append one row to reports/alerts_log.csv.

    Called whenever a noteworthy automation event occurs so that
    you can review the bot's decisions without reading full logs.

    Args:
        event_type:      Short event identifier (e.g. "trade_signal", "bot_started")
        severity:        "INFO", "WARNING", "ERROR", or "CRITICAL"
        message:         Human-readable description of the event
        ticker:          Stock symbol if event is ticker-specific (optional)
        action_required: What the user should do, if anything (optional)
        alerts_path:     Path to the alerts CSV file

    Severity guide:
        INFO:     Normal bot operations (started, stopped, task completed)
        WARNING:  Unusual but handled (news provider failed, low confidence)
        ERROR:    Something went wrong but bot is still running
        CRITICAL: Bot paused itself — human investigation required
    """
    row = {
        "timestamp": now_str(),
        "event_type": str(event_type),
        "severity": str(severity).upper(),
        "message": str(message)[:300],
        "ticker": str(ticker).upper() if ticker else "",
        "action_required": str(action_required)[:200],
    }
    append_to_csv(alerts_path, row)
    trim_csv_if_large(alerts_path, max_rows=1000, days_to_keep=90)


# ============================================================
# Email notifications
# ============================================================

def send_email_alert(subject: str, body: str) -> bool:
    """
    Send a plain-text email alert via Gmail SMTP.

    Reads credentials from environment variables (loaded from .env):
      ALERT_EMAIL_FROM      Your Gmail address used to send  (e.g. mybot@gmail.com)
      ALERT_EMAIL_TO        Where to send alerts             (e.g. you@gmail.com)
      ALERT_EMAIL_PASSWORD  Gmail App Password — NOT your regular password.
                            Generate one at: myaccount.google.com/apppasswords
                            (Requires Gmail 2-Factor Authentication to be on)

    Subject is automatically prefixed with "[PaperBot]".

    Returns True if the email was sent successfully.
    Returns False and logs a warning if sending fails or credentials are missing.
    Silently returns False if credentials are not configured — never raises.

    Setup (one-time):
      1. Enable 2FA on your Google account
      2. Go to myaccount.google.com/apppasswords
      3. Create an App Password for "Mail"
      4. Add to your .env file:
           ALERT_EMAIL_FROM=yourbot@gmail.com
           ALERT_EMAIL_TO=you@gmail.com
           ALERT_EMAIL_PASSWORD=xxxx xxxx xxxx xxxx
    """
    import smtplib
    import ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    email_from = os.getenv("ALERT_EMAIL_FROM", "").strip()
    email_to = os.getenv("ALERT_EMAIL_TO", "").strip()
    email_password = os.getenv("ALERT_EMAIL_PASSWORD", "").strip()

    if not email_from or not email_to or not email_password:
        return False  # Not configured — silently skip

    logger = setup_logger(__name__)
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[PaperBot] {subject}"
        msg["From"] = email_from
        msg["To"] = email_to
        msg.attach(MIMEText(body, "plain"))

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(email_from, email_password)
            server.sendmail(email_from, email_to, msg.as_string())

        logger.info(f"Email alert sent: {subject}")
        return True

    except Exception as e:
        logger.warning(f"send_email_alert: failed — {e}")
        return False

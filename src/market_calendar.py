"""
src/market_calendar.py — U.S. Market Calendar and Trading Hours

Determines whether a given day is a U.S. stock market (NYSE/NASDAQ) trading day,
and whether the market is currently open.

Two sources of truth (used in priority order when a broker is provided):
  1. Alpaca market clock — live, authoritative, handles early closings
  2. Local holiday list  — hardcoded 2024-2027, always works offline

Safety principle:
  When in doubt, do NOT trade. All functions that return a boolean
  return False (not trading) as the conservative fallback.

NYSE regular session (Eastern Time):
  Open:  9:30 AM
  Close: 4:00 PM

Known limitations of the local fallback:
  - Does not model early closings (e.g. Black Friday, Christmas Eve)
  - Holiday list is only guaranteed accurate through 2027
  - For years beyond 2027, use Alpaca clock or add to _NYSE_HOLIDAYS

Usage:
    from src.market_calendar import is_trading_day, is_market_open, now_eastern

    if is_trading_day():
        print("Market is open today (or will be)")

    if is_market_open():
        print("Market is open right now")

    # With Alpaca broker for live status:
    clock = broker.get_clock()
    print("Live market open:", clock["is_open"])
"""

from datetime import date, datetime, time, timedelta
from typing import Optional

import pytz

from src.utils import setup_logger

logger = setup_logger(__name__)

EASTERN = pytz.timezone("America/New_York")
MARKET_OPEN_TIME  = time(9, 30)   # 9:30 AM Eastern
MARKET_CLOSE_TIME = time(16, 0)   # 4:00 PM Eastern


# ============================================================
# NYSE holiday list — hardcoded 2024-2027
# Source: NYSE Holiday Schedule (nyse.com/markets/hours-calendars)
# Observed dates used when a holiday falls on a weekend.
# ============================================================

_NYSE_HOLIDAYS: frozenset = frozenset({
    # --- 2024 ---
    date(2024, 1, 1),    # New Year's Day
    date(2024, 1, 15),   # Martin Luther King Jr. Day
    date(2024, 2, 19),   # Presidents' Day
    date(2024, 3, 29),   # Good Friday
    date(2024, 5, 27),   # Memorial Day
    date(2024, 6, 19),   # Juneteenth National Independence Day
    date(2024, 7, 4),    # Independence Day
    date(2024, 9, 2),    # Labor Day
    date(2024, 11, 28),  # Thanksgiving Day
    date(2024, 12, 25),  # Christmas Day
    # --- 2025 ---
    date(2025, 1, 1),    # New Year's Day
    date(2025, 1, 20),   # Martin Luther King Jr. Day
    date(2025, 2, 17),   # Presidents' Day
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 26),   # Memorial Day
    date(2025, 6, 19),   # Juneteenth National Independence Day
    date(2025, 7, 4),    # Independence Day
    date(2025, 9, 1),    # Labor Day
    date(2025, 11, 27),  # Thanksgiving Day
    date(2025, 12, 25),  # Christmas Day
    # --- 2026 ---
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # Martin Luther King Jr. Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth National Independence Day
    date(2026, 7, 3),    # Independence Day (observed — Jul 4 is Saturday)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving Day
    date(2026, 12, 25),  # Christmas Day
    # --- 2027 ---
    date(2027, 1, 1),    # New Year's Day
    date(2027, 1, 18),   # Martin Luther King Jr. Day
    date(2027, 2, 15),   # Presidents' Day
    date(2027, 3, 26),   # Good Friday
    date(2027, 5, 31),   # Memorial Day
    date(2027, 6, 18),   # Juneteenth (observed — Jun 19 is Saturday)
    date(2027, 7, 5),    # Independence Day (observed — Jul 4 is Sunday)
    date(2027, 9, 6),    # Labor Day
    date(2027, 11, 25),  # Thanksgiving Day
    date(2027, 12, 24),  # Christmas (observed — Dec 25 is Saturday)
})


# ============================================================
# Core date/time helpers
# ============================================================

def now_eastern() -> datetime:
    """Return the current datetime in U.S. Eastern time (timezone-aware)."""
    return datetime.now(tz=EASTERN)


def is_holiday(d: date) -> bool:
    """
    Return True if the given date is a NYSE market holiday.

    Uses the hardcoded holiday list. Does not contact any API.
    If the date falls outside 2024-2027, returns False (unknown — not assumed
    to be a holiday).

    Args:
        d: The date to check.
    """
    return d in _NYSE_HOLIDAYS


def is_weekend(d: date) -> bool:
    """Return True if the given date is a Saturday or Sunday."""
    return d.weekday() >= 5  # 5=Saturday, 6=Sunday


def is_trading_day(d: Optional[date] = None) -> bool:
    """
    Return True if the given date is a U.S. stock market trading day.

    A trading day is a weekday that is not a NYSE holiday.

    Args:
        d: Date to check. Defaults to today in Eastern time.

    Returns:
        True if the market trades on this day. False if weekend or holiday.
        Returns False (conservative) for dates outside the known holiday range.
    """
    if d is None:
        d = now_eastern().date()
    return not is_weekend(d) and not is_holiday(d)


def is_market_open(dt: Optional[datetime] = None) -> bool:
    """
    Return True if the U.S. stock market is currently open (regular session).

    Checks:
      1. Today is a trading day
      2. Current Eastern time is between 9:30 AM and 4:00 PM

    Note: Does not detect early closings (e.g. Black Friday). For that,
    use broker.get_clock() which is authoritative.

    Args:
        dt: Datetime to check (timezone-aware). Defaults to now in Eastern.

    Returns:
        True only if market is open. False if closed, weekend, or holiday.
    """
    if dt is None:
        dt = now_eastern()

    # Convert to Eastern if not already
    if dt.tzinfo is None:
        dt = EASTERN.localize(dt)
    else:
        dt = dt.astimezone(EASTERN)

    if not is_trading_day(dt.date()):
        return False

    current_time = dt.time().replace(second=0, microsecond=0)
    return MARKET_OPEN_TIME <= current_time < MARKET_CLOSE_TIME


def get_market_open_time(d: Optional[date] = None) -> datetime:
    """
    Return the market open time for the given date as a timezone-aware
    Eastern datetime.

    Args:
        d: Date to get open time for. Defaults to today.

    Returns:
        Timezone-aware datetime for 9:30 AM Eastern on that date.
    """
    if d is None:
        d = now_eastern().date()
    naive = datetime.combine(d, MARKET_OPEN_TIME)
    return EASTERN.localize(naive)


def get_market_close_time(d: Optional[date] = None) -> datetime:
    """
    Return the market close time for the given date as a timezone-aware
    Eastern datetime.

    Args:
        d: Date to get close time for. Defaults to today.

    Returns:
        Timezone-aware datetime for 4:00 PM Eastern on that date.
    """
    if d is None:
        d = now_eastern().date()
    naive = datetime.combine(d, MARKET_CLOSE_TIME)
    return EASTERN.localize(naive)


def next_trading_day(from_date: Optional[date] = None) -> date:
    """
    Return the next U.S. stock market trading day after the given date.

    Skips weekends and NYSE holidays. Searches up to 14 days ahead
    (handles holiday clusters).

    Args:
        from_date: Starting date (exclusive). Defaults to today.

    Returns:
        The next trading day. Raises RuntimeError if none found in 14 days
        (should never happen with the NYSE calendar).
    """
    if from_date is None:
        from_date = now_eastern().date()

    candidate = from_date + timedelta(days=1)
    for _ in range(14):
        if is_trading_day(candidate):
            return candidate
        candidate += timedelta(days=1)

    raise RuntimeError(
        f"Could not find next trading day within 14 days of {from_date}. "
        "Check the holiday list in market_calendar.py."
    )


def seconds_until_eastern(time_str: str) -> float:
    """
    Return the number of seconds until the next occurrence of the given
    time (HH:MM) in U.S. Eastern time.

    If the time has already passed today, returns seconds until the same
    time tomorrow.

    Args:
        time_str: Time in "HH:MM" format, e.g. "09:32" or "16:10"

    Returns:
        Seconds as a float. Always positive.

    Example:
        secs = seconds_until_eastern("09:32")
        time.sleep(secs)
    """
    hour, minute = map(int, time_str.split(":"))
    now = now_eastern()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if target <= now:
        target += timedelta(days=1)

    return (target - now).total_seconds()


def market_status_summary() -> dict:
    """
    Return a human-readable summary of the current market status.

    Does not connect to any external API. Uses local calendar only.

    Returns:
        {
            "now_eastern":     str  — current Eastern time string
            "is_trading_day":  bool — is today a trading day?
            "is_open_now":     bool — is market currently open?
            "is_holiday":      bool — is today a holiday?
            "is_weekend":      bool — is today a weekend?
            "next_trading_day": str — next trading day (YYYY-MM-DD)
        }
    """
    now = now_eastern()
    today = now.date()
    _next = next_trading_day(today) if not is_trading_day(today) else None

    return {
        "now_eastern": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "is_trading_day": is_trading_day(today),
        "is_open_now": is_market_open(now),
        "is_holiday": is_holiday(today),
        "is_weekend": is_weekend(today),
        "next_trading_day": str(_next) if _next else str(today),
    }


# ============================================================
# Alpaca clock integration (optional — requires a broker instance)
# ============================================================

def get_alpaca_market_open(broker) -> Optional[bool]:
    """
    Ask Alpaca whether the market is currently open.

    Returns None (not False) if the request fails, so callers can
    distinguish "definitely closed" from "couldn't check".

    Args:
        broker: AlpacaPaperBroker instance

    Returns:
        True if open, False if closed, None if Alpaca unavailable.
    """
    try:
        clock = broker.get_clock()
        return bool(clock["is_open"])
    except Exception as e:
        logger.warning(f"Could not fetch Alpaca market clock: {e} — using local fallback")
        return None


def is_market_open_safe(dt: Optional[datetime] = None, broker=None) -> bool:
    """
    Determine if the market is open, using Alpaca if available.

    Priority:
      1. Alpaca clock (live, handles early closings)
      2. Local calendar fallback

    If Alpaca is unavailable, falls back to local calendar.
    If even the local calendar can't determine status, returns False
    (conservative — do not trade on uncertainty).

    Args:
        dt:     Datetime to check (used for local fallback only)
        broker: Optional AlpacaPaperBroker instance

    Returns:
        True only if market is confirmed open. False otherwise.
    """
    if broker is not None:
        alpaca_result = get_alpaca_market_open(broker)
        if alpaca_result is not None:
            return alpaca_result
        logger.warning(
            "Alpaca clock unavailable — falling back to local market calendar. "
            "Will not trade if status is uncertain."
        )

    return is_market_open(dt)

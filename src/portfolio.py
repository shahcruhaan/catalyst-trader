"""
src/portfolio.py — Portfolio Position Tracking and Reporting

Pure functions for managing paper trading portfolio state.
No network I/O — all data comes from paper_state.json or orders_log.csv.

Responsibilities:
  - Track peak_price per position (needed for trailing stop logic)
  - Compute the active stop level for each position based on gain tier
  - Summarize overall portfolio P&L and allocation
  - Format position data for display in paper-report
  - Load recent order history for the report

Stop level tiers (mirror the exit rules in signal_engine.evaluate_exit_signal):
  gain < +20%:      Hard stop at entry * (1 - stop_loss_pct)
  +20% to +40%:     Breakeven stop — stop raised to entry price
  +40% to +75%:     Partial sell triggered, remainder trails 10-day MA
  >= +75%:          Big winner — trails 20-day MA for maximum protection

MA-based stops (10d / 20d) require fresh price data that is not stored in
paper_state.json. compute_stop_level() returns stop_price=None for those tiers.
The actual MA check is performed by evaluate_exit_signal() in signal_engine.py
during every paper-scan run.

paper_state.json position dict (extended by Stage 6):
    {
        "ticker":           "NVDA",
        "entry_price":      120.50,     original intended entry price
        "avg_entry_price":  120.75,     actual Alpaca fill price (from reconcile)
        "shares":           149,
        "market_value":     18031.75,   current market value (from reconcile)
        "unrealized_pl":    374.75,     current P&L in $ (from reconcile)
        "unrealized_plpc":  0.0208,     current P&L fraction (from reconcile)
        "current_price":    120.75,     latest price (from reconcile)
        "entry_date":       "2026-06-01",
        "holding_days":     5,          calendar days since entry
        "partial_taken":    false,
        "catalyst_score":   9,
        "catalyst_headline":"Beat and raise",
        "peak_price":       125.00      highest price seen since entry (Stage 6)
    }
"""

import os
from typing import Dict, List

from src.utils import setup_logger, load_csv_safe

logger = setup_logger(__name__)


# ============================================================
# Peak price tracking
# ============================================================

def update_peak_price(positions: Dict[str, dict]) -> Dict[str, dict]:
    """
    Update peak_price for every position in the state dict.

    peak_price tracks the highest closing price seen since entry.
    It is used by winner management to know when stop levels should
    be raised (e.g., once peak was > +20%, the breakeven stop activates
    even if the stock has since pulled back).

    Logic per position:
      - If peak_price not yet set: initialize to current_price
      - If current_price > peak_price: update peak_price
      - Otherwise: preserve existing peak_price (never lowers)

    Reads current_price from the position dict — this is set by
    reconcile_state() after fetching live data from Alpaca.

    Args:
        positions: Dict of {ticker: position_dict} from paper_state.json

    Returns:
        The same positions dict, mutated in place, with peak_price updated.
    """
    for ticker, pos in positions.items():
        current_price = float(pos.get("current_price") or pos.get("entry_price") or 0)

        if current_price <= 0:
            logger.debug(f"{ticker}: cannot update peak_price — no valid current_price")
            continue

        existing_peak = float(pos.get("peak_price") or 0)
        new_peak = max(existing_peak, current_price)

        if new_peak != existing_peak:
            logger.debug(f"{ticker}: peak_price updated {existing_peak:.2f} → {new_peak:.2f}")

        pos["peak_price"] = new_peak

    return positions


# ============================================================
# Stop level computation
# ============================================================

def compute_stop_level(position: dict, config) -> dict:
    """
    Compute the current active stop level for a single position.

    The stop changes as the position gains (tiered winner management):

      Tier "normal"     (gain < +20%):
        Hard stop loss at entry * (1 - stop_loss_pct).
        Default: 8% below entry.

      Tier "breakeven"  (gain >= +20%):
        Stop raised to entry_price. Can't lose money on this trade.
        Also trails on 10-day MA from this point (MA check at scan time).

      Tier "partial"    (gain >= +40%):
        1/3 partial sell was (or should be) triggered.
        Remainder trails on 10-day MA.
        stop_price is None — MA checked at scan time.

      Tier "big_winner" (gain >= +75%):
        Trails on 20-day MA for maximum protection of the big win.
        stop_price is None — MA checked at scan time.

    Note: MA-based stops return stop_price=None. The caller should use the
    MA values from indicators when available (during paper-scan).

    Args:
        position: Single position dict from paper_state.json
        config:   AppConfig object (or SimpleNamespace for tests)

    Returns:
        {
            "stop_price":    float or None  — hard dollar stop (None = MA-based)
            "stop_type":     str            — human-readable description
            "gain_pct":      float          — current gain from entry_price
            "tier":          str            — "normal", "breakeven", "partial", "big_winner"
        }
    """
    stop_loss_pct = getattr(config, "stop_loss_pct", 0.08)
    breakeven_trigger = getattr(config, "breakeven_trigger_pct", 0.20)
    partial_trigger = getattr(config, "partial_take_profit_trigger_pct", 0.40)
    big_winner_trigger = getattr(config, "big_winner_trigger_pct", 0.75)

    entry_price = float(position.get("entry_price") or 0)
    current_price = float(position.get("current_price") or entry_price)

    if entry_price <= 0:
        return {
            "stop_price": None,
            "stop_type": "unknown",
            "gain_pct": 0.0,
            "tier": "unknown",
        }

    gain_pct = round((current_price / entry_price) - 1.0, 8)

    # Use peak_price for tier classification — once you hit a tier, you stay in it
    # even if price pulls back (avoids flip-flopping on the stop type).
    peak_price = float(position.get("peak_price") or current_price)
    peak_gain_pct = round((peak_price / entry_price) - 1.0, 8) if entry_price > 0 else 0.0

    if peak_gain_pct >= big_winner_trigger:
        return {
            "stop_price": None,   # trails 20-day MA — set at scan time
            "stop_type": "trail_20d_ma",
            "gain_pct": gain_pct,
            "tier": "big_winner",
        }
    elif peak_gain_pct >= partial_trigger:
        return {
            "stop_price": None,   # trails 10-day MA — set at scan time
            "stop_type": "trail_10d_ma",
            "gain_pct": gain_pct,
            "tier": "partial",
        }
    elif peak_gain_pct >= breakeven_trigger:
        return {
            "stop_price": entry_price,   # can't lose money on this one
            "stop_type": "breakeven",
            "gain_pct": gain_pct,
            "tier": "breakeven",
        }
    else:
        hard_stop = entry_price * (1.0 - stop_loss_pct)
        return {
            "stop_price": hard_stop,
            "stop_type": f"hard_stop_{stop_loss_pct:.0%}",
            "gain_pct": gain_pct,
            "tier": "normal",
        }


# ============================================================
# Portfolio summary
# ============================================================

def compute_portfolio_summary(state: dict, config) -> dict:
    """
    Compute overall portfolio statistics from paper_state.json.

    This is a snapshot calculation — it does not connect to Alpaca.
    Values come from the last reconcile that populated market_value
    and unrealized_pl in each position dict.

    Args:
        state:  Paper trading state dict (from load_state())
        config: AppConfig object

    Returns:
        {
            "portfolio_value":       float — total portfolio value ($)
            "buying_power":          float — available buying power ($)
            "cash":                  float — uninvested cash ($)
            "position_count":        int   — number of open positions
            "total_invested":        float — sum of market_value across positions
            "total_unrealized_pl":   float — total unrealized P&L ($)
            "total_unrealized_plpc": float — total unrealized P&L as fraction
            "starting_cash":         float — from config (initial paper portfolio)
            "total_return_pct":      float — (portfolio_value - starting_cash) / starting_cash
        }
    """
    positions = state.get("positions", {})
    portfolio_value = float(state.get("portfolio_value") or 0)
    buying_power = float(state.get("buying_power") or 0)
    cash = float(state.get("cash") or 0)
    starting_cash = float(getattr(config, "starting_cash", 100_000))

    total_invested = sum(
        float(p.get("market_value") or 0) for p in positions.values()
    )
    total_unrealized_pl = sum(
        float(p.get("unrealized_pl") or 0) for p in positions.values()
    )

    total_unrealized_plpc = (
        total_unrealized_pl / total_invested if total_invested > 0 else 0.0
    )
    total_return_pct = (
        (portfolio_value - starting_cash) / starting_cash if starting_cash > 0 else 0.0
    )

    return {
        "portfolio_value": portfolio_value,
        "buying_power": buying_power,
        "cash": cash,
        "position_count": len(positions),
        "total_invested": total_invested,
        "total_unrealized_pl": total_unrealized_pl,
        "total_unrealized_plpc": total_unrealized_plpc,
        "starting_cash": starting_cash,
        "total_return_pct": total_return_pct,
    }


# ============================================================
# Position table formatting
# ============================================================

def format_positions_table(state: dict, config) -> List[dict]:
    """
    Format each open position as a display row for paper-report.

    Returns a list of dicts, one per position, sorted by unrealized P&L
    descending (biggest winner first).

    Each row contains all fields needed for terminal display:
      ticker, shares, entry_price, current_price, market_value,
      unrealized_pl, unrealized_plpc, holding_days, entry_date,
      stop_price, stop_type, gain_tier, gain_pct, peak_price,
      partial_taken, catalyst_score, catalyst_headline

    Args:
        state:  Paper trading state dict
        config: AppConfig object

    Returns:
        List of position row dicts, sorted largest-winner-first.
    """
    positions = state.get("positions", {})
    rows = []

    for ticker, pos in positions.items():
        stop_info = compute_stop_level(pos, config)

        row = {
            "ticker": ticker,
            "shares": int(pos.get("shares") or 0),
            "entry_price": float(pos.get("entry_price") or 0),
            "avg_entry_price": float(
                pos.get("avg_entry_price") or pos.get("entry_price") or 0
            ),
            "current_price": float(pos.get("current_price") or 0),
            "market_value": float(pos.get("market_value") or 0),
            "unrealized_pl": float(pos.get("unrealized_pl") or 0),
            "unrealized_plpc": float(pos.get("unrealized_plpc") or 0),
            "holding_days": int(pos.get("holding_days") or 0),
            "entry_date": str(pos.get("entry_date") or ""),
            "stop_price": stop_info["stop_price"],
            "stop_type": stop_info["stop_type"],
            "gain_tier": stop_info["tier"],
            "gain_pct": stop_info["gain_pct"],
            "peak_price": float(pos.get("peak_price") or 0),
            "partial_taken": bool(pos.get("partial_taken", False)),
            "catalyst_score": pos.get("catalyst_score", ""),
            "catalyst_headline": str(pos.get("catalyst_headline") or ""),
        }
        rows.append(row)

    rows.sort(key=lambda r: r["unrealized_pl"], reverse=True)
    return rows


# ============================================================
# Recent orders
# ============================================================

def load_recent_orders(log_path: str, n: int = 10) -> List[dict]:
    """
    Load the N most recent rows from orders_log.csv.

    Returns a list of order dicts sorted newest-first.
    Returns [] if the log doesn't exist or is empty.

    Args:
        log_path: Path to reports/orders_log.csv
        n:        Maximum number of recent rows to return (default 10)

    Returns:
        List of order dicts, newest first, up to n rows.
    """
    if not os.path.exists(log_path):
        return []

    df = load_csv_safe(log_path)
    if df is None or df.empty:
        return []

    if "timestamp" in df.columns:
        df = df.sort_values("timestamp", ascending=False)

    return df.head(n).to_dict("records")

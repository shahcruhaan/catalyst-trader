"""
src/execution_engine.py — Paper Order Execution with Safety Checks

This module takes signals from reports/paper_signals.csv and submits
them to Alpaca Paper Trading via AlpacaPaperBroker.

PAPER TRADING ONLY — no real orders are ever placed.

Pre-order safety checks (run before every BUY order):
  1. Symbol is in today's discovered universe
  2. Symbol is not on the blocked list
  3. Catalyst is fresh, score >= min_catalyst_score, and tradeable=True
  4. Max open positions limit not exceeded
  5. Max orders per day limit not exceeded
  6. No duplicate open order already pending in Alpaca for this symbol
  7. Sufficient buying power for the target allocation

Pre-order safety checks (run before every SELL order):
  1. Symbol has an open position in Alpaca
  2. No duplicate open sell order already pending for this symbol

Every order attempt — submitted or blocked — is logged to
reports/orders_log.csv with a timestamp, action, and reason.

State is persisted in reports/paper_state.json and updated after
every execute run. paper-reconcile overwrites this with Alpaca's
actual filled data.

paper_state.json format:
    {
        "last_updated":          "2026-06-02 09:32:15",
        "portfolio_value":        100000.0,
        "buying_power":           82000.0,
        "cash":                   82000.0,
        "orders_submitted_today": 2,
        "last_execute_date":      "2026-06-02",
        "positions": {
            "NVDA": {
                "ticker":           "NVDA",
                "entry_price":      120.50,
                "shares":           149,
                "entry_date":       "2026-06-01",
                "holding_days":     1,
                "partial_taken":    false,
                "catalyst_score":   9,
                "catalyst_headline":"Beat and raise"
            }
        }
    }
"""

import json
import os
from datetime import datetime, date
from typing import Dict, List, Tuple

from src.utils import (
    setup_logger, today_str, now_str,
    load_csv_safe, load_blocked_symbols,
    ensure_dir, normalize_ticker, append_to_csv, trim_csv_if_large,
)
from src.catalyst_scorer import load_catalysts, get_tradeable_catalyst
from src.ranking import calculate_position_size

logger = setup_logger(__name__)


# ============================================================
# Alpha helpers
# ============================================================

def _conviction_position_pct(signal: dict, config) -> float:
    """
    Scale position size by catalyst score conviction.

      score 8  → target_position_size  (base)
      score 9  → midpoint between target and max
      score 10 → max_position_size

    Returns a fraction (e.g. 0.21 for 21%).
    """
    target = getattr(config, "target_position_size", 0.18)
    max_pct = getattr(config, "max_position_size", 0.25)
    try:
        score = int(float(signal.get("catalyst_score") or 8))
    except (TypeError, ValueError):
        score = 8
    score = max(8, min(10, score))
    conviction = target + (score - 8) / 2.0 * (max_pct - target)
    return min(conviction, max_pct)


def _is_gapped_up(ticker: str, signal_close: float, max_gap: float) -> bool:
    """
    Returns True if the stock's current market price has already gapped up
    more than max_gap (e.g. 0.05 = 5%) relative to the signal's close price.

    Uses yfinance fast_info for a lightweight current-price check.
    Defaults to False (allow the trade) if the check cannot complete.
    """
    if signal_close <= 0 or max_gap <= 0:
        return False
    try:
        import yfinance as yf
        fast = yf.Ticker(ticker).fast_info
        last_price = getattr(fast, "last_price", None)
        if last_price and float(last_price) > 0:
            gap = (float(last_price) / signal_close) - 1.0
            if gap > max_gap:
                logger.info(
                    f"{ticker}: gap-up {gap:.1%} vs signal close ${signal_close:.2f} "
                    f"exceeds {max_gap:.0%} limit — skipping buy"
                )
                return True
    except Exception:
        pass
    return False


# Default path constants (overridden by config in practice)
_DEFAULT_STATE_PATH = "reports/paper_state.json"
_DEFAULT_ORDER_LOG = "reports/orders_log.csv"
_DEFAULT_SIGNALS_PATH = "reports/paper_signals.csv"
_DEFAULT_UNIVERSE_PATH = "reports/daily_discovered_universe.csv"


# ============================================================
# State file helpers
# ============================================================

def load_state(path: str = _DEFAULT_STATE_PATH) -> dict:
    """
    Load the paper trading state from JSON.

    Returns a fresh empty state if the file doesn't exist or is corrupt.
    """
    if not os.path.exists(path):
        return _empty_state()
    try:
        with open(path, "r") as f:
            state = json.load(f)
        if "positions" not in state:
            state["positions"] = {}
        return state
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"Could not load state from {path}: {e} — using empty state")
        return _empty_state()


def save_state(state: dict, path: str = _DEFAULT_STATE_PATH) -> None:
    """Persist the paper trading state to JSON. Backs up previous file first."""
    import shutil
    ensure_dir("reports")
    # Back up before overwriting so a bad write never destroys position history
    if os.path.exists(path):
        try:
            shutil.copy2(path, path + ".bak")
        except Exception:
            pass
    state["last_updated"] = now_str()
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def _empty_state() -> dict:
    return {
        "last_updated": now_str(),
        "portfolio_value": 0.0,
        "buying_power": 0.0,
        "cash": 0.0,
        "orders_submitted_today": 0,
        "last_execute_date": "",
        "positions": {},
    }


# ============================================================
# Orders-today counter
# ============================================================

def count_orders_today(log_path: str) -> int:
    """
    Count how many orders were submitted today by reading orders_log.csv.

    Only counts rows where action == "SUBMITTED" and date == today.
    """
    if not os.path.exists(log_path):
        return 0
    df = load_csv_safe(log_path)
    if df is None or df.empty:
        return 0
    if "date" not in df.columns or "action" not in df.columns:
        return 0
    today = today_str()
    submitted_today = df[
        (df["date"].astype(str) == today) &
        (df["action"].astype(str) == "SUBMITTED")
    ]
    return len(submitted_today)


# ============================================================
# Universe loader
# ============================================================

def load_universe_tickers(universe_path: str) -> set:
    """
    Load tickers from today's discovered universe CSV.

    Returns a set of uppercase ticker strings.
    Returns empty set if the file is missing or stale.
    """
    df = load_csv_safe(universe_path)
    if df is None or df.empty:
        return set()

    ticker_col = "ticker" if "ticker" in df.columns else df.columns[0]

    # Only include tickers from today's discovery
    if "date" in df.columns:
        today = today_str()
        df = df[df["date"].astype(str).str[:10] == today]

    # Only include tickers that passed filters (if column exists)
    if "passed_filters" in df.columns:
        df = df[df["passed_filters"] == True]

    tickers = set()
    for val in df[ticker_col].dropna():
        t = normalize_ticker(str(val))
        if t:
            tickers.add(t)
    return tickers


# ============================================================
# Order logging
# ============================================================

def log_order(
    log_path: str,
    ticker: str,
    signal_type: str,
    action: str,
    amount: float,        # notional $ for buys, share count for sells
    amount_label: str,    # "notional" or "qty"
    order_id: str = "",
    order_status: str = "",
    check_passed: bool = True,
    fail_reason: str = "",
) -> None:
    """
    Append one row to the orders log CSV.

    Every order attempt — submitted, blocked, or errored — is logged here.
    This creates a complete audit trail of every decision the bot made.

    Args:
        log_path:     Path to orders_log.csv
        ticker:       Stock symbol
        signal_type:  BUY / EXIT / EXIT_PARTIAL / EMERGENCY_CLOSE
        action:       SUBMITTED / BLOCKED / ERROR
        amount:       Dollar notional (buy) or share count (sell)
        amount_label: "notional" or "qty"
        order_id:     Alpaca order UUID (empty if blocked)
        order_status: Alpaca order status (empty if blocked)
        check_passed: True if all pre-order checks passed
        fail_reason:  Which check failed (empty if all passed)
    """
    ensure_dir("reports")
    row = {
        "timestamp": now_str(),
        "date": today_str(),
        "ticker": ticker,
        "signal_type": signal_type,
        "action": action,
        amount_label: amount,
        "order_id": order_id,
        "order_status": order_status,
        "checks_passed": check_passed,
        "fail_reason": fail_reason,
    }
    # Normalize to a fixed column set
    standard_row = {
        "timestamp": row["timestamp"],
        "date": row["date"],
        "ticker": row["ticker"],
        "signal_type": row["signal_type"],
        "action": row["action"],
        "notional": row.get("notional", ""),
        "qty": row.get("qty", ""),
        "order_id": row["order_id"],
        "order_status": row["order_status"],
        "checks_passed": row["checks_passed"],
        "fail_reason": row["fail_reason"],
    }
    append_to_csv(log_path, standard_row)
    trim_csv_if_large(log_path, max_rows=1000, days_to_keep=90)


# ============================================================
# Pre-order safety checks
# ============================================================

def check_buy_preconditions(
    ticker: str,
    universe_tickers: set,
    blocked_symbols: set,
    catalysts: dict,
    alpaca_positions: Dict[str, dict],
    alpaca_open_orders: List[dict],
    buying_power: float,
    orders_today: int,
    config,
) -> Tuple[bool, str]:
    """
    Run all 7 safety checks before submitting a BUY order.

    Returns:
        (True, "")           — all checks passed, safe to submit
        (False, "reason")    — a check failed, do not submit

    Checks (in order):
        1. Symbol is in today's discovered universe
        2. Symbol is not blocked
        3. Fresh catalyst with score >= min_catalyst_score and tradeable=True
        4. Max open positions not exceeded
        5. Max orders per day not exceeded
        6. No duplicate open order for this symbol
        7. Sufficient buying power for target allocation
    """
    min_score = getattr(config, "min_catalyst_score", 8)
    max_positions = getattr(config, "max_positions", 8)
    max_orders = getattr(config, "max_orders_per_day", 10)
    target_pct = getattr(config, "target_position_size", 0.18)
    max_pct = getattr(config, "max_position_size", 0.25)
    tx_bps = getattr(config, "transaction_cost_bps", 5)
    slip_bps = getattr(config, "slippage_bps", 10)

    # 1. In discovered universe?
    if universe_tickers and ticker not in universe_tickers:
        return False, f"not_in_universe: {ticker} not in today's discovered universe"

    # 2. Not blocked?
    if ticker in blocked_symbols:
        return False, f"blocked_symbol: {ticker} is on the blocked list"

    # 3. Valid catalyst?
    cat = get_tradeable_catalyst(ticker, catalysts, min_score=min_score)
    if cat is None:
        return False, (
            f"no_valid_catalyst: {ticker} has no fresh tradeable catalyst "
            f"with score >= {min_score}"
        )

    # 4. Position limit?
    current_count = len(alpaca_positions)
    if current_count >= max_positions:
        return False, (
            f"max_positions: already have {current_count}/{max_positions} open positions"
        )

    # 5. Daily order limit?
    if orders_today >= max_orders:
        return False, (
            f"max_orders_per_day: already submitted {orders_today}/{max_orders} orders today"
        )

    # 6. Duplicate open order?
    open_buy_tickers = {
        o["ticker"] for o in alpaca_open_orders
        if o.get("side", "").lower() == "buy"
    }
    if ticker in open_buy_tickers:
        return False, f"duplicate_order: already have an open BUY order for {ticker}"

    # 7. Also skip if already holds this position
    if ticker in alpaca_positions:
        return False, f"already_held: {ticker} is already in an open position"

    # 8. Enough buying power?
    # Calculate required notional based on the full portfolio (starting_cash),
    # then verify buying_power can cover it.
    starting_cash = getattr(config, "starting_cash", 100_000.0)
    cost_fraction = (tx_bps + slip_bps) / 10_000
    required = min(starting_cash * target_pct, starting_cash * max_pct) * (1.0 - cost_fraction)
    if required > buying_power:
        return False, (
            f"insufficient_buying_power: need ${required:,.0f} "
            f"but only ${buying_power:,.0f} available"
        )

    return True, ""


def check_sell_preconditions(
    ticker: str,
    alpaca_positions: Dict[str, dict],
    alpaca_open_orders: List[dict],
) -> Tuple[bool, str]:
    """
    Run safety checks before submitting a SELL order.

    Returns:
        (True, "")          — safe to sell
        (False, "reason")   — cannot sell
    """
    # 1. Have position?
    if ticker not in alpaca_positions:
        return False, f"no_position: {ticker} has no open position in Alpaca"

    qty = alpaca_positions[ticker].get("qty", 0)
    if qty <= 0:
        return False, f"zero_qty: {ticker} position has qty={qty}"

    # 2. Duplicate open sell order?
    open_sell_tickers = {
        o["ticker"] for o in alpaca_open_orders
        if o.get("side", "").lower() == "sell"
    }
    if ticker in open_sell_tickers:
        return False, f"duplicate_sell_order: already have an open SELL order for {ticker}"

    return True, ""


# ============================================================
# Main execution functions
# ============================================================

def execute_buy_signals(
    buy_signals: List[dict],
    broker,
    config,
    state: dict,
    account: dict,
    alpaca_positions: Dict[str, dict],
    alpaca_open_orders: List[dict],
    universe_tickers: set,
    blocked_symbols: set,
    catalysts: dict,
    orders_today: int,
) -> Tuple[List[dict], int]:
    """
    Attempt to execute all buy signals through Alpaca Paper Trading.

    Runs all safety checks before each order. Logs every attempt.

    Returns:
        (submitted_orders, new_orders_count)
    """
    log_path = getattr(config, "order_log_path", _DEFAULT_ORDER_LOG)
    max_pct = getattr(config, "max_position_size", 0.25)
    tx_bps = getattr(config, "transaction_cost_bps", 5)
    slip_bps = getattr(config, "slippage_bps", 10)
    gap_up_max = getattr(config, "gap_up_max_pct", 0.05)
    buying_power = account["buying_power"]

    submitted = []
    new_count = 0

    for signal in buy_signals:
        ticker = normalize_ticker(str(signal.get("ticker") or ""))
        if not ticker:
            continue

        # Run all pre-order checks
        ok, reason = check_buy_preconditions(
            ticker=ticker,
            universe_tickers=universe_tickers,
            blocked_symbols=blocked_symbols,
            catalysts=catalysts,
            alpaca_positions=alpaca_positions,
            alpaca_open_orders=alpaca_open_orders,
            buying_power=buying_power,
            orders_today=orders_today + new_count,
            config=config,
        )

        if not ok:
            logger.info(f"BUY blocked — {ticker}: {reason}")
            log_order(
                log_path=log_path,
                ticker=ticker,
                signal_type="BUY",
                action="BLOCKED",
                amount=0,
                amount_label="notional",
                check_passed=False,
                fail_reason=reason,
            )
            continue

        close_price = float(signal.get("close") or 1.0)

        # Feature 5: Gap-up execution filter
        if _is_gapped_up(ticker, close_price, gap_up_max):
            log_order(
                log_path=log_path,
                ticker=ticker,
                signal_type="BUY",
                action="BLOCKED",
                amount=0,
                amount_label="notional",
                check_passed=False,
                fail_reason=f"gap_up_filter: price already gapped >{gap_up_max:.0%} vs signal close",
            )
            continue

        # Feature 1: Conviction-based position sizing
        conviction_pct = _conviction_position_pct(signal, config)
        sizing = calculate_position_size(
            portfolio_value=buying_power,
            current_price=close_price,
            target_pct=conviction_pct,
            max_pct=max_pct,
            transaction_cost_bps=tx_bps,
            slippage_bps=slip_bps,
        )
        notional = sizing["adjusted_dollars"]

        if notional <= 0:
            log_order(
                log_path=log_path,
                ticker=ticker,
                signal_type="BUY",
                action="BLOCKED",
                amount=0,
                amount_label="notional",
                check_passed=False,
                fail_reason="zero_notional: calculated notional is 0",
            )
            continue

        # Submit the order
        try:
            order = broker.submit_buy_order(ticker, notional)

            log_order(
                log_path=log_path,
                ticker=ticker,
                signal_type="BUY",
                action="SUBMITTED",
                amount=notional,
                amount_label="notional",
                order_id=order["id"],
                order_status=order["status"],
                check_passed=True,
            )

            # Update local state with expected position
            state["positions"][ticker] = {
                "ticker": ticker,
                "entry_price": close_price,
                "shares": sizing["shares"],
                "entry_date": today_str(),
                "holding_days": 0,
                "partial_taken": False,
                "catalyst_score": signal.get("catalyst_score", 0),
                "catalyst_headline": signal.get("catalyst_headline", ""),
            }

            submitted.append(order)
            new_count += 1
            buying_power -= notional  # Update local tracking

        except RuntimeError as e:
            logger.error(f"BUY order failed for {ticker}: {e}")
            log_order(
                log_path=log_path,
                ticker=ticker,
                signal_type="BUY",
                action="ERROR",
                amount=notional,
                amount_label="notional",
                check_passed=True,
                fail_reason=str(e),
            )

    return submitted, new_count


def execute_sell_signals(
    exit_signals: List[dict],
    broker,
    config,
    state: dict,
    alpaca_positions: Dict[str, dict],
    alpaca_open_orders: List[dict],
) -> List[dict]:
    """
    Attempt to execute all exit/sell signals through Alpaca Paper Trading.

    For EXIT_PARTIAL: sells 1/3 of the position (rounded down).
    For EXIT: sells the full position quantity from Alpaca.

    Returns:
        List of submitted sell order dicts.
    """
    log_path = getattr(config, "order_log_path", _DEFAULT_ORDER_LOG)
    submitted = []

    for signal in exit_signals:
        ticker = normalize_ticker(str(signal.get("ticker") or ""))
        if not ticker:
            continue

        signal_type = str(signal.get("signal_type", "EXIT"))
        reason = str(signal.get("reason", ""))

        # Run sell pre-checks
        ok, fail_reason = check_sell_preconditions(
            ticker=ticker,
            alpaca_positions=alpaca_positions,
            alpaca_open_orders=alpaca_open_orders,
        )

        if not ok:
            logger.info(f"SELL blocked — {ticker}: {fail_reason}")
            log_order(
                log_path=log_path,
                ticker=ticker,
                signal_type=signal_type,
                action="BLOCKED",
                amount=0,
                amount_label="qty",
                check_passed=False,
                fail_reason=fail_reason,
            )
            continue

        actual_qty = alpaca_positions[ticker]["qty"]
        partial_fraction = float(signal.get("partial_fraction", 1.0))

        if signal_type == "EXIT_PARTIAL":
            sell_qty = max(1, int(actual_qty * partial_fraction))
        else:
            sell_qty = actual_qty

        if sell_qty <= 0:
            log_order(
                log_path=log_path,
                ticker=ticker,
                signal_type=signal_type,
                action="BLOCKED",
                amount=0,
                amount_label="qty",
                check_passed=False,
                fail_reason="zero_qty: calculated sell qty is 0",
            )
            continue

        # Submit the sell
        try:
            order = broker.submit_sell_order(ticker, sell_qty)

            log_order(
                log_path=log_path,
                ticker=ticker,
                signal_type=signal_type,
                action="SUBMITTED",
                amount=sell_qty,
                amount_label="qty",
                order_id=order["id"],
                order_status=order["status"],
                check_passed=True,
                fail_reason=f"exit_reason={reason}",
            )

            # Update local state
            if signal_type == "EXIT_PARTIAL":
                if ticker in state["positions"]:
                    state["positions"][ticker]["shares"] = actual_qty - sell_qty
                    state["positions"][ticker]["partial_taken"] = True
            else:
                state["positions"].pop(ticker, None)

            submitted.append(order)

        except RuntimeError as e:
            logger.error(f"SELL order failed for {ticker}: {e}")
            log_order(
                log_path=log_path,
                ticker=ticker,
                signal_type=signal_type,
                action="ERROR",
                amount=sell_qty,
                amount_label="qty",
                check_passed=True,
                fail_reason=str(e),
            )

    return submitted


# ============================================================
# Reconcile: sync local state with Alpaca's actual state
# ============================================================

def reconcile_state(broker, config, state: dict) -> dict:
    """
    Sync paper_state.json with Alpaca's actual filled positions and account data.

    This is called by paper-reconcile. It overwrites local position data with
    Alpaca's actual state, which reflects what was actually filled.

    For each Alpaca position:
      - If we knew about it: preserve entry_date, holding_days, catalyst info
      - If it's new/unknown: record entry_date as today with a note

    Returns:
        Updated state dict (also saves to disk).
    """
    logger.info("Reconciling local state with Alpaca...")

    account = broker.get_account()
    alpaca_positions = broker.get_positions()

    state["portfolio_value"] = account["portfolio_value"]
    state["buying_power"] = account["buying_power"]
    state["cash"] = account["cash"]

    # Build reconciled positions
    reconciled = {}

    for ticker, alpaca_pos in alpaca_positions.items():
        existing = state.get("positions", {}).get(ticker, {})

        entry_date = existing.get("entry_date") or today_str()
        partial_taken = existing.get("partial_taken", False)
        catalyst_score = existing.get("catalyst_score", "")
        catalyst_headline = existing.get("catalyst_headline", "")
        # Preserve peak_price — update_peak_price() will raise it if current is higher
        peak_price = existing.get("peak_price", 0)

        # Calculate holding days (calendar days since entry)
        try:
            entry_dt = datetime.strptime(entry_date, "%Y-%m-%d").date()
            holding_days = (date.today() - entry_dt).days
        except (ValueError, TypeError):
            holding_days = 0

        reconciled[ticker] = {
            "ticker": ticker,
            "entry_price": existing.get("entry_price") or alpaca_pos["avg_entry_price"],
            "avg_entry_price": alpaca_pos["avg_entry_price"],
            "shares": alpaca_pos["qty"],
            "market_value": alpaca_pos["market_value"],
            "unrealized_pl": alpaca_pos["unrealized_pl"],
            "unrealized_plpc": alpaca_pos["unrealized_plpc"],
            "current_price": alpaca_pos["current_price"],
            "entry_date": entry_date,
            "holding_days": holding_days,
            "partial_taken": partial_taken,
            "catalyst_score": catalyst_score,
            "catalyst_headline": catalyst_headline,
            "peak_price": peak_price,
        }

    state["positions"] = reconciled

    # Reset daily order counter if it's a new day
    if state.get("last_execute_date") != today_str():
        state["orders_submitted_today"] = 0

    state_path = getattr(config, "state_file_path", _DEFAULT_STATE_PATH)
    save_state(state, state_path)

    logger.info(
        f"Reconcile complete: {len(reconciled)} position(s)  "
        f"portfolio=${account['portfolio_value']:,.2f}  "
        f"buying_power=${account['buying_power']:,.2f}"
    )
    return state


# ============================================================
# Top-level orchestration
# ============================================================

def run_paper_execute(config) -> dict:
    """
    Execute today's signals against Alpaca Paper Trading.

    Steps:
      1. Safety gate: require paper mode
      2. Connect to Alpaca
      3. Load signals, state, universe, blocked, catalysts
      4. Execute buy signals (with all pre-checks)
      5. Execute sell/exit signals (with pre-checks)
      6. Save updated state
      7. Return summary dict

    Returns:
        {
            "buys_submitted":   int,
            "buys_blocked":     int,
            "sells_submitted":  int,
            "sells_blocked":    int,
            "errors":           int,
            "total_signals":    int,
        }
    """
    from src.broker_alpaca import AlpacaPaperBroker

    config.require_paper_mode()
    broker = AlpacaPaperBroker.from_config(config)

    signals_path = getattr(config, "signal_file_path", _DEFAULT_SIGNALS_PATH)
    state_path = getattr(config, "state_file_path", _DEFAULT_STATE_PATH)
    universe_path = getattr(config, "discovered_universe_path", _DEFAULT_UNIVERSE_PATH)
    blocked_path = getattr(config, "blocked_symbols_path", "data/blocked_symbols.csv")
    catalyst_path = getattr(config, "manual_catalyst_path", "data/catalyst_overrides.csv")
    log_path = getattr(config, "order_log_path", _DEFAULT_ORDER_LOG)

    # Load signals
    signals_df = load_csv_safe(signals_path)
    if signals_df is None or signals_df.empty:
        logger.warning(f"No signals found at {signals_path}. Run paper-scan first.")
        return {"buys_submitted": 0, "buys_blocked": 0, "sells_submitted": 0,
                "sells_blocked": 0, "errors": 0, "total_signals": 0}

    # EXIT signals always execute regardless of age (protecting open positions).
    # BUY signals are only valid for 1 calendar day — stale breakout signals must not fire.
    from src.market_calendar import now_eastern
    today_date = now_eastern().date()
    yesterday_date = today_date - __import__("datetime").timedelta(days=1)

    all_buy = signals_df[
        signals_df["signal_type"].astype(str).str.upper() == "BUY"
    ].to_dict("records")

    buy_signals = []
    stale_skipped = 0
    for sig in all_buy:
        sig_date_str = str(sig.get("signal_date", "")).strip()
        try:
            sig_date = date.fromisoformat(sig_date_str)
            if sig_date < yesterday_date:
                logger.warning(
                    f"Skipping stale BUY signal for {sig.get('ticker','?')} "
                    f"(signal_date={sig_date_str}, today={today_date})"
                )
                stale_skipped += 1
                continue
        except (ValueError, TypeError):
            pass  # If date is missing/malformed, allow through
        buy_signals.append(sig)

    if stale_skipped:
        logger.warning(f"Skipped {stale_skipped} stale BUY signal(s) — re-run paper-scan to refresh")

    exit_signals = signals_df[
        signals_df["signal_type"].astype(str).str.upper().isin(["EXIT", "EXIT_PARTIAL"])
    ].to_dict("records")

    # Fetch live data from Alpaca
    account = broker.get_account()
    alpaca_positions = broker.get_positions()
    alpaca_open_orders = broker.get_open_orders()

    # Load local resources
    state = load_state(state_path)
    universe_tickers = load_universe_tickers(universe_path)
    blocked_symbols = load_blocked_symbols(blocked_path)
    catalysts = load_catalysts(catalyst_path)
    orders_today = count_orders_today(log_path)

    logger.info(
        f"paper-execute: {len(buy_signals)} buy signal(s), "
        f"{len(exit_signals)} exit signal(s)  |  "
        f"portfolio=${account['portfolio_value']:,.2f}  "
        f"buying_power=${account['buying_power']:,.2f}  "
        f"open_positions={len(alpaca_positions)}"
    )

    # Execute buys
    buy_submitted, new_buy_count = execute_buy_signals(
        buy_signals=buy_signals,
        broker=broker,
        config=config,
        state=state,
        account=account,
        alpaca_positions=alpaca_positions,
        alpaca_open_orders=alpaca_open_orders,
        universe_tickers=universe_tickers,
        blocked_symbols=blocked_symbols,
        catalysts=catalysts,
        orders_today=orders_today,
    )

    # Execute sells
    sell_submitted = execute_sell_signals(
        exit_signals=exit_signals,
        broker=broker,
        config=config,
        state=state,
        alpaca_positions=alpaca_positions,
        alpaca_open_orders=alpaca_open_orders,
    )

    # Update state
    state["orders_submitted_today"] = orders_today + new_buy_count + len(sell_submitted)
    state["last_execute_date"] = today_str()
    save_state(state, state_path)

    buys_blocked = len(buy_signals) - len(buy_submitted)
    sells_blocked = len(exit_signals) - len(sell_submitted)

    return {
        "buys_submitted": len(buy_submitted),
        "buys_blocked": buys_blocked,
        "sells_submitted": len(sell_submitted),
        "sells_blocked": sells_blocked,
        "errors": 0,
        "total_signals": len(buy_signals) + len(exit_signals),
        "portfolio_value": account["portfolio_value"],
        "buying_power": account["buying_power"],
        "open_positions": len(alpaca_positions),
    }


def run_paper_reconcile(config) -> dict:
    """
    Sync local paper_state.json with Alpaca's actual positions.

    Returns:
        Updated state dict.
    """
    from src.broker_alpaca import AlpacaPaperBroker

    config.require_paper_mode()
    broker = AlpacaPaperBroker.from_config(config)
    state_path = getattr(config, "state_file_path", _DEFAULT_STATE_PATH)
    state = load_state(state_path)
    return reconcile_state(broker, config, state)


def run_emergency_close_all(config) -> dict:
    """
    Cancel all open orders and close every position at market price.

    Returns:
        {
            "orders_cancelled": int,
            "positions_closed":  int,
            "errors":            int,
        }
    """
    from src.broker_alpaca import AlpacaPaperBroker

    config.require_paper_mode()
    broker = AlpacaPaperBroker.from_config(config)
    log_path = getattr(config, "order_log_path", _DEFAULT_ORDER_LOG)
    state_path = getattr(config, "state_file_path", _DEFAULT_STATE_PATH)

    # Cancel all open orders first
    cancelled = broker.cancel_all_orders()
    logger.info(f"Emergency close: cancelled {cancelled} open order(s)")

    # Close all positions
    close_results = broker.close_all_positions()
    errors = sum(1 for r in close_results if r.get("status") == "failed")
    closed = len(close_results) - errors

    # Log each close
    for result in close_results:
        ticker = result.get("ticker", "")
        if result.get("status") == "failed":
            log_order(
                log_path=log_path, ticker=ticker,
                signal_type="EMERGENCY_CLOSE", action="ERROR",
                amount=0, amount_label="qty",
                check_passed=False, fail_reason=result.get("error", ""),
            )
        else:
            log_order(
                log_path=log_path, ticker=ticker,
                signal_type="EMERGENCY_CLOSE", action="SUBMITTED",
                amount=result.get("qty") or 0, amount_label="qty",
                order_id=result.get("id", ""),
                order_status=result.get("status", ""),
                check_passed=True,
            )

    # Clear local state
    state = load_state(state_path)
    state["positions"] = {}
    save_state(state, state_path)

    return {
        "orders_cancelled": cancelled,
        "positions_closed": closed,
        "errors": errors,
    }

"""
src/ranking.py — Signal Ranking and Position Sizing

After the signal engine generates BUY candidates, this module:
  1. Scores each candidate on a composite 0–1 scale
  2. Sorts them best-to-worst
  3. Enforces the max_positions limit from config
  4. Returns the final ranked shortlist

Composite Score Weights:
  35%  Catalyst score       (0–10 scale, normalized to 0–1 within the set)
  25%  Relative strength    (stock return minus SPY return, 20 days)
  20%  Volume ratio         (today's volume / 20-day avg volume)
  20%  20-day return        (raw price momentum)

Normalization:
  All four components are min-max normalized within the current candidate
  set (0 = worst in today's set, 1 = best in today's set).
  This keeps scores comparable regardless of market environment.

Position sizing:
  Target allocation: 18% of portfolio value per position (from config).
  Maximum allocation: 25% per position.
  Hard cap: never exceed max_positions (default 8) total open positions.
"""

from typing import List

from src.utils import setup_logger

logger = setup_logger(__name__)


# ============================================================
# Normalization
# ============================================================

def _normalize(values: List[float]) -> List[float]:
    """
    Min-max normalize a list of floats to the [0, 1] range.

    If all values are identical, returns 0.5 for every item (neutral).
    None values are treated as the minimum (0.0 before scaling).

    Args:
        values: List of numbers (may contain None)

    Returns:
        List of floats in [0, 1], same length as input.
    """
    clean = [v if v is not None else 0.0 for v in values]
    min_v = min(clean)
    max_v = max(clean)
    if max_v == min_v:
        return [0.5] * len(clean)
    return [(v - min_v) / (max_v - min_v) for v in clean]


# ============================================================
# Composite scoring
# ============================================================

def score_candidate(
    catalyst_norm: float,
    rs_norm: float,
    vol_norm: float,
    ret_norm: float,
) -> float:
    """
    Compute the composite rank score for one candidate.

    All inputs must be in [0, 1] (already normalized).

    Weights: 35% catalyst, 25% RS, 20% volume, 20% return.

    Returns:
        Float in [0, 1]. Higher = stronger buy candidate.
    """
    return (
        0.35 * catalyst_norm
        + 0.25 * rs_norm
        + 0.20 * vol_norm
        + 0.20 * ret_norm
    )


def rank_signals(
    buy_signals: List[dict],
    max_positions: int = 8,
    current_position_count: int = 0,
) -> List[dict]:
    """
    Rank buy signals by composite score and apply the position limit.

    Args:
        buy_signals:            Raw buy signal list from signal_engine
        max_positions:          Maximum total open positions (from config)
        current_position_count: How many positions are currently open

    Returns:
        Ranked list, best first, capped to the number of available slots.
        Each signal has its "rank_score" field set (0.0 to 1.0).
        Signals that exceed the position limit are dropped.
    """
    if not buy_signals:
        logger.info("No buy signals to rank.")
        return []

    # Extract raw values for normalization
    catalyst_scores = [float(s.get("catalyst_score") or 0) for s in buy_signals]
    rs_values = [float(s.get("relative_strength_20d") or 0) for s in buy_signals]
    vol_values = [float(s.get("volume_ratio") or 0) for s in buy_signals]
    ret_values = [float(s.get("return_20d") or 0) for s in buy_signals]

    cat_norm = _normalize(catalyst_scores)
    rs_norm = _normalize(rs_values)
    vol_norm = _normalize(vol_values)
    ret_norm = _normalize(ret_values)

    scored = []
    for i, signal in enumerate(buy_signals):
        composite = score_candidate(cat_norm[i], rs_norm[i], vol_norm[i], ret_norm[i])
        sig_copy = dict(signal)
        sig_copy["rank_score"] = round(composite, 4)
        scored.append(sig_copy)

    scored.sort(key=lambda s: s["rank_score"], reverse=True)

    available_slots = max(0, max_positions - current_position_count)
    if available_slots == 0:
        logger.info(
            f"Position limit reached ({current_position_count}/{max_positions}). "
            "No new positions will be opened."
        )
        return []

    final = scored[:available_slots]

    if len(scored) > available_slots:
        logger.info(
            f"Ranking: kept top {len(final)} of {len(scored)} signals "
            f"(dropped {len(scored) - available_slots} due to {max_positions}-position limit)"
        )
    else:
        logger.info(f"Ranking: {len(final)} signal(s) ranked")

    return final


# ============================================================
# Position sizing
# ============================================================

def calculate_position_size(
    portfolio_value: float,
    current_price: float,
    target_pct: float = 0.18,
    max_pct: float = 0.25,
    transaction_cost_bps: float = 5,
    slippage_bps: float = 10,
) -> dict:
    """
    Calculate how many shares to buy for a new position.

    The dollar amount is first capped at max_pct, then reduced by
    assumed transaction costs + slippage before converting to shares.

    Args:
        portfolio_value:      Total portfolio value in dollars
        current_price:        Current stock price
        target_pct:           Target allocation (0.18 = 18% of portfolio)
        max_pct:              Maximum allocation per position (0.25 = 25%)
        transaction_cost_bps: Trading fee assumption in basis points (5 = 0.05%)
        slippage_bps:         Slippage assumption in basis points (10 = 0.10%)

    Returns:
        Dict with:
            target_dollars:   Dollar amount before cost deductions
            adjusted_dollars: Dollar amount after costs
            shares:           Whole shares to buy (floor division)
            actual_pct:       Actual portfolio fraction used
    """
    if portfolio_value <= 0 or current_price <= 0:
        return {"target_dollars": 0, "adjusted_dollars": 0, "shares": 0, "actual_pct": 0}

    target_dollars = min(portfolio_value * target_pct, portfolio_value * max_pct)

    total_cost_fraction = (transaction_cost_bps + slippage_bps) / 10_000
    adjusted_dollars = target_dollars * (1.0 - total_cost_fraction)

    shares = int(adjusted_dollars / current_price)
    actual_pct = (shares * current_price) / portfolio_value if portfolio_value > 0 else 0.0

    return {
        "target_dollars": round(target_dollars, 2),
        "adjusted_dollars": round(adjusted_dollars, 2),
        "shares": shares,
        "actual_pct": round(actual_pct, 4),
    }

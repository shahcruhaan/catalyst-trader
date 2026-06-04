"""
src/options_overlay.py — Educational Options Recommendations

IMPORTANT: This module generates RECOMMENDATIONS ONLY.
No options orders are ever placed. Options trading is disabled
in config.yaml (allow_options_trading: false).

These recommendations are purely educational:
  "If you were to use options alongside this signal, here is a
   reasonable bull call spread structure."

Strategy: Bull Call Spread
--------------------------
  1. BUY a call slightly IN-THE-MONEY (ITM)
       Strike = current_price * (1 - long_call_itm_pct)
       Default: 7% ITM — strike is 7% below current price.
       You pay a premium but own real upside exposure.

  2. SELL a call OUT-OF-THE-MONEY (OTM)
       Strike = current_price * (1 + short_call_otm_pct)
       Default: 20% OTM — strike is 20% above current price.
       This caps your max gain but reduces the net cost.

Target expiration: ~75 days out (DTE = days to expiration).
The longer expiration gives the catalyst time to play out.

Why recommend this strategy?
  - Limited risk: maximum loss = net premium paid
  - Defined profit target: maximum gain if price reaches short strike
  - Lower cost than buying a naked call
  - Teaches real options concepts in a paper trading context

CSV output (reports/options_overlay_recommendations.csv):
  scan_date, ticker, strategy, current_price,
  long_strike, short_strike, spread_width,
  target_dte, estimated_expiry, target_gain_pct,
  catalyst_score, catalyst_type, catalyst_headline, note
"""

from datetime import date, timedelta
from typing import List, Optional

import pandas as pd

from src.utils import setup_logger, today_str, ensure_dir

logger = setup_logger(__name__)

DISCLAIMER = "RECOMMENDATION ONLY — NOT A REAL ORDER. For educational purposes."


# ============================================================
# Helpers
# ============================================================

def _round_to_strike(price: float, increment: float = 1.0) -> float:
    """
    Round a raw strike price to the nearest standard increment.

    Standard equity option strikes are usually $1 apart for stocks
    under ~$100, and $5 apart for higher-priced stocks.
    We use $1 increments as a conservative default.

    Args:
        price:     Raw calculated strike price
        increment: Rounding increment (default $1.0)

    Returns:
        Rounded strike price, minimum $1.
    """
    if price <= 0:
        return 1.0
    rounded = round(price / increment) * increment
    return max(1.0, rounded)


def _estimate_expiry_date(target_dte: int = 75) -> str:
    """
    Estimate an options expiration date approximately target_dte days from today.

    Options expire on specific Fridays. This function finds the nearest
    Friday on or after (today + target_dte days).

    Args:
        target_dte: Target days to expiration (default 75)

    Returns:
        Date string YYYY-MM-DD.
    """
    target_date = date.today() + timedelta(days=target_dte)
    # weekday(): Monday=0 … Friday=4
    days_to_friday = (4 - target_date.weekday()) % 7
    expiry = target_date + timedelta(days=days_to_friday)
    return expiry.strftime("%Y-%m-%d")


# ============================================================
# Single recommendation builder
# ============================================================

def generate_call_spread_recommendation(
    ticker: str,
    current_price: float,
    catalyst: dict,
    long_call_itm_pct: float = 0.07,
    short_call_otm_pct: float = 0.20,
    target_dte: int = 75,
) -> Optional[dict]:
    """
    Build a bull call spread recommendation for one ticker.

    This is a RECOMMENDATION ONLY — no order is ever placed.

    Args:
        ticker:             Stock symbol
        current_price:      Latest stock price
        catalyst:           Catalyst dict from catalyst_scorer
        long_call_itm_pct:  Long call is this far ITM (default 7%)
        short_call_otm_pct: Short call is this far OTM (default 20%)
        target_dte:         Target days to expiration (default 75)

    Returns:
        Recommendation dict, or None if inputs are invalid.
    """
    if current_price <= 0 or not ticker:
        return None

    long_strike = _round_to_strike(current_price * (1.0 - long_call_itm_pct))
    short_strike = _round_to_strike(current_price * (1.0 + short_call_otm_pct))
    spread_width = short_strike - long_strike
    expiry_date = _estimate_expiry_date(target_dte)
    max_gain_pct = ((short_strike - current_price) / current_price) * 100

    return {
        "scan_date": today_str(),
        "ticker": ticker,
        "strategy": "Bull Call Spread",
        "current_price": round(current_price, 2),
        "long_strike": long_strike,
        "short_strike": short_strike,
        "spread_width": round(spread_width, 2),
        "target_dte": target_dte,
        "estimated_expiry": expiry_date,
        "target_gain_pct": f"+{max_gain_pct:.1f}% if stock reaches short strike",
        "catalyst_score": catalyst.get("catalyst_score", ""),
        "catalyst_type": catalyst.get("catalyst_type", ""),
        "catalyst_headline": catalyst.get("headline", ""),
        "note": DISCLAIMER,
    }


# ============================================================
# Batch recommendation generator
# ============================================================

def generate_options_recommendations(
    ranked_signals: List[dict],
    max_recommendations: int = 3,
    long_call_itm_pct: float = 0.07,
    short_call_otm_pct: float = 0.20,
    target_dte: int = 75,
) -> List[dict]:
    """
    Generate call spread recommendations for the top-ranked buy signals.

    Only the top N signals receive recommendations.
    All are clearly marked as educational recommendations, never real orders.

    Args:
        ranked_signals:      Ranked buy signal list from ranking.py
        max_recommendations: Max number of recommendations to produce (default 3)
        long_call_itm_pct:   ITM percentage for long call (default 7%)
        short_call_otm_pct:  OTM percentage for short call (default 20%)
        target_dte:          Target DTE (default 75 days)

    Returns:
        List of recommendation dicts (at most max_recommendations).
    """
    recommendations = []

    for signal in ranked_signals[:max_recommendations]:
        ticker = signal.get("ticker", "")
        current_price = signal.get("close")
        catalyst = signal.get("_catalyst") or {}

        if not ticker or current_price is None:
            continue

        try:
            current_price = float(current_price)
        except (TypeError, ValueError):
            continue

        rec = generate_call_spread_recommendation(
            ticker=ticker,
            current_price=current_price,
            catalyst=catalyst,
            long_call_itm_pct=long_call_itm_pct,
            short_call_otm_pct=short_call_otm_pct,
            target_dte=target_dte,
        )

        if rec:
            recommendations.append(rec)
            logger.info(
                f"Options rec: {ticker}  buy ${rec['long_strike']} call / "
                f"sell ${rec['short_strike']} call  expiry ~{rec['estimated_expiry']}"
            )

    return recommendations


# ============================================================
# Report writer
# ============================================================

def save_options_recommendations(
    recommendations: List[dict],
    path: str = "reports/options_overlay_recommendations.csv",
) -> None:
    """
    Save options recommendations to CSV.

    The CSV always includes a "note" column with the disclaimer
    so it can never be mistaken for a real order file.
    """
    ensure_dir("reports")

    columns = [
        "scan_date", "ticker", "strategy", "current_price",
        "long_strike", "short_strike", "spread_width",
        "target_dte", "estimated_expiry", "target_gain_pct",
        "catalyst_score", "catalyst_type", "catalyst_headline", "note",
    ]

    if not recommendations:
        pd.DataFrame(columns=columns).to_csv(path, index=False)
        logger.info(f"No options recommendations today. Empty file → {path}")
        return

    rows = [{col: rec.get(col, "") for col in columns} for rec in recommendations]
    pd.DataFrame(rows).to_csv(path, index=False)
    logger.info(f"Saved {len(rows)} options recommendations → {path}")

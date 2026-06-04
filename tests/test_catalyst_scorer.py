"""
tests/test_catalyst_scorer.py — Tests for src/catalyst_scorer.py

Covers:
  - Loading catalysts from CSV (valid, missing, empty, malformed)
  - Freshness checking (today, boundary, stale, future, invalid)
  - Best catalyst lookup (score, freshness, min_score filter)
  - Tradeable catalyst lookup (tradeable flag, min_score, freshness)
  - Catalyst summarizer
  - Count summary helper
"""

import csv
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.catalyst_scorer import (
    get_best_catalyst,
    get_catalyst_count_summary,
    get_tradeable_catalyst,
    is_catalyst_fresh,
    load_catalysts,
    summarize_catalyst,
)


# ============================================================
# Helpers
# ============================================================

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def _days_ahead(n: int) -> str:
    return (datetime.now() + timedelta(days=n)).strftime("%Y-%m-%d")


def _write_csv(rows: list, tmp_path) -> str:
    """Write catalyst rows to a temp CSV and return the path."""
    path = os.path.join(str(tmp_path), "test_catalysts.csv")
    columns = [
        "date", "ticker", "catalyst_type", "headline", "summary",
        "catalyst_score", "tradeable", "confidence",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _row(ticker="NVDA", score=9, tradeable="true", date=None, cat_type="earnings"):
    return {
        "date": date or _today(),
        "ticker": ticker,
        "catalyst_type": cat_type,
        "headline": f"{ticker} headline",
        "summary": "Strong results",
        "catalyst_score": str(score),
        "tradeable": tradeable,
        "confidence": "high",
    }


# ============================================================
# TestLoadCatalysts
# ============================================================

class TestLoadCatalysts:

    def test_missing_file_returns_empty_dict(self):
        result = load_catalysts("data/__nonexistent_file__.csv")
        assert result == {}

    def test_loads_valid_single_row(self, tmp_path):
        path = _write_csv([_row()], tmp_path)
        result = load_catalysts(path)
        assert "NVDA" in result
        assert result["NVDA"][0]["catalyst_score"] == 9
        assert result["NVDA"][0]["tradeable"] is True

    def test_ticker_normalized_to_uppercase(self, tmp_path):
        path = _write_csv([_row(ticker="nvda")], tmp_path)
        result = load_catalysts(path)
        assert "NVDA" in result
        assert "nvda" not in result

    def test_multiple_different_tickers(self, tmp_path):
        rows = [_row("AAPL", 8), _row("META", 7, tradeable="false")]
        path = _write_csv(rows, tmp_path)
        result = load_catalysts(path)
        assert "AAPL" in result
        assert "META" in result
        assert result["META"][0]["tradeable"] is False

    def test_multiple_rows_same_ticker(self, tmp_path):
        rows = [_row("NVDA", 9), _row("NVDA", 7, date=_days_ago(3))]
        path = _write_csv(rows, tmp_path)
        result = load_catalysts(path)
        assert len(result["NVDA"]) == 2

    def test_invalid_score_defaults_to_zero(self, tmp_path):
        row = _row()
        row["catalyst_score"] = "not_a_number"
        path = _write_csv([row], tmp_path)
        result = load_catalysts(path)
        assert result["NVDA"][0]["catalyst_score"] == 0

    def test_empty_file_returns_empty_dict(self, tmp_path):
        path = os.path.join(str(tmp_path), "empty.csv")
        with open(path, "w") as f:
            f.write("")
        result = load_catalysts(path)
        assert result == {}

    def test_tradeable_various_true_values(self, tmp_path):
        for val in ["true", "True", "TRUE", "1", "yes"]:
            row = _row(tradeable=val)
            path = _write_csv([row], tmp_path)
            result = load_catalysts(path)
            assert result["NVDA"][0]["tradeable"] is True, f"Failed for tradeable={val!r}"

    def test_tradeable_various_false_values(self, tmp_path):
        for val in ["false", "False", "0", "no", ""]:
            row = _row(tradeable=val)
            path = _write_csv([row], tmp_path)
            result = load_catalysts(path)
            assert result["NVDA"][0]["tradeable"] is False, f"Failed for tradeable={val!r}"

    def test_float_score_parsed_as_int(self, tmp_path):
        row = _row()
        row["catalyst_score"] = "8.9"
        path = _write_csv([row], tmp_path)
        result = load_catalysts(path)
        assert result["NVDA"][0]["catalyst_score"] == 8


# ============================================================
# TestIsCatalystFresh
# ============================================================

class TestIsCatalystFresh:

    def test_today_is_fresh(self):
        assert is_catalyst_fresh(_today()) is True

    def test_one_day_ago_is_fresh(self):
        assert is_catalyst_fresh(_days_ago(1)) is True

    def test_max_age_boundary_is_fresh(self):
        # 10 days ago is exactly at the boundary (default max = 10)
        assert is_catalyst_fresh(_days_ago(10)) is True

    def test_one_past_max_age_is_stale(self):
        assert is_catalyst_fresh(_days_ago(11)) is False

    def test_old_date_is_stale(self):
        assert is_catalyst_fresh(_days_ago(30)) is False

    def test_future_date_is_stale(self):
        # age_days < 0 for future dates
        assert is_catalyst_fresh(_days_ahead(1)) is False

    def test_empty_string_returns_false(self):
        assert is_catalyst_fresh("") is False

    def test_none_returns_false(self):
        assert is_catalyst_fresh(None) is False

    def test_invalid_string_returns_false(self):
        assert is_catalyst_fresh("not-a-date") is False

    def test_nan_string_returns_false(self):
        assert is_catalyst_fresh("nan") is False

    def test_custom_max_days_respected(self):
        assert is_catalyst_fresh(_days_ago(3), max_days_old=3) is True
        assert is_catalyst_fresh(_days_ago(4), max_days_old=3) is False


# ============================================================
# TestGetBestCatalyst
# ============================================================

class TestGetBestCatalyst:

    def test_no_catalysts_returns_none(self):
        assert get_best_catalyst("NVDA", {}) is None

    def test_unknown_ticker_returns_none(self):
        cats = {"AAPL": [{"date": _today(), "catalyst_score": 9, "tradeable": True}]}
        assert get_best_catalyst("NVDA", cats) is None

    def test_returns_highest_score_among_fresh(self):
        cats = {
            "NVDA": [
                {"date": _today(), "catalyst_score": 7, "tradeable": True, "headline": "Low"},
                {"date": _today(), "catalyst_score": 9, "tradeable": True, "headline": "High"},
            ]
        }
        result = get_best_catalyst("NVDA", cats)
        assert result["catalyst_score"] == 9

    def test_stale_catalyst_excluded(self):
        cats = {"NVDA": [{"date": _days_ago(15), "catalyst_score": 9, "tradeable": True}]}
        assert get_best_catalyst("NVDA", cats) is None

    def test_fresh_catalyst_within_window_returned(self):
        cats = {"NVDA": [{"date": _days_ago(5), "catalyst_score": 8, "tradeable": True}]}
        result = get_best_catalyst("NVDA", cats)
        assert result is not None
        assert result["catalyst_score"] == 8

    def test_min_score_filter_applied(self):
        cats = {"NVDA": [{"date": _today(), "catalyst_score": 6, "tradeable": True}]}
        assert get_best_catalyst("NVDA", cats, min_score=7) is None

    def test_min_score_boundary_passes(self):
        cats = {"NVDA": [{"date": _today(), "catalyst_score": 7, "tradeable": True}]}
        result = get_best_catalyst("NVDA", cats, min_score=7)
        assert result is not None

    def test_all_stale_returns_none(self):
        cats = {
            "NVDA": [
                {"date": _days_ago(15), "catalyst_score": 10, "tradeable": True},
                {"date": _days_ago(20), "catalyst_score": 9, "tradeable": True},
            ]
        }
        assert get_best_catalyst("NVDA", cats) is None


# ============================================================
# TestGetTradeableCatalyst
# ============================================================

class TestGetTradeableCatalyst:

    def test_untradeable_catalyst_excluded(self):
        cats = {"NVDA": [{"date": _today(), "catalyst_score": 9, "tradeable": False}]}
        assert get_tradeable_catalyst("NVDA", cats) is None

    def test_tradeable_catalyst_returned(self):
        cats = {"NVDA": [{"date": _today(), "catalyst_score": 9, "tradeable": True}]}
        result = get_tradeable_catalyst("NVDA", cats)
        assert result is not None
        assert result["tradeable"] is True

    def test_score_below_min_excluded(self):
        cats = {"NVDA": [{"date": _today(), "catalyst_score": 7, "tradeable": True}]}
        assert get_tradeable_catalyst("NVDA", cats, min_score=8) is None

    def test_stale_tradeable_excluded(self):
        cats = {"NVDA": [{"date": _days_ago(15), "catalyst_score": 10, "tradeable": True}]}
        assert get_tradeable_catalyst("NVDA", cats, min_score=8) is None

    def test_picks_best_of_multiple_qualifying(self):
        cats = {
            "NVDA": [
                {"date": _today(), "catalyst_score": 8, "tradeable": True, "headline": "A"},
                {"date": _today(), "catalyst_score": 10, "tradeable": True, "headline": "B"},
            ]
        }
        result = get_tradeable_catalyst("NVDA", cats, min_score=8)
        assert result["catalyst_score"] == 10

    def test_score_exactly_at_min_passes(self):
        cats = {"NVDA": [{"date": _today(), "catalyst_score": 8, "tradeable": True}]}
        result = get_tradeable_catalyst("NVDA", cats, min_score=8)
        assert result is not None


# ============================================================
# TestSummarizeCatalyst
# ============================================================

class TestSummarizeCatalyst:

    def test_none_returns_no_catalyst(self):
        assert summarize_catalyst(None) == "No catalyst"

    def test_includes_type_score_and_headline(self):
        cat = {
            "catalyst_type": "earnings",
            "catalyst_score": 9,
            "headline": "Beat and raise",
            "tradeable": True,
        }
        result = summarize_catalyst(cat)
        assert "earnings" in result
        assert "9" in result
        assert "Beat and raise" in result

    def test_not_tradeable_flag_present(self):
        cat = {
            "catalyst_type": "analyst",
            "catalyst_score": 7,
            "headline": "Upgrade",
            "tradeable": False,
        }
        assert "NOT TRADEABLE" in summarize_catalyst(cat)

    def test_tradeable_has_no_flag(self):
        cat = {
            "catalyst_type": "earnings",
            "catalyst_score": 9,
            "headline": "Strong",
            "tradeable": True,
        }
        assert "NOT TRADEABLE" not in summarize_catalyst(cat)


# ============================================================
# TestGetCatalystCountSummary
# ============================================================

class TestGetCatalystCountSummary:

    def test_empty_input(self):
        result = get_catalyst_count_summary({})
        assert result["total_tickers"] == 0
        assert result["fresh_tickers"] == 0
        assert result["tradeable_tickers"] == 0
        assert result["tradeable_ticker_list"] == []

    def test_counts_fresh_and_tradeable(self):
        cats = {
            "NVDA": [{"date": _today(), "catalyst_score": 9, "tradeable": True}],
            "META": [{"date": _days_ago(5), "catalyst_score": 8, "tradeable": True}],
            "OLD": [{"date": _days_ago(20), "catalyst_score": 9, "tradeable": True}],
        }
        result = get_catalyst_count_summary(cats)
        assert result["total_tickers"] == 3
        assert result["fresh_tickers"] == 2    # NVDA and META are fresh
        assert result["tradeable_tickers"] == 2
        assert "OLD" not in result["tradeable_ticker_list"]

    def test_low_score_not_counted_as_tradeable(self):
        cats = {"LOW": [{"date": _today(), "catalyst_score": 6, "tradeable": True}]}
        result = get_catalyst_count_summary(cats)
        assert result["fresh_tickers"] == 1    # is fresh
        assert result["tradeable_tickers"] == 0  # score < 8

    def test_untradeable_not_counted(self):
        cats = {"NT": [{"date": _today(), "catalyst_score": 9, "tradeable": False}]}
        result = get_catalyst_count_summary(cats)
        assert result["fresh_tickers"] == 1
        assert result["tradeable_tickers"] == 0

    def test_tradeable_ticker_list_is_sorted(self):
        cats = {
            "TSLA": [{"date": _today(), "catalyst_score": 9, "tradeable": True}],
            "AAPL": [{"date": _today(), "catalyst_score": 9, "tradeable": True}],
        }
        result = get_catalyst_count_summary(cats)
        assert result["tradeable_ticker_list"] == sorted(result["tradeable_ticker_list"])

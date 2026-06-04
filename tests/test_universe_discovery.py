"""
tests/test_universe_discovery.py — Tests for the universe discovery module

These tests focus on the filtering and logic layers.
Most tests use only synthetic data — no internet connection required.

Tests marked with @pytest.mark.integration make real network calls
and are skipped by default. Run them manually with:
    pytest tests/test_universe_discovery.py -v -m integration

Normal test run (offline, fast):
    pytest tests/test_universe_discovery.py -v
"""

import os
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import MagicMock

# Import the modules we are testing
from src.universe_discovery import (
    UniverseDiscovery,
    run_discovery,
    load_todays_universe,
    is_todays_universe_fresh,
    SEED_UNIVERSE,
)
from src.config import AppConfig, reset_config


# ============================================================
# Helpers
# ============================================================

def make_mock_config(
    min_price=5.0,
    min_market_cap=1_000_000_000,
    min_avg_dollar_volume=50_000_000,
    min_avg_share_volume=1_000_000,
    min_volume_ratio=2.5,
    min_20d_return=0.10,
    min_relative_strength_20d=0.08,
    min_catalyst_score=8,
    blocked_path="data/blocked_symbols.csv",
    discovered_path="reports/daily_discovered_universe.csv",
    alpaca_api_key="test_key",
    alpaca_secret_key="test_secret",
    alpaca_paper=True,
):
    """Create a minimal mock AppConfig object for testing."""
    cfg = MagicMock(spec=AppConfig)
    cfg.min_price = min_price
    cfg.min_market_cap = min_market_cap
    cfg.min_avg_dollar_volume = min_avg_dollar_volume
    cfg.min_avg_share_volume = min_avg_share_volume
    cfg.min_volume_ratio = min_volume_ratio
    cfg.min_20d_return = min_20d_return
    cfg.min_relative_strength_20d = min_relative_strength_20d
    cfg.min_catalyst_score = min_catalyst_score
    cfg.blocked_symbols_path = blocked_path
    cfg.discovered_universe_path = discovered_path
    cfg.alpaca_api_key = alpaca_api_key
    cfg.alpaca_secret_key = alpaca_secret_key
    cfg.alpaca_paper = alpaca_paper
    cfg.discovery_alpaca_volume = True
    cfg.discovery_alpaca_trades = True
    cfg.discovery_top_gainers = True
    cfg.discovery_yfinance_volume = True
    cfg.discovery_yfinance_breakout = True
    return cfg


def make_good_row(
    ticker="NVDA",
    price=150.0,
    avg_dollar_volume=1_000_000_000,
    avg_share_volume=10_000_000,
    market_cap=None,
):
    """Create a passing candidate row dict."""
    return {
        "date": "2026-06-02",
        "ticker": ticker,
        "source": "test",
        "reason_added": "test",
        "price": price,
        "volume": 20_000_000,
        "avg_volume_20d": avg_share_volume,
        "volume_ratio": 2.0,
        "avg_dollar_volume_20d": avg_dollar_volume,
        "market_cap": market_cap,
        "return_20d": 0.15,
        "relative_strength_vs_spy": 0.12,
        "above_20d_high": True,
        "above_50d_ma": True,
        "passed_filters": False,
        "rejection_reason": "",
    }


# ============================================================
# Tests: Symbol-level filters (_filter_symbols_fast)
# ============================================================

class TestSymbolFilters:
    """
    Tests for the fast symbol-level filter.
    These run before any data is downloaded.
    """

    def setup_method(self):
        """Create a fresh UniverseDiscovery for each test."""
        self.cfg = make_mock_config()
        self.discovery = UniverseDiscovery(self.cfg)
        # Override blocked symbols with a known test set
        self.discovery.blocked_symbols = {"SQQQ", "TQQQ", "UVXY", "BLOCKED"}

    def test_valid_ticker_passes(self):
        """A normal 4-letter ticker should pass symbol filters."""
        passed, rejected = self.discovery._filter_symbols_fast(["NVDA"])
        assert "NVDA" in passed
        assert "NVDA" not in rejected

    def test_blocked_symbol_is_rejected(self):
        """A symbol on the blocked list must be rejected."""
        passed, rejected = self.discovery._filter_symbols_fast(["SQQQ"])
        assert "SQQQ" not in passed
        assert "SQQQ" in rejected
        assert "blocked" in rejected["SQQQ"].lower()

    def test_warrant_rejected_five_char_ending_w(self):
        """A 5-character ticker ending in W is a likely warrant — reject it."""
        # SPNVW is a real warrant ticker format
        passed, rejected = self.discovery._filter_symbols_fast(["SPNVW"])
        assert "SPNVW" not in passed
        assert "SPNVW" in rejected
        assert "warrant" in rejected["SPNVW"].lower()

    def test_unit_rejected_five_char_ending_u(self):
        """A 5-character ticker ending in U is a likely unit — reject it."""
        passed, rejected = self.discovery._filter_symbols_fast(["PSACU"])
        assert "PSACU" not in passed
        assert "PSACU" in rejected
        assert "unit" in rejected["PSACU"].lower()

    def test_rights_rejected_five_char_ending_r(self):
        """A 5-character ticker ending in R is a likely rights offering — reject it."""
        passed, rejected = self.discovery._filter_symbols_fast(["XYZRR"])
        assert "XYZRR" not in passed
        assert "XYZRR" in rejected
        assert "rights" in rejected["XYZRR"].lower()

    def test_four_char_ending_w_is_not_rejected(self):
        """
        A 4-character ticker ending in W should NOT be rejected as a warrant.
        (Only 5-char tickers ending in W are treated as warrants.)
        e.g., 'CGNW' is a hypothetical valid stock.
        """
        passed, rejected = self.discovery._filter_symbols_fast(["CGNW"])
        assert "CGNW" in passed

    def test_empty_string_is_rejected(self):
        """An empty string should not pass through."""
        passed, rejected = self.discovery._filter_symbols_fast([""])
        # Empty string is invalid, should end up rejected (or just not in passed)
        assert "" not in passed

    def test_ticker_with_numbers_is_rejected(self):
        """A ticker containing numbers is not a valid US common stock format."""
        passed, rejected = self.discovery._filter_symbols_fast(["AB12C"])
        assert "AB12C" not in passed

    def test_ticker_too_long_is_rejected(self):
        """A 6+ character ticker is not a valid US common stock format."""
        passed, rejected = self.discovery._filter_symbols_fast(["TOOLONGNAME"])
        assert "TOOLONGNAME" not in passed

    def test_lowercase_ticker_is_normalized_and_passes(self):
        """Lowercase tickers should be normalized to uppercase and still pass."""
        passed, rejected = self.discovery._filter_symbols_fast(["aapl"])
        assert "AAPL" in passed

    def test_multiple_tickers_mixed_results(self):
        """
        Test a batch with a mix of valid and invalid tickers.
        """
        tickers = ["NVDA", "SQQQ", "SPNVW", "AAPL", "PSACU"]
        passed, rejected = self.discovery._filter_symbols_fast(tickers)
        assert "NVDA" in passed
        assert "AAPL" in passed
        assert "SQQQ" in rejected
        assert "SPNVW" in rejected
        assert "PSACU" in rejected
        assert len(passed) == 2
        assert len(rejected) == 3


# ============================================================
# Tests: Quality filters (_check_quality_filters)
# ============================================================

class TestQualityFilters:
    """
    Tests for the data-based quality filter.
    These check price, volume, and market cap thresholds.
    """

    def setup_method(self):
        self.cfg = make_mock_config()
        self.discovery = UniverseDiscovery(self.cfg)

    def test_good_stock_passes_all_filters(self):
        """A stock that meets all minimums should pass."""
        row = make_good_row()
        passed, reason = self.discovery._check_quality_filters(row)
        assert passed is True
        assert reason is None

    def test_missing_price_data_fails(self):
        """If there is no price, the stock must be rejected."""
        row = make_good_row()
        row["price"] = None
        passed, reason = self.discovery._check_quality_filters(row)
        assert passed is False
        assert reason is not None
        assert "price" in reason.lower() or "no" in reason.lower()

    def test_price_below_minimum_fails(self):
        """A stock priced below $5 must be rejected."""
        row = make_good_row(price=3.50)
        passed, reason = self.discovery._check_quality_filters(row)
        assert passed is False
        assert "price" in reason.lower() or "$3.50" in reason

    def test_price_exactly_at_minimum_passes(self):
        """A stock exactly at $5 should pass the price filter."""
        row = make_good_row(price=5.00)
        passed, reason = self.discovery._check_quality_filters(row)
        assert passed is True

    def test_price_just_above_minimum_passes(self):
        """A stock at $5.01 should pass."""
        row = make_good_row(price=5.01)
        passed, reason = self.discovery._check_quality_filters(row)
        assert passed is True

    def test_market_cap_below_minimum_fails(self):
        """A stock with known market cap below $1B should be rejected."""
        row = make_good_row(market_cap=500_000_000)  # $500M
        passed, reason = self.discovery._check_quality_filters(row)
        assert passed is False
        assert "market cap" in reason.lower() or "cap" in reason.lower()

    def test_market_cap_none_does_not_block(self):
        """
        If market cap is unknown (None), we should NOT reject the stock.
        We only filter on market cap when we have a reliable reading.
        """
        row = make_good_row(market_cap=None)
        passed, reason = self.discovery._check_quality_filters(row)
        assert passed is True  # Unknown market cap = no rejection

    def test_market_cap_above_minimum_passes(self):
        """A $5B market cap should comfortably pass."""
        row = make_good_row(market_cap=5_000_000_000)
        passed, reason = self.discovery._check_quality_filters(row)
        assert passed is True

    def test_avg_dollar_volume_too_low_fails(self):
        """Average dollar volume below $50M should be rejected."""
        row = make_good_row(avg_dollar_volume=10_000_000)  # $10M — too low
        passed, reason = self.discovery._check_quality_filters(row)
        assert passed is False
        assert "dollar volume" in reason.lower() or "volume" in reason.lower()

    def test_avg_dollar_volume_none_fails(self):
        """Missing dollar volume (not enough history) should reject."""
        row = make_good_row()
        row["avg_dollar_volume_20d"] = None
        passed, reason = self.discovery._check_quality_filters(row)
        assert passed is False

    def test_avg_share_volume_too_low_fails(self):
        """Average share volume below 1M should be rejected."""
        row = make_good_row(avg_share_volume=200_000)  # 200K — too low
        passed, reason = self.discovery._check_quality_filters(row)
        assert passed is False

    def test_avg_share_volume_none_fails(self):
        """Missing share volume data should reject the stock."""
        row = make_good_row()
        row["avg_volume_20d"] = None
        passed, reason = self.discovery._check_quality_filters(row)
        assert passed is False


# ============================================================
# Tests: Blocked symbols
# ============================================================

class TestBlockedSymbols:
    """Tests for the blocked symbols list."""

    def test_blocked_symbol_in_set(self):
        """Symbols in blocked_symbols.csv should be in the blocked set."""
        from src.utils import load_blocked_symbols
        blocked = load_blocked_symbols("data/blocked_symbols.csv")
        # These are in the default blocked_symbols.csv from Stage 1
        assert "SQQQ" in blocked
        assert "TQQQ" in blocked
        assert "UVXY" in blocked

    def test_valid_stock_not_in_blocked_set(self):
        """Major stocks like NVDA should NOT be in the blocked set."""
        from src.utils import load_blocked_symbols
        blocked = load_blocked_symbols("data/blocked_symbols.csv")
        assert "NVDA" not in blocked
        assert "AAPL" not in blocked
        assert "MSFT" not in blocked

    def test_blocked_symbols_path_missing_returns_empty_set(self):
        """If blocked_symbols.csv doesn't exist, return an empty set — don't crash."""
        from src.utils import load_blocked_symbols
        blocked = load_blocked_symbols("data/nonexistent_file.csv")
        assert isinstance(blocked, set)
        assert len(blocked) == 0

    def test_discovery_excludes_blocked_symbols(self):
        """
        When running discovery, any ticker in the blocked list should be
        excluded even before downloading data.
        """
        cfg = make_mock_config()
        discovery = UniverseDiscovery(cfg)
        # Override blocked symbols
        discovery.blocked_symbols = {"NVDA", "AAPL"}

        passed, rejected = discovery._filter_symbols_fast(["NVDA", "AAPL", "MSFT"])
        assert "NVDA" not in passed
        assert "AAPL" not in passed
        assert "MSFT" in passed
        assert len(passed) == 1


# ============================================================
# Tests: Seed universe structure
# ============================================================

class TestSeedUniverse:
    """Tests for the SEED_UNIVERSE constant."""

    def test_seed_universe_is_not_empty(self):
        """SEED_UNIVERSE must have stocks in it."""
        assert len(SEED_UNIVERSE) > 0

    def test_seed_universe_contains_major_stocks(self):
        """Core large-cap stocks should be in the seed universe."""
        for expected in ["NVDA", "AAPL", "MSFT", "GOOGL", "META"]:
            assert expected in SEED_UNIVERSE, f"{expected} should be in SEED_UNIVERSE"

    def test_seed_universe_has_no_duplicates(self):
        """The seed universe should not contain duplicate tickers."""
        assert len(SEED_UNIVERSE) == len(set(SEED_UNIVERSE)), "Duplicates found in SEED_UNIVERSE"

    def test_seed_universe_tickers_are_uppercase(self):
        """All tickers should be uppercase strings."""
        for ticker in SEED_UNIVERSE:
            assert ticker == ticker.upper(), f"Ticker not uppercase: {ticker}"
            assert ticker.isalpha(), f"Ticker contains non-alpha chars: {ticker}"

    def test_seed_universe_does_not_contain_blocked_defaults(self):
        """Default blocked symbols should not be in the seed universe."""
        blocked_defaults = {"SQQQ", "TQQQ", "UVXY", "SVXY", "VIXY"}
        for sym in blocked_defaults:
            assert sym not in SEED_UNIVERSE, f"Blocked symbol {sym} is in SEED_UNIVERSE"


# ============================================================
# Tests: CSV utility functions
# ============================================================

class TestCandidateMerging:
    """Tests for _add_candidates merging logic."""

    def setup_method(self):
        self.cfg = make_mock_config()
        self.discovery = UniverseDiscovery(self.cfg)
        self.discovery.blocked_symbols = set()  # No blocked symbols for these tests

    def test_new_ticker_is_added(self):
        """A ticker not yet in candidates should be added."""
        items = [{"ticker": "NVDA", "source": "test_source", "reason_added": "test reason"}]
        new_count = self.discovery._add_candidates(items)
        assert new_count == 1
        assert "NVDA" in self.discovery._candidates

    def test_duplicate_ticker_merges_sources(self):
        """
        If the same ticker appears from two different sources,
        both sources should be recorded — not duplicated.
        """
        items1 = [{"ticker": "NVDA", "source": "alpaca_volume", "reason_added": "Most active"}]
        items2 = [{"ticker": "NVDA", "source": "yfinance_seed", "reason_added": "Volume spike"}]

        self.discovery._add_candidates(items1)
        new_count = self.discovery._add_candidates(items2)

        # NVDA was already there — no new tickers added
        assert new_count == 0
        # But sources should be merged
        candidate = self.discovery._candidates["NVDA"]
        assert "alpaca_volume" in candidate["sources"]
        assert "yfinance_seed" in candidate["sources"]

    def test_ticker_normalized_to_uppercase(self):
        """Lowercase tickers in items should be normalized to uppercase."""
        items = [{"ticker": "nvda", "source": "test", "reason_added": "test"}]
        self.discovery._add_candidates(items)
        assert "NVDA" in self.discovery._candidates
        assert "nvda" not in self.discovery._candidates

    def test_empty_ticker_is_skipped(self):
        """Items with missing or empty tickers should be silently skipped."""
        items = [
            {"ticker": "", "source": "test", "reason_added": "test"},
            {"ticker": None, "source": "test", "reason_added": "test"},
        ]
        new_count = self.discovery._add_candidates(items)
        assert new_count == 0


# ============================================================
# Tests: Fallback universe loading
# ============================================================

class TestFallbackUniverse:
    """Tests for loading the fallback universe_sample.csv."""

    def setup_method(self):
        self.cfg = make_mock_config()
        self.discovery = UniverseDiscovery(self.cfg)
        self.discovery.blocked_symbols = set()

    def test_fallback_universe_loads_from_csv(self):
        """
        data/universe_sample.csv should exist (created in Stage 1).
        Loading the fallback universe should populate candidates.
        """
        self.discovery._load_fallback_universe()
        # Should have loaded some tickers
        assert len(self.discovery._candidates) > 0

    def test_fallback_universe_tickers_are_in_candidates(self):
        """After loading, known tickers from universe_sample.csv should be present."""
        self.discovery._load_fallback_universe()
        # These are in the default universe_sample.csv
        assert "NVDA" in self.discovery._candidates
        assert "AAPL" in self.discovery._candidates

    def test_fallback_source_is_labeled_correctly(self):
        """Tickers from fallback should have 'fallback_universe' as their source."""
        self.discovery._load_fallback_universe()
        if "NVDA" in self.discovery._candidates:
            assert "fallback_universe" in self.discovery._candidates["NVDA"]["sources"]

    def test_fallback_with_missing_file_does_not_crash(self, tmp_path, monkeypatch):
        """If universe_sample.csv is missing, _load_fallback_universe should not crash."""
        # Temporarily make the function look for a non-existent file
        # by patching load_csv_safe to return None
        from unittest.mock import patch
        with patch("src.universe_discovery.load_csv_safe", return_value=None):
            # Should not raise any exception
            self.discovery._load_fallback_universe()
            # Candidates should remain empty (no data loaded)
            assert len(self.discovery._candidates) == 0


# ============================================================
# Tests: Universe freshness check
# ============================================================

class TestUniverseFreshness:
    """Tests for is_todays_universe_fresh() and load_todays_universe()."""

    def test_missing_file_returns_not_fresh(self, tmp_path):
        """If the file doesn't exist, is_todays_universe_fresh should return False."""
        path = str(tmp_path / "nonexistent.csv")
        assert is_todays_universe_fresh(path) is False

    def test_load_missing_file_returns_none(self, tmp_path):
        """load_todays_universe on a missing file should return None."""
        path = str(tmp_path / "nonexistent.csv")
        result = load_todays_universe(path)
        assert result is None

    def test_old_file_returns_not_fresh(self, tmp_path):
        """A file with yesterday's date should NOT be considered fresh."""
        from src.utils import today_str
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # Create a CSV with yesterday's date
        df = pd.DataFrame({
            "date": [yesterday, yesterday],
            "ticker": ["NVDA", "AAPL"],
            "passed_filters": [True, True],
        })
        path = str(tmp_path / "universe.csv")
        df.to_csv(path, index=False)

        assert is_todays_universe_fresh(path) is False

    def test_todays_file_returns_fresh(self, tmp_path):
        """A file with today's date should be considered fresh."""
        from src.utils import today_str

        df = pd.DataFrame({
            "date": [today_str(), today_str()],
            "ticker": ["NVDA", "AAPL"],
            "passed_filters": [True, True],
        })
        path = str(tmp_path / "universe.csv")
        df.to_csv(path, index=False)

        assert is_todays_universe_fresh(path) is True

    def test_load_todays_universe_returns_only_passed(self, tmp_path):
        """
        load_todays_universe should return only rows where passed_filters is True.
        """
        from src.utils import today_str

        df = pd.DataFrame({
            "date": [today_str(), today_str(), today_str()],
            "ticker": ["NVDA", "AAPL", "JUNK"],
            "passed_filters": [True, True, False],
        })
        path = str(tmp_path / "universe.csv")
        df.to_csv(path, index=False)

        result = load_todays_universe(path)
        assert result is not None
        assert len(result) == 2
        assert "JUNK" not in result["ticker"].values


# ============================================================
# Integration tests (require internet — skipped by default)
# ============================================================

@pytest.mark.integration
class TestIntegrationDiscovery:
    """
    Integration tests that make real API calls.
    These are skipped by default. Run them with:
        pytest tests/test_universe_discovery.py -v -m integration
    """

    def test_fallback_only_run_returns_dataframe(self):
        """
        Run discovery in fallback-only mode.
        Should return a DataFrame (may be empty on weekends/holidays).
        """
        reset_config()
        # Make sure .env exists before running
        if not os.path.exists(".env"):
            pytest.skip("No .env file found — skipping integration test")

        from src.config import load_config
        cfg = load_config()

        # Use fallback mode — no Alpaca API calls, but does use yfinance
        # for the universe_sample.csv tickers
        result = run_discovery(cfg, use_fallback_only=True)

        assert isinstance(result, pd.DataFrame)
        # Result might be empty on weekends, but should not crash

    def test_full_discovery_run(self):
        """
        Run the full discovery pipeline.
        WARNING: Makes real Alpaca and yfinance API calls.
        May take 60–120 seconds.
        """
        reset_config()
        if not os.path.exists(".env"):
            pytest.skip("No .env file found")

        from src.config import load_config
        cfg = load_config()

        result = run_discovery(cfg)
        assert isinstance(result, pd.DataFrame)

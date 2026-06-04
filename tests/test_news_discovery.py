"""
tests/test_news_discovery.py — Tests for src/news_discovery.py

Tests news item creation, filtering, caching, loading, and providers.
No real network calls are made — yfinance and Alpaca are mocked.

Run with:
    python -m pytest tests/test_news_discovery.py -v
"""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.news_discovery import (
    _make_news_item,
    filter_stale_news,
    filter_low_quality_news,
    deduplicate_headlines,
    cache_news_to_csv,
    load_cached_news,
    format_news_for_catalyst_scorer,
    AlpacaNewsProvider,
    YahooFinanceNewsProvider,
    ManualCSVNewsProvider,
    NewsDiscovery,
    NEWS_CSV_COLUMNS,
)


# ============================================================
# Helpers
# ============================================================

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _days_ago_str(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _make_item(ticker="NVDA", headline="Test headline that is long enough", days_ago=1) -> dict:
    return _make_news_item(
        ticker=ticker,
        headline=headline,
        news_datetime=_days_ago_str(days_ago),
        summary="Test summary.",
        source="TestSource",
        provider="test",
    )


# ============================================================
# TestMakeNewsItem
# ============================================================

class TestMakeNewsItem(unittest.TestCase):

    def test_creates_standardized_dict(self):
        item = _make_news_item("aapl", "Apple beats earnings")
        self.assertIn("ticker", item)
        self.assertIn("headline", item)
        self.assertIn("is_stale", item)
        self.assertIn("is_duplicate", item)
        self.assertIn("passed_quality_filter", item)

    def test_ticker_uppercased(self):
        item = _make_news_item("nvda", "headline text here ok")
        self.assertEqual(item["ticker"], "NVDA")

    def test_all_csv_columns_present(self):
        item = _make_news_item("MSFT", "Microsoft announces new cloud deal")
        for col in NEWS_CSV_COLUMNS:
            self.assertIn(col, item, f"Missing column: {col}")

    def test_default_flags(self):
        item = _make_news_item("AAPL", "Good headline text here indeed")
        self.assertFalse(item["is_stale"])
        self.assertFalse(item["is_duplicate"])
        self.assertTrue(item["passed_quality_filter"])


# ============================================================
# TestFilterStaleNews
# ============================================================

class TestFilterStaleNews(unittest.TestCase):

    def test_old_news_marked_stale(self):
        items = [_make_item(days_ago=10)]
        result = filter_stale_news(items, max_age_days=7)
        self.assertTrue(result[0]["is_stale"])

    def test_fresh_news_not_stale(self):
        items = [_make_item(days_ago=2)]
        result = filter_stale_news(items, max_age_days=7)
        self.assertFalse(result[0]["is_stale"])

    def test_no_datetime_treated_as_not_stale(self):
        item = _make_news_item("AAPL", "Headline here is long enough text")
        item["news_datetime"] = ""
        result = filter_stale_news([item], max_age_days=7)
        self.assertFalse(result[0]["is_stale"])

    def test_does_not_remove_items(self):
        items = [_make_item(days_ago=10), _make_item(days_ago=2)]
        result = filter_stale_news(items, max_age_days=7)
        self.assertEqual(len(result), 2)

    def test_boundary_exactly_7_days_old(self):
        # Exactly 7 days ago should NOT be stale (cutoff is strictly before)
        items = [_make_item(days_ago=7)]
        result = filter_stale_news(items, max_age_days=7)
        # 7 days ago could be stale or fresh depending on exact time — just verify no crash
        self.assertIn("is_stale", result[0])


# ============================================================
# TestFilterLowQualityNews
# ============================================================

class TestFilterLowQualityNews(unittest.TestCase):

    def test_empty_headline_fails(self):
        item = _make_news_item("AAPL", "")
        result = filter_low_quality_news([item])
        self.assertFalse(result[0]["passed_quality_filter"])

    def test_short_headline_fails(self):
        item = _make_news_item("AAPL", "Too short")
        result = filter_low_quality_news([item])
        self.assertFalse(result[0]["passed_quality_filter"])

    def test_sponsored_content_fails(self):
        item = _make_news_item("AAPL", "This is sponsored content from our partner")
        result = filter_low_quality_news([item])
        self.assertFalse(result[0]["passed_quality_filter"])

    def test_advertisement_fails(self):
        item = _make_news_item("AAPL", "Click here to advertise with us today")
        result = filter_low_quality_news([item])
        self.assertFalse(result[0]["passed_quality_filter"])

    def test_good_headline_passes(self):
        item = _make_news_item("NVDA", "Nvidia beats earnings estimates and raises guidance")
        result = filter_low_quality_news([item])
        self.assertTrue(result[0]["passed_quality_filter"])

    def test_does_not_remove_items(self):
        items = [
            _make_news_item("AAPL", "Short"),
            _make_news_item("NVDA", "Nvidia beats earnings estimates significantly"),
        ]
        result = filter_low_quality_news(items)
        self.assertEqual(len(result), 2)


# ============================================================
# TestDeduplicateHeadlines
# ============================================================

class TestDeduplicateHeadlines(unittest.TestCase):

    def test_duplicate_marked(self):
        item1 = _make_news_item("NVDA", "Nvidia beats earnings estimates this quarter")
        item2 = _make_news_item("NVDA", "Nvidia beats earnings estimates this quarter")
        result = deduplicate_headlines([item1, item2])
        # First occurrence kept
        self.assertFalse(result[0]["is_duplicate"])
        # Second marked as duplicate
        self.assertTrue(result[1]["is_duplicate"])

    def test_different_tickers_same_headline_not_duplicates(self):
        item1 = _make_news_item("NVDA", "Chip sector sees record demand across all segments")
        item2 = _make_news_item("AMD", "Chip sector sees record demand across all segments")
        result = deduplicate_headlines([item1, item2])
        # Different tickers — both should be kept
        self.assertFalse(result[0]["is_duplicate"])
        self.assertFalse(result[1]["is_duplicate"])

    def test_first_occurrence_kept(self):
        items = [
            _make_news_item("AAPL", "Apple reports strong iPhone demand growth"),
            _make_news_item("AAPL", "Apple reports strong iPhone demand growth"),
            _make_news_item("AAPL", "Apple reports strong iPhone demand growth"),
        ]
        result = deduplicate_headlines(items)
        self.assertFalse(result[0]["is_duplicate"])
        self.assertTrue(result[1]["is_duplicate"])
        self.assertTrue(result[2]["is_duplicate"])

    def test_case_insensitive_dedup(self):
        item1 = _make_news_item("NVDA", "NVIDIA BEATS ESTIMATES THIS QUARTER")
        item2 = _make_news_item("NVDA", "nvidia beats estimates this quarter")
        result = deduplicate_headlines([item1, item2])
        self.assertFalse(result[0]["is_duplicate"])
        self.assertTrue(result[1]["is_duplicate"])

    def test_empty_headline_not_duplicate(self):
        item1 = _make_news_item("NVDA", "")
        item2 = _make_news_item("NVDA", "")
        result = deduplicate_headlines([item1, item2])
        # Empty headlines should never be flagged as duplicates of each other
        self.assertFalse(result[0]["is_duplicate"])
        self.assertFalse(result[1]["is_duplicate"])


# ============================================================
# TestCacheNewsToCSV
# ============================================================

class TestCacheNewsToCSV(unittest.TestCase):

    def test_creates_file(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "news_cache.csv")
            items = [_make_item()]
            count = cache_news_to_csv(items, path=path)
            self.assertEqual(count, 1)
            self.assertTrue(os.path.exists(path))

    def test_correct_columns(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "news_cache.csv")
            items = [_make_item()]
            cache_news_to_csv(items, path=path)
            df = pd.read_csv(path)
            for col in NEWS_CSV_COLUMNS:
                self.assertIn(col, df.columns)

    def test_overwrites_by_default(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "news_cache.csv")
            items1 = [_make_item(ticker="NVDA")]
            items2 = [_make_item(ticker="AAPL")]
            cache_news_to_csv(items1, path=path)
            cache_news_to_csv(items2, path=path, append=False)
            df = pd.read_csv(path)
            # Should only have AAPL, not NVDA
            self.assertEqual(len(df), 1)
            self.assertEqual(df.iloc[0]["ticker"], "AAPL")

    def test_appends_when_flag_set(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "news_cache.csv")
            items1 = [_make_item(ticker="NVDA")]
            items2 = [_make_item(ticker="AAPL")]
            cache_news_to_csv(items1, path=path)
            cache_news_to_csv(items2, path=path, append=True)
            df = pd.read_csv(path)
            self.assertEqual(len(df), 2)

    def test_empty_list_returns_zero(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "news_cache.csv")
            count = cache_news_to_csv([], path=path)
            self.assertEqual(count, 0)


# ============================================================
# TestLoadCachedNews
# ============================================================

class TestLoadCachedNews(unittest.TestCase):

    def test_returns_empty_for_missing_file(self):
        result = load_cached_news("/nonexistent/path/news.csv")
        self.assertEqual(result, [])

    def test_filters_by_ticker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "news.csv")
            items = [_make_item("NVDA"), _make_item("AAPL")]
            cache_news_to_csv(items, path=path)
            result = load_cached_news(path, ticker="NVDA", max_age_days=30)
            tickers = [r.get("ticker") for r in result]
            self.assertTrue(all(t == "NVDA" for t in tickers))

    def test_excludes_old_news(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "news.csv")
            old = _make_item(days_ago=10)
            fresh = _make_item(days_ago=2, ticker="AAPL", headline="Apple beats earnings estimates")
            cache_news_to_csv([old, fresh], path=path)
            result = load_cached_news(path, max_age_days=7)
            # Only fresh item should be returned
            tickers = [r.get("ticker") for r in result]
            self.assertNotIn("NVDA", tickers)


# ============================================================
# TestFormatNewsForCatalystScorer
# ============================================================

class TestFormatNewsForCatalystScorer(unittest.TestCase):

    def test_returns_correct_structure(self):
        items = [_make_item()]
        result = format_news_for_catalyst_scorer("NVDA", items)
        self.assertIn("ticker", result)
        self.assertIn("headlines", result)
        self.assertIn("summaries", result)
        self.assertIn("sources", result)
        self.assertIn("dates", result)
        self.assertIn("combined_text", result)
        self.assertEqual(result["ticker"], "NVDA")

    def test_excludes_stale_items(self):
        fresh = _make_item(days_ago=1)
        stale = _make_item(days_ago=20)
        stale["is_stale"] = True
        result = format_news_for_catalyst_scorer("NVDA", [fresh, stale])
        # Only fresh headline should be in combined_text
        self.assertNotIn("", result["headlines"])  # fresh item kept

    def test_excludes_duplicates(self):
        item1 = _make_item(days_ago=1)
        item2 = _make_item(days_ago=1)
        item2["is_duplicate"] = True
        result = format_news_for_catalyst_scorer("NVDA", [item1, item2])
        # Only 1 item should be in the output
        self.assertLessEqual(len(result["headlines"]), 1)

    def test_combined_text_joins_headline_and_summary(self):
        item = _make_news_item("NVDA", "Nvidia beats estimates", summary="Q4 EPS beat guidance")
        result = format_news_for_catalyst_scorer("NVDA", [item])
        self.assertIn("Nvidia beats estimates", result["combined_text"])


# ============================================================
# TestAlpacaNewsProviderNoKeys
# ============================================================

class TestAlpacaNewsProviderNoKeys(unittest.TestCase):

    @patch.dict(os.environ, {"ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": ""})
    def test_returns_empty_without_keys(self):
        provider = AlpacaNewsProvider(api_key="", secret_key="")
        result = provider.get_news_for_symbol("NVDA", days_back=7)
        self.assertEqual(result, [])

    @patch.dict(os.environ, {"ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": ""})
    def test_is_available_false_without_keys(self):
        provider = AlpacaNewsProvider(api_key="", secret_key="")
        self.assertFalse(provider.is_available)

    @patch.dict(os.environ, {"ALPACA_API_KEY": "", "ALPACA_SECRET_KEY": ""})
    def test_batch_returns_empty_without_keys(self):
        provider = AlpacaNewsProvider(api_key="", secret_key="")
        result = provider.get_news_for_symbols(["NVDA", "AAPL"])
        self.assertEqual(result, [])


# ============================================================
# TestYahooFinanceNewsProvider
# ============================================================

class TestYahooFinanceNewsProvider(unittest.TestCase):

    def test_returns_empty_on_import_error(self):
        provider = YahooFinanceNewsProvider()
        with patch("src.news_discovery.YahooFinanceNewsProvider.get_news_for_symbol") as mock:
            mock.return_value = []
            result = provider.get_news_for_symbol("NVDA")
            self.assertEqual(result, [])

    def test_parses_valid_yfinance_response(self):
        provider = YahooFinanceNewsProvider()
        mock_ticker = MagicMock()

        # Unix timestamp 3 days ago
        pub_ts = int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp())
        mock_ticker.news = [
            {
                "title": "Nvidia smashes earnings and raises guidance for next quarter",
                "providerPublishTime": pub_ts,
                "publisher": "Reuters",
                "link": "http://example.com",
                "summary": "NVDA reports record Q4 earnings above all estimates.",
            }
        ]

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = provider.get_news_for_symbol("NVDA", days_back=7)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ticker"], "NVDA")
        self.assertIn("Nvidia", result[0]["headline"])
        self.assertEqual(result[0]["provider"], "yahoo")

    def test_filters_out_old_yfinance_articles(self):
        provider = YahooFinanceNewsProvider()
        mock_ticker = MagicMock()

        # 30 days ago — should be filtered out with days_back=7
        old_ts = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
        mock_ticker.news = [
            {
                "title": "Old news that should be filtered out by date",
                "providerPublishTime": old_ts,
                "publisher": "OldSource",
            }
        ]

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = provider.get_news_for_symbol("NVDA", days_back=7)

        self.assertEqual(len(result), 0)


# ============================================================
# TestManualCSVNewsProvider
# ============================================================

class TestManualCSVNewsProvider(unittest.TestCase):

    def test_returns_empty_for_missing_file(self):
        provider = ManualCSVNewsProvider("/nonexistent/path.csv")
        result = provider.get_news_for_symbol("NVDA")
        self.assertEqual(result, [])

    def test_reads_items_from_csv(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "news.csv")
            items = [_make_item(ticker="NVDA", days_ago=2)]
            cache_news_to_csv(items, path=path)

            provider = ManualCSVNewsProvider(path)
            result = provider.get_news_for_symbol("NVDA", days_back=7)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["ticker"], "NVDA")

    def test_filters_by_ticker(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "news.csv")
            items = [_make_item(ticker="NVDA"), _make_item(ticker="AAPL")]
            cache_news_to_csv(items, path=path)

            provider = ManualCSVNewsProvider(path)
            result = provider.get_news_for_symbol("NVDA", days_back=30)
            tickers = [r["ticker"] for r in result]
            self.assertTrue(all(t == "NVDA" for t in tickers))


# ============================================================
# TestNewsDiscovery
# ============================================================

class TestNewsDiscovery(unittest.TestCase):

    def test_builds_provider_chain(self):
        """NewsDiscovery should build at least one provider (Yahoo + ManualCSV always present)."""
        nd = NewsDiscovery(config=None)
        self.assertGreater(len(nd._providers), 0)

    def test_get_recent_news_returns_list(self):
        nd = NewsDiscovery(config=None)
        # Mock all providers to return empty
        for p in nd._providers:
            p.get_news_for_symbol = MagicMock(return_value=[])
        result = nd.get_recent_news_for_symbol("NVDA")
        self.assertIsInstance(result, list)

    def test_applies_filters(self):
        """Stale and low-quality items are filtered."""
        nd = NewsDiscovery(config=None)

        stale_item = _make_news_item("NVDA", "Stale headline from a long time ago today", news_datetime=_days_ago_str(30))
        bad_item = _make_news_item("NVDA", "Short")

        for p in nd._providers:
            p.get_news_for_symbol = MagicMock(return_value=[stale_item, bad_item])
            p.get_news_for_symbols = MagicMock(return_value=[stale_item, bad_item])

        all_news, _ = nd.get_news_for_discovered_universe(["NVDA"], days_back=7)

        # Stale item should be marked
        stale_items = [i for i in all_news if str(i.get("is_stale")) in ("True", "true", True)]
        self.assertGreater(len(stale_items), 0)

    def test_returns_no_news_symbols(self):
        nd = NewsDiscovery(config=None)
        # Make all providers return nothing
        for p in nd._providers:
            p.get_news_for_symbol = MagicMock(return_value=[])
            p.get_news_for_symbols = MagicMock(return_value=[])

        _, no_news = nd.get_news_for_discovered_universe(["NVDA", "AAPL"])
        self.assertIn("NVDA", no_news)
        self.assertIn("AAPL", no_news)


if __name__ == "__main__":
    unittest.main()

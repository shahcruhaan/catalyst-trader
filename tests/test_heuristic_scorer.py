"""
tests/test_heuristic_scorer.py — Tests for src/heuristic_scorer.py

All pure functions — no mocks or network calls required.

Run with:
    python -m pytest tests/test_heuristic_scorer.py -v
"""

import unittest
from datetime import datetime, timedelta, timezone

from src.heuristic_scorer import (
    score_headline_heuristic,
    score_all_tickers,
    build_llm_prompt,
    parse_llm_response,
    _calc_freshness_bonus,
    _determine_catalyst_type,
    _no_catalyst_result,
    STRONG_BULLISH_KEYWORDS,
    STRONG_NEGATIVE_KEYWORDS,
    HYPE_KEYWORDS,
)


# ============================================================
# Helpers
# ============================================================

def _now_dt_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _days_ago_str(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# TestScoreHeadlineHeuristic
# ============================================================

class TestScoreHeadlineHeuristic(unittest.TestCase):

    # --- Strong bullish signals ---

    def test_earnings_beat_scores_nine_or_above(self):
        result = score_headline_heuristic(
            ticker="NVDA",
            headlines=["Nvidia beats earnings estimates by wide margin this quarter"],
            summaries=["EPS of $5.10 vs $4.20 estimate"],
            news_dates=[_now_dt_str()],
        )
        self.assertGreaterEqual(result["catalyst_score"], 9)
        self.assertEqual(result["catalyst_type"], "earnings_beat")

    def test_guidance_raise_scores_nine_or_above(self):
        result = score_headline_heuristic(
            ticker="MSFT",
            headlines=["Microsoft raised guidance for full year fiscal 2025 revenue"],
            news_dates=[_now_dt_str()],
        )
        self.assertGreaterEqual(result["catalyst_score"], 9)

    def test_earnings_beat_plus_guidance_raise_scores_ten(self):
        result = score_headline_heuristic(
            ticker="NVDA",
            headlines=["Nvidia crushes earnings estimates and raised guidance for next quarter"],
            news_dates=[_now_dt_str()],
        )
        self.assertEqual(result["catalyst_score"], 10)
        self.assertIn(result["catalyst_type"], ["earnings_beat_and_guidance_raise", "earnings_beat", "guidance_raise"])

    def test_fda_approval_scores_high(self):
        result = score_headline_heuristic(
            ticker="MRNA",
            headlines=["Moderna received FDA approval for new cancer vaccine treatment"],
            news_dates=[_now_dt_str()],
        )
        self.assertGreaterEqual(result["catalyst_score"], 9)
        self.assertEqual(result["catalyst_type"], "fda_approval")

    def test_major_contract_scores_high(self):
        result = score_headline_heuristic(
            ticker="LMT",
            headlines=["Lockheed wins major defense contract worth billions with Pentagon"],
            news_dates=[_now_dt_str()],
        )
        self.assertGreaterEqual(result["catalyst_score"], 9)

    def test_acquisition_offer_scores_high(self):
        result = score_headline_heuristic(
            ticker="ATVI",
            headlines=["Activision receives acquisition offer from Microsoft in major deal"],
            news_dates=[_now_dt_str()],
        )
        self.assertGreaterEqual(result["catalyst_score"], 9)

    # --- Moderate signals ---

    def test_analyst_upgrade_scores_seven_or_above(self):
        result = score_headline_heuristic(
            ticker="AAPL",
            headlines=["Goldman Sachs upgraded to buy with raised price target of $230"],
            news_dates=[_now_dt_str()],
        )
        self.assertGreaterEqual(result["catalyst_score"], 7)

    def test_buyback_scores_at_least_seven(self):
        result = score_headline_heuristic(
            ticker="AAPL",
            headlines=["Apple announces $100 billion share buyback repurchase program"],
            news_dates=[_now_dt_str()],
        )
        self.assertGreaterEqual(result["catalyst_score"], 7)

    # --- Negative flag rejection ---

    def test_missed_earnings_reduces_score(self):
        result = score_headline_heuristic(
            ticker="XYZ",
            headlines=["Company missed earnings estimates for the third consecutive quarter"],
            news_dates=[_now_dt_str()],
        )
        self.assertLessEqual(result["catalyst_score"], 6)
        self.assertFalse(result["tradeable"])

    def test_guidance_cut_reduces_score(self):
        result = score_headline_heuristic(
            ticker="XYZ",
            headlines=["Management lowered guidance and cut forecast for full year revenue"],
            news_dates=[_now_dt_str()],
        )
        self.assertLessEqual(result["catalyst_score"], 6)
        self.assertFalse(result["tradeable"])

    def test_bankruptcy_gives_very_low_score(self):
        result = score_headline_heuristic(
            ticker="XYZ",
            headlines=["Company files for chapter 11 bankruptcy protection today"],
            news_dates=[_now_dt_str()],
        )
        self.assertLessEqual(result["catalyst_score"], 4)
        self.assertFalse(result["tradeable"])

    def test_multiple_negatives_cap_at_two(self):
        result = score_headline_heuristic(
            ticker="XYZ",
            headlines=[
                "Company missed earnings and lowered guidance while facing SEC investigation",
                "Reverse stock split announced after bankruptcy warning issued",
            ],
            news_dates=[_now_dt_str()],
        )
        self.assertLessEqual(result["catalyst_score"], 2)

    def test_tradeable_false_with_negative_flags(self):
        result = score_headline_heuristic(
            ticker="XYZ",
            headlines=["SEC investigation ongoing as company missed earnings estimates badly"],
            news_dates=[_now_dt_str()],
        )
        self.assertFalse(result["tradeable"])
        self.assertGreater(len(result["negative_flags"]), 0)

    # --- Hype rejection ---

    def test_hype_only_score_capped_at_four(self):
        result = score_headline_heuristic(
            ticker="GME",
            headlines=["GME going viral on Reddit wallstreetbets to the moon short squeeze play"],
            news_dates=[_now_dt_str()],
        )
        self.assertLessEqual(result["catalyst_score"], 4)
        self.assertFalse(result["tradeable"])
        self.assertGreater(len(result["hype_flags"]), 0)

    def test_hype_does_not_override_real_catalyst(self):
        """Hype flags should not suppress an otherwise valid catalyst."""
        result = score_headline_heuristic(
            ticker="NVDA",
            headlines=[
                "Nvidia beats earnings estimates and raised guidance — also trending on Reddit",
            ],
            news_dates=[_now_dt_str()],
        )
        # Real catalyst (earnings beat + guidance raise) should dominate
        self.assertGreaterEqual(result["catalyst_score"], 7)

    # --- No headlines ---

    def test_no_headlines_returns_zero(self):
        result = score_headline_heuristic(
            ticker="NVDA",
            headlines=[],
        )
        self.assertEqual(result["catalyst_score"], 0)
        self.assertFalse(result["tradeable"])

    def test_empty_string_headlines_return_zero(self):
        result = score_headline_heuristic(
            ticker="NVDA",
            headlines=["", "   "],
        )
        self.assertEqual(result["catalyst_score"], 0)

    # --- Tradeable flag logic ---

    def test_score_8_no_negatives_is_tradeable(self):
        result = score_headline_heuristic(
            ticker="NVDA",
            headlines=["Nvidia beats earnings estimates for the fourth consecutive quarter"],
            news_dates=[_days_ago_str(1)],
        )
        if result["catalyst_score"] >= 8 and result["confidence"] in ("medium", "high"):
            self.assertTrue(result["tradeable"])

    def test_score_below_8_not_tradeable(self):
        result = score_headline_heuristic(
            ticker="AAPL",
            headlines=["Apple announces new investor day and collaboration agreement"],
            news_dates=[_days_ago_str(1)],
        )
        if result["catalyst_score"] < 8:
            self.assertFalse(result["tradeable"])

    # --- Confidence levels ---

    def test_confidence_high_for_very_strong_signals(self):
        result = score_headline_heuristic(
            ticker="NVDA",
            headlines=[
                "Nvidia smashes earnings estimates by wide margin this quarter",
                "Nvidia raised guidance for full year fiscal 2026 outlook",
            ],
            news_dates=[_days_ago_str(1), _days_ago_str(1)],
        )
        self.assertIn(result["confidence"], ("high", "medium"))

    def test_confidence_low_with_negatives(self):
        result = score_headline_heuristic(
            ticker="XYZ",
            headlines=["Company missed earnings and SEC investigation launched into accounting"],
            news_dates=[_days_ago_str(1)],
        )
        self.assertEqual(result["confidence"], "low")

    # --- Freshness bonus ---

    def test_fresh_news_adds_bonus(self):
        """Very fresh news (< 3 days) should get +1 bonus."""
        fresh_result = score_headline_heuristic(
            ticker="AAPL",
            headlines=["Apple upgraded to buy by Goldman Sachs with raised price target"],
            news_dates=[_days_ago_str(1)],  # 1 day ago — fresh
        )
        stale_result = score_headline_heuristic(
            ticker="AAPL",
            headlines=["Apple upgraded to buy by Goldman Sachs with raised price target"],
            news_dates=[_days_ago_str(5)],  # 5 days ago — not fresh enough for bonus
        )
        # Fresh should score >= stale (bonus applied)
        self.assertGreaterEqual(fresh_result["catalyst_score"], stale_result["catalyst_score"])

    # --- Score bounds ---

    def test_score_never_exceeds_10(self):
        result = score_headline_heuristic(
            ticker="NVDA",
            headlines=[
                "Nvidia beats earnings and raised guidance, wins major contract",
                "FDA approval received, activist stake disclosed, acquisition offer",
            ],
            news_dates=[_days_ago_str(1)],
        )
        self.assertLessEqual(result["catalyst_score"], 10)

    def test_score_minimum_is_1_with_content(self):
        result = score_headline_heuristic(
            ticker="XYZ",
            headlines=["Some random unrelated news story with no relevance"],
        )
        self.assertGreaterEqual(result["catalyst_score"], 1)

    # --- Result structure ---

    def test_result_has_all_required_fields(self):
        result = score_headline_heuristic(
            ticker="NVDA",
            headlines=["Nvidia beats earnings estimates this quarter significantly"],
        )
        required = [
            "ticker", "catalyst_score", "tradeable", "confidence",
            "catalyst_type", "one_sentence_reason", "bullish_mechanism",
            "risk", "matched_keywords", "negative_flags", "hype_flags",
            "score_method", "headlines_used",
        ]
        for field in required:
            self.assertIn(field, result, f"Missing field: {field}")

    def test_score_method_is_heuristic(self):
        result = score_headline_heuristic(
            ticker="NVDA",
            headlines=["Nvidia beats earnings estimates this quarter"],
        )
        self.assertEqual(result["score_method"], "heuristic")


# ============================================================
# TestCalcFreshnessBonus
# ============================================================

class TestCalcFreshnessBonus(unittest.TestCase):

    def test_returns_one_for_very_recent_news(self):
        dates = [_days_ago_str(1)]
        self.assertEqual(_calc_freshness_bonus(dates), 1)

    def test_returns_zero_for_old_news(self):
        dates = [_days_ago_str(6)]
        self.assertEqual(_calc_freshness_bonus(dates), 0)

    def test_returns_zero_for_empty(self):
        self.assertEqual(_calc_freshness_bonus([]), 0)

    def test_returns_one_if_any_is_fresh(self):
        dates = [_days_ago_str(6), _days_ago_str(1)]
        self.assertEqual(_calc_freshness_bonus(dates), 1)


# ============================================================
# TestNoCatalystResult
# ============================================================

class TestNoCatalystResult(unittest.TestCase):

    def test_zero_score(self):
        result = _no_catalyst_result("NVDA")
        self.assertEqual(result["catalyst_score"], 0)

    def test_not_tradeable(self):
        result = _no_catalyst_result("NVDA")
        self.assertFalse(result["tradeable"])

    def test_catalyst_type_is_no_news(self):
        result = _no_catalyst_result("NVDA")
        self.assertEqual(result["catalyst_type"], "no_news")

    def test_ticker_uppercased(self):
        result = _no_catalyst_result("nvda")
        self.assertEqual(result["ticker"], "NVDA")


# ============================================================
# TestScoreAllTickers
# ============================================================

class TestScoreAllTickers(unittest.TestCase):

    def test_scores_multiple_tickers(self):
        news_by_ticker = {
            "NVDA": {
                "headlines": ["Nvidia beats earnings estimates and raised guidance for next year"],
                "summaries": ["Record Q4 earnings beat all analyst estimates"],
                "dates": [_days_ago_str(1)],
            },
            "XYZ": {
                "headlines": ["Company missed earnings estimates for third quarter in a row"],
                "summaries": [],
                "dates": [_days_ago_str(1)],
            },
        }
        results = score_all_tickers(news_by_ticker)
        self.assertIn("NVDA", results)
        self.assertIn("XYZ", results)
        # NVDA should score higher than XYZ
        self.assertGreater(
            results["NVDA"]["catalyst_score"],
            results["XYZ"]["catalyst_score"]
        )

    def test_empty_input_returns_empty_dict(self):
        results = score_all_tickers({})
        self.assertEqual(results, {})

    def test_handles_missing_headlines_key(self):
        """Should not crash if news_data is missing optional keys."""
        news_by_ticker = {
            "AAPL": {
                "headlines": [],  # empty headlines
                "summaries": [],
                "dates": [],
            }
        }
        results = score_all_tickers(news_by_ticker)
        self.assertIn("AAPL", results)
        self.assertEqual(results["AAPL"]["catalyst_score"], 0)


# ============================================================
# TestBuildLLMPrompt
# ============================================================

class TestBuildLLMPrompt(unittest.TestCase):

    def test_contains_ticker(self):
        prompt = build_llm_prompt("NVDA", ["Nvidia beats earnings"])
        self.assertIn("NVDA", prompt)

    def test_contains_headlines(self):
        prompt = build_llm_prompt("NVDA", ["Nvidia beats earnings estimates significantly"])
        self.assertIn("Nvidia beats earnings estimates significantly", prompt)

    def test_contains_json_instructions(self):
        prompt = build_llm_prompt("NVDA", ["Some headline here"])
        self.assertIn("catalyst_score", prompt)
        self.assertIn("tradeable", prompt)

    def test_works_without_summaries(self):
        prompt = build_llm_prompt("NVDA", ["Headline only"])
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 100)


# ============================================================
# TestParseLLMResponse
# ============================================================

class TestParseLLMResponse(unittest.TestCase):

    def test_valid_json(self):
        response = """{
            "catalyst_score": 9,
            "tradeable": true,
            "confidence": "high",
            "catalyst_type": "earnings_beat",
            "one_sentence_reason": "Beat and raise.",
            "bullish_mechanism": "Revenue growth driving re-rating.",
            "risk": "High valuation.",
            "negative_flags": []
        }"""
        result = parse_llm_response(response, "NVDA")
        self.assertIsNotNone(result)
        self.assertEqual(result["catalyst_score"], 9)
        self.assertTrue(result["tradeable"])
        self.assertEqual(result["score_method"], "llm")

    def test_invalid_json_returns_none(self):
        result = parse_llm_response("not valid json at all !!!", "NVDA")
        self.assertIsNone(result)

    def test_score_out_of_range_returns_none(self):
        response = '{"catalyst_score": 15, "tradeable": true, "confidence": "high"}'
        result = parse_llm_response(response, "NVDA")
        self.assertIsNone(result)

    def test_zero_score_returns_none(self):
        response = '{"catalyst_score": 0, "tradeable": false, "confidence": "low"}'
        result = parse_llm_response(response, "NVDA")
        self.assertIsNone(result)

    def test_extracts_json_from_markdown_code_block(self):
        response = """Here is my analysis:
```json
{
    "catalyst_score": 8,
    "tradeable": true,
    "confidence": "medium",
    "catalyst_type": "analyst_upgrade",
    "one_sentence_reason": "Strong upgrade.",
    "bullish_mechanism": "Institutional buying expected.",
    "risk": "Already priced in.",
    "negative_flags": []
}
```
        """
        result = parse_llm_response(response, "AAPL")
        self.assertIsNotNone(result)
        self.assertEqual(result["catalyst_score"], 8)

    def test_ticker_uppercased_in_result(self):
        response = '{"catalyst_score": 8, "tradeable": true, "confidence": "medium", "catalyst_type": "test", "one_sentence_reason": "ok", "bullish_mechanism": "ok", "risk": "ok", "negative_flags": []}'
        result = parse_llm_response(response, "nvda")
        self.assertIsNotNone(result)
        self.assertEqual(result["ticker"], "NVDA")


# ============================================================
# TestKeywordDictionaries
# ============================================================

class TestKeywordDictionaries(unittest.TestCase):

    def test_strong_bullish_has_required_categories(self):
        required = ["earnings_beat", "guidance_raise", "fda_approval", "major_contract"]
        for cat in required:
            self.assertIn(cat, STRONG_BULLISH_KEYWORDS)
            self.assertGreater(len(STRONG_BULLISH_KEYWORDS[cat]), 0)

    def test_strong_negative_has_key_phrases(self):
        required_phrases = ["missed earnings", "guidance cut", "sec investigation", "bankruptcy"]
        for phrase in required_phrases:
            self.assertIn(phrase, STRONG_NEGATIVE_KEYWORDS)

    def test_hype_keywords_has_meme_phrases(self):
        self.assertIn("reddit", HYPE_KEYWORDS)
        self.assertIn("wallstreetbets", HYPE_KEYWORDS)
        self.assertIn("meme stock", HYPE_KEYWORDS)


if __name__ == "__main__":
    unittest.main()

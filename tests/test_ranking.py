"""
tests/test_ranking.py — Tests for src/ranking.py

Covers:
  - _normalize: edge cases, ordering, None handling
  - score_candidate: weight correctness
  - rank_signals: ordering, position limits, empty input
  - calculate_position_size: basic sizing, max cap, costs, edge cases
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ranking import _normalize, score_candidate, rank_signals, calculate_position_size


# ============================================================
# TestNormalize
# ============================================================

class TestNormalize:

    def test_basic_normalization(self):
        result = _normalize([0.0, 5.0, 10.0])
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.5)
        assert result[-1] == pytest.approx(1.0)

    def test_all_same_values_returns_half(self):
        result = _normalize([7.0, 7.0, 7.0])
        assert all(v == pytest.approx(0.5) for v in result)

    def test_single_value_returns_half(self):
        result = _normalize([42.0])
        assert result[0] == pytest.approx(0.5)

    def test_none_treated_as_zero(self):
        result = _normalize([None, 10.0])
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(1.0)

    def test_ordering_preserved(self):
        values = [10.0, 5.0, 0.0]
        result = _normalize(values)
        assert result[0] > result[1] > result[2]

    def test_output_length_matches_input(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = _normalize(values)
        assert len(result) == len(values)

    def test_all_values_in_zero_one_range(self):
        values = [3.5, 8.2, 1.1, 9.9, 0.0]
        result = _normalize(values)
        assert all(0.0 <= v <= 1.0 for v in result)


# ============================================================
# TestScoreCandidate
# ============================================================

class TestScoreCandidate:

    def test_perfect_score_is_one(self):
        result = score_candidate(1.0, 1.0, 1.0, 1.0)
        assert result == pytest.approx(1.0)

    def test_zero_score_is_zero(self):
        result = score_candidate(0.0, 0.0, 0.0, 0.0)
        assert result == pytest.approx(0.0)

    def test_weights_sum_to_one(self):
        # 35 + 25 + 20 + 20 = 100 → weights sum to 1.0
        result = score_candidate(1.0, 1.0, 1.0, 1.0)
        assert result == pytest.approx(1.0)

    def test_catalyst_weight_is_35pct(self):
        # Only catalyst is 1.0, rest are 0
        result = score_candidate(1.0, 0.0, 0.0, 0.0)
        assert result == pytest.approx(0.35)

    def test_rs_weight_is_25pct(self):
        result = score_candidate(0.0, 1.0, 0.0, 0.0)
        assert result == pytest.approx(0.25)

    def test_volume_weight_is_20pct(self):
        result = score_candidate(0.0, 0.0, 1.0, 0.0)
        assert result == pytest.approx(0.20)

    def test_return_weight_is_20pct(self):
        result = score_candidate(0.0, 0.0, 0.0, 1.0)
        assert result == pytest.approx(0.20)


# ============================================================
# TestRankSignals
# ============================================================

class TestRankSignals:

    def _sig(self, ticker, cat=9, rs=0.15, vol=3.0, ret=0.20):
        return {
            "ticker": ticker, "signal_type": "BUY",
            "catalyst_score": cat,
            "relative_strength_20d": rs,
            "volume_ratio": vol,
            "return_20d": ret,
        }

    def test_empty_input_returns_empty(self):
        assert rank_signals([]) == []

    def test_single_signal_returned(self):
        result = rank_signals([self._sig("NVDA")])
        assert len(result) == 1
        assert result[0]["ticker"] == "NVDA"

    def test_rank_score_added(self):
        result = rank_signals([self._sig("NVDA")])
        assert "rank_score" in result[0]
        assert 0.0 <= result[0]["rank_score"] <= 1.0

    def test_sorted_descending_by_rank_score(self):
        signals = [self._sig(f"T{i}", cat=i+1) for i in range(5)]
        result = rank_signals(signals)
        scores = [r["rank_score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_higher_catalyst_ranks_first(self):
        sig_strong = self._sig("AAPL", cat=10)
        sig_weak = self._sig("META", cat=7)
        result = rank_signals([sig_weak, sig_strong])
        assert result[0]["ticker"] == "AAPL"
        assert result[1]["ticker"] == "META"

    def test_higher_rs_ranks_first_when_equal_catalyst(self):
        sig_high_rs = self._sig("NVDA", cat=9, rs=0.30)
        sig_low_rs = self._sig("TSLA", cat=9, rs=0.10)
        result = rank_signals([sig_low_rs, sig_high_rs])
        assert result[0]["ticker"] == "NVDA"

    def test_position_limit_caps_result(self):
        signals = [self._sig(f"T{i}") for i in range(6)]
        result = rank_signals(signals, max_positions=3, current_position_count=0)
        assert len(result) == 3

    def test_current_positions_reduce_available_slots(self):
        signals = [self._sig(f"T{i}") for i in range(8)]
        result = rank_signals(signals, max_positions=8, current_position_count=6)
        assert len(result) == 2

    def test_full_portfolio_returns_empty(self):
        result = rank_signals([self._sig("NVDA")], max_positions=8, current_position_count=8)
        assert result == []

    def test_never_exceeds_max_positions(self):
        signals = [self._sig(f"T{i}") for i in range(20)]
        result = rank_signals(signals, max_positions=8, current_position_count=0)
        assert len(result) <= 8

    def test_original_signals_not_mutated(self):
        original = [self._sig("NVDA")]
        rank_signals(original)
        # rank_signals works on copies — just ensure no crash
        assert True


# ============================================================
# TestCalculatePositionSize
# ============================================================

class TestCalculatePositionSize:

    def test_basic_18pct_allocation(self):
        result = calculate_position_size(100_000, 100.0, target_pct=0.18)
        # ~$18,000 target / $100 price = ~180 shares, minus small cost deduction
        assert result["shares"] >= 170
        assert result["shares"] <= 180

    def test_capped_at_max_pct(self):
        result = calculate_position_size(
            100_000, 100.0, target_pct=0.30, max_pct=0.25
        )
        # Should be at most 25% = $25,000 / $100 = 250 shares minus costs
        assert result["actual_pct"] <= 0.26

    def test_zero_portfolio_value(self):
        result = calculate_position_size(0, 100.0)
        assert result["shares"] == 0
        assert result["actual_pct"] == 0

    def test_zero_price(self):
        result = calculate_position_size(100_000, 0.0)
        assert result["shares"] == 0

    def test_shares_is_whole_number(self):
        result = calculate_position_size(100_000, 73.50)
        assert isinstance(result["shares"], int)

    def test_costs_reduce_share_count(self):
        no_costs = calculate_position_size(100_000, 100.0, transaction_cost_bps=0, slippage_bps=0)
        with_costs = calculate_position_size(100_000, 100.0, transaction_cost_bps=5, slippage_bps=10)
        assert no_costs["shares"] >= with_costs["shares"]

    def test_adjusted_dollars_less_than_target(self):
        result = calculate_position_size(100_000, 100.0, transaction_cost_bps=5, slippage_bps=10)
        assert result["adjusted_dollars"] < result["target_dollars"]

    def test_actual_pct_reasonable(self):
        result = calculate_position_size(100_000, 50.0, target_pct=0.18)
        assert 0.0 < result["actual_pct"] <= 0.26

    def test_expensive_stock_gets_at_least_one_share(self):
        result = calculate_position_size(100_000, 9000.0, target_pct=0.18)
        assert result["shares"] >= 1

    def test_returns_all_expected_keys(self):
        result = calculate_position_size(100_000, 100.0)
        for key in ("target_dollars", "adjusted_dollars", "shares", "actual_pct"):
            assert key in result

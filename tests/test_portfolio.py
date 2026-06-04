"""
tests/test_portfolio.py — Tests for Stage 6 portfolio management

Covers:
  TestUpdatePeakPrice         — peak_price tracking logic
  TestComputeStopLevel        — stop level for each gain tier
  TestWinnerManagement        — stop tier uses peak_price (not current_price)
  TestPortfolioSummary        — compute_portfolio_summary math
  TestPositionSizing          — calculate_position_size from ranking.py
  TestFormatPositionsTable    — format_positions_table output shape
  TestLoadRecentOrders        — load_recent_orders from CSV
  TestCheckPositionsForExits  — hard stop / breakeven / time limit checks
  TestReconcileWithPortfolio  — run_reconcile_with_portfolio (mocked broker)
  TestPaperReport             — run_paper_report (mocked broker)

All tests are OFFLINE — no network calls. Broker is fully mocked.
"""

import csv
import json
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.portfolio import (
    compute_portfolio_summary,
    compute_stop_level,
    format_positions_table,
    load_recent_orders,
    update_peak_price,
)
from src.paper_trader import (
    check_positions_for_exits,
    run_paper_report,
    run_reconcile_with_portfolio,
)
from src.ranking import calculate_position_size


# ============================================================
# Shared helpers
# ============================================================

def _cfg(**overrides):
    defaults = dict(
        stop_loss_pct=0.08,
        breakeven_trigger_pct=0.20,
        partial_take_profit_trigger_pct=0.40,
        big_winner_trigger_pct=0.75,
        max_holding_days=20,
        starting_cash=100_000.0,
        target_position_size=0.18,
        max_position_size=0.25,
        transaction_cost_bps=5,
        slippage_bps=10,
        state_file_path="reports/paper_state.json",
        order_log_path="reports/orders_log.csv",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _pos(
    entry=100.0,
    current=100.0,
    shares=100,
    holding_days=3,
    partial_taken=False,
    peak_price=None,
    market_value=None,
    unrealized_pl=None,
    entry_date="2026-05-28",
    catalyst_score=9,
    catalyst_headline="Test headline",
    ticker="TEST",
):
    if peak_price is None:
        peak_price = current
    if market_value is None:
        market_value = shares * current
    if unrealized_pl is None:
        unrealized_pl = (current - entry) * shares
    return {
        "ticker": ticker,
        "entry_price": entry,
        "current_price": current,
        "avg_entry_price": entry,
        "shares": shares,
        "market_value": market_value,
        "unrealized_pl": unrealized_pl,
        "unrealized_plpc": (current / entry - 1) if entry > 0 else 0,
        "holding_days": holding_days,
        "entry_date": entry_date,
        "partial_taken": partial_taken,
        "peak_price": peak_price,
        "catalyst_score": catalyst_score,
        "catalyst_headline": catalyst_headline,
    }


def _state(positions=None, portfolio_value=100_000.0, buying_power=82_000.0, cash=82_000.0):
    return {
        "last_updated": "2026-06-02 09:30:00",
        "portfolio_value": portfolio_value,
        "buying_power": buying_power,
        "cash": cash,
        "orders_submitted_today": 0,
        "last_execute_date": "2026-06-02",
        "positions": positions or {},
    }


def _mock_broker(portfolio=100_000.0, buying_power=82_000.0, cash=82_000.0,
                 positions=None):
    broker = MagicMock()
    broker.get_account.return_value = {
        "portfolio_value": portfolio,
        "buying_power": buying_power,
        "cash": cash,
        "status": "AccountStatus.ACTIVE",
    }
    broker.get_positions.return_value = positions or {}
    return broker


# ============================================================
# TestUpdatePeakPrice
# ============================================================

class TestUpdatePeakPrice:

    def test_initializes_peak_price_when_zero(self):
        """If peak_price not set, sets it to current_price."""
        positions = {"NVDA": _pos(current=125.0, peak_price=0)}
        update_peak_price(positions)
        assert positions["NVDA"]["peak_price"] == 125.0

    def test_updates_when_current_higher(self):
        """Raises peak_price when current_price exceeds it."""
        positions = {"NVDA": _pos(current=130.0, peak_price=125.0)}
        update_peak_price(positions)
        assert positions["NVDA"]["peak_price"] == 130.0

    def test_preserves_when_current_lower(self):
        """Never lowers peak_price below existing value."""
        positions = {"NVDA": _pos(current=110.0, peak_price=125.0)}
        update_peak_price(positions)
        assert positions["NVDA"]["peak_price"] == 125.0

    def test_multiple_positions_updated_independently(self):
        positions = {
            "NVDA": _pos(ticker="NVDA", current=130.0, peak_price=120.0),
            "AAPL": _pos(ticker="AAPL", current=180.0, peak_price=190.0),
        }
        update_peak_price(positions)
        assert positions["NVDA"]["peak_price"] == 130.0  # raised
        assert positions["AAPL"]["peak_price"] == 190.0  # preserved

    def test_falls_back_to_entry_price_when_current_is_zero(self):
        """If current_price is 0, falls back to entry_price for peak_price."""
        positions = {"NVDA": {
            "entry_price": 100.0, "current_price": 0, "peak_price": 0
        }}
        update_peak_price(positions)
        # entry_price is used as fallback when current_price is falsy
        assert positions["NVDA"]["peak_price"] == pytest.approx(100.0)

    def test_skips_position_when_both_prices_zero(self):
        """If both current_price and entry_price are 0, peak_price stays 0."""
        positions = {"NVDA": {"entry_price": 0, "current_price": 0, "peak_price": 0}}
        update_peak_price(positions)
        assert positions["NVDA"]["peak_price"] == 0

    def test_returns_same_dict(self):
        """Returns the same dict object (mutates in place)."""
        positions = {"NVDA": _pos(current=125.0, peak_price=120.0)}
        result = update_peak_price(positions)
        assert result is positions


# ============================================================
# TestComputeStopLevel
# ============================================================

class TestComputeStopLevel:
    cfg = _cfg()

    def test_normal_tier_hard_stop(self):
        """Below +20% gain → hard stop at entry * (1 - 8%)."""
        pos = _pos(entry=100.0, current=110.0, peak_price=110.0)
        result = compute_stop_level(pos, self.cfg)
        assert result["tier"] == "normal"
        assert result["stop_price"] == pytest.approx(92.0, abs=0.01)
        assert "hard_stop" in result["stop_type"]
        assert result["gain_pct"] == pytest.approx(0.10, abs=0.001)

    def test_breakeven_tier(self):
        """Peak gain >= +20% → breakeven stop at entry_price."""
        pos = _pos(entry=100.0, current=120.0, peak_price=120.0)
        result = compute_stop_level(pos, self.cfg)
        assert result["tier"] == "breakeven"
        assert result["stop_price"] == pytest.approx(100.0, abs=0.01)
        assert result["stop_type"] == "breakeven"

    def test_partial_tier(self):
        """Peak gain >= +40% → trail 10d MA, stop_price is None."""
        pos = _pos(entry=100.0, current=145.0, peak_price=145.0)
        result = compute_stop_level(pos, self.cfg)
        assert result["tier"] == "partial"
        assert result["stop_price"] is None
        assert result["stop_type"] == "trail_10d_ma"

    def test_big_winner_tier(self):
        """Peak gain >= +75% → trail 20d MA, stop_price is None."""
        pos = _pos(entry=100.0, current=180.0, peak_price=180.0)
        result = compute_stop_level(pos, self.cfg)
        assert result["tier"] == "big_winner"
        assert result["stop_price"] is None
        assert result["stop_type"] == "trail_20d_ma"

    def test_zero_entry_price_returns_unknown(self):
        pos = _pos(entry=0, current=100.0)
        result = compute_stop_level(pos, self.cfg)
        assert result["tier"] == "unknown"
        assert result["stop_price"] is None

    def test_gain_pct_reflects_current_price(self):
        """gain_pct shows current vs entry, not peak vs entry."""
        pos = _pos(entry=100.0, current=110.0, peak_price=130.0)
        result = compute_stop_level(pos, self.cfg)
        assert result["gain_pct"] == pytest.approx(0.10, abs=0.001)


# ============================================================
# TestWinnerManagement — tier determined by peak_price, not current_price
# ============================================================

class TestWinnerManagement:
    """
    Once a position reaches a gain tier, it stays in that tier
    even if the price pulls back (stop tier uses peak_price).
    """
    cfg = _cfg()

    def test_breakeven_tier_locked_in_after_pullback(self):
        """Peaked at +25%, pulled back to +5% → still breakeven tier."""
        pos = _pos(entry=100.0, current=105.0, peak_price=125.0)
        result = compute_stop_level(pos, self.cfg)
        assert result["tier"] == "breakeven"
        assert result["stop_price"] == pytest.approx(100.0)

    def test_partial_tier_locked_in_after_pullback(self):
        """Peaked at +50%, pulled back to +15% → still partial tier."""
        pos = _pos(entry=100.0, current=115.0, peak_price=150.0)
        result = compute_stop_level(pos, self.cfg)
        assert result["tier"] == "partial"

    def test_big_winner_locked_in_after_pullback(self):
        """Peaked at +80%, pulled back to +20% → still big_winner tier."""
        pos = _pos(entry=100.0, current=120.0, peak_price=180.0)
        result = compute_stop_level(pos, self.cfg)
        assert result["tier"] == "big_winner"

    def test_gain_pct_shows_actual_current_vs_entry(self):
        """gain_pct is always current vs entry (not peak)."""
        pos = _pos(entry=100.0, current=120.0, peak_price=180.0)
        result = compute_stop_level(pos, self.cfg)
        assert result["gain_pct"] == pytest.approx(0.20, abs=0.001)

    def test_exact_tier_boundary_breakeven(self):
        """Exactly at +20% → breakeven tier."""
        pos = _pos(entry=100.0, current=120.0, peak_price=120.0)
        result = compute_stop_level(pos, self.cfg)
        assert result["tier"] == "breakeven"

    def test_exact_tier_boundary_partial(self):
        """Exactly at +40% → partial tier."""
        pos = _pos(entry=100.0, current=140.0, peak_price=140.0)
        result = compute_stop_level(pos, self.cfg)
        assert result["tier"] == "partial"

    def test_exact_tier_boundary_big_winner(self):
        """Exactly at +75% → big_winner tier."""
        pos = _pos(entry=100.0, current=175.0, peak_price=175.0)
        result = compute_stop_level(pos, self.cfg)
        assert result["tier"] == "big_winner"


# ============================================================
# TestPortfolioSummary
# ============================================================

class TestPortfolioSummary:

    def test_empty_portfolio(self):
        state = _state()
        summary = compute_portfolio_summary(state, _cfg())
        assert summary["position_count"] == 0
        assert summary["total_invested"] == 0.0
        assert summary["total_unrealized_pl"] == 0.0
        assert summary["portfolio_value"] == 100_000.0

    def test_single_position_totals(self):
        pos = _pos(entry=100.0, current=110.0, shares=100,
                   market_value=11_000.0, unrealized_pl=1_000.0)
        state = _state(
            positions={"NVDA": pos},
            portfolio_value=101_000.0,
            buying_power=90_000.0,
            cash=90_000.0,
        )
        summary = compute_portfolio_summary(state, _cfg())
        assert summary["position_count"] == 1
        assert summary["total_invested"] == pytest.approx(11_000.0)
        assert summary["total_unrealized_pl"] == pytest.approx(1_000.0)

    def test_multiple_positions_summed(self):
        positions = {
            "NVDA": _pos(ticker="NVDA", market_value=18_000.0, unrealized_pl=800.0),
            "AAPL": _pos(ticker="AAPL", market_value=16_000.0, unrealized_pl=-200.0),
        }
        state = _state(positions=positions, portfolio_value=106_600.0)
        summary = compute_portfolio_summary(state, _cfg())
        assert summary["position_count"] == 2
        assert summary["total_invested"] == pytest.approx(34_000.0)
        assert summary["total_unrealized_pl"] == pytest.approx(600.0)

    def test_total_return_pct(self):
        state = _state(portfolio_value=103_000.0)
        summary = compute_portfolio_summary(state, _cfg(starting_cash=100_000.0))
        assert summary["total_return_pct"] == pytest.approx(0.03, abs=0.0001)

    def test_total_return_negative(self):
        state = _state(portfolio_value=96_000.0)
        summary = compute_portfolio_summary(state, _cfg(starting_cash=100_000.0))
        assert summary["total_return_pct"] == pytest.approx(-0.04, abs=0.0001)

    def test_total_unrealized_plpc(self):
        pos = _pos(market_value=10_000.0, unrealized_pl=1_000.0)
        state = _state(positions={"X": pos})
        summary = compute_portfolio_summary(state, _cfg())
        assert summary["total_unrealized_plpc"] == pytest.approx(0.10, abs=0.001)

    def test_zero_starting_cash_does_not_crash(self):
        state = _state(portfolio_value=0)
        summary = compute_portfolio_summary(state, _cfg(starting_cash=0))
        assert summary["total_return_pct"] == 0.0


# ============================================================
# TestPositionSizing — calculate_position_size from ranking.py
# ============================================================

class TestPositionSizing:

    def test_target_pct_applied(self):
        result = calculate_position_size(
            portfolio_value=100_000.0,
            current_price=100.0,
            target_pct=0.18,
            max_pct=0.25,
        )
        # 18% of 100k = $18,000 before costs
        assert result["target_dollars"] == pytest.approx(18_000.0)

    def test_max_pct_caps_target(self):
        result = calculate_position_size(
            portfolio_value=100_000.0,
            current_price=100.0,
            target_pct=0.30,  # exceeds max_pct
            max_pct=0.25,
        )
        # Capped at 25%
        assert result["target_dollars"] == pytest.approx(25_000.0)

    def test_costs_reduce_notional(self):
        result = calculate_position_size(
            portfolio_value=100_000.0,
            current_price=100.0,
            target_pct=0.18,
            max_pct=0.25,
            transaction_cost_bps=5,
            slippage_bps=10,
        )
        # 15 bps = 0.15% cost → $18,000 * (1 - 0.0015) = $17,973
        assert result["adjusted_dollars"] == pytest.approx(18_000 * (1 - 0.0015), abs=1.0)

    def test_shares_is_whole_number(self):
        result = calculate_position_size(
            portfolio_value=100_000.0,
            current_price=123.45,
            target_pct=0.18,
            max_pct=0.25,
        )
        assert isinstance(result["shares"], int)
        assert result["shares"] >= 0

    def test_zero_price_returns_zeros(self):
        result = calculate_position_size(
            portfolio_value=100_000.0,
            current_price=0.0,
            target_pct=0.18,
            max_pct=0.25,
        )
        assert result["shares"] == 0
        assert result["adjusted_dollars"] == 0

    def test_zero_portfolio_returns_zeros(self):
        result = calculate_position_size(
            portfolio_value=0.0,
            current_price=100.0,
            target_pct=0.18,
            max_pct=0.25,
        )
        assert result["shares"] == 0


# ============================================================
# TestFormatPositionsTable
# ============================================================

class TestFormatPositionsTable:

    def test_empty_positions_returns_empty_list(self):
        state = _state()
        rows = format_positions_table(state, _cfg())
        assert rows == []

    def test_row_has_required_fields(self):
        state = _state(positions={"NVDA": _pos()})
        rows = format_positions_table(state, _cfg())
        assert len(rows) == 1
        row = rows[0]
        for field in [
            "ticker", "shares", "entry_price", "current_price",
            "market_value", "unrealized_pl", "unrealized_plpc",
            "holding_days", "stop_price", "stop_type", "gain_tier",
            "gain_pct", "peak_price", "partial_taken",
            "catalyst_score", "catalyst_headline",
        ]:
            assert field in row, f"Missing field: {field}"

    def test_sorted_by_unrealized_pl_descending(self):
        positions = {
            "AAPL": _pos(ticker="AAPL", unrealized_pl=-500.0),
            "NVDA": _pos(ticker="NVDA", unrealized_pl=2000.0),
            "MSFT": _pos(ticker="MSFT", unrealized_pl=500.0),
        }
        state = _state(positions=positions)
        rows = format_positions_table(state, _cfg())
        pls = [r["unrealized_pl"] for r in rows]
        assert pls == sorted(pls, reverse=True)

    def test_stop_fields_propagated_from_compute_stop_level(self):
        pos = _pos(entry=100.0, current=110.0, peak_price=110.0)  # normal tier
        state = _state(positions={"TEST": pos})
        rows = format_positions_table(state, _cfg())
        row = rows[0]
        assert row["gain_tier"] == "normal"
        assert row["stop_price"] == pytest.approx(92.0, abs=0.01)


# ============================================================
# TestLoadRecentOrders
# ============================================================

class TestLoadRecentOrders:

    def test_missing_file_returns_empty(self):
        result = load_recent_orders("/nonexistent/orders_log.csv")
        assert result == []

    def test_loads_up_to_n_rows(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv",
                                         delete=False, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "ticker", "action"])
            writer.writeheader()
            for i in range(15):
                writer.writerow({
                    "timestamp": f"2026-06-02 09:{i:02d}:00",
                    "ticker": "NVDA",
                    "action": "SUBMITTED",
                })
            path = f.name

        try:
            result = load_recent_orders(path, n=5)
            assert len(result) == 5
        finally:
            os.unlink(path)

    def test_sorted_newest_first(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv",
                                         delete=False, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "ticker"])
            writer.writeheader()
            writer.writerow({"timestamp": "2026-06-01 09:00:00", "ticker": "AAPL"})
            writer.writerow({"timestamp": "2026-06-02 09:00:00", "ticker": "NVDA"})
            writer.writerow({"timestamp": "2026-06-03 09:00:00", "ticker": "MSFT"})
            path = f.name

        try:
            result = load_recent_orders(path, n=3)
            assert result[0]["ticker"] == "MSFT"   # newest first
            assert result[-1]["ticker"] == "AAPL"
        finally:
            os.unlink(path)

    def test_empty_file_returns_empty(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv",
                                         delete=False) as f:
            path = f.name

        try:
            result = load_recent_orders(path, n=5)
            assert result == []
        finally:
            os.unlink(path)


# ============================================================
# TestCheckPositionsForExits
# ============================================================

class TestCheckPositionsForExits:

    def test_no_exits_for_healthy_position(self):
        state = _state(positions={"NVDA": _pos(entry=100.0, current=115.0,
                                               holding_days=5)})
        exits = check_positions_for_exits(state, _cfg())
        assert exits == []

    def test_hard_stop_loss_triggered(self):
        """Current price at or below stop → EXIT."""
        state = _state(positions={"NVDA": _pos(entry=100.0, current=91.0,
                                               peak_price=91.0)})
        exits = check_positions_for_exits(state, _cfg())
        assert len(exits) == 1
        assert exits[0]["ticker"] == "NVDA"
        assert "stop_loss" in exits[0]["reason"]

    def test_stop_loss_boundary_not_triggered_above(self):
        """Just above stop price → no exit."""
        state = _state(positions={"NVDA": _pos(entry=100.0, current=92.1,
                                               peak_price=92.1)})
        exits = check_positions_for_exits(state, _cfg())
        assert exits == []

    def test_breakeven_stop_triggered_after_peak(self):
        """Peak was +25%, price fell below entry → breakeven stop."""
        state = _state(positions={"NVDA": _pos(entry=100.0, current=98.0,
                                               peak_price=125.0)})
        exits = check_positions_for_exits(state, _cfg())
        assert len(exits) == 1
        assert exits[0]["reason"] == "breakeven_stop"

    def test_breakeven_stop_not_triggered_below_tier(self):
        """Peak was +15% (below +20% tier) → no breakeven stop even if below entry."""
        state = _state(positions={"NVDA": _pos(entry=100.0, current=98.0,
                                               peak_price=115.0)})
        exits = check_positions_for_exits(state, _cfg())
        # Only hard stop would trigger; current=98 > stop=92 → no exit
        assert exits == []

    def test_time_limit_triggered(self):
        """Holding for max_holding_days → EXIT."""
        state = _state(positions={"NVDA": _pos(entry=100.0, current=105.0,
                                               holding_days=20)})
        exits = check_positions_for_exits(state, _cfg())
        assert len(exits) == 1
        assert "max_holding_days" in exits[0]["reason"]

    def test_time_limit_not_triggered_before(self):
        state = _state(positions={"NVDA": _pos(entry=100.0, current=105.0,
                                               holding_days=19)})
        exits = check_positions_for_exits(state, _cfg())
        assert exits == []

    def test_multiple_positions_checked_independently(self):
        positions = {
            "NVDA": _pos(ticker="NVDA", entry=100.0, current=115.0,
                         peak_price=115.0, holding_days=5),
            "AAPL": _pos(ticker="AAPL", entry=100.0, current=91.0,
                         peak_price=91.0, holding_days=3),
        }
        state = _state(positions=positions)
        exits = check_positions_for_exits(state, _cfg())
        assert len(exits) == 1
        assert exits[0]["ticker"] == "AAPL"

    def test_skips_positions_with_zero_prices(self):
        state = _state(positions={"BAD": {
            "entry_price": 0, "current_price": 0, "holding_days": 0,
            "peak_price": 0,
        }})
        exits = check_positions_for_exits(state, _cfg())
        assert exits == []


# ============================================================
# TestReconcileWithPortfolio
# ============================================================

class TestReconcileWithPortfolio:

    def _mock_config(self, tmp_path):
        cfg = MagicMock()
        cfg.require_paper_mode.return_value = None
        cfg.state_file_path = str(tmp_path / "paper_state.json")
        cfg.order_log_path = str(tmp_path / "orders_log.csv")
        cfg.alpaca_api_key = "test_key"
        cfg.alpaca_secret_key = "test_secret"
        return cfg

    def _write_state(self, path, state):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(state, f)

    @patch("src.paper_trader.AlpacaPaperBroker")
    def test_reconcile_updates_peak_price(self, MockBroker, tmp_path):
        """After reconcile, peak_price is set from current_price."""
        cfg = self._mock_config(tmp_path)

        initial_state = _state(positions={
            "NVDA": _pos(entry=100.0, current=110.0, peak_price=110.0)
        })
        self._write_state(cfg.state_file_path, initial_state)

        broker_instance = _mock_broker(
            positions={
                "NVDA": {
                    "ticker": "NVDA",
                    "qty": 100,
                    "avg_entry_price": 100.0,
                    "market_value": 12_000.0,
                    "unrealized_pl": 2_000.0,
                    "unrealized_plpc": 0.20,
                    "current_price": 120.0,  # higher than peak_price
                }
            }
        )
        MockBroker.from_config.return_value = broker_instance

        state = run_reconcile_with_portfolio(cfg)
        assert state["positions"]["NVDA"]["peak_price"] == pytest.approx(120.0)

    @patch("src.paper_trader.AlpacaPaperBroker")
    def test_reconcile_preserves_peak_when_price_drops(self, MockBroker, tmp_path):
        """peak_price is not lowered when current_price falls."""
        cfg = self._mock_config(tmp_path)

        initial_state = _state(positions={
            "NVDA": _pos(entry=100.0, current=130.0, peak_price=130.0)
        })
        self._write_state(cfg.state_file_path, initial_state)

        broker_instance = _mock_broker(
            positions={
                "NVDA": {
                    "ticker": "NVDA",
                    "qty": 100,
                    "avg_entry_price": 100.0,
                    "market_value": 11_500.0,
                    "unrealized_pl": 1_500.0,
                    "unrealized_plpc": 0.15,
                    "current_price": 115.0,  # lower than peak
                }
            }
        )
        MockBroker.from_config.return_value = broker_instance

        state = run_reconcile_with_portfolio(cfg)
        # peak_price should remain at 130 (not drop to 115)
        assert state["positions"]["NVDA"]["peak_price"] == pytest.approx(130.0)

    @patch("src.paper_trader.AlpacaPaperBroker")
    def test_reconcile_saves_state_to_disk(self, MockBroker, tmp_path):
        """Updated state is written to disk."""
        cfg = self._mock_config(tmp_path)

        initial_state = _state()
        self._write_state(cfg.state_file_path, initial_state)

        broker_instance = _mock_broker(positions={})
        MockBroker.from_config.return_value = broker_instance

        run_reconcile_with_portfolio(cfg)

        assert os.path.exists(cfg.state_file_path)
        with open(cfg.state_file_path) as f:
            saved = json.load(f)
        assert "last_updated" in saved

    @patch("src.paper_trader.AlpacaPaperBroker")
    def test_reconcile_requires_paper_mode(self, MockBroker, tmp_path):
        """Raises if paper mode not correctly configured."""
        cfg = MagicMock()
        cfg.require_paper_mode.side_effect = RuntimeError("Not in paper mode")

        with pytest.raises(RuntimeError, match="Not in paper mode"):
            run_reconcile_with_portfolio(cfg)


# ============================================================
# TestPaperReport
# ============================================================

class TestPaperReport:

    def _mock_config(self, tmp_path):
        cfg = MagicMock()
        cfg.require_paper_mode.return_value = None
        cfg.state_file_path = str(tmp_path / "paper_state.json")
        cfg.order_log_path = str(tmp_path / "orders_log.csv")
        cfg.starting_cash = 100_000.0
        cfg.stop_loss_pct = 0.08
        cfg.breakeven_trigger_pct = 0.20
        cfg.partial_take_profit_trigger_pct = 0.40
        cfg.big_winner_trigger_pct = 0.75
        cfg.alpaca_api_key = "test_key"
        cfg.alpaca_secret_key = "test_secret"
        return cfg

    def _write_state(self, path, state):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(state, f)

    @patch("src.paper_trader.AlpacaPaperBroker")
    def test_report_contains_required_keys(self, MockBroker, tmp_path):
        cfg = self._mock_config(tmp_path)
        self._write_state(cfg.state_file_path, _state())

        broker_instance = _mock_broker(positions={})
        MockBroker.from_config.return_value = broker_instance

        report = run_paper_report(cfg)
        for key in ["summary", "positions", "recent_orders", "account"]:
            assert key in report

    @patch("src.paper_trader.AlpacaPaperBroker")
    def test_report_reflects_alpaca_account(self, MockBroker, tmp_path):
        cfg = self._mock_config(tmp_path)
        self._write_state(cfg.state_file_path, _state())

        broker_instance = _mock_broker(
            portfolio=104_500.0, buying_power=76_000.0, cash=76_000.0,
        )
        MockBroker.from_config.return_value = broker_instance

        report = run_paper_report(cfg)
        assert report["summary"]["portfolio_value"] == pytest.approx(104_500.0)
        assert report["summary"]["buying_power"] == pytest.approx(76_000.0)

    @patch("src.paper_trader.AlpacaPaperBroker")
    def test_report_merges_local_and_alpaca_positions(self, MockBroker, tmp_path):
        """entry_price comes from local state; current_price from Alpaca."""
        cfg = self._mock_config(tmp_path)
        local_state = _state(positions={
            "NVDA": _pos(entry=100.0, current=100.0, shares=100,
                         catalyst_headline="AI demand", catalyst_score=9)
        })
        self._write_state(cfg.state_file_path, local_state)

        broker_instance = _mock_broker(
            positions={
                "NVDA": {
                    "ticker": "NVDA",
                    "qty": 100,
                    "avg_entry_price": 100.0,
                    "market_value": 12_000.0,
                    "unrealized_pl": 2_000.0,
                    "unrealized_plpc": 0.20,
                    "current_price": 120.0,
                }
            }
        )
        MockBroker.from_config.return_value = broker_instance

        report = run_paper_report(cfg)
        assert len(report["positions"]) == 1
        pos_row = report["positions"][0]
        assert pos_row["ticker"] == "NVDA"
        assert pos_row["entry_price"] == pytest.approx(100.0)
        assert pos_row["current_price"] == pytest.approx(120.0)
        assert pos_row["catalyst_headline"] == "AI demand"

    @patch("src.paper_trader.AlpacaPaperBroker")
    def test_report_excludes_closed_positions(self, MockBroker, tmp_path):
        """Positions in local state but NOT in Alpaca are excluded from report."""
        cfg = self._mock_config(tmp_path)
        local_state = _state(positions={
            "NVDA": _pos(ticker="NVDA"),
            "AAPL": _pos(ticker="AAPL"),
        })
        self._write_state(cfg.state_file_path, local_state)

        broker_instance = _mock_broker(
            positions={
                "NVDA": {
                    "ticker": "NVDA", "qty": 100, "avg_entry_price": 100.0,
                    "market_value": 11_000.0, "unrealized_pl": 1_000.0,
                    "unrealized_plpc": 0.10, "current_price": 110.0,
                }
            }  # AAPL not in Alpaca (closed)
        )
        MockBroker.from_config.return_value = broker_instance

        report = run_paper_report(cfg)
        tickers = [r["ticker"] for r in report["positions"]]
        assert "NVDA" in tickers
        assert "AAPL" not in tickers

    @patch("src.paper_trader.AlpacaPaperBroker")
    def test_report_requires_paper_mode(self, MockBroker, tmp_path):
        cfg = MagicMock()
        cfg.require_paper_mode.side_effect = RuntimeError("Not in paper mode")
        with pytest.raises(RuntimeError):
            run_paper_report(cfg)

"""
tests/test_broker_alpaca.py — Tests for broker_alpaca.py and execution_engine.py

All tests run OFFLINE with mocked Alpaca API calls.
No real network requests are made. No real orders are placed.

Covers:
  - AlpacaPaperBroker: init safety, paper=True enforcement, output shapes
  - Pre-order safety checks: each of the 7 buy checks and 2 sell checks
  - Order logging format
  - State file: load, save, empty default
  - Orders-today counter
  - Reconcile: merges Alpaca positions with local state
"""

import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.broker_alpaca import AlpacaPaperBroker
from src.execution_engine import (
    check_buy_preconditions,
    check_sell_preconditions,
    count_orders_today,
    load_state,
    load_universe_tickers,
    log_order,
    save_state,
    reconcile_state,
    execute_buy_signals,
    execute_sell_signals,
)


# ============================================================
# Helpers
# ============================================================

def _today():
    return datetime.now().strftime("%Y-%m-%d")

def _days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def _cfg(**overrides):
    defaults = dict(
        alpaca_paper=True,
        alpaca_api_key="test_key",
        alpaca_secret_key="test_secret",
        paper_only=True,
        allow_live_trading=False,
        min_catalyst_score=8,
        max_positions=8,
        max_orders_per_day=10,
        starting_cash=100_000.0,
        target_position_size=0.18,
        max_position_size=0.25,
        transaction_cost_bps=5,
        slippage_bps=10,
        gap_up_max_pct=0,  # disable live price check in tests
        order_log_path="reports/test_orders_log.csv",
        state_file_path="reports/test_paper_state.json",
        signal_file_path="reports/paper_signals.csv",
        discovered_universe_path="reports/daily_discovered_universe.csv",
        blocked_symbols_path="data/blocked_symbols.csv",
        manual_catalyst_path="data/catalyst_overrides.csv",
    )
    defaults.update(overrides)
    cfg = SimpleNamespace(**defaults)
    cfg.require_paper_mode = lambda: None  # Mock — never raises
    return cfg


def _mock_config_requires_paper_fail():
    """Config that raises RuntimeError on require_paper_mode."""
    cfg = _cfg()
    cfg.require_paper_mode = MagicMock(
        side_effect=RuntimeError("ALPACA_PAPER is not true")
    )
    return cfg


def _mock_client():
    """Return a MagicMock that mimics the TradingClient."""
    client = MagicMock()

    # Account
    account = MagicMock()
    account.portfolio_value = "100000.00"
    account.buying_power = "82000.00"
    account.cash = "82000.00"
    account.status = "AccountStatus.ACTIVE"
    client.get_account.return_value = account

    # Positions
    pos = MagicMock()
    pos.symbol = "NVDA"
    pos.qty = "150"
    pos.avg_entry_price = "120.50"
    pos.market_value = "18075.00"
    pos.unrealized_pl = "75.00"
    pos.unrealized_plpc = "0.00415"
    pos.current_price = "120.75"
    client.get_all_positions.return_value = [pos]

    # Open orders
    client.get_orders.return_value = []

    # Submit order
    order = MagicMock()
    order.id = "test-order-uuid-123"
    order.status = "accepted"
    order.submitted_at = "2026-06-02T09:32:00Z"
    order.qty = "150"
    order.notional = None
    client.submit_order.return_value = order

    # Cancel and close
    client.cancel_orders.return_value = []

    return client


def _good_catalysts(score=9, tradeable=True):
    return {
        "NVDA": [{
            "date": _today(),
            "ticker": "NVDA",
            "catalyst_type": "earnings",
            "headline": "Beat and raise",
            "summary": "AI demand strong",
            "catalyst_score": score,
            "tradeable": tradeable,
            "confidence": "high",
        }]
    }


def _buy_signal(ticker="NVDA", close=120.0, cat_score=9):
    return {
        "signal_type": "BUY",
        "signal_date": _today(),
        "ticker": ticker,
        "close": close,
        "volume_ratio": 3.5,
        "return_20d": 0.20,
        "relative_strength_20d": 0.15,
        "catalyst_score": cat_score,
        "catalyst_type": "earnings",
        "catalyst_headline": "Beat and raise",
        "rank_score": 0.85,
    }


# ============================================================
# TestAlpacaPaperBrokerInit
# ============================================================

class TestAlpacaPaperBrokerInit:

    @patch("src.broker_alpaca.TradingClient")
    def test_creates_client_with_paper_true(self, MockTC):
        AlpacaPaperBroker("key123", "secret456")
        MockTC.assert_called_once_with(
            api_key="key123",
            secret_key="secret456",
            paper=True,
        )

    @patch("src.broker_alpaca.TradingClient")
    def test_paper_true_is_hardcoded(self, MockTC):
        """paper=True must be in every TradingClient call, never False."""
        AlpacaPaperBroker("key", "secret")
        call_kwargs = MockTC.call_args[1]
        assert call_kwargs["paper"] is True

    def test_raises_on_empty_api_key(self):
        with pytest.raises(ValueError, match="ALPACA_API_KEY"):
            AlpacaPaperBroker("", "secret")

    def test_raises_on_empty_secret_key(self):
        with pytest.raises(ValueError, match="ALPACA_SECRET_KEY"):
            AlpacaPaperBroker("key", "")

    @patch("src.broker_alpaca.TradingClient")
    def test_from_config_calls_require_paper_mode(self, MockTC):
        cfg = _cfg()
        cfg.require_paper_mode = MagicMock()
        AlpacaPaperBroker.from_config(cfg)
        cfg.require_paper_mode.assert_called_once()

    def test_from_config_raises_if_paper_mode_invalid(self):
        cfg = _mock_config_requires_paper_fail()
        with pytest.raises(RuntimeError, match="ALPACA_PAPER"):
            AlpacaPaperBroker.from_config(cfg)


# ============================================================
# TestAlpacaPaperBrokerOperations
# ============================================================

class TestAlpacaPaperBrokerOperations:

    def setup_method(self):
        with patch("src.broker_alpaca.TradingClient") as MockTC:
            MockTC.return_value = _mock_client()
            self.broker = AlpacaPaperBroker("key", "secret")
            self.broker._client = _mock_client()

    def test_get_account_returns_floats(self):
        result = self.broker.get_account()
        assert isinstance(result["portfolio_value"], float)
        assert isinstance(result["buying_power"], float)
        assert isinstance(result["cash"], float)
        assert isinstance(result["status"], str)

    def test_get_positions_returns_dict_by_ticker(self):
        result = self.broker.get_positions()
        assert "NVDA" in result
        assert result["NVDA"]["qty"] == 150
        assert isinstance(result["NVDA"]["avg_entry_price"], float)

    def test_get_open_orders_returns_list(self):
        result = self.broker.get_open_orders()
        assert isinstance(result, list)

    def test_submit_buy_uses_buy_side(self):
        from alpaca.trading.enums import OrderSide
        self.broker.submit_buy_order("NVDA", 18000.0)
        call_args = self.broker._client.submit_order.call_args[0][0]
        assert call_args.side == OrderSide.BUY

    def test_submit_sell_uses_sell_side(self):
        from alpaca.trading.enums import OrderSide
        self.broker.submit_sell_order("NVDA", 150)
        call_args = self.broker._client.submit_order.call_args[0][0]
        assert call_args.side == OrderSide.SELL

    def test_submit_buy_never_uses_sell_side(self):
        from alpaca.trading.enums import OrderSide
        self.broker.submit_buy_order("NVDA", 18000.0)
        call_args = self.broker._client.submit_order.call_args[0][0]
        assert call_args.side != OrderSide.SELL

    def test_submit_sell_never_uses_buy_side(self):
        from alpaca.trading.enums import OrderSide
        self.broker.submit_sell_order("NVDA", 150)
        call_args = self.broker._client.submit_order.call_args[0][0]
        assert call_args.side != OrderSide.BUY

    def test_buy_order_result_has_required_fields(self):
        result = self.broker.submit_buy_order("NVDA", 18000.0)
        for key in ("id", "ticker", "side", "notional", "status", "submitted_at"):
            assert key in result
        assert result["side"] == "buy"
        assert result["ticker"] == "NVDA"

    def test_sell_order_result_has_required_fields(self):
        result = self.broker.submit_sell_order("NVDA", 150)
        for key in ("id", "ticker", "side", "qty", "status", "submitted_at"):
            assert key in result
        assert result["side"] == "sell"
        assert result["qty"] == 150

    def test_buy_raises_on_zero_notional(self):
        with pytest.raises(ValueError):
            self.broker.submit_buy_order("NVDA", 0)

    def test_buy_raises_on_negative_notional(self):
        with pytest.raises(ValueError):
            self.broker.submit_buy_order("NVDA", -100)

    def test_sell_raises_on_zero_qty(self):
        with pytest.raises(ValueError):
            self.broker.submit_sell_order("NVDA", 0)

    def test_sell_raises_on_negative_qty(self):
        with pytest.raises(ValueError):
            self.broker.submit_sell_order("NVDA", -5)

    def test_buy_raises_on_empty_ticker(self):
        with pytest.raises(ValueError):
            self.broker.submit_buy_order("", 18000.0)

    def test_sell_raises_on_empty_ticker(self):
        with pytest.raises(ValueError):
            self.broker.submit_sell_order("", 150)


# ============================================================
# TestBuyPreconditions
# ============================================================

class TestBuyPreconditions:
    """Test each of the 7 buy pre-order checks independently."""

    def _check(self, **overrides):
        """Run check_buy_preconditions with sensible defaults, overridable."""
        defaults = dict(
            ticker="NVDA",
            universe_tickers={"NVDA", "AAPL", "MSFT"},
            blocked_symbols=set(),
            catalysts=_good_catalysts(),
            alpaca_positions={},
            alpaca_open_orders=[],
            buying_power=100_000.0,
            orders_today=0,
            config=_cfg(),
        )
        defaults.update(overrides)
        return check_buy_preconditions(**defaults)

    def test_all_checks_pass(self):
        ok, reason = self._check()
        assert ok is True
        assert reason == ""

    def test_not_in_universe_blocked(self):
        ok, reason = self._check(universe_tickers={"AAPL", "MSFT"})  # NVDA not included
        assert ok is False
        assert "not_in_universe" in reason

    def test_empty_universe_passes(self):
        """Empty universe set means universe check is skipped."""
        ok, reason = self._check(universe_tickers=set())
        assert ok is True

    def test_blocked_symbol_rejected(self):
        ok, reason = self._check(blocked_symbols={"NVDA"})
        assert ok is False
        assert "blocked_symbol" in reason

    def test_no_catalyst_rejected(self):
        ok, reason = self._check(catalysts={})
        assert ok is False
        assert "no_valid_catalyst" in reason

    def test_low_catalyst_score_rejected(self):
        ok, reason = self._check(catalysts=_good_catalysts(score=6))
        assert ok is False
        assert "no_valid_catalyst" in reason

    def test_untradeable_catalyst_rejected(self):
        ok, reason = self._check(catalysts=_good_catalysts(tradeable=False))
        assert ok is False
        assert "no_valid_catalyst" in reason

    def test_max_positions_rejected(self):
        positions = {f"TICK{i}": {} for i in range(8)}
        ok, reason = self._check(
            alpaca_positions=positions,
            config=_cfg(max_positions=8),
        )
        assert ok is False
        assert "max_positions" in reason

    def test_just_under_max_positions_passes(self):
        positions = {f"TICK{i}": {} for i in range(7)}
        ok, reason = self._check(
            alpaca_positions=positions,
            config=_cfg(max_positions=8),
        )
        assert ok is True

    def test_max_orders_per_day_rejected(self):
        ok, reason = self._check(
            orders_today=10,
            config=_cfg(max_orders_per_day=10),
        )
        assert ok is False
        assert "max_orders_per_day" in reason

    def test_duplicate_open_order_rejected(self):
        open_orders = [{"ticker": "NVDA", "side": "buy", "status": "new"}]
        ok, reason = self._check(alpaca_open_orders=open_orders)
        assert ok is False
        assert "duplicate_order" in reason

    def test_already_holding_position_rejected(self):
        ok, reason = self._check(alpaca_positions={"NVDA": {"qty": 100}})
        assert ok is False
        assert "already_held" in reason

    def test_insufficient_buying_power_rejected(self):
        ok, reason = self._check(buying_power=100.0)  # Only $100 available
        assert ok is False
        assert "insufficient_buying_power" in reason


# ============================================================
# TestSellPreconditions
# ============================================================

class TestSellPreconditions:

    def test_no_position_rejected(self):
        ok, reason = check_sell_preconditions("NVDA", {}, [])
        assert ok is False
        assert "no_position" in reason

    def test_zero_qty_rejected(self):
        ok, reason = check_sell_preconditions(
            "NVDA", {"NVDA": {"qty": 0}}, []
        )
        assert ok is False
        assert "zero_qty" in reason

    def test_valid_position_passes(self):
        ok, reason = check_sell_preconditions(
            "NVDA", {"NVDA": {"qty": 150}}, []
        )
        assert ok is True
        assert reason == ""

    def test_duplicate_sell_order_rejected(self):
        open_orders = [{"ticker": "NVDA", "side": "sell", "status": "new"}]
        ok, reason = check_sell_preconditions(
            "NVDA", {"NVDA": {"qty": 150}}, open_orders
        )
        assert ok is False
        assert "duplicate_sell_order" in reason

    def test_open_buy_order_does_not_block_sell(self):
        """A pending BUY order for the same ticker should not block a SELL."""
        open_orders = [{"ticker": "NVDA", "side": "buy", "status": "new"}]
        ok, reason = check_sell_preconditions(
            "NVDA", {"NVDA": {"qty": 150}}, open_orders
        )
        assert ok is True


# ============================================================
# TestStateFile
# ============================================================

class TestStateFile:

    def test_empty_state_returned_when_file_missing(self, tmp_path):
        state = load_state(str(tmp_path / "nonexistent.json"))
        assert state["positions"] == {}
        assert "portfolio_value" in state

    def test_save_and_reload_state(self, tmp_path):
        path = str(tmp_path / "state.json")
        state = {
            "positions": {"NVDA": {"ticker": "NVDA", "shares": 150}},
            "portfolio_value": 100000.0,
        }
        save_state(state, path)

        loaded = load_state(path)
        assert "NVDA" in loaded["positions"]
        assert loaded["positions"]["NVDA"]["shares"] == 150

    def test_save_adds_last_updated(self, tmp_path):
        path = str(tmp_path / "state.json")
        state = {"positions": {}}
        save_state(state, path)
        loaded = load_state(path)
        assert "last_updated" in loaded

    def test_corrupt_file_returns_empty_state(self, tmp_path):
        path = str(tmp_path / "corrupt.json")
        with open(path, "w") as f:
            f.write("{ this is not valid json }")
        state = load_state(path)
        assert state["positions"] == {}


# ============================================================
# TestOrderLogging
# ============================================================

class TestOrderLogging:

    def test_log_creates_file(self, tmp_path):
        path = str(tmp_path / "orders.csv")
        log_order(
            log_path=path, ticker="NVDA", signal_type="BUY",
            action="SUBMITTED", amount=18000.0, amount_label="notional",
            order_id="uuid-123", order_status="accepted",
            check_passed=True, fail_reason="",
        )
        import pandas as pd
        df = pd.read_csv(path)
        assert len(df) == 1
        assert df.iloc[0]["ticker"] == "NVDA"
        assert df.iloc[0]["action"] == "SUBMITTED"

    def test_blocked_order_logged_with_reason(self, tmp_path):
        path = str(tmp_path / "orders.csv")
        log_order(
            log_path=path, ticker="SQQQ", signal_type="BUY",
            action="BLOCKED", amount=0, amount_label="notional",
            check_passed=False, fail_reason="blocked_symbol: SQQQ is on blocked list",
        )
        import pandas as pd
        df = pd.read_csv(path)
        assert df.iloc[0]["action"] == "BLOCKED"
        assert "blocked_symbol" in str(df.iloc[0]["fail_reason"])

    def test_multiple_orders_appended(self, tmp_path):
        path = str(tmp_path / "orders.csv")
        for ticker in ["NVDA", "AAPL", "MSFT"]:
            log_order(
                log_path=path, ticker=ticker, signal_type="BUY",
                action="SUBMITTED", amount=18000.0, amount_label="notional",
                check_passed=True,
            )
        import pandas as pd
        df = pd.read_csv(path)
        assert len(df) == 3

    def test_log_row_has_standard_columns(self, tmp_path):
        path = str(tmp_path / "orders.csv")
        log_order(
            log_path=path, ticker="NVDA", signal_type="BUY",
            action="SUBMITTED", amount=18000.0, amount_label="notional",
            check_passed=True,
        )
        import pandas as pd
        df = pd.read_csv(path)
        for col in ("timestamp", "date", "ticker", "signal_type", "action",
                    "notional", "qty", "order_id", "order_status",
                    "checks_passed", "fail_reason"):
            assert col in df.columns


# ============================================================
# TestCountOrdersToday
# ============================================================

class TestCountOrdersToday:

    def test_returns_zero_when_no_log(self, tmp_path):
        count = count_orders_today(str(tmp_path / "nonexistent.csv"))
        assert count == 0

    def test_counts_only_submitted_today(self, tmp_path):
        path = str(tmp_path / "orders.csv")
        import pandas as pd
        today = _today()
        yesterday = _days_ago(1)
        rows = [
            {"date": today, "action": "SUBMITTED", "ticker": "NVDA"},
            {"date": today, "action": "SUBMITTED", "ticker": "AAPL"},
            {"date": today, "action": "BLOCKED", "ticker": "TSLA"},   # Not counted
            {"date": yesterday, "action": "SUBMITTED", "ticker": "MSFT"},  # Not counted
        ]
        pd.DataFrame(rows).to_csv(path, index=False)
        count = count_orders_today(path)
        assert count == 2


# ============================================================
# TestLoadUniverseTickers
# ============================================================

class TestLoadUniverseTickers:

    def test_returns_empty_when_file_missing(self, tmp_path):
        result = load_universe_tickers(str(tmp_path / "nonexistent.csv"))
        assert result == set()

    def test_loads_tickers_from_csv(self, tmp_path):
        import pandas as pd
        path = str(tmp_path / "universe.csv")
        today = _today()
        df = pd.DataFrame([
            {"date": today, "ticker": "NVDA", "passed_filters": True},
            {"date": today, "ticker": "AAPL", "passed_filters": True},
            {"date": today, "ticker": "SQQQ", "passed_filters": False},  # excluded
        ])
        df.to_csv(path, index=False)
        result = load_universe_tickers(path)
        assert "NVDA" in result
        assert "AAPL" in result
        assert "SQQQ" not in result

    def test_excludes_stale_dates(self, tmp_path):
        import pandas as pd
        path = str(tmp_path / "universe.csv")
        yesterday = _days_ago(1)
        df = pd.DataFrame([
            {"date": yesterday, "ticker": "NVDA", "passed_filters": True},
        ])
        df.to_csv(path, index=False)
        result = load_universe_tickers(path)
        assert "NVDA" not in result

    def test_tickers_uppercase(self, tmp_path):
        import pandas as pd
        path = str(tmp_path / "universe.csv")
        today = _today()
        df = pd.DataFrame([
            {"date": today, "ticker": "nvda", "passed_filters": True},
        ])
        df.to_csv(path, index=False)
        result = load_universe_tickers(path)
        assert "NVDA" in result
        assert "nvda" not in result


# ============================================================
# TestExecuteBuySignals (with mocked broker)
# ============================================================

class TestExecuteBuySignals:

    def _mock_broker(self):
        broker = MagicMock()
        broker.submit_buy_order.return_value = {
            "id": "uuid-123", "ticker": "NVDA", "side": "buy",
            "notional": 18000.0, "qty": None,
            "status": "accepted", "submitted_at": "2026-06-02T09:32:00Z",
        }
        return broker

    def test_submits_buy_when_all_checks_pass(self, tmp_path):
        broker = self._mock_broker()
        cfg = _cfg(order_log_path=str(tmp_path / "log.csv"))
        state = {"positions": {}}
        account = {"buying_power": 100_000.0, "portfolio_value": 100_000.0}

        submitted, count = execute_buy_signals(
            buy_signals=[_buy_signal()],
            broker=broker,
            config=cfg,
            state=state,
            account=account,
            alpaca_positions={},
            alpaca_open_orders=[],
            universe_tickers={"NVDA"},
            blocked_symbols=set(),
            catalysts=_good_catalysts(),
            orders_today=0,
        )
        assert count == 1
        assert len(submitted) == 1
        broker.submit_buy_order.assert_called_once()

    def test_blocks_buy_when_symbol_not_in_universe(self, tmp_path):
        broker = self._mock_broker()
        cfg = _cfg(order_log_path=str(tmp_path / "log.csv"))
        state = {"positions": {}}
        account = {"buying_power": 100_000.0, "portfolio_value": 100_000.0}

        submitted, count = execute_buy_signals(
            buy_signals=[_buy_signal()],
            broker=broker, config=cfg, state=state,
            account=account, alpaca_positions={}, alpaca_open_orders=[],
            universe_tickers={"AAPL"},  # NVDA not in universe
            blocked_symbols=set(), catalysts=_good_catalysts(), orders_today=0,
        )
        assert count == 0
        broker.submit_buy_order.assert_not_called()

    def test_adds_position_to_state_after_buy(self, tmp_path):
        broker = self._mock_broker()
        cfg = _cfg(order_log_path=str(tmp_path / "log.csv"))
        state = {"positions": {}}
        account = {"buying_power": 100_000.0, "portfolio_value": 100_000.0}

        execute_buy_signals(
            buy_signals=[_buy_signal()],
            broker=broker, config=cfg, state=state,
            account=account, alpaca_positions={}, alpaca_open_orders=[],
            universe_tickers={"NVDA"}, blocked_symbols=set(),
            catalysts=_good_catalysts(), orders_today=0,
        )
        assert "NVDA" in state["positions"]


# ============================================================
# TestExecuteSellSignals (with mocked broker)
# ============================================================

class TestExecuteSellSignals:

    def _mock_broker(self):
        broker = MagicMock()
        broker.submit_sell_order.return_value = {
            "id": "uuid-456", "ticker": "NVDA", "side": "sell",
            "qty": 150, "notional": None,
            "status": "accepted", "submitted_at": "2026-06-02T09:32:00Z",
        }
        return broker

    def _exit_signal(self, ticker="NVDA", signal_type="EXIT", partial_fraction=1.0):
        return {
            "ticker": ticker, "signal_type": signal_type,
            "reason": "stop_loss_8%", "partial_fraction": partial_fraction,
        }

    def test_submits_sell_when_position_exists(self, tmp_path):
        broker = self._mock_broker()
        cfg = _cfg(order_log_path=str(tmp_path / "log.csv"))
        state = {"positions": {"NVDA": {"ticker": "NVDA", "shares": 150}}}
        alpaca_positions = {"NVDA": {"qty": 150}}

        submitted = execute_sell_signals(
            exit_signals=[self._exit_signal()],
            broker=broker, config=cfg, state=state,
            alpaca_positions=alpaca_positions, alpaca_open_orders=[],
        )
        assert len(submitted) == 1
        broker.submit_sell_order.assert_called_once_with("NVDA", 150)

    def test_blocks_sell_when_no_position(self, tmp_path):
        broker = self._mock_broker()
        cfg = _cfg(order_log_path=str(tmp_path / "log.csv"))
        state = {"positions": {}}

        submitted = execute_sell_signals(
            exit_signals=[self._exit_signal()],
            broker=broker, config=cfg, state=state,
            alpaca_positions={}, alpaca_open_orders=[],
        )
        assert len(submitted) == 0
        broker.submit_sell_order.assert_not_called()

    def test_partial_sell_uses_fraction_of_qty(self, tmp_path):
        broker = self._mock_broker()
        cfg = _cfg(order_log_path=str(tmp_path / "log.csv"))
        state = {"positions": {"NVDA": {"shares": 150, "partial_taken": False}}}
        alpaca_positions = {"NVDA": {"qty": 150}}

        execute_sell_signals(
            exit_signals=[self._exit_signal(signal_type="EXIT_PARTIAL", partial_fraction=1/3)],
            broker=broker, config=cfg, state=state,
            alpaca_positions=alpaca_positions, alpaca_open_orders=[],
        )
        # Should sell 1/3 of 150 = 50 shares
        broker.submit_sell_order.assert_called_once_with("NVDA", 50)

    def test_full_exit_removes_position_from_state(self, tmp_path):
        broker = self._mock_broker()
        cfg = _cfg(order_log_path=str(tmp_path / "log.csv"))
        state = {"positions": {"NVDA": {"shares": 150}}}
        alpaca_positions = {"NVDA": {"qty": 150}}

        execute_sell_signals(
            exit_signals=[self._exit_signal(signal_type="EXIT")],
            broker=broker, config=cfg, state=state,
            alpaca_positions=alpaca_positions, alpaca_open_orders=[],
        )
        assert "NVDA" not in state["positions"]

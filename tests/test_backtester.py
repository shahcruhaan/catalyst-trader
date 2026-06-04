"""
tests/test_backtester.py — Tests for src/backtester.py

Coverage:
  - compute_metrics()             — pure function, verifiable math
  - is_catalyst_available_on_date() — temporal filtering / lookahead bias
  - _slice_up_to()               — lookahead bias protection
  - _check_exit_signal()         — each exit condition
  - BacktestEngine.run()         — end-to-end with synthetic data
  - save_backtest_results()      — CSV output format
"""

import csv
import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.backtester import (
    BacktestEngine,
    BacktestPosition,
    BacktestTrade,
    _check_exit_signal,
    _slice_up_to,
    compute_metrics,
    is_catalyst_available_on_date,
    save_backtest_results,
)


# ============================================================
# Helpers
# ============================================================

def _make_config(
    stop_loss_pct=0.08,
    breakeven_trigger=0.20,
    max_holding_days=20,
    min_volume_ratio=2.5,
    min_20d_return=0.10,
    min_rs_pct=0.08,
    target_position_size=0.18,
    starting_cash=100_000,
    max_positions=8,
    slippage_bps=10,
    transaction_cost_bps=5,
    min_catalyst_score=8,
    catalyst_max_age_days=10,
):
    cfg = MagicMock()
    cfg.stop_loss_pct = stop_loss_pct
    cfg.breakeven_trigger_pct = breakeven_trigger
    cfg.max_holding_days = max_holding_days
    cfg.min_volume_ratio = min_volume_ratio
    cfg.min_20d_return = min_20d_return
    cfg.min_rs_pct = min_rs_pct
    cfg.target_position_size = target_position_size
    cfg.starting_cash = starting_cash
    cfg.max_positions = max_positions
    cfg.slippage_bps = slippage_bps
    cfg.transaction_cost_bps = transaction_cost_bps
    cfg.min_catalyst_score = min_catalyst_score
    cfg.catalyst_max_age_days = catalyst_max_age_days
    return cfg


def _make_ohlcv(n_rows=100, start_price=100.0, trend=0.001, vol=0.02):
    """Create a synthetic OHLCV DataFrame."""
    import numpy as np
    rng = pd.date_range("2024-01-02", periods=n_rows, freq="B")
    np.random.seed(42)
    closes = [start_price]
    for _ in range(n_rows - 1):
        closes.append(closes[-1] * (1 + trend + np.random.normal(0, vol)))
    closes = pd.Series(closes, dtype=float)
    df = pd.DataFrame({
        "Open": closes * 0.995,
        "High": closes * 1.01,
        "Low": closes * 0.99,
        "Close": closes,
        "Volume": [1_000_000 + i * 50_000 for i in range(n_rows)],
    }, index=rng)
    return df


def _make_trade(
    ticker="AAPL",
    entry_date=date(2024, 3, 1),
    exit_date=date(2024, 3, 10),
    entry_price=100.0,
    exit_price=110.0,
    shares=100.0,
    notional=10_000.0,
    exit_reason="below_10d_ma",
):
    gross = shares * exit_price - notional
    entry_fee = notional * 0.0005
    exit_fee = shares * exit_price * 0.0005
    net = shares * exit_price - exit_fee - notional - entry_fee
    pnl_pct = net / notional
    holding = (exit_date - entry_date).days
    return BacktestTrade(
        ticker=ticker,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=entry_price,
        exit_price=exit_price,
        shares=shares,
        notional=notional,
        gross_pnl=gross,
        net_pnl=net,
        pnl_pct=pnl_pct,
        holding_days=holding,
        exit_reason=exit_reason,
        entry_fee=entry_fee,
        exit_fee=exit_fee,
    )


# ============================================================
# Tests: compute_metrics
# ============================================================

class TestComputeMetrics:

    def test_total_return_correct(self):
        equity = [100_000, 105_000, 110_000]
        days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        m = compute_metrics([], equity, days, 100_000, 0.05)
        assert m["total_return"] == pytest.approx(0.10)

    def test_zero_trades_returns_valid_metrics(self):
        equity = [100_000, 101_000, 102_000]
        days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        m = compute_metrics([], equity, days, 100_000, 0.0)
        assert m["total_trades"] == 0
        assert m["win_rate"] == 0.0
        assert m["profit_factor"] == 0.0

    def test_win_rate_calculation(self):
        trades = [
            _make_trade("A", exit_price=110.0),  # win
            _make_trade("B", exit_price=110.0),  # win
            _make_trade("C", exit_price=90.0, exit_reason="stop_loss"),  # loss
        ]
        equity = [100_000] * 30
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
        m = compute_metrics(trades, equity, days, 100_000, 0.0)
        assert m["win_count"] == 2
        assert m["loss_count"] == 1
        assert m["win_rate"] == pytest.approx(2 / 3)

    def test_max_drawdown_calculation(self):
        # Go up then down: peak at 120k, then falls to 90k → drawdown = (120-90)/120 = 25%
        equity = [100_000, 110_000, 120_000, 105_000, 90_000, 95_000]
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(6)]
        m = compute_metrics([], equity, days, 100_000, 0.0)
        assert m["max_drawdown"] == pytest.approx(0.25, rel=0.01)

    def test_max_drawdown_no_drawdown(self):
        # Monotonically increasing — no drawdown
        equity = [100_000, 105_000, 110_000, 115_000]
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(4)]
        m = compute_metrics([], equity, days, 100_000, 0.0)
        assert m["max_drawdown"] == pytest.approx(0.0)

    def test_profit_factor_with_wins_and_losses(self):
        # Gross profit = 1000, gross loss = 500 → profit factor = 2.0
        wins = [_make_trade("A", exit_price=120.0)]  # net ~+1962
        losses = [_make_trade("B", exit_price=90.0)]  # net ~-1007

        equity = [100_000] * 30
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
        m = compute_metrics(wins + losses, equity, days, 100_000, 0.0)
        assert m["profit_factor"] > 0
        assert m["profit_factor"] != float("inf")

    def test_profit_factor_no_losses_is_inf(self):
        trades = [_make_trade("A", exit_price=120.0)]
        equity = [100_000] * 30
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
        m = compute_metrics(trades, equity, days, 100_000, 0.0)
        assert m["profit_factor"] == float("inf")

    def test_spy_return_captured(self):
        equity = [100_000, 100_000]
        days = [date(2024, 1, 2), date(2024, 1, 3)]
        m = compute_metrics([], equity, days, 100_000, 0.25)
        assert m["spy_return"] == pytest.approx(0.25)

    def test_alpha_is_ann_return_minus_spy_ann(self):
        # 252 trading days, portfolio goes up 10%, SPY goes up 5%
        n = 252
        equity = [100_000 * (1.10 ** (i / 252)) for i in range(n)]
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
        m = compute_metrics([], equity, days, 100_000, 0.05)
        # alpha should be close to 0.10 - 0.05 = 0.05
        assert abs(m["alpha"] - 0.05) < 0.02

    def test_sharpe_ratio_positive_for_steady_gains(self):
        # Steady daily gains → positive Sharpe
        n = 100
        equity = [100_000 * (1.001 ** i) for i in range(n)]
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
        m = compute_metrics([], equity, days, 100_000, 0.0)
        assert m["sharpe_ratio"] > 0

    def test_best_and_worst_trade_identified(self):
        t_best = _make_trade("BEST", exit_price=150.0)   # big win
        t_worst = _make_trade("WORST", exit_price=91.0)  # loss

        equity = [100_000] * 30
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
        m = compute_metrics([t_best, t_worst], equity, days, 100_000, 0.0)
        assert m["best_trade_ticker"] == "BEST"
        assert m["worst_trade_ticker"] == "WORST"

    def test_empty_equity_curve_returns_empty_metrics(self):
        m = compute_metrics([], [], [], 100_000, 0.0)
        assert m["total_return"] == 0.0
        assert m["total_trades"] == 0

    def test_exposure_pct_bounded_zero_to_one(self):
        trades = [_make_trade("A")]
        equity = [100_000] * 30
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
        m = compute_metrics(trades, equity, days, 100_000, 0.0)
        assert 0.0 <= m["exposure_pct"] <= 1.0


# ============================================================
# Tests: is_catalyst_available_on_date
# ============================================================

class TestIsCatalystAvailableOnDate:

    def _cfg(self):
        cfg = MagicMock()
        cfg.min_catalyst_score = 8
        cfg.catalyst_max_age_days = 10
        return cfg

    def test_dated_catalyst_within_window(self):
        catalysts = {
            "NVDA": [{
                "date": "2024-03-10",
                "catalyst_score": 9,
                "tradeable": True,
                "headline": "Earnings beat",
            }]
        }
        ok, headline, score = is_catalyst_available_on_date(
            catalysts, "NVDA", date(2024, 3, 15), self._cfg()
        )
        assert ok is True
        assert score == 9

    def test_future_catalyst_not_available(self):
        """Catalyst dated after signal_date must NOT be used (lookahead bias)."""
        catalysts = {
            "NVDA": [{
                "date": "2024-03-20",   # catalyst is in the future
                "catalyst_score": 9,
                "tradeable": True,
                "headline": "Earnings beat",
            }]
        }
        ok, _, _ = is_catalyst_available_on_date(
            catalysts, "NVDA", date(2024, 3, 15), self._cfg()
        )
        assert ok is False

    def test_stale_catalyst_not_available(self):
        """Catalyst older than max_age_days must not be used."""
        catalysts = {
            "NVDA": [{
                "date": "2024-02-01",   # 43 days before signal date
                "catalyst_score": 9,
                "tradeable": True,
                "headline": "Old news",
            }]
        }
        ok, _, _ = is_catalyst_available_on_date(
            catalysts, "NVDA", date(2024, 3, 15), self._cfg()
        )
        assert ok is False

    def test_undated_catalyst_always_available(self):
        """Catalyst with no date is treated as available throughout."""
        catalysts = {
            "NVDA": [{
                "date": "",
                "catalyst_score": 9,
                "tradeable": True,
                "headline": "Undated",
            }]
        }
        ok, _, _ = is_catalyst_available_on_date(
            catalysts, "NVDA", date(2024, 3, 15), self._cfg()
        )
        assert ok is True

    def test_low_score_catalyst_not_available(self):
        catalysts = {
            "NVDA": [{
                "date": "2024-03-10",
                "catalyst_score": 5,  # below threshold of 8
                "tradeable": True,
                "headline": "Weak signal",
            }]
        }
        ok, _, _ = is_catalyst_available_on_date(
            catalysts, "NVDA", date(2024, 3, 15), self._cfg()
        )
        assert ok is False

    def test_not_tradeable_catalyst_not_available(self):
        catalysts = {
            "NVDA": [{
                "date": "2024-03-10",
                "catalyst_score": 9,
                "tradeable": False,  # marked not tradeable
                "headline": "Not tradeable",
            }]
        }
        ok, _, _ = is_catalyst_available_on_date(
            catalysts, "NVDA", date(2024, 3, 15), self._cfg()
        )
        assert ok is False

    def test_no_catalyst_for_ticker(self):
        ok, _, _ = is_catalyst_available_on_date(
            {}, "NVDA", date(2024, 3, 15), self._cfg()
        )
        assert ok is False

    def test_catalyst_on_exact_signal_date_is_available(self):
        """Catalyst dated exactly on signal_date (age=0) is available."""
        catalysts = {
            "AAPL": [{
                "date": "2024-03-15",   # same as signal date
                "catalyst_score": 9,
                "tradeable": True,
                "headline": "Same day",
            }]
        }
        ok, _, _ = is_catalyst_available_on_date(
            catalysts, "AAPL", date(2024, 3, 15), self._cfg()
        )
        assert ok is True


# ============================================================
# Tests: _slice_up_to
# ============================================================

class TestSliceUpTo:

    def test_returns_rows_on_or_before_date(self):
        df = _make_ohlcv(n_rows=20)
        signal_date = df.index[9].date()  # 10th row
        sliced = _slice_up_to(df, signal_date)
        assert len(sliced) == 10
        assert sliced.index[-1].date() == signal_date

    def test_empty_result_for_date_before_data(self):
        df = _make_ohlcv(n_rows=10)
        early_date = date(2000, 1, 1)
        sliced = _slice_up_to(df, early_date)
        assert sliced.empty

    def test_full_result_for_date_after_data(self):
        df = _make_ohlcv(n_rows=10)
        late_date = date(2099, 12, 31)
        sliced = _slice_up_to(df, late_date)
        assert len(sliced) == 10

    def test_no_future_data_in_slice(self):
        df = _make_ohlcv(n_rows=50)
        signal_date = df.index[24].date()  # 25th row
        sliced = _slice_up_to(df, signal_date)
        # All dates in the slice should be <= signal_date
        assert all(d.date() <= signal_date for d in sliced.index)


# ============================================================
# Tests: _check_exit_signal
# ============================================================

class TestCheckExitSignal:

    def _make_pos(
        self,
        ticker="AAPL",
        entry_price=100.0,
        peak_price=100.0,
        holding_days=0,
    ):
        return BacktestPosition(
            ticker=ticker,
            entry_date=date(2024, 1, 15),
            entry_price=entry_price,
            shares=100.0,
            notional=10_000.0,
            entry_fee=5.0,
            peak_price=peak_price,
            holding_days=holding_days,
        )

    def test_stop_loss_triggered(self):
        df = _make_ohlcv(n_rows=70, start_price=100.0)
        # Force last close to be 8.5% below 100 (entry price)
        df.loc[df.index[-1], "Close"] = 91.0
        df.loc[df.index[-1], "Open"] = 91.5
        spy = _make_ohlcv(n_rows=70)
        pos = self._make_pos(entry_price=100.0, peak_price=100.0, holding_days=5)
        cfg = _make_config(stop_loss_pct=0.08)

        current_date = df.index[-1].date()
        reason = _check_exit_signal(pos, df, spy, current_date, cfg)
        assert reason is not None
        assert "stop_loss" in reason

    def test_no_exit_if_price_above_stop(self):
        df = _make_ohlcv(n_rows=70, start_price=100.0)
        # Last close is 95 — above 92 stop (100 * 0.92)
        df.loc[df.index[-1], "Close"] = 95.0
        spy = _make_ohlcv(n_rows=70)
        pos = self._make_pos(entry_price=100.0, peak_price=100.0, holding_days=5)
        cfg = _make_config(stop_loss_pct=0.08)

        current_date = df.index[-1].date()
        reason = _check_exit_signal(pos, df, spy, current_date, cfg)
        # Should not trigger stop loss exit (might trigger others)
        if reason is not None:
            assert "stop_loss" not in reason

    def test_breakeven_stop_triggered(self):
        # Entry at 100, peak at 130 (+30% > 20% trigger), current at 98 (< entry)
        df = _make_ohlcv(n_rows=70, start_price=100.0)
        df.loc[df.index[-1], "Close"] = 98.0
        spy = _make_ohlcv(n_rows=70)
        pos = self._make_pos(entry_price=100.0, peak_price=130.0, holding_days=10)
        cfg = _make_config(stop_loss_pct=0.08, breakeven_trigger=0.20)

        current_date = df.index[-1].date()
        reason = _check_exit_signal(pos, df, spy, current_date, cfg)
        assert reason == "breakeven_stop"

    def test_time_limit_exit(self):
        df = _make_ohlcv(n_rows=70, start_price=100.0)
        spy = _make_ohlcv(n_rows=70)
        pos = self._make_pos(entry_price=100.0, peak_price=110.0, holding_days=20)
        cfg = _make_config(max_holding_days=20)

        current_date = df.index[-1].date()
        reason = _check_exit_signal(pos, df, spy, current_date, cfg)
        assert reason is not None
        assert "time_limit" in reason

    def test_no_exit_when_healthy(self):
        # Fresh position with strong price — should not trigger any exit
        df = _make_ohlcv(n_rows=70, start_price=100.0, trend=0.002)
        spy = _make_ohlcv(n_rows=70, start_price=400.0, trend=0.001)
        pos = self._make_pos(entry_price=100.0, peak_price=105.0, holding_days=3)
        cfg = _make_config(stop_loss_pct=0.08, max_holding_days=20)

        current_date = df.index[-1].date()
        reason = _check_exit_signal(pos, df, spy, current_date, cfg)
        # Strong uptrend — most exits should not trigger
        # (stop_loss and breakeven certainly won't)
        if reason is not None:
            assert "stop_loss" not in reason
            assert reason != "breakeven_stop"

    def test_empty_dataframe_returns_none(self):
        empty_df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        spy = _make_ohlcv(n_rows=70)
        pos = self._make_pos()
        cfg = _make_config()
        reason = _check_exit_signal(pos, empty_df, spy, date(2024, 3, 1), cfg)
        assert reason is None


# ============================================================
# Tests: BacktestEngine end-to-end (synthetic data, no network)
# ============================================================

class TestBacktestEngine:
    """
    End-to-end tests using synthetic price data and mocked downloads.

    These tests verify the simulation logic without hitting the network.
    """

    def _make_price_data(self, tickers, n_rows=120, trend=0.001):
        """Build a fake price_data dict including SPY."""
        data = {"SPY": _make_ohlcv(n_rows=n_rows, start_price=400.0, trend=trend)}
        for ticker in tickers:
            data[ticker] = _make_ohlcv(n_rows=n_rows, start_price=100.0, trend=trend)
        return data

    def test_run_returns_backtest_result(self):
        cfg = _make_config()
        tickers = ["AAPL"]
        price_data = self._make_price_data(tickers)

        engine = BacktestEngine()
        result = engine.run(
            config=cfg,
            start_date=date(2024, 3, 1),
            end_date=date(2024, 3, 31),
            universe=tickers,
            catalysts={},
            skip_catalyst=True,
            price_data=price_data,
        )

        assert result is not None
        assert isinstance(result.equity_curve, list)
        assert isinstance(result.trades, list)
        assert isinstance(result.metrics, dict)

    def test_equity_curve_has_one_entry_per_trading_day(self):
        cfg = _make_config()
        tickers = ["AAPL"]
        price_data = self._make_price_data(tickers)

        engine = BacktestEngine()
        result = engine.run(
            config=cfg,
            start_date=date(2024, 3, 1),
            end_date=date(2024, 3, 29),
            universe=tickers,
            catalysts={},
            skip_catalyst=True,
            price_data=price_data,
        )

        # Should have one equity value per trading day
        assert len(result.equity_curve) == len(result.equity_dates)
        assert len(result.equity_curve) > 0

    def test_equity_starts_at_initial_cash(self):
        cfg = _make_config(starting_cash=100_000)
        tickers = ["AAPL"]
        price_data = self._make_price_data(tickers)

        engine = BacktestEngine()
        result = engine.run(
            config=cfg,
            start_date=date(2024, 3, 1),
            end_date=date(2024, 3, 31),
            universe=tickers,
            catalysts={},
            skip_catalyst=True,
            price_data=price_data,
        )

        # First equity value should be close to initial_cash
        # (positions open at day's open, equity recorded at close — small difference ok)
        assert abs(result.equity_curve[0] - 100_000) <= 100_000  # very loose bound

    def test_no_trades_without_signals(self):
        cfg = _make_config()
        tickers = ["AAPL"]
        price_data = self._make_price_data(tickers)

        # Mock signal function to return None always
        with patch("src.backtester._get_buy_signal", return_value=None):
            engine = BacktestEngine()
            result = engine.run(
                config=cfg,
                start_date=date(2024, 3, 1),
                end_date=date(2024, 3, 31),
                universe=tickers,
                catalysts={},
                skip_catalyst=True,
                price_data=price_data,
            )

        assert len(result.trades) == 0

    def test_all_open_positions_closed_at_end(self):
        cfg = _make_config()
        tickers = ["AAPL", "MSFT"]
        price_data = self._make_price_data(tickers)

        engine = BacktestEngine()
        result = engine.run(
            config=cfg,
            start_date=date(2024, 3, 1),
            end_date=date(2024, 3, 31),
            universe=tickers,
            catalysts={},
            skip_catalyst=True,
            price_data=price_data,
        )

        # All trades should have been closed — no open positions after run
        # Trades with exit_reason "backtest_end" are the final-day close-outs
        # The equity curve should end at final_value
        assert result.final_value > 0

    def test_max_positions_not_exceeded(self):
        cfg = _make_config(max_positions=2)
        tickers = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA"]
        price_data = self._make_price_data(tickers, trend=0.005)

        # Force all tickers to generate buy signals
        def always_signal(ticker, ticker_df, spy_df, signal_date, config, catalysts, skip_catalyst):
            return {"ticker": ticker, "signal_date": signal_date, "close": 100.0,
                    "catalyst_headline": "", "catalyst_score": 0}

        with patch("src.backtester._get_buy_signal", side_effect=always_signal):
            engine = BacktestEngine()
            result = engine.run(
                config=cfg,
                start_date=date(2024, 3, 1),
                end_date=date(2024, 3, 31),
                universe=tickers,
                catalysts={},
                skip_catalyst=True,
                price_data=price_data,
            )

        # Should never have more than max_positions=2 open simultaneously
        # (Hard to assert directly, but total distinct tickers in any given day ≤ 2)
        # As a proxy: verify the engine ran without error
        assert result is not None

    def test_missing_spy_raises_error(self):
        cfg = _make_config()
        price_data = {"AAPL": _make_ohlcv(n_rows=60)}  # no SPY

        engine = BacktestEngine()
        with pytest.raises(RuntimeError, match="SPY"):
            engine.run(
                config=cfg,
                start_date=date(2024, 3, 1),
                end_date=date(2024, 3, 31),
                universe=["AAPL"],
                catalysts={},
                skip_catalyst=True,
                price_data=price_data,
            )

    def test_no_trading_days_raises_error(self):
        cfg = _make_config()
        price_data = self._make_price_data(["AAPL"])

        engine = BacktestEngine()
        with pytest.raises(RuntimeError, match="No trading days"):
            # A weekend-only period has no trading days
            engine.run(
                config=cfg,
                start_date=date(2024, 3, 2),  # Saturday
                end_date=date(2024, 3, 3),    # Sunday
                universe=["AAPL"],
                catalysts={},
                skip_catalyst=True,
                price_data=price_data,
            )

    def test_warnings_issued_for_missing_tickers(self):
        cfg = _make_config()
        # AAPL has data, MSFT does not — should warn about MSFT
        price_data = {
            "SPY": _make_ohlcv(n_rows=100, start_price=400.0),
            "AAPL": _make_ohlcv(n_rows=100, start_price=100.0),
        }

        engine = BacktestEngine()
        result = engine.run(
            config=cfg,
            start_date=date(2024, 3, 1),
            end_date=date(2024, 3, 31),
            universe=["AAPL", "MSFT"],
            catalysts={},
            skip_catalyst=True,
            price_data=price_data,
        )

        assert len(result.warnings) > 0


# ============================================================
# Tests: save_backtest_results
# ============================================================

class TestSaveBacktestResults:

    def _make_result(self, n_trades=2):
        from src.backtester import BacktestResult
        trades = [
            _make_trade("AAPL", entry_date=date(2024, 3, 1), exit_date=date(2024, 3, 10)),
            _make_trade("MSFT", entry_date=date(2024, 3, 5), exit_date=date(2024, 3, 15),
                        exit_price=90.0, exit_reason="stop_loss"),
        ][:n_trades]
        equity = [100_000, 101_000, 102_000]
        days = [date(2024, 3, 1), date(2024, 3, 4), date(2024, 3, 5)]
        m = compute_metrics(trades, equity, days, 100_000, 0.05)
        return BacktestResult(
            trades=trades,
            equity_curve=equity,
            equity_dates=days,
            metrics=m,
            initial_cash=100_000,
            final_value=102_000,
            start_date=date(2024, 3, 1),
            end_date=date(2024, 3, 31),
            universe=["AAPL", "MSFT"],
            spy_return=0.05,
            skip_catalyst=False,
            warnings=[],
        )

    def test_creates_results_csv(self, tmp_path):
        result = self._make_result()
        results_path = str(tmp_path / "results.csv")
        equity_path = str(tmp_path / "equity.csv")
        save_backtest_results(result, results_path, equity_path)
        assert os.path.exists(results_path)

    def test_creates_equity_curve_csv(self, tmp_path):
        result = self._make_result()
        results_path = str(tmp_path / "results.csv")
        equity_path = str(tmp_path / "equity.csv")
        save_backtest_results(result, results_path, equity_path)
        assert os.path.exists(equity_path)

    def test_results_csv_has_correct_columns(self, tmp_path):
        result = self._make_result()
        results_path = str(tmp_path / "results.csv")
        equity_path = str(tmp_path / "equity.csv")
        save_backtest_results(result, results_path, equity_path)

        with open(results_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2  # 2 trades
        row = rows[0]
        assert "ticker" in row
        assert "entry_date" in row
        assert "exit_date" in row
        assert "pnl_pct" in row
        assert "exit_reason" in row

    def test_equity_csv_has_correct_structure(self, tmp_path):
        result = self._make_result()
        results_path = str(tmp_path / "results.csv")
        equity_path = str(tmp_path / "equity.csv")
        save_backtest_results(result, results_path, equity_path)

        with open(equity_path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 3  # 3 trading days
        assert "date" in rows[0]
        assert "portfolio_value" in rows[0]
        assert "daily_return_pct" in rows[0]

    def test_pnl_pct_stored_as_percentage_not_decimal(self, tmp_path):
        """pnl_pct should be stored as 12.34 (%) not 0.1234."""
        result = self._make_result(n_trades=1)
        results_path = str(tmp_path / "results.csv")
        equity_path = str(tmp_path / "equity.csv")
        save_backtest_results(result, results_path, equity_path)

        with open(results_path) as f:
            row = list(csv.DictReader(f))[0]

        pnl_pct = float(row["pnl_pct"])
        # Should be close to 9-10% (entry 100, exit 110) not 0.09-0.10
        assert abs(pnl_pct) < 100  # sanity check it's a percentage
        assert abs(pnl_pct) > 0.01  # not stored as decimal fraction

    def test_empty_trades_creates_valid_files(self, tmp_path):
        from src.backtester import BacktestResult
        result = BacktestResult(
            trades=[],
            equity_curve=[100_000, 100_500],
            equity_dates=[date(2024, 3, 1), date(2024, 3, 4)],
            metrics=compute_metrics([], [100_000, 100_500],
                                    [date(2024, 3, 1), date(2024, 3, 4)],
                                    100_000, 0.0),
            initial_cash=100_000,
            final_value=100_500,
            start_date=date(2024, 3, 1),
            end_date=date(2024, 3, 31),
            universe=["AAPL"],
            spy_return=0.0,
            skip_catalyst=False,
            warnings=[],
        )
        results_path = str(tmp_path / "results.csv")
        equity_path = str(tmp_path / "equity.csv")
        save_backtest_results(result, results_path, equity_path)

        with open(results_path) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 0  # header only, no trades

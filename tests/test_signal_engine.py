"""
tests/test_signal_engine.py — Tests for src/signal_engine.py

Covers:
  - evaluate_buy_signal: all 6 buy conditions, review routing
  - evaluate_exit_signal: all 5 exit conditions, winner management
  - run_signal_scan: integration-level scan behavior
  - save_needs_review / save_signals_csv: report writers
"""

import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.signal_engine import (
    evaluate_buy_signal,
    evaluate_exit_signal,
    run_signal_scan,
    save_needs_review,
    save_signals_csv,
)


# ============================================================
# Helpers
# ============================================================

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def _cfg(**overrides):
    """Return a minimal config-like SimpleNamespace."""
    defaults = dict(
        min_volume_ratio=2.5,
        min_20d_return=0.10,
        min_relative_strength_20d=0.08,
        min_catalyst_score=8,
        stop_loss_pct=0.08,
        breakeven_trigger_pct=0.20,
        partial_take_profit_trigger_pct=0.40,
        big_winner_trigger_pct=0.75,
        max_holding_days=20,
        exit_ma_fast=10,
        exit_ma_slow=20,
        rs_weakness_days=5,
        stall_days=5,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _ind(
    close=120.0, volume_ratio=3.5, return_20d=0.20, rs_20d=0.15,
    above_high=True, above_ma=True,
    below_ma=False, rs_weak=False, vol_stall=False,
    ma_10=118.0, ma_20=115.0, enough_data=True, error=None,
):
    return {
        "close": close,
        "volume_ratio": volume_ratio,
        "return_20d": return_20d,
        "relative_strength_20d": rs_20d,
        "above_20d_high": above_high,
        "above_50d_ma": above_ma,
        "below_10d_ma": below_ma,
        "rs_weakness_5d": rs_weak,
        "volume_stall_5d": vol_stall,
        "ma_10": ma_10,
        "ma_20": ma_20,
        "enough_data": enough_data,
        "error": error,
    }


def _cats(score=9, tradeable=True):
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


def _pos(entry=100.0, days=5, partial_taken=False):
    return {"entry_price": entry, "holding_days": days, "shares": 100,
            "partial_taken": partial_taken}


def _make_ohlcv(n=60, close_start=100.0, close_end=125.0,
                volume=4_000_000, volume_today=14_000_000):
    dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")
    closes = np.linspace(close_start, close_end, n)
    vols = [volume] * (n - 1) + [volume_today]
    return pd.DataFrame({
        "Open": closes * 0.99, "High": closes * 1.01,
        "Low": closes * 0.98, "Close": closes, "Volume": vols,
    }, index=dates)


# ============================================================
# TestEvaluateBuySignal
# ============================================================

class TestEvaluateBuySignal:

    def test_all_conditions_pass_generates_buy(self):
        buy, review = evaluate_buy_signal("NVDA", _ind(), _cats(), _cfg())
        assert buy is not None
        assert review is None
        assert buy["signal_type"] == "BUY"
        assert buy["ticker"] == "NVDA"

    def test_no_breakout_fails_technical(self):
        buy, review = evaluate_buy_signal("NVDA", _ind(above_high=False), _cats(), _cfg())
        assert buy is None
        assert review is None  # Never reaches catalyst step

    def test_below_50d_ma_fails(self):
        buy, review = evaluate_buy_signal("NVDA", _ind(above_ma=False), _cats(), _cfg())
        assert buy is None
        assert review is None

    def test_low_volume_ratio_fails(self):
        buy, review = evaluate_buy_signal("NVDA", _ind(volume_ratio=1.5), _cats(), _cfg())
        assert buy is None
        assert review is None

    def test_low_momentum_fails(self):
        buy, review = evaluate_buy_signal("NVDA", _ind(return_20d=0.05), _cats(), _cfg())
        assert buy is None
        assert review is None

    def test_low_relative_strength_fails(self):
        buy, review = evaluate_buy_signal("NVDA", _ind(rs_20d=0.03), _cats(), _cfg())
        assert buy is None
        assert review is None

    def test_no_catalyst_routes_to_review(self):
        buy, review = evaluate_buy_signal("NVDA", _ind(), {}, _cfg())
        assert buy is None
        assert review is not None
        assert review["ticker"] == "NVDA"
        assert "catalyst" in review["note"].lower()

    def test_low_catalyst_score_routes_to_review(self):
        buy, review = evaluate_buy_signal("NVDA", _ind(), _cats(score=6), _cfg())
        assert buy is None
        assert review is not None

    def test_untradeable_catalyst_routes_to_review(self):
        buy, review = evaluate_buy_signal("NVDA", _ind(), _cats(tradeable=False), _cfg())
        assert buy is None
        assert review is not None

    def test_not_enough_data_returns_none_none(self):
        buy, review = evaluate_buy_signal("NVDA", _ind(enough_data=False), _cats(), _cfg())
        assert buy is None
        assert review is None

    def test_error_in_indicators_returns_none_none(self):
        buy, review = evaluate_buy_signal("NVDA", _ind(error="bad data"), _cats(), _cfg())
        assert buy is None
        assert review is None

    def test_buy_signal_has_correct_fields(self):
        buy, _ = evaluate_buy_signal("NVDA", _ind(), _cats(), _cfg())
        assert buy["catalyst_score"] == 9
        assert buy["catalyst_type"] == "earnings"
        assert "Beat" in buy["catalyst_headline"]
        assert buy["signal_date"] == _today()

    def test_volume_ratio_at_exact_minimum_passes(self):
        buy, _ = evaluate_buy_signal("NVDA", _ind(volume_ratio=2.5), _cats(), _cfg())
        assert buy is not None

    def test_volume_ratio_just_below_minimum_fails(self):
        buy, review = evaluate_buy_signal("NVDA", _ind(volume_ratio=2.49), _cats(), _cfg())
        assert buy is None
        assert review is None


# ============================================================
# TestEvaluateExitSignal
# ============================================================

class TestEvaluateExitSignal:

    def test_no_exit_when_position_healthy(self):
        signals = evaluate_exit_signal("NVDA", _pos(100.0, 3), _ind(close=105.0), _cfg())
        assert signals == []

    def test_stop_loss_triggers_at_8pct_drop(self):
        # entry=100, price=92 → 8% drop → triggers
        signals = evaluate_exit_signal("NVDA", _pos(100.0), _ind(close=92.0), _cfg())
        assert len(signals) == 1
        assert "stop_loss" in signals[0]["reason"]

    def test_stop_loss_triggers_below_threshold(self):
        # entry=100, price=91 → 9% drop > 8% stop
        signals = evaluate_exit_signal("NVDA", _pos(100.0), _ind(close=91.0), _cfg())
        assert len(signals) == 1
        assert "stop_loss" in signals[0]["reason"]

    def test_stop_loss_not_triggered_above_threshold(self):
        # entry=100, price=93 → only 7% drop, stop is 8%
        signals = evaluate_exit_signal("NVDA", _pos(100.0), _ind(close=93.0), _cfg())
        assert signals == []

    def test_below_10d_ma_triggers(self):
        signals = evaluate_exit_signal("NVDA", _pos(100.0), _ind(close=99.0, below_ma=True), _cfg())
        assert len(signals) == 1
        assert "below_10d_ma" in signals[0]["reason"]

    def test_rs_weakness_triggers(self):
        signals = evaluate_exit_signal("NVDA", _pos(100.0), _ind(close=103.0, rs_weak=True), _cfg())
        assert len(signals) == 1
        assert "rs_weakness" in signals[0]["reason"]

    def test_volume_stall_triggers(self):
        signals = evaluate_exit_signal("NVDA", _pos(100.0), _ind(close=103.0, vol_stall=True), _cfg())
        assert len(signals) == 1
        assert "volume_stall" in signals[0]["reason"]

    def test_max_holding_days_triggers(self):
        signals = evaluate_exit_signal("NVDA", _pos(100.0, days=20), _ind(close=105.0), _cfg())
        assert len(signals) == 1
        assert "max_holding_days" in signals[0]["reason"]

    def test_one_day_before_limit_no_exit(self):
        signals = evaluate_exit_signal("NVDA", _pos(100.0, days=19), _ind(close=105.0), _cfg())
        assert signals == []

    def test_partial_take_profit_at_40pct_gain(self):
        # entry=100, price=145 → +45% gain → partial
        signals = evaluate_exit_signal("NVDA", _pos(100.0, partial_taken=False), _ind(close=145.0), _cfg())
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "EXIT_PARTIAL"
        assert "partial" in signals[0]["reason"]
        assert signals[0]["partial_fraction"] == pytest.approx(1/3, rel=0.01)

    def test_partial_not_triggered_again_if_already_taken(self):
        # If partial was already taken, should trail on 10-day MA instead
        # entry=80, price=114 → +42.5% gain, partial already taken → no exit unless below MA
        signals = evaluate_exit_signal(
            "NVDA", _pos(80.0, partial_taken=True),
            _ind(close=114.0, below_ma=False, ma_10=110.0),
            _cfg()
        )
        assert signals == []

    def test_breakeven_zone_trails_on_10d_ma(self):
        # entry=100, price=122 → +22% gain (breakeven zone: 20%-40%)
        # Price is below the 10-day MA → triggers exit
        signals = evaluate_exit_signal(
            "NVDA", _pos(100.0), _ind(close=122.0, below_ma=True, ma_10=125.0), _cfg()
        )
        assert len(signals) == 1
        assert "below_10d_ma" in signals[0]["reason"]

    def test_breakeven_zone_no_exit_when_above_ma(self):
        # entry=100, price=122 → +22% gain (breakeven zone), price above 10-day MA → no exit
        signals = evaluate_exit_signal(
            "NVDA", _pos(100.0), _ind(close=122.0, below_ma=False, ma_10=119.0), _cfg()
        )
        assert signals == []

    def test_big_winner_trails_on_20d_ma(self):
        # entry=80, price=145 → +81.25% → big winner mode, trail on 20-day MA
        # ma_20=148 → price(145) < ma_20(148) → exit
        signals = evaluate_exit_signal(
            "NVDA", _pos(80.0),
            _ind(close=145.0, ma_20=148.0),
            _cfg()
        )
        assert len(signals) == 1
        assert "big_winner" in signals[0]["reason"]

    def test_big_winner_no_exit_when_above_20d_ma(self):
        # entry=80, price=160 → +100% gain, ma_20=150 → still above MA, no exit
        signals = evaluate_exit_signal(
            "NVDA", _pos(80.0),
            _ind(close=160.0, ma_20=150.0),
            _cfg()
        )
        assert signals == []

    def test_exit_signal_contains_gain_pct(self):
        # entry=100, price=92 → -8% gain
        signals = evaluate_exit_signal("NVDA", _pos(100.0), _ind(close=92.0), _cfg())
        assert "gain_pct" in signals[0]
        assert signals[0]["gain_pct"] == pytest.approx(-0.08, rel=0.01)

    def test_invalid_entry_price_returns_empty(self):
        signals = evaluate_exit_signal("NVDA", {"entry_price": 0}, _ind(close=100.0), _cfg())
        assert signals == []


# ============================================================
# TestRunSignalScan
# ============================================================

class TestRunSignalScan:

    def test_empty_universe_returns_empty(self):
        spy = _make_ohlcv(60, 500, 510)
        result = run_signal_scan([], {}, spy, {}, _cfg())
        assert result["buy_signals"] == []
        assert result["exit_signals"] == []
        assert result["needs_review"] == []
        assert result["tickers_scanned"] == 0

    def test_missing_price_data_counted_as_error(self):
        spy = _make_ohlcv(60, 500, 510)
        result = run_signal_scan(["NVDA"], {}, spy, {}, _cfg())
        assert result["errors"] == 1
        assert result["tickers_scanned"] == 1

    def test_held_position_checked_for_exit_not_buy(self):
        spy = _make_ohlcv()
        stock = _make_ohlcv()
        held = {"NVDA": {"entry_price": 100.0, "holding_days": 5, "shares": 100}}
        result = run_signal_scan(["NVDA"], {"NVDA": stock}, spy, _cats(), _cfg(), held)
        buy_tickers = [s["ticker"] for s in result["buy_signals"]]
        assert "NVDA" not in buy_tickers

    def test_result_contains_expected_keys(self):
        spy = _make_ohlcv()
        result = run_signal_scan([], {}, spy, {}, _cfg())
        for key in ("buy_signals", "exit_signals", "needs_review",
                    "scan_date", "tickers_scanned", "errors"):
            assert key in result

    def test_scan_date_is_today(self):
        spy = _make_ohlcv()
        result = run_signal_scan([], {}, spy, {}, _cfg())
        assert result["scan_date"] == _today()


# ============================================================
# TestSaveFunctions
# ============================================================

class TestSaveFunctions:

    def test_save_needs_review_empty(self, tmp_path):
        path = str(tmp_path / "review.csv")
        save_needs_review([], path=path)
        df = pd.read_csv(path)
        assert df.empty

    def test_save_needs_review_with_rows(self, tmp_path):
        path = str(tmp_path / "review.csv")
        records = [{
            "ticker": "AAPL", "scan_date": _today(),
            "close": 180.0, "volume_ratio": 3.2,
            "return_20d": 0.12, "relative_strength_20d": 0.09,
            "above_20d_high": True, "above_50d_ma": True,
            "note": "Test record",
        }]
        save_needs_review(records, path=path)
        df = pd.read_csv(path)
        assert len(df) == 1
        assert df.iloc[0]["ticker"] == "AAPL"

    def test_save_signals_empty(self, tmp_path):
        path = str(tmp_path / "signals.csv")
        save_signals_csv([], [], path=path)
        df = pd.read_csv(path)
        assert df.empty

    def test_save_signals_buy_row(self, tmp_path):
        path = str(tmp_path / "signals.csv")
        buy = [{
            "signal_type": "BUY", "signal_date": _today(),
            "ticker": "NVDA", "close": 130.0, "volume_ratio": 3.5,
            "return_20d": 0.20, "relative_strength_20d": 0.15,
            "above_20d_high": True, "above_50d_ma": True,
            "catalyst_score": 9, "catalyst_type": "earnings",
            "catalyst_headline": "Beat and raise", "rank_score": 0.87,
        }]
        save_signals_csv(buy, [], path=path)
        df = pd.read_csv(path)
        assert len(df) == 1
        assert df.iloc[0]["ticker"] == "NVDA"
        assert df.iloc[0]["signal_type"] == "BUY"

    def test_save_signals_exit_row(self, tmp_path):
        path = str(tmp_path / "signals.csv")
        exits = [{
            "signal_type": "EXIT", "signal_date": _today(),
            "ticker": "META", "current_price": 95.0,
            "gain_pct": -0.05, "reason": "stop_loss_8%",
        }]
        save_signals_csv([], exits, path=path)
        df = pd.read_csv(path)
        assert len(df) == 1
        assert df.iloc[0]["signal_type"] == "EXIT"
        assert df.iloc[0]["reason"] == "stop_loss_8%"

    def test_save_signals_mixed_buy_and_exit(self, tmp_path):
        path = str(tmp_path / "signals.csv")
        buy = [{"signal_type": "BUY", "signal_date": _today(), "ticker": "NVDA",
                "close": 130.0, "volume_ratio": 3.5, "return_20d": 0.20,
                "relative_strength_20d": 0.15, "above_20d_high": True,
                "above_50d_ma": True, "catalyst_score": 9,
                "catalyst_type": "earnings", "catalyst_headline": "H", "rank_score": 0.9}]
        exits = [{"signal_type": "EXIT", "signal_date": _today(), "ticker": "AAPL",
                  "current_price": 170.0, "gain_pct": -0.08, "reason": "stop_loss_8%"}]
        save_signals_csv(buy, exits, path=path)
        df = pd.read_csv(path)
        assert len(df) == 2
        tickers = set(df["ticker"].tolist())
        assert "NVDA" in tickers
        assert "AAPL" in tickers

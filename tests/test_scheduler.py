"""
tests/test_scheduler.py — Tests for src/scheduler.py and src/market_calendar.py

Coverage:
  - log_scheduler_event()        — writes correct CSV rows
  - has_task_run_today()         — detects SUCCESS rows, ignores ERROR/SKIPPED
  - run_task()                   — dispatches, catches exceptions, logs result
  - run_full_cycle()             — runs all tasks, skips already-successful ones
  - market_calendar helpers      — is_trading_day, is_market_open, next_trading_day
  - run_bot()                    — starts, checks schedule, stops on _shutdown_requested
"""

import csv
import os
import tempfile
from datetime import date, datetime, time
from unittest.mock import MagicMock, patch, call

import pytest
import pytz

from src.market_calendar import (
    EASTERN,
    is_holiday,
    is_market_open,
    is_trading_day,
    is_weekend,
    next_trading_day,
    now_eastern,
    seconds_until_eastern,
    market_status_summary,
    get_alpaca_market_open,
    is_market_open_safe,
)
from src.scheduler import (
    DAILY_SCHEDULE,
    FULL_CYCLE_ORDER,
    has_task_run_today,
    log_scheduler_event,
    run_full_cycle,
    run_task,
)


# ============================================================
# Helpers
# ============================================================

def _write_log(path: str, rows: list[dict]) -> None:
    """Write a minimal scheduler_log.csv for testing."""
    if not rows:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def _make_config(tmp_path: str | None = None):
    """Return a minimal mock config object."""
    cfg = MagicMock()
    cfg.require_paper_mode.return_value = None
    cfg.scheduler_log_path = tmp_path or "reports/scheduler_log.csv"
    cfg.state_file_path = "reports/paper_state.json"
    cfg.order_log_path = "reports/orders_log.csv"
    cfg.starting_cash = 100_000
    cfg.max_positions = 8
    cfg.stop_loss_pct = 0.08
    cfg.breakeven_trigger_pct = 0.20
    cfg.max_holding_days = 20
    return cfg


# ============================================================
# Tests: log_scheduler_event
# ============================================================

class TestLogSchedulerEvent:

    def test_creates_file_and_appends_row(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        log_scheduler_event("paper_scan", "SUCCESS", 12.5, "10 tickers", log_path=log)

        with open(log) as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 1
        row = rows[0]
        assert row["task_name"] == "paper_scan"
        assert row["status"] == "SUCCESS"
        assert float(row["duration_seconds"]) == pytest.approx(12.5)
        assert "10 tickers" in row["detail"]

    def test_appends_multiple_rows(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        log_scheduler_event("paper_execute", "SUCCESS", 5.0, "", log_path=log)
        log_scheduler_event("paper_reconcile", "ERROR", 1.0, "timeout", log_path=log)

        with open(log) as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 2
        assert rows[0]["task_name"] == "paper_execute"
        assert rows[1]["task_name"] == "paper_reconcile"
        assert rows[1]["status"] == "ERROR"

    def test_detail_capped_at_200_chars(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        long_detail = "x" * 500
        log_scheduler_event("paper_scan", "ERROR", 0.0, long_detail, log_path=log)

        with open(log) as f:
            rows = list(csv.DictReader(f))

        assert len(rows[0]["detail"]) <= 200

    def test_custom_log_date(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        log_scheduler_event("paper_scan", "SUCCESS", 0.0, "", log_path=log, log_date="2025-01-15")

        with open(log) as f:
            rows = list(csv.DictReader(f))

        assert rows[0]["date"] == "2025-01-15"

    def test_writes_to_custom_path_in_existing_dir(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        log_scheduler_event("paper_scan", "SUCCESS", 0.0, "custom path", log_path=log)
        assert os.path.exists(log)


# ============================================================
# Tests: has_task_run_today
# ============================================================

class TestHasTaskRunToday:

    def test_returns_false_when_no_log_file(self, tmp_path):
        log = str(tmp_path / "missing.csv")
        assert has_task_run_today("paper_scan", log_path=log) is False

    def test_returns_false_when_no_matching_row(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        today = date(2025, 6, 3)
        _write_log(log, [
            {"timestamp": "2025-06-03T09:32:00", "date": "2025-06-03",
             "task_name": "paper_execute", "status": "SUCCESS",
             "duration_seconds": "5.0", "detail": ""},
        ])
        assert has_task_run_today("paper_scan", log_path=log, today=today) is False

    def test_returns_true_for_success_today(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        today = date(2025, 6, 3)
        _write_log(log, [
            {"timestamp": "2025-06-03T16:35:00", "date": "2025-06-03",
             "task_name": "paper_scan", "status": "SUCCESS",
             "duration_seconds": "10.0", "detail": "5 buy signals"},
        ])
        assert has_task_run_today("paper_scan", log_path=log, today=today) is True

    def test_ignores_error_row(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        today = date(2025, 6, 3)
        _write_log(log, [
            {"timestamp": "2025-06-03T16:35:00", "date": "2025-06-03",
             "task_name": "paper_scan", "status": "ERROR",
             "duration_seconds": "1.0", "detail": "timeout"},
        ])
        assert has_task_run_today("paper_scan", log_path=log, today=today) is False

    def test_ignores_skipped_row(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        today = date(2025, 6, 3)
        _write_log(log, [
            {"timestamp": "2025-06-03T16:35:00", "date": "2025-06-03",
             "task_name": "paper_scan", "status": "SKIPPED",
             "duration_seconds": "0.0", "detail": "already ran today"},
        ])
        assert has_task_run_today("paper_scan", log_path=log, today=today) is False

    def test_ignores_different_date(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        today = date(2025, 6, 3)
        _write_log(log, [
            {"timestamp": "2025-06-02T16:35:00", "date": "2025-06-02",
             "task_name": "paper_scan", "status": "SUCCESS",
             "duration_seconds": "10.0", "detail": "yesterday"},
        ])
        assert has_task_run_today("paper_scan", log_path=log, today=today) is False

    def test_returns_false_when_log_is_empty(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        open(log, "w").close()  # empty file
        assert has_task_run_today("paper_scan", log_path=log) is False


# ============================================================
# Tests: run_task
# ============================================================

class TestRunTask:

    def test_success_returns_correct_dict(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        cfg = _make_config(log)

        with patch("src.scheduler._dispatch_task", return_value="5 buy signals"):
            result = run_task("paper_scan", cfg)

        assert result["task_name"] == "paper_scan"
        assert result["status"] == "SUCCESS"
        assert result["detail"] == "5 buy signals"
        assert result["duration"] >= 0

    def test_exception_returns_error_dict(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        cfg = _make_config(log)

        with patch("src.scheduler._dispatch_task", side_effect=RuntimeError("network error")):
            result = run_task("paper_scan", cfg)

        assert result["task_name"] == "paper_scan"
        assert result["status"] == "ERROR"
        assert "network error" in result["detail"]

    def test_success_is_logged_to_csv(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        cfg = _make_config(log)

        with patch("src.scheduler._dispatch_task", return_value="ok"):
            run_task("paper_scan", cfg)

        with open(log) as f:
            rows = list(csv.DictReader(f))

        assert any(r["task_name"] == "paper_scan" and r["status"] == "SUCCESS" for r in rows)

    def test_error_is_logged_to_csv(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        cfg = _make_config(log)

        with patch("src.scheduler._dispatch_task", side_effect=ValueError("bad value")):
            run_task("paper_scan", cfg)

        with open(log) as f:
            rows = list(csv.DictReader(f))

        assert any(r["task_name"] == "paper_scan" and r["status"] == "ERROR" for r in rows)

    def test_duration_is_non_negative(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        cfg = _make_config(log)

        with patch("src.scheduler._dispatch_task", return_value="ok"):
            result = run_task("paper_execute", cfg)

        assert result["duration"] >= 0.0


# ============================================================
# Tests: run_full_cycle
# ============================================================

class TestRunFullCycle:

    def test_runs_all_tasks_when_none_done(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        cfg = _make_config(log)

        with patch("src.scheduler.run_task") as mock_run:
            mock_run.return_value = {"task_name": "x", "status": "SUCCESS",
                                     "duration": 0.1, "detail": "ok"}
            result = run_full_cycle(cfg)

        assert result["tasks_run"] == len(FULL_CYCLE_ORDER)
        assert result["tasks_skipped"] == 0
        assert result["tasks_failed"] == 0
        assert len(result["results"]) == len(FULL_CYCLE_ORDER)

    def test_skips_tasks_already_run_today(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        today = date(2025, 6, 3)

        # Mark two tasks as already done
        _write_log(log, [
            {"timestamp": "2025-06-03T16:20:00", "date": "2025-06-03",
             "task_name": "discover_universe", "status": "SUCCESS",
             "duration_seconds": "8.0", "detail": "50 tickers"},
            {"timestamp": "2025-06-03T16:35:00", "date": "2025-06-03",
             "task_name": "paper_scan", "status": "SUCCESS",
             "duration_seconds": "12.0", "detail": "3 buy signals"},
        ])

        cfg = _make_config(log)

        with patch("src.scheduler.run_task") as mock_run, \
             patch("src.scheduler.now_eastern") as mock_now:
            mock_now.return_value = MagicMock()
            mock_now.return_value.date.return_value = today
            mock_run.return_value = {"task_name": "x", "status": "SUCCESS",
                                     "duration": 0.1, "detail": "ok"}
            result = run_full_cycle(cfg)

        assert result["tasks_skipped"] == 2
        assert result["tasks_run"] == len(FULL_CYCLE_ORDER) - 2

    def test_counts_failed_tasks(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        cfg = _make_config(log)

        with patch("src.scheduler.run_task") as mock_run:
            mock_run.return_value = {"task_name": "x", "status": "ERROR",
                                     "duration": 0.1, "detail": "boom"}
            result = run_full_cycle(cfg)

        assert result["tasks_failed"] == len(FULL_CYCLE_ORDER)
        assert result["tasks_run"] == 0

    def test_continues_after_failed_task(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        cfg = _make_config(log)

        call_count = 0
        def side_effect(task_name, config):
            nonlocal call_count
            call_count += 1
            if task_name == "paper_scan":
                return {"task_name": task_name, "status": "ERROR",
                        "duration": 0.1, "detail": "failed"}
            return {"task_name": task_name, "status": "SUCCESS",
                    "duration": 0.1, "detail": "ok"}

        with patch("src.scheduler.run_task", side_effect=side_effect):
            result = run_full_cycle(cfg)

        # All tasks ran (none skipped), one failed, rest succeeded
        assert call_count == len(FULL_CYCLE_ORDER)
        assert result["tasks_failed"] == 1
        assert result["tasks_run"] == len(FULL_CYCLE_ORDER) - 1

    def test_full_cycle_order_matches_constant(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        cfg = _make_config(log)
        ran_order = []

        def capture(task_name, config):
            ran_order.append(task_name)
            return {"task_name": task_name, "status": "SUCCESS",
                    "duration": 0.1, "detail": "ok"}

        with patch("src.scheduler.run_task", side_effect=capture):
            run_full_cycle(cfg)

        assert ran_order == FULL_CYCLE_ORDER

    def test_results_list_has_one_entry_per_task(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        cfg = _make_config(log)

        with patch("src.scheduler.run_task") as mock_run:
            mock_run.return_value = {"task_name": "x", "status": "SUCCESS",
                                     "duration": 0.1, "detail": "ok"}
            result = run_full_cycle(cfg)

        assert len(result["results"]) == len(FULL_CYCLE_ORDER)

    def test_skipped_results_have_skipped_status(self, tmp_path):
        log = str(tmp_path / "sched.csv")
        today = date(2025, 6, 3)

        _write_log(log, [
            {"timestamp": "2025-06-03T16:20:00", "date": "2025-06-03",
             "task_name": "discover_universe", "status": "SUCCESS",
             "duration_seconds": "8.0", "detail": "ok"},
        ])

        cfg = _make_config(log)

        with patch("src.scheduler.run_task") as mock_run, \
             patch("src.scheduler.now_eastern") as mock_now:
            mock_now.return_value = MagicMock()
            mock_now.return_value.date.return_value = today
            mock_run.return_value = {"task_name": "x", "status": "SUCCESS",
                                     "duration": 0.1, "detail": "ok"}
            result = run_full_cycle(cfg)

        skipped_results = [r for r in result["results"] if r["status"] == "SKIPPED"]
        assert len(skipped_results) == 1
        assert skipped_results[0]["task_name"] == "discover_universe"


# ============================================================
# Tests: is_trading_day (market_calendar)
# ============================================================

class TestIsTradingDay:

    def test_monday_is_trading_day(self):
        # 2025-06-02 is a Monday (not a holiday)
        assert is_trading_day(date(2025, 6, 2)) is True

    def test_saturday_is_not_trading_day(self):
        assert is_trading_day(date(2025, 6, 7)) is False

    def test_sunday_is_not_trading_day(self):
        assert is_trading_day(date(2025, 6, 8)) is False

    def test_new_years_day_2025_is_holiday(self):
        assert is_trading_day(date(2025, 1, 1)) is False

    def test_thanksgiving_2025_is_holiday(self):
        assert is_trading_day(date(2025, 11, 27)) is False

    def test_memorial_day_2025_is_holiday(self):
        assert is_trading_day(date(2025, 5, 26)) is False

    def test_good_friday_2025_is_holiday(self):
        assert is_trading_day(date(2025, 4, 18)) is False

    def test_regular_tuesday_is_trading_day(self):
        # 2025-03-11 is a Tuesday, not a holiday
        assert is_trading_day(date(2025, 3, 11)) is True


# ============================================================
# Tests: is_market_open (market_calendar)
# ============================================================

class TestIsMarketOpen:

    def _eastern_dt(self, year, month, day, hour, minute):
        """Helper: create a timezone-aware Eastern datetime."""
        naive = datetime(year, month, day, hour, minute)
        return EASTERN.localize(naive)

    def test_open_at_930_on_trading_day(self):
        # 2025-06-02 Monday 9:30 AM — should be open
        dt = self._eastern_dt(2025, 6, 2, 9, 30)
        assert is_market_open(dt) is True

    def test_open_at_noon_on_trading_day(self):
        dt = self._eastern_dt(2025, 6, 2, 12, 0)
        assert is_market_open(dt) is True

    def test_closed_at_400pm_on_trading_day(self):
        # 4:00 PM is the close — exclusive upper bound
        dt = self._eastern_dt(2025, 6, 2, 16, 0)
        assert is_market_open(dt) is False

    def test_closed_before_930_on_trading_day(self):
        dt = self._eastern_dt(2025, 6, 2, 9, 0)
        assert is_market_open(dt) is False

    def test_closed_on_saturday(self):
        dt = self._eastern_dt(2025, 6, 7, 10, 0)
        assert is_market_open(dt) is False

    def test_closed_on_holiday(self):
        # New Year's Day 2025
        dt = self._eastern_dt(2025, 1, 1, 10, 0)
        assert is_market_open(dt) is False

    def test_open_at_359pm_on_trading_day(self):
        dt = self._eastern_dt(2025, 6, 2, 15, 59)
        assert is_market_open(dt) is True


# ============================================================
# Tests: next_trading_day (market_calendar)
# ============================================================

class TestNextTradingDay:

    def test_friday_gives_monday(self):
        # 2025-06-06 (Friday) → 2025-06-09 (Monday)
        result = next_trading_day(date(2025, 6, 6))
        assert result == date(2025, 6, 9)

    def test_saturday_gives_monday(self):
        result = next_trading_day(date(2025, 6, 7))
        assert result == date(2025, 6, 9)

    def test_sunday_gives_monday(self):
        result = next_trading_day(date(2025, 6, 8))
        assert result == date(2025, 6, 9)

    def test_thursday_before_good_friday_gives_monday(self):
        # 2025-04-17 (Thursday) → Good Friday 2025-04-18 is a holiday
        # Apr 19 = Saturday, Apr 20 = Sunday → next trading day is Monday Apr 21
        result = next_trading_day(date(2025, 4, 17))
        assert result == date(2025, 4, 21)

    def test_next_trading_day_is_strictly_after_given_date(self):
        d = date(2025, 6, 2)  # Monday
        result = next_trading_day(d)
        assert result > d

    def test_wednesday_gives_thursday(self):
        # 2025-06-04 (Wednesday) → 2025-06-05 (Thursday)
        result = next_trading_day(date(2025, 6, 4))
        assert result == date(2025, 6, 5)


# ============================================================
# Tests: is_holiday / is_weekend
# ============================================================

class TestIsHolidayIsWeekend:

    def test_is_holiday_known_dates(self):
        assert is_holiday(date(2025, 1, 1)) is True
        assert is_holiday(date(2025, 12, 25)) is True
        assert is_holiday(date(2026, 7, 3)) is True  # Independence Day observed

    def test_is_holiday_regular_day(self):
        assert is_holiday(date(2025, 6, 3)) is False

    def test_is_weekend_saturday(self):
        assert is_weekend(date(2025, 6, 7)) is True

    def test_is_weekend_sunday(self):
        assert is_weekend(date(2025, 6, 8)) is True

    def test_is_weekend_weekday(self):
        assert is_weekend(date(2025, 6, 2)) is False


# ============================================================
# Tests: seconds_until_eastern
# ============================================================

class TestSecondsUntilEastern:

    def test_returns_positive_number(self):
        # Result should always be positive
        result = seconds_until_eastern("09:32")
        assert result > 0

    def test_within_24_hours(self):
        result = seconds_until_eastern("16:20")
        assert result <= 86400  # at most one day

    def test_valid_time_format(self):
        # Should not raise
        seconds_until_eastern("09:32")
        seconds_until_eastern("16:10")
        seconds_until_eastern("00:00")


# ============================================================
# Tests: market_status_summary
# ============================================================

class TestMarketStatusSummary:

    def test_returns_all_keys(self):
        status = market_status_summary()
        required_keys = {
            "now_eastern", "is_trading_day", "is_open_now",
            "is_holiday", "is_weekend", "next_trading_day"
        }
        assert required_keys.issubset(set(status.keys()))

    def test_bool_fields_are_bool(self):
        status = market_status_summary()
        assert isinstance(status["is_trading_day"], bool)
        assert isinstance(status["is_open_now"], bool)
        assert isinstance(status["is_holiday"], bool)
        assert isinstance(status["is_weekend"], bool)

    def test_now_eastern_is_string(self):
        status = market_status_summary()
        assert isinstance(status["now_eastern"], str)
        assert len(status["now_eastern"]) > 0


# ============================================================
# Tests: get_alpaca_market_open / is_market_open_safe
# ============================================================

class TestAlpacaMarketOpen:

    def test_get_alpaca_returns_true_when_open(self):
        broker = MagicMock()
        broker.get_clock.return_value = {"is_open": True}
        assert get_alpaca_market_open(broker) is True

    def test_get_alpaca_returns_false_when_closed(self):
        broker = MagicMock()
        broker.get_clock.return_value = {"is_open": False}
        assert get_alpaca_market_open(broker) is False

    def test_get_alpaca_returns_none_on_exception(self):
        broker = MagicMock()
        broker.get_clock.side_effect = RuntimeError("network error")
        assert get_alpaca_market_open(broker) is None

    def test_is_market_open_safe_uses_alpaca_when_available(self):
        broker = MagicMock()
        broker.get_clock.return_value = {"is_open": True}
        # Alpaca says open — result must be True regardless of local calendar
        assert is_market_open_safe(broker=broker) is True

    def test_is_market_open_safe_alpaca_says_closed(self):
        broker = MagicMock()
        broker.get_clock.return_value = {"is_open": False}
        assert is_market_open_safe(broker=broker) is False

    def test_is_market_open_safe_falls_back_to_local_on_error(self):
        broker = MagicMock()
        broker.get_clock.side_effect = RuntimeError("network down")
        # Should not raise — should fall back to local calendar
        result = is_market_open_safe(broker=broker)
        assert isinstance(result, bool)

    def test_is_market_open_safe_no_broker(self):
        # No broker — use local calendar
        result = is_market_open_safe()
        assert isinstance(result, bool)


# ============================================================
# Tests: run_bot (smoke tests — mock the loop internals)
# ============================================================

class TestRunBot:

    def test_run_bot_fails_if_paper_mode_check_fails(self):
        cfg = MagicMock()
        cfg.require_paper_mode.side_effect = RuntimeError("not paper mode")
        cfg.scheduler_log_path = "reports/scheduler_log.csv"

        with pytest.raises(RuntimeError, match="not paper mode"):
            from src.scheduler import run_bot
            run_bot(cfg)

    def test_run_bot_stops_on_keyboard_interrupt(self, tmp_path):
        """Bot exits cleanly when KeyboardInterrupt fires in the loop."""
        log = str(tmp_path / "sched.csv")
        cfg = _make_config(log)

        # Raise KeyboardInterrupt on the first is_trading_day call,
        # which the loop catches and breaks on.
        with patch("src.scheduler._print_bot_status"), \
             patch("src.scheduler.is_trading_day", side_effect=KeyboardInterrupt):
            from src.scheduler import run_bot
            run_bot(cfg)  # Should return without blocking

    def test_run_bot_logs_start_and_stop(self, tmp_path):
        """Bot logs bot_started and bot_stopped events."""
        log = str(tmp_path / "sched.csv")
        cfg = _make_config(log)

        with patch("src.scheduler._print_bot_status"), \
             patch("src.scheduler.is_trading_day", side_effect=KeyboardInterrupt):
            from src.scheduler import run_bot
            run_bot(cfg)

        with open(log) as f:
            rows = list(csv.DictReader(f))

        task_names = [r["task_name"] for r in rows]
        assert "bot_started" in task_names
        assert "bot_stopped" in task_names


# ============================================================
# Tests: DAILY_SCHEDULE and FULL_CYCLE_ORDER structure
# ============================================================

class TestScheduleConstants:

    def test_daily_schedule_has_eight_entries(self):
        assert len(DAILY_SCHEDULE) == 8

    def test_full_cycle_order_has_eight_entries(self):
        assert len(FULL_CYCLE_ORDER) == 8

    def test_daily_schedule_time_format(self):
        for time_str, _ in DAILY_SCHEDULE:
            parts = time_str.split(":")
            assert len(parts) == 2
            hour, minute = int(parts[0]), int(parts[1])
            assert 0 <= hour <= 23
            assert 0 <= minute <= 59

    def test_daily_schedule_is_sorted_by_time(self):
        times = [t for t, _ in DAILY_SCHEDULE]
        assert times == sorted(times)

    def test_full_cycle_order_contains_all_scheduled_tasks(self):
        scheduled_tasks = {task for _, task in DAILY_SCHEDULE}
        # All scheduled tasks should appear in FULL_CYCLE_ORDER
        for task in FULL_CYCLE_ORDER:
            assert task in scheduled_tasks

    def test_paper_execute_before_reconcile_in_full_cycle(self):
        execute_idx = FULL_CYCLE_ORDER.index("paper_execute")
        reconcile_idx = FULL_CYCLE_ORDER.index("paper_reconcile")
        assert execute_idx < reconcile_idx

    def test_discover_universe_before_paper_scan_in_full_cycle(self):
        discover_idx = FULL_CYCLE_ORDER.index("discover_universe")
        scan_idx = FULL_CYCLE_ORDER.index("paper_scan")
        assert discover_idx < scan_idx

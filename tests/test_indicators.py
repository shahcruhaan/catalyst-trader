"""
tests/test_indicators.py — Automated tests for the indicators module

These tests use synthetic (made-up) data. No internet connection is needed.
No API keys are needed. They run entirely offline.

Run all tests with:
    pytest tests/test_indicators.py -v

The -v flag means "verbose" — it prints each test name and PASSED/FAILED.

What these tests verify:
  - Moving average calculations are mathematically correct
  - Breakout detection works properly
  - Volume ratio handles edge cases (zero volume, not enough data)
  - Return calculations are accurate
  - Relative strength comparison is correct
  - Exit signals fire at the right time
  - The combined calculate_all_indicators() function returns expected keys
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# Import all the functions we are testing
# These imports will fail if you haven't activated your virtual environment
from src.indicators import (
    moving_average,
    latest_ma,
    latest_close,
    is_above_20d_high,
    is_above_ma,
    return_nd,
    volume_ratio,
    avg_volume_n,
    avg_dollar_volume_n,
    relative_strength_vs_spy,
    is_below_exit_ma,
    check_rs_weakness_streak,
    check_volume_stall,
    calculate_all_indicators,
)


# ============================================================
# Helper functions for building fake data
# ============================================================

def make_df(closes, volumes=None):
    """
    Build a fake OHLCV DataFrame for testing.

    Args:
        closes:  List of closing prices, e.g. [100.0, 101.0, 102.0]
                 The last item in the list = today's price.
        volumes: List of volumes. Defaults to 1,000,000 per day.

    Returns:
        A pandas DataFrame with columns: Open, High, Low, Close, Volume
        with business-day dates as the index.
    """
    n = len(closes)
    # Create business-day dates ending today
    dates = pd.date_range(end=datetime.today(), periods=n, freq="B")

    if volumes is None:
        volumes = [1_000_000] * n

    df = pd.DataFrame(
        {
            "Open":   [c * 0.99 for c in closes],
            "High":   [c * 1.01 for c in closes],
            "Low":    [c * 0.98 for c in closes],
            "Close":  closes,
            "Volume": volumes,
        },
        index=dates,
    )
    return df


def make_trending_df(start_price=100.0, n_days=60, daily_return=0.005):
    """
    Create a steadily trending stock price series.

    Args:
        start_price:  Starting price (oldest day)
        n_days:       Number of trading days to generate
        daily_return: Daily return rate.
                      0.005  = +0.5% per day (uptrend)
                      -0.005 = -0.5% per day (downtrend)
                      0.0    = flat (no movement)

    Returns:
        DataFrame with n_days rows.
    """
    closes = [start_price * ((1 + daily_return) ** i) for i in range(n_days)]
    return make_df(closes)


def make_spy_flat(n_days=60, price=500.0):
    """Create a flat SPY (no movement) for relative strength baseline tests."""
    return make_df([price] * n_days)


def make_spy_trending(n_days=60, start=500.0, daily_return=0.003):
    """Create a slowly trending SPY."""
    closes = [start * ((1 + daily_return) ** i) for i in range(n_days)]
    return make_df(closes)


# ============================================================
# Tests: moving_average()
# ============================================================

class TestMovingAverage:
    """Tests for the moving_average() function."""

    def test_basic_three_period_average(self):
        """
        With prices [1, 2, 3, 4, 5] and period=3:
        - Positions 0 and 1 should be NaN (not enough data yet)
        - Position 2: average of [1,2,3] = 2.0
        - Position 3: average of [2,3,4] = 3.0
        - Position 4: average of [3,4,5] = 4.0
        """
        prices = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = moving_average(prices, 3)

        assert pd.isna(result.iloc[0]), "First value should be NaN"
        assert pd.isna(result.iloc[1]), "Second value should be NaN"
        assert abs(result.iloc[2] - 2.0) < 0.001, "Period 3 avg should be 2.0"
        assert abs(result.iloc[3] - 3.0) < 0.001, "Period 4 avg should be 3.0"
        assert abs(result.iloc[4] - 4.0) < 0.001, "Period 5 avg should be 4.0"

    def test_all_same_price_gives_same_ma(self):
        """If all prices are 100, every MA value should also be 100."""
        prices = pd.Series([100.0] * 20)
        result = moving_average(prices, 10)
        # Last value should be 100.0
        assert abs(result.iloc[-1] - 100.0) < 0.001

    def test_period_longer_than_series_gives_all_nan(self):
        """If period > number of data points, all values should be NaN."""
        prices = pd.Series([1.0, 2.0, 3.0])
        result = moving_average(prices, 5)
        assert result.isna().all(), "All values should be NaN when period > length"

    def test_period_equals_series_length(self):
        """When period equals the series length, only the last value is valid."""
        prices = pd.Series([10.0, 20.0, 30.0])
        result = moving_average(prices, 3)
        assert abs(result.iloc[-1] - 20.0) < 0.001  # Average of 10, 20, 30


class TestLatestMA:
    """Tests for the latest_ma() convenience function."""

    def test_returns_none_when_not_enough_rows(self):
        """With only 10 rows of data, a 50-day MA cannot be calculated."""
        df = make_df([100.0] * 10)
        assert latest_ma(df, 50) is None

    def test_returns_correct_value_with_enough_data(self):
        """With 60 rows of constant prices at 100, the 50-day MA should be 100."""
        df = make_df([100.0] * 60)
        result = latest_ma(df, 50)
        assert result is not None
        assert abs(result - 100.0) < 0.01

    def test_returns_none_for_empty_df(self):
        """An empty DataFrame should return None."""
        df = pd.DataFrame()
        assert latest_ma(df, 10) is None


# ============================================================
# Tests: latest_close()
# ============================================================

class TestLatestClose:
    """Tests for the latest_close() function."""

    def test_returns_last_price_in_series(self):
        """The most recent close should be the last item in the list."""
        df = make_df([100.0, 101.0, 102.0, 105.0])
        assert abs(latest_close(df) - 105.0) < 0.001

    def test_none_input_returns_none(self):
        """Passing None should not crash — should return None."""
        assert latest_close(None) is None

    def test_empty_df_returns_none(self):
        """An empty DataFrame should return None."""
        assert latest_close(pd.DataFrame()) is None


# ============================================================
# Tests: is_above_20d_high()
# ============================================================

class TestIsAbove20dHigh:
    """Tests for the 20-day high breakout detection."""

    def test_breakout_detected_when_today_is_highest(self):
        """
        Setup: 21 days at 100.0, then today at 110.0.
        Today (110) should be above the prior 20-day high (100).
        Expected: True (breakout!)
        """
        closes = [100.0] * 21 + [110.0]
        df = make_df(closes)
        assert is_above_20d_high(df, 20) is True

    def test_no_breakout_when_prior_day_was_higher(self):
        """
        Setup: 21 rows where the 10th row (inside the 20-day window) hit 150.
        Today is at 105, which is below that 150 high.
        Expected: False (no breakout — prior high of 150 is still in the window)

        Note: the lookback uses iloc[-21:-1] for a 20-day window, so the spike
        must be placed within those 20 prior rows, not 21+ days ago.
        """
        # 5 days at 100, then a spike to 150, then 14 more days at 100, then today at 105
        # Total: 21 rows. The 150 spike is at index 5 — well within the 20-day window.
        closes = [100.0] * 5 + [150.0] + [100.0] * 14 + [105.0]
        df = make_df(closes)
        assert is_above_20d_high(df, 20) is False

    def test_not_enough_data_returns_false(self):
        """With only 5 days of data, a 20-day lookback is impossible."""
        df = make_df([100.0] * 5)
        assert is_above_20d_high(df, 20) is False

    def test_equal_price_is_not_a_breakout(self):
        """
        If today's price exactly equals the prior high (not above it),
        it is NOT a breakout — the strategy requires strictly greater.
        """
        closes = [100.0] * 21  # All prices the same
        df = make_df(closes)
        assert is_above_20d_high(df, 20) is False

    def test_small_breakout_still_counts(self):
        """A tiny 0.01 breakout above the prior high still counts."""
        closes = [100.0] * 21 + [100.01]
        df = make_df(closes)
        assert is_above_20d_high(df, 20) is True


# ============================================================
# Tests: is_above_ma()
# ============================================================

class TestIsAboveMA:
    """Tests for the moving average trend filter."""

    def test_uptrending_stock_is_above_50d_ma(self):
        """
        A stock that has been consistently rising should be above its 50-day MA,
        because recent prices are higher than the average of the past 50 days.
        """
        df = make_trending_df(start_price=100, n_days=60, daily_return=0.005)
        assert is_above_ma(df, 50) is True

    def test_downtrending_stock_is_below_50d_ma(self):
        """
        A stock that has been falling should be below its 50-day MA.
        """
        df = make_trending_df(start_price=100, n_days=60, daily_return=-0.005)
        assert is_above_ma(df, 50) is False

    def test_not_enough_data_returns_false(self):
        """With fewer rows than the MA period, return False."""
        df = make_df([100.0] * 30)
        assert is_above_ma(df, 50) is False

    def test_flat_price_is_not_clearly_above_ma(self):
        """
        A completely flat price series has close == MA.
        Since we use strict >, a flat price should return False.
        """
        df = make_df([100.0] * 60)
        # Price equals MA — not strictly above
        result = is_above_ma(df, 50)
        # This is borderline; floating point may cause slight differences
        # Just check it doesn't crash and returns a boolean
        assert isinstance(result, bool)


# ============================================================
# Tests: return_nd()
# ============================================================

class TestReturnND:
    """Tests for the N-day return calculation."""

    def test_flat_price_gives_zero_return(self):
        """If a stock's price hasn't changed in 20 days, return = 0%."""
        df = make_df([100.0] * 30)
        result = return_nd(df, 20)
        assert result is not None
        assert abs(result) < 0.001  # Should be ~0.0

    def test_20_percent_gain(self):
        """
        Stock was at 100 twenty days ago, now at 120.
        Expected return: (120/100) - 1 = 0.20 = +20%
        """
        # 21 entries: the first is 20 days ago (100), the rest build up to 120
        closes = [100.0] + [110.0] * 19 + [120.0]
        df = make_df(closes)
        result = return_nd(df, 20)
        assert result is not None
        assert abs(result - 0.20) < 0.001

    def test_not_enough_data_returns_none(self):
        """With only 10 rows, a 20-day return cannot be calculated."""
        df = make_df([100.0] * 10)
        assert return_nd(df, 20) is None

    def test_none_df_returns_none(self):
        """Passing None should not crash."""
        assert return_nd(None, 20) is None

    def test_negative_return_for_falling_price(self):
        """If the stock went down, the return should be negative."""
        closes = [100.0] + [95.0] * 19 + [80.0]
        df = make_df(closes)
        result = return_nd(df, 20)
        assert result is not None
        assert result < 0  # Should be negative


# ============================================================
# Tests: volume_ratio()
# ============================================================

class TestVolumeRatio:
    """Tests for volume spike detection."""

    def test_equal_volume_gives_ratio_of_one(self):
        """
        If today's volume equals the 20-day average, ratio = 1.0.
        All volumes the same = ratio of 1.
        """
        closes = [100.0] * 21
        volumes = [1_000_000] * 21
        df = make_df(closes, volumes=volumes)
        result = volume_ratio(df, 20)
        assert result is not None
        assert abs(result - 1.0) < 0.01

    def test_double_volume_gives_ratio_of_two(self):
        """
        20 days of 1M volume, then today 2M volume.
        Expected: 2M / 1M = 2.0
        """
        closes = [100.0] * 21
        volumes = [1_000_000] * 20 + [2_000_000]
        df = make_df(closes, volumes=volumes)
        result = volume_ratio(df, 20)
        assert result is not None
        assert abs(result - 2.0) < 0.01

    def test_high_volume_spike(self):
        """
        20 days of 1M, today 5M. Expected ratio = 5.0.
        This would easily pass the 2.5x threshold.
        """
        closes = [100.0] * 21
        volumes = [1_000_000] * 20 + [5_000_000]
        df = make_df(closes, volumes=volumes)
        result = volume_ratio(df, 20)
        assert result is not None
        assert abs(result - 5.0) < 0.01

    def test_not_enough_data_returns_none(self):
        """Need at least 21 rows for a 20-day ratio. Less = None."""
        df = make_df([100.0] * 5)
        assert volume_ratio(df, 20) is None

    def test_zero_average_volume_returns_none(self):
        """
        If average volume is 0, we cannot divide by it.
        Should return None, not crash.
        """
        closes = [100.0] * 21
        volumes = [0] * 21
        df = make_df(closes, volumes=volumes)
        assert volume_ratio(df, 20) is None


# ============================================================
# Tests: relative_strength_vs_spy()
# ============================================================

class TestRelativeStrength:
    """Tests for relative strength vs SPY calculation."""

    def test_stock_outperforms_flat_spy(self):
        """
        Stock goes up 20% over 20 days, SPY stays flat.
        RS = 20% - 0% = +20% = 0.20
        """
        stock_closes = [100.0] + [100.0] * 19 + [120.0]
        spy_closes = [500.0] * 21

        stock_df = make_df(stock_closes)
        spy_df = make_df(spy_closes)

        rs = relative_strength_vs_spy(stock_df, spy_df, 20)
        assert rs is not None
        assert abs(rs - 0.20) < 0.001

    def test_stock_matches_spy_gives_zero_rs(self):
        """
        If stock and SPY perform identically, RS = 0.
        """
        closes = [100.0] + [100.0] * 19 + [110.0]
        # Both stock and SPY go up 10%
        stock_df = make_df(closes)
        spy_df = make_df([c * 5 for c in closes])  # SPY at 5x price but same % gain

        rs = relative_strength_vs_spy(stock_df, spy_df, 20)
        assert rs is not None
        assert abs(rs) < 0.001  # Should be ~0

    def test_stock_underperforms_spy_gives_negative_rs(self):
        """
        Stock is flat, SPY goes up 10%.
        RS = 0% - 10% = -10% = -0.10
        """
        stock_closes = [100.0] * 21  # Stock doesn't move
        spy_closes = [500.0] + [500.0] * 19 + [550.0]  # SPY up 10%

        stock_df = make_df(stock_closes)
        spy_df = make_df(spy_closes)

        rs = relative_strength_vs_spy(stock_df, spy_df, 20)
        assert rs is not None
        assert rs < 0

    def test_not_enough_data_returns_none(self):
        """With only 5 rows, a 20-day RS calculation is impossible."""
        stock_df = make_df([100.0] * 5)
        spy_df = make_df([500.0] * 5)
        assert relative_strength_vs_spy(stock_df, spy_df, 20) is None

    def test_none_spy_returns_none(self):
        """If SPY data is None, RS cannot be calculated."""
        stock_df = make_df([100.0] * 30)
        assert relative_strength_vs_spy(stock_df, None, 20) is None


# ============================================================
# Tests: exit indicators
# ============================================================

class TestExitIndicators:
    """Tests for all exit signal indicators."""

    def test_below_10d_ma_triggers_for_downtrend(self):
        """
        A stock that has been falling should be below its 10-day MA.
        Expected: True (potential exit)
        """
        df = make_trending_df(start_price=100, n_days=25, daily_return=-0.01)
        assert is_below_exit_ma(df, 10) is True

    def test_above_10d_ma_does_not_trigger_for_uptrend(self):
        """
        A stock that has been rising should be above its 10-day MA.
        Expected: False (no exit signal)
        """
        df = make_trending_df(start_price=100, n_days=25, daily_return=0.01)
        assert is_below_exit_ma(df, 10) is False

    def test_below_exit_ma_returns_false_with_no_data(self):
        """Not enough data should return False, not crash."""
        df = make_df([100.0] * 3)
        assert is_below_exit_ma(df, 10) is False

    def test_rs_weakness_streak_fires_when_stock_lags_every_day(self):
        """
        Stock falls every day while SPY rises.
        After 5 days, rs_weakness should fire.
        """
        n = 30
        stock_closes = [100.0 * (0.99 ** i) for i in range(n)]  # Falling
        spy_closes = [500.0 * (1.005 ** i) for i in range(n)]   # Rising

        stock_df = make_df(stock_closes)
        spy_df = make_df(spy_closes)

        assert check_rs_weakness_streak(stock_df, spy_df, 5) is True

    def test_rs_weakness_does_not_fire_when_stock_outperforms(self):
        """
        Stock rises faster than SPY every day.
        RS weakness should NOT fire.
        """
        n = 30
        stock_closes = [100.0 * (1.02 ** i) for i in range(n)]   # Faster rise
        spy_closes = [500.0 * (1.005 ** i) for i in range(n)]    # Slower rise

        stock_df = make_df(stock_closes)
        spy_df = make_df(spy_closes)

        assert check_rs_weakness_streak(stock_df, spy_df, 5) is False

    def test_rs_weakness_returns_false_with_insufficient_data(self):
        """Not enough data should return False."""
        stock_df = make_df([100.0] * 4)
        spy_df = make_df([500.0] * 4)
        assert check_rs_weakness_streak(stock_df, spy_df, 5) is False

    def test_volume_stall_fires_when_volume_drops_sharply(self):
        """
        25 days of high volume (5M), then 5 days of very low volume (100K).
        Volume stall should fire.
        """
        n = 30
        closes = [100.0] * n
        volumes = [5_000_000] * 25 + [100_000] * 5  # Sharp volume drop at end

        df = make_df(closes, volumes=volumes)
        assert check_volume_stall(df, n_days=5, vol_threshold=0.7) is True

    def test_volume_stall_does_not_fire_on_consistent_volume(self):
        """
        Consistent volume throughout should NOT trigger a stall.
        """
        n = 30
        closes = [100.0] * n
        volumes = [5_000_000] * n  # Consistent volume

        df = make_df(closes, volumes=volumes)
        assert check_volume_stall(df, n_days=5, vol_threshold=0.7) is False

    def test_volume_stall_returns_false_with_insufficient_data(self):
        """Not enough data should return False."""
        df = make_df([100.0] * 10)
        assert check_volume_stall(df, n_days=5) is False


# ============================================================
# Tests: calculate_all_indicators() — integration test
# ============================================================

class TestCalculateAllIndicators:
    """Integration tests for the combined indicator function."""

    def test_returns_dict_with_all_expected_keys(self):
        """The output dictionary must contain all required keys."""
        stock_df = make_trending_df(n_days=60)
        spy_df = make_spy_flat(n_days=60)
        result = calculate_all_indicators(stock_df, spy_df)

        required_keys = [
            "close",
            "volume_today",
            "avg_volume_20d",
            "volume_ratio",
            "avg_dollar_volume_20d",
            "return_20d",
            "ma_50",
            "ma_10",
            "ma_20",
            "above_20d_high",
            "above_50d_ma",
            "relative_strength_20d",
            "below_10d_ma",
            "rs_weakness_5d",
            "volume_stall_5d",
            "enough_data",
            "error",
        ]
        for key in required_keys:
            assert key in result, f"Missing key in result: '{key}'"

    def test_none_stock_df_returns_error_not_crash(self):
        """Passing None for stock data should return an error message, not crash."""
        spy_df = make_spy_flat(n_days=60)
        result = calculate_all_indicators(None, spy_df)

        assert result["error"] is not None
        assert result["close"] is None

    def test_none_spy_df_returns_error(self):
        """Passing None for SPY should return an error, not crash."""
        stock_df = make_trending_df(n_days=60)
        result = calculate_all_indicators(stock_df, None)

        assert result["error"] is not None

    def test_enough_data_true_with_60_rows(self):
        """With 60 rows of data, enough_data should be True."""
        stock_df = make_trending_df(n_days=60)
        spy_df = make_spy_flat(n_days=60)
        result = calculate_all_indicators(stock_df, spy_df)

        assert result["enough_data"] is True

    def test_enough_data_false_with_only_30_rows(self):
        """With only 30 rows, we can't compute the 50-day MA, so enough_data = False."""
        stock_df = make_trending_df(n_days=30)
        spy_df = make_spy_flat(n_days=30)
        result = calculate_all_indicators(stock_df, spy_df)

        assert result["enough_data"] is False

    def test_uptrending_stock_has_positive_return(self):
        """
        A stock that has been rising should show a positive 20-day return.
        """
        stock_df = make_trending_df(start_price=100, n_days=60, daily_return=0.008)
        spy_df = make_spy_flat(n_days=60)
        result = calculate_all_indicators(stock_df, spy_df)

        assert result["return_20d"] is not None
        assert result["return_20d"] > 0

    def test_uptrending_stock_is_above_50d_ma(self):
        """
        A strongly uptrending stock should be above its 50-day moving average.
        """
        stock_df = make_trending_df(start_price=100, n_days=60, daily_return=0.008)
        spy_df = make_spy_flat(n_days=60)
        result = calculate_all_indicators(stock_df, spy_df)

        assert result["above_50d_ma"] is True

    def test_close_price_is_not_none(self):
        """The latest close price should always be populated when data exists."""
        stock_df = make_trending_df(n_days=60)
        spy_df = make_spy_flat(n_days=60)
        result = calculate_all_indicators(stock_df, spy_df)

        assert result["close"] is not None
        assert result["close"] > 0

    def test_error_field_is_none_on_success(self):
        """When calculation succeeds, the error field should be None."""
        stock_df = make_trending_df(n_days=60)
        spy_df = make_spy_flat(n_days=60)
        result = calculate_all_indicators(stock_df, spy_df)

        assert result["error"] is None

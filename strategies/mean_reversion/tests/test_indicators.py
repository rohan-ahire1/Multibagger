from __future__ import annotations

import math

import pytest

from strategies.mean_reversion.indicators import (
    adx,
    adv,
    atr,
    connors_rsi,
    cumulative_rsi,
    days_down,
    ibs,
    percent_rank,
    rsi,
    sma,
    streak,
    volume_percent_rank,
)


def test_rsi_two_matches_manual_sequence() -> None:
    values = [1, 2, 3, 2, 1, 2, 3]
    result = rsi(values, period=2)

    assert math.isnan(result[0])
    assert math.isnan(result[1])
    assert result[2] == 100.0
    assert result[3] == 50.0
    assert result[4] == 25.0
    assert result[5] == pytest.approx(62.5)
    assert result[6] == pytest.approx(81.25)


def test_rsi_identical_prices_returns_neutral_after_warmup() -> None:
    result = rsi([10, 10, 10, 10, 10], period=2)
    assert result[2:] == [50.0, 50.0, 50.0]


def test_rsi_monotonic_edges() -> None:
    assert rsi([1, 2, 3, 4, 5], period=2)[-1] == 100.0
    assert rsi([5, 4, 3, 2, 1], period=2)[-1] == 0.0


def test_sma_five_manual() -> None:
    result = sma([1, 2, 3, 4, 5, 6, 7], 5)
    assert [math.isnan(value) for value in result[:4]] == [True, True, True, True]
    assert result[4:] == [3.0, 4.0, 5.0]


def test_sma_two_hundred_warmup() -> None:
    result = sma(list(range(205)), 200)
    assert all(math.isnan(value) for value in result[:199])
    assert result[199] == 99.5


def test_atr_wilder_warmup_and_value() -> None:
    highs = [11.0] * 20
    lows = [10.0] * 20
    closes = [10.5] * 20
    result = atr(highs, lows, closes, period=14)

    assert all(math.isnan(value) for value in result[:14])
    assert result[14] == 1.0
    assert result[-1] == 1.0


def test_adv_is_average_volume_times_close() -> None:
    result = adv([10, 20, 30], [2, 2, 2], period=2)
    assert math.isnan(result[0])
    assert result[1:] == [30.0, 50.0]


def test_streak_counts_signed_runs() -> None:
    result = streak([100, 101, 102, 99, 98, 98, 99])
    assert math.isnan(result[0])
    assert result[1:] == [1.0, 2.0, -1.0, -2.0, 0.0, 1.0]


def test_streak_empty_and_missing_values() -> None:
    assert streak([]) == []
    result = streak([100, None, 101])
    assert all(math.isnan(value) for value in result)


def test_percent_rank_known_final_value() -> None:
    result = percent_rank([1, 5, 2, 4, 3, 6, 0, 7, 8, 9], period=5)
    assert result[-1] == 100.0
    assert result[5] == 100.0


def test_percent_rank_edges() -> None:
    assert percent_rank([], period=3) == []
    result = percent_rank([1, 2, None, 4], period=2)
    assert all(math.isnan(value) for value in result)


def test_ibs_low_high_mid_and_flat_bar() -> None:
    result = ibs([10, 10, 10, 5], [0, 0, 0, 5], [0, 10, 5, 5])
    assert result == [0.0, 1.0, 0.5, 0.5]


def test_ibs_empty_and_missing() -> None:
    assert ibs([], [], []) == []
    result = ibs([10], [0], [None])
    assert math.isnan(result[0])


def test_adx_trending_series_is_strong() -> None:
    closes = [100.0 + idx for idx in range(40)]
    highs = [close + 1.0 for close in closes]
    lows = [close - 1.0 for close in closes]
    result = adx(highs, lows, closes, period=14)
    assert result[-1] > 25.0


def test_adx_empty_and_flat_series() -> None:
    assert adx([], [], []) == []
    result = adx([10.0] * 40, [10.0] * 40, [10.0] * 40, period=14)
    assert result[-1] == 0.0


def test_cumulative_rsi_sums_last_two_rsi_values() -> None:
    closes = [1, 2, 3, 2, 1, 2, 3]
    rsi_values = rsi(closes, period=2)
    result = cumulative_rsi(closes, rsi_period=2, sum_period=2)
    assert result[-1] == pytest.approx(rsi_values[-1] + rsi_values[-2])


def test_cumulative_rsi_edges() -> None:
    assert cumulative_rsi([], rsi_period=2, sum_period=2) == []
    result = cumulative_rsi([1], rsi_period=2, sum_period=2)
    assert math.isnan(result[0])


def test_volume_percent_rank_delegates_to_percent_rank() -> None:
    volumes = [100, 110, 90, 80, 120, 130]
    assert volume_percent_rank(volumes, period=3) == percent_rank(volumes, period=3)


def test_volume_percent_rank_edges() -> None:
    assert volume_percent_rank([], period=20) == []
    result = volume_percent_rank([100, None, 90], period=2)
    assert math.isnan(result[-1])


def test_days_down_counts_last_lookback_comparisons() -> None:
    result = days_down([100, 99, 98, 99, 97, 96], lookback=5)
    assert result[-1] == 4.0


def test_days_down_edges() -> None:
    assert days_down([], lookback=5) == []
    result = days_down([100, None, 99], lookback=2)
    assert math.isnan(result[-1])


def test_connors_rsi_matches_manual_components() -> None:
    closes = [100, 101, 102, 101, 100, 99, 100, 101, 100, 98, 99, 100, 101, 99, 98, 100, 102, 101, 103, 104]
    result = connors_rsi(closes, rsi_period=3, streak_rsi_period=2, pct_rank_period=5)
    close_component = rsi(closes, period=3)[-1]
    streak_values = streak(closes)
    streak_values[0] = 0.0
    streak_component = rsi(streak_values, period=2)[-1]
    roc1 = [math.nan]
    roc1.extend((closes[idx] - closes[idx - 1]) / closes[idx - 1] for idx in range(1, len(closes)))
    rank_component = percent_rank(roc1, period=5)[-1]

    assert result[-1] == pytest.approx((close_component + streak_component + rank_component) / 3.0)


def test_connors_rsi_empty_and_warmup() -> None:
    assert connors_rsi([]) == []
    result = connors_rsi([100, 101, 100], pct_rank_period=5)
    assert all(math.isnan(value) for value in result)

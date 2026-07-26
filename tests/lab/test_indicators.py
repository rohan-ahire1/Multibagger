from __future__ import annotations

import math

import numpy as np
import pytest

from sepa_scanner.lab.indicators import (
    atr_simple, gap_close_to_close, gap_open, log_regression_slope_r2, max_gap, sma,
)


def test_log_regression_recovers_perfect_exponential_and_annualisation_identity():
    beta = 0.0004
    prices = np.exp(beta * np.arange(90))
    annualised, r_squared = log_regression_slope_r2(prices, 250)  # type: ignore[misc]
    assert annualised == pytest.approx(math.exp(beta * 250) - 1.0, abs=1e-9)
    assert annualised == pytest.approx(math.exp(beta) ** 250 - 1.0, abs=1e-12)
    assert r_squared == pytest.approx(1.0, abs=1e-12)


def test_regression_r_squared_is_low_for_seeded_noise():
    rng = np.random.default_rng(7)
    _, r_squared = log_regression_slope_r2(np.exp(rng.normal(size=90)))  # type: ignore[misc]
    assert r_squared < 0.1


def test_sma_atr_and_both_gap_variants_are_exact():
    assert np.isnan(sma([1.0, 2.0, 3.0], 3)[:2]).all()
    assert sma([1.0, 2.0, 3.0], 3)[2] == 2.0
    ohlc = {"high": [11.0, 13.0, 15.0], "low": [9.0, 10.0, 12.0], "close": [10.0, 12.0, 14.0]}
    assert atr_simple(ohlc, 2)[1] == pytest.approx(2.5)
    close_gap = gap_close_to_close([100.0, 50.0, 55.0])
    open_gap = gap_open([100.0, 100.0, 55.0], [np.nan, 100.0, 50.0])
    assert close_gap[1] == pytest.approx(0.5)
    assert open_gap[1] == pytest.approx(0.0)
    assert max_gap(close_gap, 2)[2] == pytest.approx(0.5)


def test_non_positive_price_is_rejected_before_log():
    with pytest.raises(ValueError, match="strictly positive"):
        log_regression_slope_r2([1.0, 0.0, 2.0])

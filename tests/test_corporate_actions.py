from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest


def test_load_corporate_actions_csv(tmp_path: Path) -> None:
    from sepa_scanner.data_sources.corporate_actions import load_corporate_actions_csv

    csv_content = (
        "symbol,isin,action_type,ex_date,ratio_num,ratio_den,"
        "old_face_value,new_face_value,adjustment_factor,description,source\n"
        "IRCTC,,split,2022-10-28,,,10,2,0.2,5:1 split FV 10->2,seed\n"
        "TCS,,bonus,2018-07-18,1,1,,,0.5,1:1 bonus,seed\n"
    )
    csv_path = tmp_path / "corp_actions.csv"
    csv_path.write_text(csv_content)

    df = load_corporate_actions_csv(csv_path)
    assert df.height == 2
    assert df["symbol"].to_list() == ["IRCTC", "TCS"]
    assert df["adjustment_factor"].to_list() == [0.2, 0.5]
    assert df["ex_date"].to_list() == [date(2022, 10, 28), date(2018, 7, 18)]


def test_compute_adjustment_factors_single_split() -> None:
    from sepa_scanner.data_sources.corporate_actions import compute_adjustment_factors

    ohlcv = pl.DataFrame({
        "symbol": ["ABC"] * 4,
        "date": [date(2022, 10, 26), date(2022, 10, 27), date(2022, 10, 28), date(2022, 10, 31)],
        "close": [4500.0, 4450.0, 900.0, 910.0],
    }).lazy()

    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    result = compute_adjustment_factors(ohlcv, corp_actions).collect()
    factors = dict(zip(result["date"].to_list(), result["adj_factor"].to_list()))

    assert factors[date(2022, 10, 26)] == pytest.approx(0.2)
    assert factors[date(2022, 10, 27)] == pytest.approx(0.2)
    assert factors[date(2022, 10, 28)] == pytest.approx(1.0)
    assert factors[date(2022, 10, 31)] == pytest.approx(1.0)


def test_compute_adjustment_factors_multiple_actions_stack() -> None:
    from sepa_scanner.data_sources.corporate_actions import compute_adjustment_factors

    ohlcv = pl.DataFrame({
        "symbol": ["XYZ"] * 3,
        "date": [date(2020, 1, 1), date(2021, 6, 1), date(2023, 1, 1)],
        "close": [100.0, 50.0, 10.0],
    }).lazy()

    corp_actions = pl.DataFrame({
        "symbol": ["XYZ", "XYZ"],
        "ex_date": [date(2021, 1, 1), date(2022, 1, 1)],
        "adjustment_factor": [0.5, 0.2],
    })

    result = compute_adjustment_factors(ohlcv, corp_actions).collect()
    factors = dict(zip(result["date"].to_list(), result["adj_factor"].to_list()))

    assert factors[date(2020, 1, 1)] == pytest.approx(0.1)
    assert factors[date(2021, 6, 1)] == pytest.approx(0.2)
    assert factors[date(2023, 1, 1)] == pytest.approx(1.0)


def test_adjustment_factor_irctc_real_world() -> None:
    """Verify IRCTC 5:1 split: pre-split price 4500 adjusts to 900."""
    from sepa_scanner.data_sources.corporate_actions import compute_adjustment_factors

    ohlcv = pl.DataFrame({
        "symbol": ["IRCTC"] * 3,
        "date": [date(2022, 10, 27), date(2022, 10, 28), date(2022, 10, 31)],
        "close": [4500.0, 900.0, 910.0],
    }).lazy()

    corp_actions = pl.DataFrame({
        "symbol": ["IRCTC"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    result = compute_adjustment_factors(ohlcv, corp_actions).collect()
    factors = dict(zip(result["date"].to_list(), result["adj_factor"].to_list()))

    assert factors[date(2022, 10, 27)] == pytest.approx(0.2)
    assert 4500.0 * factors[date(2022, 10, 27)] == pytest.approx(900.0)
    assert factors[date(2022, 10, 28)] == pytest.approx(1.0)


def test_compute_adjustment_factors_no_actions() -> None:
    from sepa_scanner.data_sources.corporate_actions import compute_adjustment_factors

    ohlcv = pl.DataFrame({
        "symbol": ["ABC"] * 2,
        "date": [date(2022, 1, 1), date(2022, 1, 2)],
        "close": [100.0, 101.0],
    }).lazy()

    corp_actions = pl.DataFrame(schema={
        "symbol": pl.String,
        "ex_date": pl.Date,
        "adjustment_factor": pl.Float64,
    })

    result = compute_adjustment_factors(ohlcv, corp_actions).collect()
    assert all(f == pytest.approx(1.0) for f in result["adj_factor"].to_list())


def test_build_adjusted_ohlcv_applies_factors() -> None:
    from sepa_scanner.data_sources.corporate_actions import (
        build_adjusted_ohlcv,
        compute_adjustment_factors,
    )

    ohlcv = pl.DataFrame({
        "symbol": ["ABC"] * 3,
        "date": [date(2022, 10, 26), date(2022, 10, 27), date(2022, 10, 28)],
        "open": [4400.0, 4450.0, 900.0],
        "high": [4550.0, 4500.0, 920.0],
        "low": [4350.0, 4400.0, 880.0],
        "close": [4500.0, 4450.0, 910.0],
        "volume": [1000, 1200, 5500],
    }).lazy()

    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    adj_factors = compute_adjustment_factors(ohlcv, corp_actions)
    result = build_adjusted_ohlcv(ohlcv, adj_factors).collect()

    pre_split = result.filter(pl.col("date") == date(2022, 10, 26))
    assert pre_split["close"].item() == pytest.approx(4500.0 * 0.2)
    assert pre_split["raw_close"].item() == pytest.approx(4500.0)
    assert pre_split["volume"].item() == int(1000 / 0.2)

    post_split = result.filter(pl.col("date") == date(2022, 10, 28))
    assert post_split["close"].item() == pytest.approx(910.0)
    assert post_split["raw_close"].item() == pytest.approx(910.0)
    assert post_split["volume"].item() == 5500


def test_signal_day_adjusted_equals_raw() -> None:
    """Invariant: on the signal date (most recent date), adjusted close == raw close."""
    from sepa_scanner.data_sources.corporate_actions import (
        build_adjusted_ohlcv,
        compute_adjustment_factors,
    )

    ohlcv = pl.DataFrame({
        "symbol": ["ABC"] * 3,
        "date": [date(2022, 10, 26), date(2022, 10, 27), date(2022, 10, 28)],
        "open": [4400.0, 4450.0, 900.0],
        "high": [4550.0, 4500.0, 920.0],
        "low": [4350.0, 4400.0, 880.0],
        "close": [4500.0, 4450.0, 910.0],
        "volume": [1000, 1200, 5500],
    }).lazy()

    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    adj_factors = compute_adjustment_factors(ohlcv, corp_actions)
    result = build_adjusted_ohlcv(ohlcv, adj_factors).collect()

    signal_day = result.filter(pl.col("date") == date(2022, 10, 28))
    assert signal_day["close"].item() == signal_day["raw_close"].item()
    assert signal_day["volume"].item() == signal_day["raw_volume"].item()


def test_has_split_in_window_true() -> None:
    from sepa_scanner.data_sources.corporate_actions import has_split_in_window

    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "action_type": ["split"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    assert has_split_in_window("ABC", date(2022, 10, 25), date(2022, 10, 30), corp_actions) is True


def test_has_split_in_window_false_wrong_symbol() -> None:
    from sepa_scanner.data_sources.corporate_actions import has_split_in_window

    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "action_type": ["split"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    assert has_split_in_window("XYZ", date(2022, 10, 25), date(2022, 10, 30), corp_actions) is False


def test_has_split_in_window_false_outside_range() -> None:
    from sepa_scanner.data_sources.corporate_actions import has_split_in_window

    corp_actions = pl.DataFrame({
        "symbol": ["ABC"],
        "action_type": ["split"],
        "ex_date": [date(2022, 10, 28)],
        "adjustment_factor": [0.2],
    })

    assert has_split_in_window("ABC", date(2022, 10, 20), date(2022, 10, 25), corp_actions) is False
    assert has_split_in_window("ABC", date(2022, 10, 28), date(2022, 11, 5), corp_actions) is False


def test_has_split_in_window_empty_actions() -> None:
    from sepa_scanner.data_sources.corporate_actions import has_split_in_window

    corp_actions = pl.DataFrame(schema={
        "symbol": pl.String,
        "action_type": pl.String,
        "ex_date": pl.Date,
        "adjustment_factor": pl.Float64,
    })

    assert has_split_in_window("ABC", date(2022, 10, 25), date(2022, 10, 30), corp_actions) is False

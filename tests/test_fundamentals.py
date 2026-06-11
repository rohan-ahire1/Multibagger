from __future__ import annotations

from datetime import date

import polars as pl

from sepa_scanner.data_sources.fundamentals import (
    compute_earnings_acceleration,
    compute_eps_growth_gate,
)


def test_eps_growth_gate_passes_strong_grower() -> None:
    earnings = pl.DataFrame({
        "symbol": ["ABC", "ABC"],
        "quarter_end": [date(2025, 9, 30), date(2025, 12, 31)],
        "eps": [12.0, 15.0],
        "revenue": [120.0, 150.0],
        "yoy_eps_growth": [0.20, 0.30],  # latest level 30% >= 25% default
    })
    res = compute_eps_growth_gate(earnings)
    assert res.filter(pl.col("symbol") == "ABC")["passes_filter"][0] is True


def test_eps_growth_gate_rejects_weak_grower_even_if_accelerating() -> None:
    earnings = pl.DataFrame({
        "symbol": ["XYZ", "XYZ"],
        "quarter_end": [date(2025, 9, 30), date(2025, 12, 31)],
        "eps": [10.0, 11.0],
        "revenue": [100.0, 110.0],
        "yoy_eps_growth": [0.05, 0.10],  # accelerating but level 10% < 25%
    })
    res = compute_eps_growth_gate(earnings)
    assert res.filter(pl.col("symbol") == "XYZ")["passes_filter"][0] is False


def test_eps_growth_gate_empty() -> None:
    res = compute_eps_growth_gate(pl.DataFrame(schema={
        "symbol": pl.String, "quarter_end": pl.Date, "eps": pl.Float64,
        "revenue": pl.Float64, "yoy_eps_growth": pl.Float64,
    }))
    assert res.is_empty()


def test_earnings_acceleration_detects_accelerating_growth() -> None:
    earnings = pl.DataFrame({
        "symbol": ["ABC", "ABC", "ABC"],
        "quarter_end": [date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)],
        "eps": [10.0, 12.0, 15.0],
        "revenue": [100.0, 120.0, 150.0],
        "yoy_eps_growth": [0.15, 0.20, 0.30],  # accelerating: 15% -> 20% -> 30%
    })

    result = compute_earnings_acceleration(earnings)
    abc = result.filter(pl.col("symbol") == "ABC")
    assert abc["passes_filter"][0] is True
    assert abc["acceleration"][0] > 0


def test_earnings_acceleration_rejects_decelerating_growth() -> None:
    earnings = pl.DataFrame({
        "symbol": ["XYZ", "XYZ", "XYZ"],
        "quarter_end": [date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)],
        "eps": [10.0, 12.0, 13.0],
        "revenue": [100.0, 120.0, 130.0],
        "yoy_eps_growth": [0.30, 0.25, 0.22],  # decelerating: 30% -> 25% -> 22%
    })

    result = compute_earnings_acceleration(earnings)
    xyz = result.filter(pl.col("symbol") == "XYZ")
    assert xyz["passes_filter"][0] is False


def test_empty_earnings_returns_empty_result() -> None:
    result = compute_earnings_acceleration(pl.DataFrame(schema={
        "symbol": pl.String,
        "quarter_end": pl.Date,
        "eps": pl.Float64,
        "revenue": pl.Float64,
        "yoy_eps_growth": pl.Float64,
    }))
    assert result.is_empty()


def test_apply_fundamental_gate_point_in_time(tmp_path) -> None:
    import polars as pl
    from sepa_scanner.data_sources.fundamentals import apply_fundamental_gate
    # earnings: ABC strong (30%), DEF weak (5%); known 75d after period_end
    earn = pl.DataFrame({
        "symbol": ["ABC", "DEF"],
        "period_end": [date(2023, 3, 31), date(2023, 3, 31)],
        "freq": ["annual", "annual"],
        "yoy_growth": [0.30, 0.05],
    })
    p = tmp_path / "earn.csv"
    earn.write_csv(p)
    signals = pl.DataFrame({
        "signal_date": [date(2023, 9, 1), date(2023, 9, 1), date(2023, 9, 1)],
        "symbol": ["ABC", "DEF", "GHI"],  # GHI has no data
        "rs_rank": [80.0, 80.0, 80.0],
    })
    out = apply_fundamental_gate(signals, data_path=p, min_yoy_growth=0.25, freq="annual")
    kept = set(out["symbol"].to_list())
    assert kept == {"ABC", "GHI"}  # ABC passes, DEF dropped, GHI kept (no data)


def test_apply_fundamental_gate_respects_reporting_lag(tmp_path) -> None:
    import polars as pl
    from sepa_scanner.data_sources.fundamentals import apply_fundamental_gate
    earn = pl.DataFrame({
        "symbol": ["ABC"], "period_end": [date(2023, 3, 31)],
        "freq": ["annual"], "yoy_growth": [0.30],
    })
    p = tmp_path / "e.csv"; earn.write_csv(p)
    # signal BEFORE results are known (Apr 2023, lag 75d -> known mid-Jun) -> no data -> kept
    signals = pl.DataFrame({"signal_date": [date(2023, 4, 1)], "symbol": ["ABC"], "rs_rank": [80.0]})
    out = apply_fundamental_gate(signals, data_path=p, min_yoy_growth=0.25, reporting_lag_days=75, freq="annual")
    assert out.height == 1  # kept via pass_if_missing (results not yet public)

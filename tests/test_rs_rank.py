from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from sepa_scanner.indicators.rs import compute_rs_ranks


def test_rs_skips_recent_days_when_configured() -> None:
    """RS with skip_recent_days=1 should use close[t-1] as the 'current' price."""
    start = date(2026, 3, 2)
    dates = [start + timedelta(days=offset) for offset in range(5)]
    frame = pl.DataFrame(
        {
            "date": dates * 3,
            "symbol": (
                ["NIFTYBEES"] * 5
                + ["AAA"] * 5
                + ["BBB"] * 5
            ),
            "close": [
                100.0, 105.0, 110.0, 115.0, 120.0,  # benchmark
                100.0, 110.0, 120.0, 130.0, 90.0,    # AAA: strong then crashes on last day
                100.0, 102.0, 104.0, 106.0, 140.0,   # BBB: weak then spikes on last day
            ],
        }
    )

    # Without skip: BBB's last-day spike makes it look strong
    result_no_skip = compute_rs_ranks(
        frame=frame, benchmark_symbol="NIFTYBEES",
        lookbacks=[1, 2], weights=[0.5, 0.5],
        scan_date=dates[-1], symbols=["AAA", "BBB"],
    )

    # With skip=1: should rank using close[t-1] instead of close[t]
    result_skip = compute_rs_ranks(
        frame=frame, benchmark_symbol="NIFTYBEES",
        lookbacks=[1, 2], weights=[0.5, 0.5],
        scan_date=dates[-1], symbols=["AAA", "BBB"],
        skip_recent_days=1,
    )

    # Without skip, BBB ranks higher due to spike
    no_skip_top = result_no_skip.sort("rs_rank", descending=True)["symbol"][0]
    assert no_skip_top == "BBB"

    # With skip, AAA ranks higher (130 vs 106, ignoring last-day noise)
    skip_top = result_skip.sort("rs_rank", descending=True)["symbol"][0]
    assert skip_top == "AAA"


def test_rs_scores_and_percentile_ranks_are_cross_sectional() -> None:
    start = date(2026, 3, 2)
    dates = [start + timedelta(days=offset) for offset in range(4)]
    frame = pl.DataFrame(
        {
            "date": dates * 4,
            "symbol": (
                ["NIFTYBEES"] * 4
                + ["AAA"] * 4
                + ["BBB"] * 4
                + ["CCC"] * 4
            ),
            "close": [
                100.0,
                105.0,
                110.0,
                115.0,
                100.0,
                110.0,
                120.0,
                130.0,
                100.0,
                102.0,
                104.0,
                106.0,
                100.0,
                98.0,
                99.0,
                97.0,
            ],
        }
    )

    result = compute_rs_ranks(
        frame=frame,
        benchmark_symbol="NIFTYBEES",
        lookbacks=[1, 2, 3],
        weights=[0.4, 0.3, 0.3],
        scan_date=dates[-1],
        symbols=["AAA", "BBB", "CCC"],
    ).sort("rs_rank", descending=True)

    assert result["symbol"].to_list() == ["AAA", "BBB", "CCC"]
    assert result["rs_rank"].to_list() == [100.0, 50.0, 0.0]
    assert result["rs_score"][0] > result["rs_score"][1] > result["rs_score"][2]

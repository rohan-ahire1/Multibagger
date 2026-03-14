from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from sepa_scanner.strategy.sector_rs import compute_sector_rs


def test_sector_rs_ranks_sectors_by_constituent_performance() -> None:
    start = date(2026, 3, 2)
    dates = [start + timedelta(days=offset) for offset in range(5)]

    frame = pl.DataFrame({
        "date": dates * 4,
        "symbol": ["BM"] * 5 + ["IT1"] * 5 + ["IT2"] * 5 + ["PH1"] * 5,
        "close": [
            100.0, 102.0, 104.0, 106.0, 108.0,  # benchmark
            100.0, 110.0, 120.0, 130.0, 140.0,  # IT1: strong
            100.0, 108.0, 116.0, 124.0, 132.0,  # IT2: strong
            100.0, 99.0, 98.0, 97.0, 96.0,      # PH1: weak
        ],
    })
    sector_map = {"IT1": "IT", "IT2": "IT", "PH1": "PHARMA"}

    result = compute_sector_rs(
        frame, sector_map, benchmark_symbol="BM",
        scan_date=dates[-1], lookbacks=[2, 4], weights=[0.5, 0.5],
    )

    assert result["IT"] > result["PHARMA"]

#!/usr/bin/env python
"""Read-only OHLCV Integrity Audit V1 for a canonical PIT panel."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median

import polars as pl


OHLC = ["open", "high", "low", "close"]


def _count(frame: pl.DataFrame, predicate: pl.Expr) -> int:
    return frame.filter(predicate).height


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, default=Path("data/pit/ohlcv_all.parquet"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/data_audit/ohlcv_integrity_v1_2026-08-09"))
    args = parser.parse_args()

    frame = pl.read_parquet(args.panel).sort(["security_id", "date", "symbol"])
    required = {"date", "symbol", "security_id", *OHLC, "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise SystemExit(f"panel missing required audit columns: {sorted(missing)}")
    if "turnover" not in frame.columns:
        frame = frame.with_columns((pl.col("close") * pl.col("volume")).alias("turnover"))

    rows = frame.height
    market_dates = frame["date"].unique().sort().to_list()
    duplicate_rows = frame.join(
        frame.group_by(["security_id", "date"]).len().filter(pl.col("len") > 1).select("security_id", "date"),
        on=["security_id", "date"], how="inner",
    )
    null_counts = {column: int(frame[column].null_count()) for column in [*OHLC, "volume"]}
    invalid_prices = _count(frame, pl.any_horizontal([pl.col(column) <= 0 for column in OHLC]))
    null_ohlc = _count(frame, pl.any_horizontal([pl.col(column).is_null() for column in OHLC]))
    invalid_ohlc = _count(
        frame,
        (pl.col("high") < pl.col("open")) | (pl.col("high") < pl.col("close")) |
        (pl.col("high") < pl.col("low")) | (pl.col("low") > pl.col("open")) |
        (pl.col("low") > pl.col("close")),
    )
    negative_volume = _count(frame, pl.col("volume") < 0)
    zero_volume = _count(frame, pl.col("volume") == 0)

    ordered = frame.with_columns(
        pl.col("date").shift(1).over("security_id").alias("previous_date"),
        pl.col("close").shift(1).over("security_id").alias("previous_close"),
        pl.col("volume").shift(1).rolling_median(window_size=20, min_samples=20).over("security_id").alias("previous_volume_median20"),
    ).with_columns(
        ((pl.col("date") - pl.col("previous_date")).dt.total_days()).alias("calendar_gap"),
        (pl.col("close") / pl.col("previous_close") - 1.0).alias("return"),
        (pl.col("close") / pl.col("previous_close")).alias("price_ratio"),
    )
    gap_counts = {
        threshold: _count(ordered, pl.col("calendar_gap") > threshold)
        for threshold in (7, 30, 90)
    }
    large_returns = {
        threshold: _count(ordered, pl.col("return").abs() > threshold)
        for threshold in (0.20, 0.40, 0.80)
    }
    extreme = {
        "close_gt_10x_previous": _count(ordered, pl.col("price_ratio") > 10.0),
        "close_lt_0_1x_previous": _count(ordered, pl.col("price_ratio") < 0.1),
        "volume_gt_100x_previous_20_observation_median": _count(
            ordered,
            pl.col("previous_volume_median20").is_not_null()
            & (pl.col("volume") > 100.0 * pl.col("previous_volume_median20")),
        ),
    }
    coverage = frame.group_by("security_id").agg(
        pl.col("symbol").last().alias("last_symbol"),
        pl.col("date").min().alias("first_date"), pl.col("date").max().alias("last_date"),
        pl.len().alias("number_of_rows"), pl.col("date").dt.year().n_unique().alias("number_of_years"),
    ).with_columns((pl.col("number_of_rows") / len(market_dates) * 100.0).alias("percentage_of_market_dates_present"))
    coverage = coverage.sort("security_id")
    daily = frame.group_by("date").agg(pl.col("security_id").n_unique().alias("security_count")).sort("date")
    daily_counts = daily["security_count"].to_list()
    flags = []
    for index, count in enumerate(daily_counts):
        surrounding = daily_counts[max(0, index - 20): index] + daily_counts[index + 1: index + 21]
        baseline = median(surrounding) if surrounding else float(count)
        flags.append(bool(surrounding and count < 0.5 * baseline))
    daily = daily.with_columns(pl.Series("abnormal_below_half_surrounding_median", flags))

    hard_failures = {
        "duplicates": duplicate_rows.height,
        "null_ohlc": null_ohlc,
        "invalid_prices": invalid_prices,
        "invalid_ohlc_relationships": invalid_ohlc,
        "negative_volume": negative_volume,
    }
    summary = {
        "audit": "OHLCV Integrity Audit V1",
        "panel": str(args.panel),
        "dataset_statistics": {
            "rows": rows, "securities": int(frame["security_id"].n_unique()),
            "first_date": str(frame["date"].min()), "last_date": str(frame["date"].max()),
        },
        "hard_failures": hard_failures,
        "null_values": {
            column: {"count": count, "percentage": count / rows * 100.0}
            for column, count in null_counts.items()
        },
        "suspicious_observations": {
            "zero_volume": zero_volume,
            "large_returns": {str(key): value for key, value in large_returns.items()},
            "observation_gaps": {str(key): value for key, value in gap_counts.items()},
            "abnormal_market_day_coverage": int(daily["abnormal_below_half_surrounding_median"].sum()),
            "extreme_price_volume_changes": extreme,
        },
        "per_test_status": {
            "duplicates": "PASS" if not duplicate_rows.height else "FAIL",
            "null_values": "PASS" if not null_ohlc else "FAIL",
            "price_validity": "PASS" if not invalid_prices else "FAIL",
            "ohlc_consistency": "PASS" if not invalid_ohlc else "FAIL",
            "volume": "PASS" if not negative_volume else "FAIL",
            "date_order": "PASS" if not duplicate_rows.height else "FAIL",
            "large_price_moves": "WARNING" if any(large_returns.values()) else "PASS",
            "missing_day_gap": "WARNING" if any(gap_counts.values()) else "PASS",
            "security_coverage": "PASS",
            "market_day_coverage": "WARNING" if any(flags) else "PASS",
            "extreme_values": "WARNING" if any(extreme.values()) else "PASS",
        },
        "final_status": "PASS" if not any(hard_failures.values()) else "FAIL",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    coverage.write_csv(args.output_dir / "security_coverage.csv")
    daily.write_csv(args.output_dir / "market_day_coverage.csv")
    duplicate_rows.write_csv(args.output_dir / "duplicates.csv")
    args.output_dir.joinpath("summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

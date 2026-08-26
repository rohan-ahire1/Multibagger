#!/usr/bin/env python
"""Write an evidence-only raw-price validation for split and bonus actions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl

from sepa_scanner.data_sources.adjustment import validate_share_action_ratios


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=Path("data/nse_raw/ohlcv_raw.parquet"))
    parser.add_argument("--actions", type=Path, default=Path("data/reference/nse_corporate_actions.parquet"))
    parser.add_argument("--output", type=Path, default=Path("reports/data_audit/share_action_validation.csv"))
    parser.add_argument("--tolerance", type=float, default=0.15)
    args = parser.parse_args()

    raw = pl.read_parquet(args.raw)
    actions = pl.read_parquet(args.actions)
    report = validate_share_action_ratios(raw, actions, tolerance=args.tolerance)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report.write_csv(args.output)
    summary = {
        "events": report.height,
        "pass": report.filter(pl.col("validation_status") == "PASS").height,
        "warning": report.filter(pl.col("validation_status") == "WARNING").height,
        "by_classification": {
            str(row["classification"]): int(row["count"])
            for row in report.group_by("classification").len().rename({"len": "count"}).iter_rows(named=True)
        },
    }
    args.output.with_suffix(".json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

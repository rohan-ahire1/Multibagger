#!/usr/bin/env python
"""Read-only Corporate Action / Point-in-Time Audit V2."""
from __future__ import annotations

import argparse
import bisect
import json
from datetime import timedelta
from pathlib import Path

import polars as pl


SHARE_ACTIONS = {"split", "bonus", "bonus_split", "face_value", "consolidation"}


def _key(isin: object, symbol: object) -> str:
    value = str(isin or "").strip()
    return f"isin:{value}" if value else f"symbol:{str(symbol)}"


def _code_contains(path: Path, text: str) -> bool:
    return text in path.read_text(encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pit", type=Path, default=Path("data/pit/ohlcv_all.parquet"))
    parser.add_argument("--actions", type=Path, default=Path("data/reference/nse_corporate_actions.parquet"))
    parser.add_argument("--coverage", type=Path, default=Path("data/reference/nse_corporate_actions_coverage.parquet"))
    parser.add_argument("--share-validation", type=Path, default=Path("reports/data_audit/share_action_validation_2026-08-09.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/data_audit/corporate_action_pit_v2_2026-08-09"))
    args = parser.parse_args()

    pit = pl.read_parquet(args.pit).sort(["security_id", "date", "symbol"])
    actions = pl.read_parquet(args.actions)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    dataset_end = pit["date"].max()
    dataset_start = pit["date"].min()

    action_counts = actions.group_by("action_type").len().rename({"len": "events"}).sort("action_type")
    action_years = actions.with_columns(pl.col("ex_date").dt.year().alias("year")).group_by(["year", "action_type"]).len().rename({"len": "events"}).sort(["year", "action_type"])
    action_counts.write_csv(args.output_dir / "corporate_action_counts_by_type.csv")
    action_years.write_csv(args.output_dir / "corporate_action_counts_by_year.csv")
    coverage = pl.read_parquet(args.coverage) if args.coverage.exists() else pl.DataFrame()
    coverage_issues = coverage.filter(pl.col("status") != "fetched") if not coverage.is_empty() else pl.DataFrame()

    lifecycle = pit.group_by("security_id").agg(
        pl.col("symbol").last().alias("last_symbol"), pl.col("date").min().alias("first_date"),
        pl.col("date").max().alias("last_date"), pl.len().alias("rows"),
    )
    active = lifecycle.filter(pl.col("last_date") == dataset_end).height
    historically_ended = lifecycle.filter(pl.col("last_date") < dataset_end).height
    ended_old = lifecycle.filter(pl.col("last_date") < dataset_end - timedelta(days=365))
    ended_by_year = ended_old.with_columns(pl.col("last_date").dt.year().alias("final_year")).group_by("final_year").len().rename({"len": "securities"}).sort("final_year")
    lifecycle.sort("security_id").write_csv(args.output_dir / "security_lifecycle.csv")
    ended_by_year.write_csv(args.output_dir / "historically_ended_by_final_year.csv")

    eras = pit.group_by(["security_id", "symbol"]).agg(
        pl.col("date").min().alias("first_date"), pl.col("date").max().alias("last_date"),
        pl.col("identity_quality").first().alias("identity_quality"),
    ).sort(["security_id", "first_date"])
    multiple_symbols = eras.group_by("security_id").len().filter(pl.col("len") > 1).height
    security_overlap = eras.with_columns(
        pl.col("last_date").shift(1).over("security_id").alias("previous_last_date")
    ).filter(pl.col("previous_last_date").is_not_null() & (pl.col("first_date") <= pl.col("previous_last_date")))
    symbol_eras = eras.sort(["symbol", "first_date"])
    reused_symbols = symbol_eras.group_by("symbol").len().filter(pl.col("len") > 1).height
    symbol_overlap = symbol_eras.with_columns(
        pl.col("last_date").shift(1).over("symbol").alias("previous_last_date"),
        pl.col("security_id").shift(1).over("symbol").alias("previous_security_id"),
        pl.col("identity_quality").shift(1).over("symbol").alias("previous_identity_quality"),
    ).filter(pl.col("previous_last_date").is_not_null() & (pl.col("first_date") <= pl.col("previous_last_date")))
    demonstrable_symbol_conflicts = symbol_overlap.filter(
        (pl.col("identity_quality") == "verified") & (pl.col("previous_identity_quality") == "verified")
    )
    unverifiable_symbol_overlaps = symbol_overlap.filter(
        (pl.col("identity_quality") != "verified") | (pl.col("previous_identity_quality") != "verified")
    )
    security_overlap.write_csv(args.output_dir / "overlapping_security_symbol_eras.csv")
    symbol_overlap.write_csv(args.output_dir / "overlapping_symbol_security_eras.csv")
    unverified_rows = pit.filter(pl.col("identity_quality") == "unverified").height

    # Match extreme raw moves to explicit/possible actions within ±5 observed sessions.
    moves = pit.with_columns(
        (pl.col("close") / pl.col("close").shift(1).over("security_id")).alias("price_ratio")
    ).filter((pl.col("price_ratio") > 10.0) | (pl.col("price_ratio") < 0.1)).select(
        "security_id", "symbol", "date", "price_ratio"
    )
    observed_dates: dict[str, list] = {}
    for key, group in pit.select("security_id", "date").group_by("security_id", maintain_order=True):
        observed_dates[str(key[0])] = group.sort("date")["date"].to_list()
    action_by_key: dict[str, list[dict[str, object]]] = {}
    for event in actions.iter_rows(named=True):
        action_by_key.setdefault(_key(event.get("isin"), event["symbol"]), []).append(event)
    classifications: list[dict[str, object]] = []
    for move in moves.iter_rows(named=True):
        sec = str(move["security_id"])
        event_key = sec if sec.startswith("isin:") else f"symbol:{move['symbol']}"
        dates = observed_dates.get(sec, [])
        idx = bisect.bisect_left(dates, move["date"])
        near: list[dict[str, object]] = []
        for event in action_by_key.get(event_key, []):
            event_idx = bisect.bisect_left(dates, event["ex_date"])
            if abs(event_idx - idx) <= 5:
                near.append(event)
        if any(str(event["action_type"]) in SHARE_ACTIONS for event in near):
            status = "EXPLAINED_CORPORATE_ACTION"
        elif near:
            status = "POSSIBLE_CORPORATE_ACTION"
        else:
            status = "UNEXPLAINED"
        classifications.append({**move, "classification": status, "nearby_actions": " | ".join(sorted({str(event["action_type"]) for event in near}))})
    extreme_report = pl.DataFrame(
        classifications,
        schema={"security_id": pl.Utf8, "symbol": pl.Utf8, "date": pl.Date, "price_ratio": pl.Float64, "classification": pl.Utf8, "nearby_actions": pl.Utf8},
    )
    extreme_report.write_csv(args.output_dir / "extreme_move_classification.csv")
    extreme_counts = {
        str(row["classification"]): int(row["events"])
        for row in extreme_report.group_by("classification").len().rename({"len": "events"}).iter_rows(named=True)
    } if not extreme_report.is_empty() else {}

    validation = pl.read_csv(args.share_validation) if args.share_validation.exists() else pl.DataFrame()
    validation_counts = {
        str(row["classification"]): int(row["events"])
        for row in validation.group_by("classification").len().rename({"len": "events"}).iter_rows(named=True)
    } if not validation.is_empty() else {}

    validation_source = Path("strategies/mean_reversion/validation.py")
    diagnostics_source = Path("strategies/mean_reversion/diagnostics.py")
    signal_source = Path("strategies/mean_reversion/signal_engine.py")
    order_source = Path("src/sepa_scanner/lab/engines/portfolio_order_engine.py")
    config_source = Path("strategies/mean_reversion/config.yaml")
    universe_pass = (
        not _code_contains(validation_source, "load_nifty100_universe")
        and not _code_contains(diagnostics_source, "load_nifty100_universe")
        and _code_contains(validation_source, "historical_liquidity_membership")
        and _code_contains(diagnostics_source, "historical_liquidity_membership")
    )
    leakage_pass = (
        _code_contains(signal_source, "assert_pit_signal_inputs")
        and not _code_contains(signal_source, "opens[idx + 1]")
        and not _code_contains(order_source, "future_dates")
        and "data/pit/ohlcv_all.parquet" in config_source.read_text(encoding="utf-8")
    )
    pit_manifest = json.loads(args.pit.with_name("manifest.json").read_text(encoding="utf-8"))
    adjusted_manifest = json.loads(Path("data/adjusted/total_return/manifest.json").read_text(encoding="utf-8"))
    parser_columns = {"raw_purpose", "original_source_text", "parser_rule", "parser_version", "confidence", "source_date", "ex_date"}
    parser_coverage_pass = parser_columns <= set(actions.columns) and coverage_issues.is_empty()
    survivorship_status = "WARNING" if active / lifecycle.height >= 0.95 else "PASS"
    identity_status = "PASS" if security_overlap.is_empty() and demonstrable_symbol_conflicts.is_empty() else "FAIL"
    if identity_status == "PASS" and unverified_rows:
        identity_status = "WARNING"

    summary = {
        "audit": "Corporate Action / PIT Audit V2",
        "dataset_dates": {"first": str(dataset_start), "last": str(dataset_end)},
        "corporate_action_coverage": {
            "counts_by_type": {str(row["action_type"]): int(row["events"]) for row in action_counts.iter_rows(named=True)},
            "coverage_status": "PASS" if parser_coverage_pass else "WARNING",
            "source_coverage_issues": coverage_issues.height,
        },
        "security_lifecycle": {
            "active_securities": active, "historically_ended_securities": historically_ended,
            "historically_ended_percentage": historically_ended / lifecycle.height * 100.0,
            "listing_date_status": "UNVERIFIABLE", "delisting_date_status": "UNVERIFIABLE",
        },
        "security_identity": {
            "securities_with_multiple_symbols": multiple_symbols, "reused_symbols": reused_symbols,
            "overlapping_security_symbol_conflicts": security_overlap.height,
            "overlapping_symbol_security_conflicts": demonstrable_symbol_conflicts.height,
            "unverifiable_symbol_identity_overlaps": unverifiable_symbol_overlaps.height,
            "unverified_identity_rows": unverified_rows, "status": identity_status,
        },
        "adjustment_validation": {
            "pit_methodology": "RAW", "pit_manifest_point_in_time_eligible": pit_manifest.get("point_in_time_eligible"),
            "retrospective_methodology": "ADJUSTED", "adjusted_manifest_point_in_time_eligible": adjusted_manifest.get("point_in_time_eligible"),
            "split_bonus_validation": validation_counts, "extreme_move_classification": extreme_counts,
        },
        "survivorship": {"status": survivorship_status, "ended_more_than_365_days_before_end": ended_old.height},
        "point_in_time_universe": {"status": "PASS" if universe_pass else "FAIL"},
        "look_ahead_leakage": {"status": "PASS" if leakage_pass else "FAIL"},
        "final_status": "RESEARCH_READY_WITH_DOCUMENTED_WARNINGS" if all([
            parser_coverage_pass, universe_pass, leakage_pass, identity_status != "FAIL"
        ]) else "NOT_RESEARCH_READY",
    }
    args.output_dir.joinpath("summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

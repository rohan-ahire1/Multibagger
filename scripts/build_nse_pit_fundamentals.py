"""Build point-in-time NSE quarterly fundamentals for SEPA research.

The collector starts from the exchange's historical filing list, keeps only
filings that could have informed an existing canonical signal, and then fetches
the corresponding result details.  Exchange dissemination timestamps—not
period-end lag assumptions—define when each result became usable.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from threading import local
from typing import Any

import polars as pl

from sepa_scanner.data_sources.fundamentals import (
    fetch_nse_financial_result_detail,
    fetch_nse_financial_result_filings,
)
from sepa_scanner.utils.http import bootstrap_nse_session, build_nse_session


ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "data/backtest/signal_quality_research/signal_feature_panel.parquet"
OUTPUT_DIR = ROOT / "data/reference"
METADATA_PATH = OUTPUT_DIR / "nse_pit_financial_filings.parquet"
DETAIL_PATH = OUTPUT_DIR / "nse_pit_financial_details.parquet"
_THREAD_STATE = local()


def _canonical_signal_dates(panel_path: Path) -> dict[str, list[date]]:
    panel = pl.read_parquet(panel_path).filter(pl.col("canonical_selected"))
    grouped = panel.group_by("symbol").agg(pl.col("signal_date").unique().sort())
    return {
        str(row["symbol"]): list(row["signal_date"])
        for row in grouped.iter_rows(named=True)
    }


def _relevant_filings(
    filings: pl.DataFrame,
    signal_dates: dict[str, list[date]],
    *,
    periods_per_basis: int = 6,
) -> pl.DataFrame:
    """Keep the latest six known periods for each signal and reporting basis."""
    selected_seq: set[str] = set()
    for symbol, dates in signal_dates.items():
        symbol_rows = filings.filter(pl.col("symbol") == symbol).sort("filing_timestamp")
        if symbol_rows.is_empty():
            continue
        rows = symbol_rows.to_dicts()
        for signal_date in dates:
            cutoff = signal_date - timedelta(days=800)
            known = [
                row
                for row in rows
                if row.get("available_date") is not None
                and row["available_date"] <= signal_date
                and row.get("period_end") is not None
                and cutoff <= row["period_end"] <= signal_date
            ]
            for basis in ("Consolidated", "Non-Consolidated"):
                latest_by_period: dict[date, dict[str, Any]] = {}
                for row in known:
                    if row.get("consolidated") == basis:
                        latest_by_period[row["period_end"]] = row
                latest_periods = sorted(latest_by_period, reverse=True)[:periods_per_basis]
                for period_end in latest_periods:
                    seq = latest_by_period[period_end].get("seq_number")
                    if seq:
                        selected_seq.add(str(seq))
    return filings.filter(pl.col("seq_number").is_in(sorted(selected_seq)))


def _thread_session():
    session = getattr(_THREAD_STATE, "session", None)
    if session is None:
        session = bootstrap_nse_session(build_nse_session(), timeout=30)
        _THREAD_STATE.session = session
    return session


def _fetch_detail(row: dict[str, Any]) -> dict[str, Any]:
    detail = fetch_nse_financial_result_detail(
        row,
        session=_thread_session(),
        timeout=30,
    )
    return {
        **row,
        "detail_period_end": detail.get("period_end"),
        "detail_filing_timestamp": detail.get("filing_timestamp"),
        "detail_available_date": detail.get("available_date"),
        "net_profit": detail.get("net_profit"),
        "revenue": detail.get("revenue"),
        "eps": detail.get("eps"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2009, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 3, 17))
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--refresh-metadata", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    signal_dates = _canonical_signal_dates(PANEL_PATH)
    print(f"Canonical signal symbols: {len(signal_dates)}")

    if METADATA_PATH.exists() and not args.refresh_metadata:
        filings = pl.read_parquet(METADATA_PATH)
        print(f"Loaded cached filing metadata: {filings.height:,}")
    else:
        filings = fetch_nse_financial_result_filings(args.start, args.end, timeout=30)
        filings.write_parquet(METADATA_PATH)
        print(f"Saved filing metadata: {filings.height:,}")

    relevant = _relevant_filings(filings, signal_dates)
    print(f"Relevant filing details: {relevant.height:,}")

    existing = pl.DataFrame()
    completed: set[str] = set()
    if DETAIL_PATH.exists():
        existing = pl.read_parquet(DETAIL_PATH)
        completed = set(existing["seq_number"].drop_nulls().cast(pl.String).to_list())
        print(f"Loaded cached details: {len(completed):,}")

    pending = [
        row
        for row in relevant.to_dicts()
        if str(row.get("seq_number")) not in completed
    ]
    fetched: list[dict[str, Any]] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(_fetch_detail, row): row for row in pending}
        for number, future in enumerate(as_completed(futures), start=1):
            try:
                fetched.append(future.result())
            except Exception as exc:  # retain progress; a rerun retries failures
                errors += 1
                row = futures[future]
                print(f"detail error {row.get('symbol')} {row.get('seq_number')}: {exc}")
            if number % 250 == 0 or number == len(pending):
                print(f"Fetched {number:,}/{len(pending):,}; errors={errors}")

    frames = []
    if not existing.is_empty():
        frames.append(existing)
    if fetched:
        frames.append(pl.from_dicts(fetched, infer_schema_length=None))
    if not frames:
        raise RuntimeError("No NSE financial-result details were collected")

    details = pl.concat(frames, how="diagonal_relaxed").unique(
        subset=["seq_number"], keep="last", maintain_order=True
    ).sort(["symbol", "available_date", "period_end"])
    details.write_parquet(DETAIL_PATH)
    details.write_csv(DETAIL_PATH.with_suffix(".csv"))
    usable = details.filter(
        pl.any_horizontal(
            pl.col("net_profit").is_not_null(),
            pl.col("revenue").is_not_null(),
            pl.col("eps").is_not_null(),
        )
    )
    print(
        f"Saved {details.height:,} details ({usable.height:,} usable); "
        f"errors={errors}"
    )


if __name__ == "__main__":
    main()

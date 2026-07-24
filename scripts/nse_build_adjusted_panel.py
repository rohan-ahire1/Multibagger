"""Build the survivorship-free, corporate-action-adjusted price panel.

Pipeline: raw daily bhavcopy zips -> raw panel -> apply split/bonus (Phase 1) or
split/bonus/dividend (Phase 2) back-adjustment -> data/nse_adjusted/ohlcv_all.parquet
(the schema the app expects). The legacy yfinance panel is left untouched; flip
``config/config.yaml`` -> ``data.source: nse_bhavcopy`` to make the app use this one.

Usage:
    uv run python scripts/nse_build_adjusted_panel.py                 # Phase 1 (price return)
    uv run python scripts/nse_build_adjusted_panel.py --dividends     # Phase 2 (total return)
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

from sepa_scanner.data_sources.adjustment import apply_adjustment
from sepa_scanner.storage.bhav_raw_store import BhavRawStore
from sepa_scanner.utils.io import ensure_directory

LOGGER = logging.getLogger("nse_build_adjusted")

RAW_ZIP_ROOT = Path("data/nse_bhavcopy_raw")
RAW_PANEL_ROOT = Path("data/nse_raw")
OUT_ROOT = Path("data/nse_adjusted")
CA_PATH = Path("data/reference/nse_corporate_actions.parquet")
QUARANTINE = Path("reports/data_audit/adjustment_quarantine.csv")


def _load_ca(dividends: bool, raw: pl.DataFrame | None = None) -> pl.DataFrame:
    ca = pl.read_parquet(CA_PATH)
    keep = ["split", "bonus", "face_value", "bonus_split"] + (
        ["dividend"] if dividends else []
    )
    ca = ca.filter(pl.col("action_type").is_in(keep))
    if dividends and raw is not None:
        # Dividend multiplier needs the raw close on the last trading bar before ex-date.
        prev = raw.select("symbol", "date", "close").rename({"close": "prev_close"})
        div = ca.filter(pl.col("action_type") == "dividend").sort(["symbol", "ex_date"])
        div = div.join_asof(
            prev.sort(["symbol", "date"]).rename({"date": "ex_date"}),
            on="ex_date",
            by="symbol",
            strategy="backward",
        )
        others = ca.filter(pl.col("action_type") != "dividend").with_columns(
            pl.lit(None, dtype=pl.Float64).alias("prev_close")
        )
        ca = pl.concat([others, div], how="diagonal")
    return ca


def _quarantine_bad_jumps(panel: pl.DataFrame, max_jump: float = 4.0) -> None:
    """Flag adjusted single-day jumps > max_jump not backed by a CA (audit guardrail)."""
    d = panel.sort(["symbol", "date"]).with_columns(
        (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1).alias("ret")
    )
    bad = d.filter((pl.col("ret").abs() > max_jump) | (pl.col("close") <= 0))
    ensure_directory(QUARANTINE.parent)
    bad.select("symbol", "date", "close", "ret").write_csv(QUARANTINE)
    if bad.height:
        LOGGER.warning("%d suspect adjusted rows quarantined -> %s", bad.height, QUARANTINE)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dividends", action="store_true", help="Phase 2 total-return panel")
    ap.add_argument("--series", default="EQ,BE")
    ap.add_argument("--rebuild-raw", action="store_true",
                    help="rebuild raw panel from zips (else reuse data/nse_raw)")
    args = ap.parse_args()

    raw_store = BhavRawStore(RAW_PANEL_ROOT)
    if args.rebuild_raw or not raw_store.flat_path.exists():
        raw = raw_store.build_raw_panel(
            RAW_ZIP_ROOT, allowed_series=[s.strip() for s in args.series.split(",")]
        )
    else:
        raw = raw_store.read_frame()
    LOGGER.info("raw panel: %d rows, %d symbols", raw.height, raw["symbol"].n_unique())

    ca = _load_ca(args.dividends, raw=raw)
    adjusted = apply_adjustment(raw, ca)
    _quarantine_bad_jumps(adjusted)

    ensure_directory(OUT_ROOT)
    out = OUT_ROOT / "ohlcv_all.parquet"
    adjusted.write_parquet(out, compression="zstd")

    manifest = {
        "dataset_version": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "total_return" if args.dividends else "price_return",
        "rows": adjusted.height,
        "symbols": int(adjusted["symbol"].n_unique()),
        "date_min": str(adjusted["date"].min()),
        "date_max": str(adjusted["date"].max()),
        "ca_events_applied": ca.height,
        "series": args.series,
    }
    (OUT_ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    LOGGER.info("Wrote %s\n%s", out, json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

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
from sepa_scanner.data_sources.security_identity import (
    apply_canonical_symbols,
    build_rename_map,
)
from sepa_scanner.storage.bhav_raw_store import BhavRawStore
from sepa_scanner.utils.io import ensure_directory

LOGGER = logging.getLogger("nse_build_adjusted")

RAW_ZIP_ROOT = Path("data/nse_bhavcopy_raw")
RAW_PANEL_ROOT = Path("data/nse_raw")
OUT_ROOT = Path("data/nse_adjusted")
CA_PATH = Path("data/reference/nse_corporate_actions.parquet")
CA_OVERRIDES_PATH = Path("config/corporate_actions.csv")

# Override rows are applied only if their source is listed here. `seed` is
# deliberately excluded: those rows predate the CA feed and four of the six
# disagree with the raw prices on their own stated ex-date.
APPLIED_CA_SOURCES = ("manual", "purpose_parsed", "isin_corroborated", "etf_inferred")
QUARANTINE = Path("reports/data_audit/adjustment_quarantine.csv")

# Columns the adjustment engine reads. Overrides are normalised to this shape.
_CA_COLUMNS = (
    "symbol",
    "isin",
    "ex_date",
    "action_type",
    "ratio_num",
    "ratio_den",
    "old_face_value",
    "new_face_value",
    "dividend_amount",
    # Carried so an override can state its multiplier outright. Inferred rows
    # know the observed price ratio but not the face values behind it.
    "adjustment_factor",
    "raw_purpose",
    "source",
)


def _load_ca_overrides() -> pl.DataFrame | None:
    """Manually curated corporate actions, merged over the fetched CA feed.

    The NSE equity CA feed does not cover every instrument — ETFs in particular.
    NIFTYBEES split 1:10 on 2019-12-19 and appears nowhere in
    ``nse_corporate_actions.parquet``, which left the configured benchmark with
    an unadjusted 10x cliff. This file is tracked in git (unlike ``data/``) so a
    panel rebuild is reproducible.
    """
    if not CA_OVERRIDES_PATH.exists():
        return None

    raw = pl.read_csv(CA_OVERRIDES_PATH, try_parse_dates=True)
    if raw.is_empty():
        return None

    # Only rows explicitly marked source="manual" are applied. The legacy
    # source="seed" rows predate the bhavcopy CA feed, were never consumed by
    # this pipeline, and several disagree with it: IRCTC is dated 2022-10-28
    # against the feed's 2021-10-28, and TCS 2018-07-18 against 2018-05-31.
    # Because the de-duplicating anti-join matches on ex_date, a wrong date
    # would not replace the feed event but stack on top of it and adjust the
    # symbol twice. They stay inert until someone verifies them.
    if "source" not in raw.columns:
        return None
    frame = raw.filter(pl.col("source").is_in(APPLIED_CA_SOURCES))
    if frame.is_empty():
        return None

    unknown = sorted(
        set(raw["source"].drop_nulls().to_list()) - set(APPLIED_CA_SOURCES) - {"seed"}
    )
    if unknown:
        # An override row that is written but never applied is invisible: the
        # jump stays in the panel and the file claims it was handled.
        raise ValueError(
            f"{CA_OVERRIDES_PATH} has rows with unrecognised source(s) {unknown}; "
            f"add them to APPLIED_CA_SOURCES or they will be silently ignored"
        )

    frame = frame.with_columns(pl.col("ex_date").cast(pl.Date))
    if "raw_purpose" not in frame.columns:
        source = "description" if "description" in frame.columns else None
        frame = frame.with_columns(
            (pl.col(source) if source else pl.lit(None, dtype=pl.Utf8)).alias("raw_purpose")
        )
    for column, dtype in (
        ("isin", pl.Utf8),
        ("ratio_num", pl.Float64),
        ("ratio_den", pl.Float64),
        ("old_face_value", pl.Float64),
        ("new_face_value", pl.Float64),
        ("dividend_amount", pl.Float64),
        ("source", pl.Utf8),
    ):
        if column not in frame.columns:
            frame = frame.with_columns(pl.lit(None, dtype=dtype).alias(column))
        else:
            frame = frame.with_columns(pl.col(column).cast(dtype))

    return frame.select(_CA_COLUMNS)


def _load_ca(dividends: bool, raw: pl.DataFrame | None = None) -> pl.DataFrame:
    ca = pl.read_parquet(CA_PATH)

    overrides = _load_ca_overrides()
    if overrides is not None:
        # Drop fetched events that an override replaces, so a correction wins
        # rather than double-applying alongside the feed.
        # Match on (symbol, ex_date) only, not action_type. An override states
        # the total multiplier observed for that symbol-date, so it must replace
        # every feed event there. Keying on action_type as well would let a
        # feed `bonus` survive alongside an override `split` for the same event
        # and adjust the symbol twice.
        keys = overrides.select("symbol", "ex_date").unique()
        ca = ca.join(keys, on=["symbol", "ex_date"], how="anti")
        # Adopt the feed's dtypes so the concat cannot fail on, say, ratio_num
        # being Int64 upstream and Float64 in the CSV.
        overrides = overrides.with_columns(
            [
                pl.col(name).cast(dtype)
                for name, dtype in ca.schema.items()
                if name in overrides.columns
            ]
        )
        ca = pl.concat([ca, overrides], how="diagonal")
        LOGGER.info(
            "merged %d manual CA override(s) from %s", overrides.height, CA_OVERRIDES_PATH
        )
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
    """Flag adjusted single-day jumps not backed by a CA (audit guardrail).

    Checks the price *ratio*, not the return. The previous version tested
    ``abs(ret) > max_jump`` with ``ret = close/prev - 1``, which can only ever
    fire upwards: a fall is bounded at ``ret = -1``, so no threshold above 1
    detects one. An unadjusted split is always a fall — a 1:10 split is
    ``ret = -0.9`` — so the check was structurally blind to the exact class of
    error it exists to catch. NIFTYBEES's 2019-12-19 split passed it silently.

    A ratio outside ``[1/max_jump, max_jump]`` is flagged in either direction.
    """
    d = panel.sort(["symbol", "date"]).with_columns(
        (pl.col("close") / pl.col("close").shift(1).over("symbol")).alias("ratio")
    ).with_columns((pl.col("ratio") - 1).alias("ret"))
    bad = d.filter(
        (
            pl.col("ratio").is_not_null()
            & ((pl.col("ratio") > max_jump) | (pl.col("ratio") < 1.0 / max_jump))
        )
        | (pl.col("close") <= 0)
    )
    ensure_directory(QUARANTINE.parent)
    bad.select("symbol", "date", "close", "ret").write_csv(QUARANTINE)
    if bad.height:
        LOGGER.warning("%d suspect adjusted rows quarantined -> %s", bad.height, QUARANTINE)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dividends", action="store_true", help="Phase 2 total-return panel")
    ap.add_argument(
        "--canonical-symbols",
        action="store_true",
        help="collapse ticker renames onto the security's latest ticker, via ISIN",
    )
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

    if args.canonical_symbols:
        master_path = Path("data/reference/security_master.parquet")
        if not master_path.exists():
            raise SystemExit(
                f"{master_path} missing; run scripts/build_security_master.py first"
            )
        mapping = build_rename_map(pl.read_parquet(master_path))
        raw = apply_canonical_symbols(raw, mapping)
        LOGGER.info("collapsed %d renamed ticker(s) onto canonical symbols", len(mapping))

    ca = _load_ca(args.dividends, raw=raw)
    if args.canonical_symbols:
        # The CA feed records the ticker in force at the time, so it has to be
        # remapped too or a renamed security loses its own corporate actions.
        ca = apply_canonical_symbols(ca, mapping)
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

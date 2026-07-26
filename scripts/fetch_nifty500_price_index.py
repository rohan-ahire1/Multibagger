"""Fetch or import the Nifty 500 **price** index history.

The spec's regime filter is the price index versus its SMA200. The repo only
carries the TRI, and substituting it is a documented deviation: total return
drifts above price by reinvested dividends, so the filter turns bullish
marginally early.

niftyindices.com blocks scripted access, so the realistic path is to download
the CSV by hand and import it here::

    # https://www.niftyindices.com/reports/historical-data  -> NIFTY 500, save CSV
    uv run python scripts/fetch_nifty500_price_index.py --from-file ~/Downloads/nifty500.csv

Or, where an endpoint is reachable::

    uv run python scripts/fetch_nifty500_price_index.py --url https://.../nifty500.csv

Either way the file is **validated before it is written**. A benchmark with an
unadjusted corporate action does not fail loudly; it silently fabricates alpha,
which is exactly how NIFTYBEES produced a -4.07% CAGR benchmark and ~16 points
of imaginary excess return across every strategy in this repo.
"""

from __future__ import annotations

import argparse
import sys
from io import BytesIO
from pathlib import Path

import polars as pl

REFERENCE_DIR = Path("data/reference")

# Column names niftyindices has used across export formats.
_DATE_CANDIDATES = ("date", "Date", "HistoricalDate", "Index Date", "INDEX_DATE")
_PRICE_CANDIDATES = ("price", "close", "Close", "CLOSE", "Closing Index Value", "TOTAL_RETURNS_INDEX")


def _pick(frame: pl.DataFrame, candidates: tuple[str, ...], label: str) -> str:
    for name in candidates:
        if name in frame.columns:
            return name
    raise SystemExit(
        f"no {label} column found; looked for {candidates}, file has {frame.columns}"
    )


def normalise(payload: bytes) -> pl.DataFrame:
    """Coerce an index export into the `date, price` shape the loader expects."""
    frame = pl.read_csv(BytesIO(payload), infer_schema_length=2000, try_parse_dates=True)
    date_col = _pick(frame, _DATE_CANDIDATES, "date")
    price_col = _pick(frame, _PRICE_CANDIDATES, "price")

    date_expr = pl.col(date_col)
    if frame.schema[date_col] != pl.Date:
        date_expr = pl.coalesce(
            [
                pl.col(date_col).cast(pl.String).str.strptime(pl.Date, fmt, strict=False)
                for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")
            ]
        )

    price_expr = pl.col(price_col)
    if frame.schema[price_col] != pl.Float64:
        price_expr = (
            pl.col(price_col).cast(pl.String).str.replace_all(",", "").cast(pl.Float64, strict=False)
        )

    return (
        frame.select(date_expr.alias("date"), price_expr.alias("price"))
        .drop_nulls()
        .unique(subset=["date"], keep="last")
        .sort("date")
    )


def validate(frame: pl.DataFrame, *, max_jump: float = 1.25) -> list[str]:
    """Reasons this series must not become a benchmark. Empty means usable."""
    problems: list[str] = []
    if frame.height < 250:
        problems.append(f"only {frame.height} rows; expected years of daily history")
    if frame.filter(pl.col("price") <= 0).height:
        problems.append("contains non-positive index values")

    ratios = frame.with_columns((pl.col("price") / pl.col("price").shift(1)).alias("r"))
    breaks = ratios.filter(
        pl.col("r").is_not_null() & ((pl.col("r") > max_jump) | (pl.col("r") < 1 / max_jump))
    )
    if breaks.height:
        first = breaks.row(0, named=True)
        problems.append(
            f"{breaks.height} discontinuity(ies) over {max_jump}x, first on "
            f"{first['date']} (ratio {first['r']:.4f}) -- an index should never gap like this"
        )

    gaps = frame.with_columns(
        (pl.col("date") - pl.col("date").shift(1)).dt.total_days().alias("d")
    ).filter(pl.col("d") > 10)
    if gaps.height:
        problems.append(f"{gaps.height} gap(s) longer than 10 days; history looks incomplete")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-file", type=Path, help="CSV downloaded by hand")
    src.add_argument("--url", help="reachable CSV endpoint")
    ap.add_argument("--reference-dir", type=Path, default=REFERENCE_DIR)
    ap.add_argument(
        "--force", action="store_true", help="write even if validation objects"
    )
    args = ap.parse_args()

    if args.from_file:
        payload = args.from_file.read_bytes()
    else:
        import requests  # imported lazily so the file path needs no network stack

        response = requests.get(args.url, timeout=30)
        response.raise_for_status()
        payload = response.content

    frame = normalise(payload)
    problems = validate(frame)

    start, end = frame["date"].min(), frame["date"].max()
    print(f"parsed {frame.height} rows, {start} -> {end}")

    if problems:
        print("\nVALIDATION FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        if not args.force:
            raise SystemExit(
                "\nrefusing to write. A broken benchmark fabricates alpha rather than "
                "erroring, so it must be wrong loudly here or not at all. Pass --force "
                "only if you have checked the series by hand."
            )
        print("\n--force given; writing anyway", file=sys.stderr)

    args.reference_dir.mkdir(parents=True, exist_ok=True)
    target = args.reference_dir / f"nifty500_price_{start}_{end}.csv"
    frame.write_csv(target)
    print(f"wrote {target}")
    print("the strategy now runs with regime_source='price_index' (its default)")


if __name__ == "__main__":
    main()

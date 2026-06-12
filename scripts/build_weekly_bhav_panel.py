"""Build a survivorship-clean WEEKLY OHLCV panel from the on-disk bhavcopy snapshots
(includes delisted names). Handles both legacy (<=2023) and modern (>=2024) formats.
Caches to data/tmp/weekly_bhav_panel.parquet. UNADJUSTED prices (corp actions not applied)
— acceptable for a directional survivorship check; noted as a caveat."""
import zipfile, io
from pathlib import Path
from datetime import datetime
import polars as pl

SNAP = Path("data/raw/universe_snapshots")
OUT = Path("data/tmp/weekly_bhav_panel.parquet")
OUT.parent.mkdir(parents=True, exist_ok=True)

def parse_one(zpath: Path) -> pl.DataFrame | None:
    try:
        with zipfile.ZipFile(zpath) as zf:
            name = zf.namelist()[0]
            raw = pl.read_csv(io.BytesIO(zf.read(name)), infer_schema_length=0)
    except Exception:
        return None
    cols = {c.strip().upper(): c for c in raw.columns}
    # modern format
    if "TCKRSYMB" in cols:
        m = {"sym": cols["TCKRSYMB"], "ser": cols["SCTYSRS"], "o": cols["OPNPRIC"],
             "h": cols["HGHPRIC"], "l": cols["LWPRIC"], "c": cols["CLSPRIC"],
             "v": cols["TTLTRADGVOL"], "val": cols["TTLTRFVAL"], "dt": cols["TRADDT"]}
        date_fmt = None  # ISO already
    elif "SYMBOL" in cols:  # legacy
        m = {"sym": cols["SYMBOL"], "ser": cols["SERIES"], "o": cols["OPEN"],
             "h": cols["HIGH"], "l": cols["LOW"], "c": cols["CLOSE"],
             "v": cols["TOTTRDQTY"], "val": cols["TOTTRDVAL"], "dt": cols["TIMESTAMP"]}
        date_fmt = "%d-%b-%Y"
    else:
        return None
    df = raw.filter(pl.col(m["ser"]).str.strip_chars() == "EQ").select(
        pl.col(m["sym"]).str.strip_chars().alias("symbol"),
        pl.col(m["o"]).cast(pl.Float64, strict=False).alias("open"),
        pl.col(m["h"]).cast(pl.Float64, strict=False).alias("high"),
        pl.col(m["l"]).cast(pl.Float64, strict=False).alias("low"),
        pl.col(m["c"]).cast(pl.Float64, strict=False).alias("close"),
        pl.col(m["v"]).cast(pl.Float64, strict=False).alias("volume"),
        pl.col(m["val"]).cast(pl.Float64, strict=False).alias("turnover"),
    )
    # date from filename (reliable & uniform)
    d = datetime.strptime(zpath.stem, "%Y-%m-%d").date()
    return df.with_columns(pl.lit(d).alias("date"))

zips = sorted(SNAP.glob("20*.zip"))
frames = []
for i, z in enumerate(zips):
    f = parse_one(z)
    if f is not None and f.height:
        frames.append(f)
    if i % 200 == 0:
        print(f"  parsed {i}/{len(zips)}")
panel = pl.concat(frames).filter(pl.col("close") > 0).unique(["symbol", "date"]).sort(["symbol", "date"])
panel.write_parquet(OUT, compression="zstd")
print(f"\nWROTE {OUT}: {panel.height} rows, {panel['symbol'].n_unique()} symbols, "
      f"{panel['date'].min()} -> {panel['date'].max()}")

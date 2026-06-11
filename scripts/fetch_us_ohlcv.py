"""Fetch S&P 500 daily OHLCV (split/div-adjusted) via yfinance into the engine's
parquet schema, for testing SEPA on US stocks.

Batched downloads (many tickers per request) to stay under yfinance throttling.
Output: data/us/ohlcv_all.parquet  (date, symbol, open, high, low, close, volume, turnover)
        data/us/benchmark_sp500tr.csv  (date, close)  -- S&P 500 Total Return index
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd
import polars as pl
import yfinance as yf

_ROOT = Path(__file__).resolve().parents[1]
_p = argparse.ArgumentParser()
_p.add_argument("--universe", default="data/reference/sp500_constituents.csv")
_p.add_argument("--out", default="data/us")
_p.add_argument("--start", default="2009-06-01")
_A = _p.parse_args()
OUT_DIR = _ROOT / _A.out
START, END = _A.start, "2026-06-01"
BATCH = 60
SLEEP = 2.0


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    syms = pl.read_csv(_ROOT / _A.universe)["symbol"].to_list()
    print(f"Fetching {len(syms)} symbols from {_A.universe} in batches of {BATCH}...")

    frames: list[pl.DataFrame] = []
    for i in range(0, len(syms), BATCH):
        batch = syms[i : i + BATCH]
        try:
            raw = yf.download(batch, start=START, end=END, auto_adjust=True,
                              group_by="ticker", progress=False, threads=True)
        except Exception as e:
            print(f"  batch {i//BATCH}: ERROR {e}")
            time.sleep(SLEEP * 3)
            continue
        got = 0
        for sym in batch:
            try:
                sub = raw[sym] if len(batch) > 1 else raw
                sub = sub.dropna(subset=["Close"])
                if sub.empty:
                    continue
                df = pl.DataFrame({
                    "date": [d for d in sub.index.date],
                    "symbol": sym,
                    "open": sub["Open"].to_numpy(),
                    "high": sub["High"].to_numpy(),
                    "low": sub["Low"].to_numpy(),
                    "close": sub["Close"].to_numpy(),
                    "volume": sub["Volume"].to_numpy(),
                }).with_columns(pl.col("volume").fill_nan(0).cast(pl.Int64))
                df = df.with_columns((pl.col("close") * pl.col("volume")).alias("turnover"))
                frames.append(df)
                got += 1
            except Exception:
                continue
        print(f"  batch {i//BATCH+1}/{(len(syms)+BATCH-1)//BATCH}: {got}/{len(batch)} ok "
              f"(total symbols so far ~{sum(1 for _ in frames)})")
        time.sleep(SLEEP)

    if not frames:
        print("No data fetched."); return
    alldf = pl.concat(frames).sort(["symbol", "date"]).unique(["symbol", "date"])
    alldf.write_parquet(OUT_DIR / "ohlcv_all.parquet")
    print(f"\nWrote {alldf.height} rows, {alldf['symbol'].n_unique()} symbols, "
          f"{alldf['date'].min()}..{alldf['date'].max()} -> {OUT_DIR/'ohlcv_all.parquet'}")

    # Benchmark: S&P 500 Total Return index
    for bsym, name in [("^SP500TR", "benchmark_sp500tr.csv"), ("^GSPC", "benchmark_gspc.csv")]:
        try:
            b = yf.download(bsym, start=START, end=END, auto_adjust=True, progress=False)
            if b is not None and len(b):
                bc = b["Close"]
                pl.DataFrame({"date": b.index.date, "close": bc.to_numpy().ravel()}).write_csv(OUT_DIR / name)
                print(f"benchmark {bsym}: {len(b)} rows -> {name}")
        except Exception as e:
            print(f"benchmark {bsym}: ERROR {e}")
        time.sleep(SLEEP)


if __name__ == "__main__":
    main()

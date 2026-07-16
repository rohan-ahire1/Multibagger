"""Run the signal scorecard directly against local data."""

import json
import sys
from pathlib import Path

import duckdb
import polars as pl

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sepa_scanner.backtest.signal_scorecard import compute_scorecard
from sepa_scanner.settings import load_settings
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "db" / "sepa.duckdb"
PARQUET_DIR = DATA_DIR / "yf_adjusted"


def main():
    conn = duckdb.connect(str(DB_PATH), read_only=True)

    # Load all BREAKOUT signals
    signals = conn.execute(
        "SELECT * FROM signals WHERE signal_type = 'BREAKOUT' ORDER BY signal_date"
    ).pl()
    print(f"Loaded {signals.height} BREAKOUT signals")
    print(f"  Date range: {signals['signal_date'].min()} to {signals['signal_date'].max()}")

    # Load diagnostics for contraction data
    diagnostics = conn.execute("SELECT * FROM daily_diagnostics").pl()

    # Load OHLCV
    parquet = ParquetOhlcvStore(PARQUET_DIR)
    symbols = signals["symbol"].unique().to_list()
    ohlcv = parquet.read_frame(columns=["date", "symbol", "open", "high", "low", "close", "volume"])
    ohlcv = ohlcv.filter(pl.col("symbol").is_in(symbols)).sort(["symbol", "date"])
    print(f"Loaded OHLCV: {ohlcv.height} rows for {ohlcv['symbol'].n_unique()} symbols")

    # Compute scorecard
    result = compute_scorecard(
        signals,
        ohlcv,
        diagnostics,
        pending_days=load_settings().backtest.pending_days,
    )

    # Print results
    print("\n" + "=" * 80)
    print("SIGNAL SCORECARD")
    print("=" * 80)

    print(f"\nTotal signals: {result['total_signals']}")
    print(f"Signals with forward data: {result['signals_with_fwd_data']}")
    print(f"Date range: {result['date_range']['earliest']} to {result['date_range']['latest']}")

    overall = result.get("overall", {})
    if overall:
        print("\n--- OVERALL ---")
        _print_slice("All signals", overall)

    for section, label in [
        ("by_rs_rank", "RS RANK"),
        ("by_vol_mult", "VOLUME MULTIPLE"),
        ("by_range10", "TIGHTNESS (range10)"),
        ("by_voldry", "VOLUME DRYUP"),
        ("by_contraction", "CONTRACTION COUNT"),
    ]:
        data = result.get(section)
        if not data:
            continue
        print(f"\n--- BY {label} ---")
        for row in data:
            bucket = None
            for k, v in row.items():
                if k.endswith("_bucket") or k == "contraction_bucket":
                    bucket = v
                    break
            _print_slice(str(bucket), row)

    # Save full JSON
    out_path = DATA_DIR / "backtest" / "scorecard.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, default=str, indent=2))
    print(f"\nFull JSON saved to {out_path}")


def _print_slice(label: str, row: dict):
    count = row.get("count", 0)
    print(f"\n  {label} (n={count})")

    # Forward returns
    for d in [1, 3, 5, 10, 20]:
        avg = row.get(f"avg_{d}d_return")
        med = row.get(f"med_{d}d_return")
        wr = row.get(f"win_rate_{d}d")
        if avg is not None:
            print(f"    {d:2d}d: avg={avg:+.2f}%  med={med:+.2f}%  win_rate={wr:.1f}%")

    # MFE/MAE
    mfe = row.get("avg_mfe_pct")
    mae = row.get("avg_mae_pct")
    mfe_r = row.get("avg_mfe_r")
    mae_r = row.get("avg_mae_r")
    if mfe is not None:
        print(f"    MFE: {mfe:+.2f}% ({mfe_r:+.2f}R)  MAE: {mae:+.2f}% ({mae_r:+.2f}R)")

    stop_pct = row.get("stop_hit_first_pct")
    r1_pct = row.get("reached_1r_first_pct")
    if stop_pct is not None:
        print(f"    Stop hit first: {stop_pct:.1f}%  Reached +1R first: {r1_pct:.1f}%")


if __name__ == "__main__":
    main()

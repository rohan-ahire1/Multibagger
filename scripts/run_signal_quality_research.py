"""Research which breakout signal cohorts actually produce outsized winners."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sepa_scanner.backtest.signal_scorecard import compute_scorecard
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "db" / "sepa.duckdb"
PARQUET_DIR = DATA_DIR / "yf_adjusted"
OUTPUT_PATH = DATA_DIR / "backtest" / "signal_quality_research.json"

SECTIONS = [
    ("overall", "Overall"),
    ("by_rs_rank", "RS Rank"),
    ("by_range10", "Tightness"),
    ("by_voldry", "Volume Dry-up"),
    ("by_contraction", "Contraction Count"),
    ("by_contraction_quality", "Contraction Quality"),
    ("by_setup_streak", "Setup Streak"),
    ("by_pivot_age", "Pivot Age"),
    ("by_near_pivot", "Near Pivot"),
    ("by_atr_pct", "ATR %"),
    ("by_offense_v2_score", "Offense V2 Score"),
]


def _bucket_label(row: dict) -> str:
    for key, value in row.items():
        if key.endswith("_bucket"):
            return str(value)
    return "all"


def _sort_key(row: dict) -> tuple[float, float, float]:
    return (
        float(row.get("reached_3r_pct") or 0.0),
        float(row.get("reached_2r_pct") or 0.0),
        float(row.get("avg_mfe_r") or 0.0),
    )


def _print_row(label: str, row: dict) -> None:
    print(
        f"  {label:>12} | n={int(row.get('count', 0)):>4}"
        f" | 20d avg={float(row.get('avg_20d_return') or 0.0):>6.2f}%"
        f" | avg MFE={float(row.get('avg_mfe_r') or 0.0):>5.2f}R"
        f" | 2R={float(row.get('reached_2r_pct') or 0.0):>5.1f}%"
        f" | 3R={float(row.get('reached_3r_pct') or 0.0):>5.1f}%"
        f" | stop-first={float(row.get('stop_hit_first_pct') or 0.0):>5.1f}%"
    )


def main() -> None:
    conn = duckdb.connect(str(DB_PATH), read_only=True)

    signals = conn.execute(
        "SELECT * FROM signals WHERE signal_type = 'BREAKOUT' ORDER BY signal_date"
    ).pl()
    diagnostics = conn.execute("SELECT * FROM daily_diagnostics ORDER BY diag_date, symbol").pl()
    conn.close()

    parquet = ParquetOhlcvStore(PARQUET_DIR)
    symbols = signals["symbol"].unique().to_list()
    ohlcv = parquet.read_frame(columns=["date", "symbol", "open", "high", "low", "close", "volume"])
    ohlcv = ohlcv.filter(pl.col("symbol").is_in(symbols)).sort(["symbol", "date"])

    result = compute_scorecard(signals, ohlcv, diagnostics)

    overall = result.get("overall", {})
    print("Signal Quality Research")
    print("=======================")
    print(f"Signals: {result.get('signals_with_fwd_data', 0)}/{result.get('total_signals', 0)} with forward data")
    print(
        "Overall:"
        f" avg MFE={float(overall.get('avg_mfe_r') or 0.0):.2f}R"
        f", 2R={float(overall.get('reached_2r_pct') or 0.0):.1f}%"
        f", 3R={float(overall.get('reached_3r_pct') or 0.0):.1f}%"
        f", 5R={float(overall.get('reached_5r_pct') or 0.0):.1f}%"
        f", stop-first={float(overall.get('stop_hit_first_pct') or 0.0):.1f}%"
    )

    for key, label in SECTIONS[1:]:
        rows = result.get(key) or []
        rows = [row for row in rows if int(row.get("count", 0)) >= 20]
        if not rows:
            continue

        ranked = sorted(rows, key=_sort_key, reverse=True)
        print(f"\nTop {label} cohorts")
        for row in ranked[:4]:
            _print_row(_bucket_label(row), row)

        print(f"Worst {label} cohorts")
        for row in list(reversed(ranked[-3:])):
            _print_row(_bucket_label(row), row)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

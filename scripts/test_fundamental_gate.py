"""Test whether a Minervini fundamental gate improves SEPA signal/trade quality.

Joins scraped quarterly fundamentals (screener_quarterly.csv) onto breakout
signals using a point-in-time as-of join (a quarter is only "known" once its
results would have been announced: quarter_end + REPORTING_LAG_DAYS), then
splits signals/trades by fundamental strength and compares outcomes.

Two views:
  1. Realized per-trade R (from the current backtest's trade_pnl.csv) —
     decision-relevant: did fundamentally strong entries do better through the
     actual engine?
  2. Forward 63/126d return from signal date (exit-agnostic).

This is a hypothesis test on the period the scrape covers (~2024+), with
survivorship-biased, recent data. A positive result justifies buying clean
deep-history fundamentals; a null says the gate doesn't help here.
"""
from __future__ import annotations

import bisect
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import polars as pl

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

REPORTING_LAG_DAYS = 45  # SEBI max for quarterly results; conservative "known by" lag
FUND_CSV = _ROOT / "data" / "reference" / "screener_quarterly.csv"
TRADE_PNL = _ROOT / "reports" / "portfolio_diagnostics" / "2010-01-04_2026-03-17" / "trade_pnl.csv"


def load_fundamentals() -> dict[str, list[tuple[date, float | None, float | None]]]:
    """symbol -> sorted list of (known_date, yoy_growth, acceleration)."""
    df = pl.read_csv(FUND_CSV, try_parse_dates=True).sort(["symbol", "quarter_end"])
    # acceleration = QoQ change in YoY growth
    df = df.with_columns(
        (pl.col("yoy_eps_growth") - pl.col("yoy_eps_growth").shift(1).over("symbol")).alias("acceleration")
    )
    out: dict[str, list[tuple[date, float | None, float | None]]] = {}
    for r in df.iter_rows(named=True):
        known = r["quarter_end"] + timedelta(days=REPORTING_LAG_DAYS)
        out.setdefault(r["symbol"], []).append((known, r["yoy_eps_growth"], r["acceleration"]))
    return out


def asof_fundamental(fund: dict, symbol: str, signal_date: date):
    """Latest quarter known on/before signal_date. Returns (yoy, accel) or (None, None)."""
    rows = fund.get(symbol)
    if not rows:
        return None, None
    knowns = [r[0] for r in rows]
    j = bisect.bisect_right(knowns, signal_date) - 1
    if j < 0:
        return None, None
    return rows[j][1], rows[j][2]


def classify(yoy, accel) -> str:
    if yoy is None:
        return "no_data"
    passes = (yoy >= 0.20) and (accel is not None and accel > 0)
    return "PASS" if passes else "FAIL"


def main() -> None:
    if not FUND_CSV.exists():
        print(f"Missing {FUND_CSV} — run scrape_screener_fundamentals.py first.")
        return
    fund = load_fundamentals()
    print(f"Fundamentals: {len(fund)} symbols, "
          f"{sum(len(v) for v in fund.values())} quarter-rows\n")

    # --- View 1: realized per-trade R ---
    if TRADE_PNL.exists():
        t = pl.read_csv(TRADE_PNL, try_parse_dates=True).filter(
            pl.col("r_multiple").is_not_null() & pl.col("entry_date").is_not_null()
        )
        tags = [classify(*asof_fundamental(fund, r["symbol"], r["entry_date"]))
                for r in t.iter_rows(named=True)]
        t = t.with_columns(pl.Series("fund_tag", tags))
        print("=== View 1: realized per-trade R by fundamental tag (through the engine) ===")
        for tag in ["PASS", "FAIL", "no_data"]:
            r = t.filter(pl.col("fund_tag") == tag)["r_multiple"].to_numpy()
            if len(r):
                print(f"  {tag:<8} N={len(r):>4}  avgR={np.mean(r):+.3f}  medR={np.median(r):+.3f}  win%={np.mean(r>0)*100:5.1f}")
        cov = t.filter(pl.col("fund_tag") != "no_data").height
        print(f"  (fundamental coverage: {cov}/{t.height} trades have point-in-time data)\n")

    # --- View 2: forward returns from signal date ---
    conn = duckdb.connect(str(_ROOT / "data" / "db" / "sepa.duckdb"), read_only=True)
    sig = conn.execute(
        "SELECT signal_date, symbol FROM signals WHERE signal_type='BREAKOUT' ORDER BY signal_date"
    ).pl()
    conn.close()
    ohlcv = pl.read_parquet(_ROOT / "data" / "yf_adjusted" / "ohlcv_all.parquet").select(
        ["date", "symbol", "close"]).sort(["symbol", "date"])
    by_sym = {}
    for s, g in ohlcv.group_by("symbol", maintain_order=True):
        by_sym[s[0] if isinstance(s, tuple) else s] = (g["date"].to_list(), g["close"].to_numpy())

    recs = []
    for r in sig.iter_rows(named=True):
        ser = by_sym.get(r["symbol"])
        if ser is None:
            continue
        dates, closes = ser
        k = bisect.bisect_left(dates, r["signal_date"])
        if k >= len(dates) or dates[k] != r["signal_date"]:
            continue
        base = closes[k]
        if not base or base <= 0:
            continue
        yoy, accel = asof_fundamental(fund, r["symbol"], r["signal_date"])
        rec = {"tag": classify(yoy, accel)}
        for h in (63, 126):
            rec[f"r{h}"] = (closes[k + h] / base - 1.0) if k + h < len(dates) else None
        recs.append(rec)
    fdf = pl.DataFrame(recs)
    print("=== View 2: forward raw return from signal date by fundamental tag ===")
    print(f"{'tag':<8}{'N':>6}{'63d mean':>10}{'63d hit':>9}{'126d mean':>11}{'126d hit':>10}")
    for tag in ["PASS", "FAIL", "no_data"]:
        s = fdf.filter(pl.col("tag") == tag)
        r63 = s["r63"].drop_nulls().to_numpy(); r126 = s["r126"].drop_nulls().to_numpy()
        if len(r63):
            print(f"{tag:<8}{len(r63):>6}{np.mean(r63)*100:>9.2f}%{np.mean(r63>0)*100:>8.1f}%"
                  f"{np.mean(r126)*100:>10.2f}%{np.mean(r126>0)*100:>9.1f}%")


if __name__ == "__main__":
    main()

"""Engine test of the earnings-growth gate using DEEP annual data (2018-2026).

Uses screener export annual Net-Profit YoY growth (proxy for EPS growth) with a
point-in-time lag (annual results known ~75d after FY end). Gates breakout
signals (keep if no-data OR growth >= cutoff), deletes the rest from a duckdb
copy, runs the full backtest, and reports full-period + the data-covered
sub-period. Far better coverage than the quarterly prototype (which was 2024+).
"""
from __future__ import annotations
import bisect, subprocess, sys
from datetime import date, timedelta
from pathlib import Path
import duckdb, numpy as np, polars as pl

_ROOT = Path(__file__).resolve().parents[1]
ANNUAL_LAG = 75
ANN_CSV = _ROOT / "data" / "reference" / "screener_annual.csv"
BK = "/tmp/sepa_gate_backup.duckdb"


def build_fund():
    df = pl.read_csv(ANN_CSV, try_parse_dates=True)
    df = df.filter((pl.col("freq") == "annual") & pl.col("yoy_growth").is_not_null()).sort(["symbol", "period_end"])
    fund = {}
    for r in df.iter_rows(named=True):
        fund.setdefault(r["symbol"], []).append((r["period_end"] + timedelta(days=ANNUAL_LAG), r["yoy_growth"]))
    return fund


def asof(fund, sym, d):
    rows = fund.get(sym)
    if not rows: return None
    ks = [x[0] for x in rows]; j = bisect.bisect_right(ks, d) - 1
    return rows[j][1] if j >= 0 else None


def submetrics(outdir):
    de = pl.read_csv(f"{outdir}/2010-01-04_2026-03-17/strategy_daily_equity.csv", try_parse_dates=True).sort("date")
    def win(lo, hi):
        g = de.filter((pl.col("date") >= lo) & (pl.col("date") < hi)); e = g["equity"].to_numpy(); d = g["date"].to_list()
        if len(e) < 20: return None
        yrs = (d[-1] - d[0]).days / 365.25; cagr = (e[-1] / e[0]) ** (1 / yrs) - 1
        peak = -1e18; mdd = 0
        for v in e: peak = max(peak, v); mdd = min(mdd, v / peak - 1)
        return cagr, mdd, (cagr / abs(mdd) if mdd < 0 else float("nan"))
    return win(date(2010, 1, 1), date(2027, 1, 1)), win(date(2019, 1, 1), date(2027, 1, 1))


def main():
    fund = build_fund()
    conn = duckdb.connect(BK, read_only=True)
    sig = conn.execute("select signal_date,symbol from signals where signal_type='BREAKOUT'").pl(); conn.close()
    tagged = [(r["signal_date"], r["symbol"], asof(fund, r["symbol"], r["signal_date"])) for r in sig.iter_rows(named=True)]
    covered = [v for (_, _, v) in tagged if v is not None]
    print(f"signals: {len(tagged)} | with point-in-time annual data: {len(covered)} ({len(covered)/len(tagged)*100:.0f}%)")
    arr = np.array(covered)
    cuts = {"drop_bottom_tercile": np.quantile(arr, 1/3), "drop_bottom_half": np.median(arr), "keep_top_tercile": np.quantile(arr, 2/3)}
    print(f"net-profit YoY growth: median={np.median(arr):+.2f}  terciles {np.quantile(arr,1/3):+.2f}/{np.quantile(arr,2/3):+.2f}\n")

    def run(label, cutoff):
        subprocess.run(["cp", BK, "data/db/sepa.duckdb"], check=True)
        if cutoff is not None:
            drop = [(d, s) for (d, s, v) in tagged if v is not None and v < cutoff]
            c = duckdb.connect("data/db/sepa.duckdb")
            c.executemany("DELETE FROM signals WHERE signal_type='BREAKOUT' AND signal_date=? AND symbol=?", drop)
            c.commit(); c.close()
            kept = len(tagged) - len(drop)
        else:
            kept = len(tagged)
        out = f"reports/_gate_ann/{label}"
        subprocess.run([".venv/bin/python", "scripts/portfolio_diagnostics.py", "--output-dir", out], capture_output=True, text=True)
        full, rec = submetrics(out)
        fc = f"{full[0]*100:5.2f}%/{full[1]*100:6.2f}%/{full[2]:.2f}" if full else "n/a"
        rc = f"{rec[0]*100:5.2f}%/{rec[1]*100:6.2f}%/{rec[2]:.2f}" if rec else "n/a"
        print(f"{label:<22} keeps={kept:>4}  FULL[CAGR/DD/Calmar]={fc}   2019+[..]={rc}")

    run("baseline_nogate", None)
    for lbl, cut in cuts.items():
        run(lbl, float(cut))
    subprocess.run(["cp", BK, "data/db/sepa.duckdb"], check=True)
    print("\nDB restored")


if __name__ == "__main__":
    main()

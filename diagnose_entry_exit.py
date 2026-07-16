"""
Entry-vs-exit quality diagnostic for the locked SEPA config (NSE), PRODUCTION engine.

Separates two things the headline CAGR conflates:
  ENTRY quality  — favorable excursion (MFE in R) a signal creates, exit-independent.
                   This is the raw opportunity the entry buys.
  EXIT  quality  — how much of that MFE we convert to realized R (capture ratio),
                   and where exits fire (status breakdown).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

_ROOT = Path(__file__).resolve().parent
for _p in (_ROOT, _ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import logging
logging.getLogger("sepa_scanner").setLevel(logging.ERROR)

import optimize_joint_wf as o
import scripts.portfolio_diagnostics as diag


def pct(a, q):
    return float(np.percentile(a, q)) if len(a) else float("nan")


def main() -> None:
    sig = o._signals.filter((pl.col("signal_date") >= o.FULL_START) & (pl.col("signal_date") <= o.FULL_END))
    filt = diag.filter_signals(
        sig, settings=o._settings, diagnostics=o._diagnostics, sector_map=o._sector_map,
        rs_rank_min=o._settings.backtest.rs_rank_min, rs_rank_max=o._settings.backtest.rs_rank_max,
        ranking_mode=o._settings.backtest.signal_ranking_mode,
        max_signals_per_day=o._settings.backtest.max_signals_per_day,
        quality_profile=o._settings.backtest.signal_quality_profile,
        quality_overrides=o._BASE_OVERRIDES,
    )
    res = diag.simulate_portfolio_python(
        filt, o._ohlcv, o._params, o._portfolio, o._sector_map,
        exit_config=o._BASE_EXIT, entry_gate=o._ENTRY_GATE, entry_size_mult=o._ENTRY_SIZE_MULT,
        breakeven_activation_r=o._BASE_EXIT["breakeven_activation_r"],
        time_stop_days=o._BASE_EXIT["time_stop_days"], partial_profit_r=o._BASE_EXIT["partial_profit_r"],
        trail_pct_bull=o._BASE_EXIT["trail_pct_bull"], trail_pct_bear=o._BASE_EXIT["trail_pct_bear"],
    )
    tr = res["trades"].filter(pl.col("entry_date").is_not_null() & pl.col("r_multiple").is_not_null())
    tr = tr.with_columns(
        ((pl.col("mfe_price") - pl.col("entry_price")) / (pl.col("entry_price") - pl.col("stop"))).alias("mfe_r"),
        ((pl.col("mae_price") - pl.col("entry_price")) / (pl.col("entry_price") - pl.col("stop"))).alias("mae_r"),
    )
    r = tr["r_multiple"].to_numpy()
    mfe = tr["mfe_r"].to_numpy()
    mae = tr["mae_r"].to_numpy()
    cap_df = tr.filter(pl.col("mfe_r") > 0.25)
    cap = (cap_df["r_multiple"] / cap_df["mfe_r"]).to_numpy()

    print(f"\nFiltered signals: {filt.height}   filled trades: {tr.height}")
    win = r > 0
    print("\n-- HEADLINE --")
    print(f"   win%={win.mean()*100:.1f}  avgR={r.mean():+.3f}  medR={np.median(r):+.3f}  "
          f"payoff={(r[win].mean()/abs(r[~win].mean())):.2f}")

    print("\n-- ENTRY QUALITY (MFE in R = raw opportunity, exit-independent) --")
    print(f"   MFE_R mean={mfe.mean():+.2f} median={np.median(mfe):+.2f} p75={pct(mfe,75):+.2f} p90={pct(mfe,90):+.2f} max={mfe.max():+.2f}")
    print(f"   MAE_R mean={mae.mean():+.2f} median={np.median(mae):+.2f} p10={pct(mae,10):+.2f}")
    print(f"   reached >=1R MFE: {np.mean(mfe>=1)*100:.1f}%   >=3R: {np.mean(mfe>=3)*100:.1f}%   >=5R: {np.mean(mfe>=5)*100:.1f}%")

    print("\n-- EXIT QUALITY (capture = realized R / MFE R, on MFE>0.25R trades) --")
    print(f"   capture mean={cap.mean()*100:.1f}%  median={np.median(cap)*100:.1f}%  n={len(cap)}")
    print(f"   realized avgR {r.mean():+.2f}  of available MFE avgR {mfe.mean():+.2f}")

    print("\n-- EXIT STATUS BREAKDOWN --")
    g = tr.group_by("status").agg(
        pl.len().alias("n"), pl.col("r_multiple").mean().alias("avgR"),
        ((pl.col("mfe_price") - pl.col("entry_price")) / (pl.col("entry_price") - pl.col("stop"))).mean().alias("avg_mfe_r"),
    ).sort("n", descending=True)
    for row in g.iter_rows(named=True):
        print(f"   {row['status']:<20} n={row['n']:4d}  avgR={row['avgR']:+.3f}  avgMFE={row['avg_mfe_r']:+.2f}  share={row['n']/tr.height*100:4.1f}%")


if __name__ == "__main__":
    main()

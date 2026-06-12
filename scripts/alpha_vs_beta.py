"""Alpha vs beta: is the committed strategy real edge or just midcap bull beta?
Regress strategy daily equity returns against Nifty500 TRI / Nifty Next50 / Nifty Midcap150.
Reports excess CAGR, correlation, beta, annualized Jensen alpha, up/down capture."""
import sys
from pathlib import Path
from datetime import timedelta, date
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import polars as pl
import numpy as np
import optimize_joint_wf as J
import scripts.portfolio_diagnostics as diag

# ---- strategy daily equity (committed config, full history) ----
s, e = J.FULL_START, J.FULL_END
win_hi = e + timedelta(days=J.BUFFER_DAYS)
sig = J._signals.filter((pl.col("signal_date") >= s) & (pl.col("signal_date") <= e))
ohlcv = J._ohlcv.filter((pl.col("date") >= s) & (pl.col("date") <= win_hi))
filt = diag.filter_signals(sig, settings=J._settings, diagnostics=J._diagnostics, sector_map=J._sector_map,
    rs_rank_min=J._settings.backtest.rs_rank_min, rs_rank_max=J._settings.backtest.rs_rank_max,
    ranking_mode=J._settings.backtest.signal_ranking_mode,
    max_signals_per_day=J._settings.backtest.max_signals_per_day,
    quality_profile=J._settings.backtest.signal_quality_profile, quality_overrides=J._BASE_OVERRIDES)
res = diag.simulate_portfolio_python(filt, ohlcv, J._params, J._portfolio, J._sector_map, exit_config=None,
    entry_gate=J._ENTRY_GATE, entry_size_mult=J._ENTRY_SIZE_MULT,
    breakeven_activation_r=J._BASE_EXIT["breakeven_activation_r"], time_stop_days=J._BASE_EXIT["time_stop_days"],
    partial_profit_r=J._BASE_EXIT["partial_profit_r"], trail_pct_bull=J._BASE_EXIT["trail_pct_bull"],
    trail_pct_bear=J._BASE_EXIT["trail_pct_bear"], trail_widen_r=J._BASE_EXIT["trail_widen_r"])
seq = res["daily_equity"].filter((pl.col("date")>=s)&(pl.col("date")<=e)).sort("date").select(["date","equity"])

# ---- benchmark series ----
panel = pl.scan_parquet("data/yf_adjusted/ohlcv_all.parquet")
def etf(sym):
    return panel.filter(pl.col("symbol")==sym).select(["date","close"]).rename({"close":sym}).collect()
n500 = pl.read_csv("data/reference/nifty500_tri_2010-01-04_2026-03-17.csv").with_columns(
    pl.col("date").str.to_date()).rename({"tri":"N500TRI"})
benches = {"Nifty500_TRI": n500, "NiftyNext50(JUNIOR)": etf("JUNIORBEES").rename({"JUNIORBEES":"NiftyNext50(JUNIOR)"}),
           "Nifty50(NIFTYBEES)": etf("NIFTYBEES").rename({"NIFTYBEES":"Nifty50(NIFTYBEES)"}),
           "NiftyMid150(MID150)": etf("MID150BEES").rename({"MID150BEES":"NiftyMid150(MID150)"})}

def cagr(vals, dts):
    yrs=(dts[-1]-dts[0]).days/365.25
    return (vals[-1]/vals[0])**(1/yrs)-1

def analyze(name, bdf):
    col = [c for c in bdf.columns if c != "date"][0]
    m = seq.join(bdf, on="date", how="inner").drop_nulls().sort("date")
    if m.height < 200:
        print(f"{name}: insufficient overlap"); return
    eq = m["equity"].to_numpy(); bm = m[col].to_numpy()
    dts = m["date"].to_list()
    sr = np.diff(eq)/eq[:-1]; br = np.diff(bm)/bm[:-1]
    # align: drop zero-variance days
    scagr, bcagr = cagr(eq,dts), cagr(bm,dts)
    corr = np.corrcoef(sr,br)[0,1]
    beta = np.cov(sr,br)[0,1]/np.var(br)
    # Jensen alpha (rf=0), annualized (252)
    alpha_daily = sr.mean() - beta*br.mean()
    alpha_ann = (1+alpha_daily)**252 - 1
    # up/down capture
    up = br>0; dn = br<0
    up_cap = sr[up].mean()/br[up].mean() if up.any() else float('nan')
    dn_cap = sr[dn].mean()/br[dn].mean() if dn.any() else float('nan')
    print(f"\n=== vs {name}  ({dts[0]} -> {dts[-1]}, {m.height} days) ===")
    print(f"  strategy CAGR {scagr*100:6.2f}%   benchmark CAGR {bcagr*100:6.2f}%   EXCESS {(scagr-bcagr)*100:+6.2f}%/yr")
    print(f"  correlation {corr:5.2f}   beta {beta:5.2f}   R^2 {corr**2:5.2f}")
    print(f"  annualized Jensen alpha (rf=0): {alpha_ann*100:+6.2f}%/yr")
    print(f"  up-capture {up_cap*100:5.0f}%   down-capture {dn_cap*100:5.0f}%")

for name, bdf in benches.items():
    analyze(name, bdf)
print("\n(beta is suppressed by ~91% avg cash; Jensen alpha + excess CAGR are the cleaner reads.)")

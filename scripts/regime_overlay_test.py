"""Deepening experiment 1: market-regime overlay using Nifty500 TRI 200-DMA.
Test gating entries in downtrends and/or leaning in (bigger size) in uptrends.
Honest harness, committed config otherwise unchanged."""
import sys
from pathlib import Path
from datetime import timedelta
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import polars as pl
import optimize_joint_wf as J
import scripts.portfolio_diagnostics as diag

# --- Nifty500 TRI 200-DMA regime ---
idx = pl.read_csv("data/reference/nifty500_tri_2010-01-04_2026-03-17.csv").with_columns(
    pl.col("date").str.to_date()).sort("date")
idx = idx.with_columns([
    pl.col("tri").rolling_mean(200).alias("sma200"),
    pl.col("tri").rolling_mean(200).shift(21).alias("sma200_prev"),
])
idx = idx.with_columns([
    (pl.col("tri") > pl.col("sma200")).alias("above"),
    ((pl.col("tri") > pl.col("sma200")) & (pl.col("sma200") > pl.col("sma200_prev"))).alias("strong"),
])
ABOVE = {r["date"]: bool(r["above"]) for r in idx.iter_rows(named=True)}
STRONG = {r["date"]: bool(r["strong"]) for r in idx.iter_rows(named=True)}

s, e = J.FULL_START, J.FULL_END
win_hi = e + timedelta(days=J.BUFFER_DAYS)
sig = J._signals.filter((pl.col("signal_date") >= s) & (pl.col("signal_date") <= e))
ohlcv = J._ohlcv.filter((pl.col("date") >= s) & (pl.col("date") <= win_hi))
filt = diag.filter_signals(sig, settings=J._settings, diagnostics=J._diagnostics, sector_map=J._sector_map,
    rs_rank_min=J._settings.backtest.rs_rank_min, rs_rank_max=J._settings.backtest.rs_rank_max,
    ranking_mode=J._settings.backtest.signal_ranking_mode,
    max_signals_per_day=J._settings.backtest.max_signals_per_day,
    quality_profile=J._settings.backtest.signal_quality_profile, quality_overrides=J._BASE_OVERRIDES)

def metrics(eq, a, b):
    eq = eq.filter((pl.col("date")>=a)&(pl.col("date")<=b)).sort("date")
    v = eq["equity"].to_list()
    yrs=(eq["date"][-1]-eq["date"][0]).days/365.25
    cagr=(v[-1]/v[0])**(1/yrs)-1
    peak=v[0]; mdd=0.0
    for x in v: peak=max(peak,x); mdd=max(mdd,(peak-x)/peak)
    return cagr*100, mdd*100, (cagr/mdd if mdd>0 else 0)

def run(gate, size_mult, a, b):
    res = diag.simulate_portfolio_python(filt, ohlcv, J._params, J._portfolio, J._sector_map, exit_config=None,
        entry_gate=gate, entry_size_mult=size_mult,
        breakeven_activation_r=J._BASE_EXIT["breakeven_activation_r"], time_stop_days=J._BASE_EXIT["time_stop_days"],
        partial_profit_r=J._BASE_EXIT["partial_profit_r"], trail_pct_bull=J._BASE_EXIT["trail_pct_bull"],
        trail_pct_bear=J._BASE_EXIT["trail_pct_bear"], trail_widen_r=J._BASE_EXIT["trail_widen_r"])
    return metrics(res["daily_equity"], a, b), len(res["trades"])

all_dates = filt["signal_date"].unique().to_list() + ohlcv["date"].unique().to_list()
def g_gate():  # entries off when index below 200DMA
    return {d: ABOVE.get(d, True) for d in set(all_dates)}
def sm_lean(mult):  # bigger size when strong uptrend
    return {d: (mult if STRONG.get(d, False) else 1.0) for d in set(all_dates)}

VARIANTS = [
    ("BASE",                    None,        None),
    ("gate<200DMA off",         g_gate(),    None),
    ("lean 1.5x when strong",   None,        sm_lean(1.5)),
    ("lean 2.0x when strong",   None,        sm_lean(2.0)),
    ("gate + lean 1.5x",        g_gate(),    sm_lean(1.5)),
    ("gate + lean 2.0x",        g_gate(),    sm_lean(2.0)),
]
print(f"{'variant':24s} | {'FULL CAGR':>9} {'DD':>6} {'Calmar':>6} | {'HOLD CAGR':>9} {'DD':>6} {'Calmar':>6} | tr")
print("-"*92)
for name, gate, sm in VARIANTS:
    (fc,fd,fcal),n = run(gate, sm, s, e)
    (hc,hd,hcal),_ = run(gate, sm, J.HOLDOUT_START, e)
    print(f"{name:24s} | {fc:8.2f}% {fd:5.1f}% {fcal:6.2f} | {hc:8.2f}% {hd:5.1f}% {hcal:6.2f} | {n}")

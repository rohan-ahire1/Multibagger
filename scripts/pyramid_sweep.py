"""Sweep pyramiding on the COMMITTED config via the production-parity harness.
Filtered signals computed once; only portfolio pyramid_* knobs vary. Honest equity metric."""
import sys, itertools
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import timedelta
import polars as pl
import optimize_joint_wf as J
import scripts.portfolio_diagnostics as diag

FULL_START, FULL_END, HOLD_START = J.FULL_START, J.FULL_END, J.HOLDOUT_START

def filtered(start, end):
    win_hi = end + timedelta(days=J.BUFFER_DAYS)
    sig = J._signals.filter((pl.col("signal_date") >= start) & (pl.col("signal_date") <= end))
    ohlcv = J._ohlcv.filter((pl.col("date") >= start) & (pl.col("date") <= win_hi))
    filt = diag.filter_signals(
        sig, settings=J._settings, diagnostics=J._diagnostics, sector_map=J._sector_map,
        rs_rank_min=J._settings.backtest.rs_rank_min, rs_rank_max=J._settings.backtest.rs_rank_max,
        ranking_mode=J._settings.backtest.signal_ranking_mode,
        max_signals_per_day=J._settings.backtest.max_signals_per_day,
        quality_profile=J._settings.backtest.signal_quality_profile,
        quality_overrides=J._BASE_OVERRIDES)
    return filt, ohlcv

def metrics(eq, start, end):
    eq = eq.filter((pl.col("date") >= start) & (pl.col("date") <= end)).sort("date")
    e = eq["equity"].to_list()
    yrs = (eq["date"][-1] - eq["date"][0]).days / 365.25
    cagr = (e[-1]/e[0])**(1/yrs)-1
    peak=e[0]; mdd=0.0
    for v in e:
        peak=max(peak,v); mdd=max(mdd,(peak-v)/peak)
    return cagr*100, mdd*100, (cagr/mdd if mdd>0 else 0)

def run(filt, ohlcv, start, end, pyr):
    p = dict(J._portfolio); p.update(pyr)
    res = diag.simulate_portfolio_python(
        filt, ohlcv, J._params, p, J._sector_map, exit_config=None,
        entry_gate=J._ENTRY_GATE, entry_size_mult=J._ENTRY_SIZE_MULT,
        breakeven_activation_r=J._BASE_EXIT["breakeven_activation_r"],
        time_stop_days=J._BASE_EXIT["time_stop_days"],
        partial_profit_r=J._BASE_EXIT["partial_profit_r"],
        trail_pct_bull=J._BASE_EXIT["trail_pct_bull"],
        trail_pct_bear=J._BASE_EXIT["trail_pct_bear"],
        trail_widen_r=J._BASE_EXIT["trail_widen_r"])
    return metrics(res["daily_equity"], start, end), len(res["trades"])

f_full, o_full = filtered(FULL_START, FULL_END)

GRID = [
    ("BASE (no pyramid)",      dict(pyramid_max_adds=0)),
    ("1 add @1R x0.5",         dict(pyramid_max_adds=1, pyramid_trigger_r=1.0, pyramid_add_interval_r=0.5, pyramid_risk_frac=0.5)),
    ("2 adds @1R/0.5 x0.5",    dict(pyramid_max_adds=2, pyramid_trigger_r=1.0, pyramid_add_interval_r=0.5, pyramid_risk_frac=0.5)),
    ("3 adds @1R/0.5 x0.5",    dict(pyramid_max_adds=3, pyramid_trigger_r=1.0, pyramid_add_interval_r=0.5, pyramid_risk_frac=0.5)),
    ("2 adds @2R/1.0 x0.5",    dict(pyramid_max_adds=2, pyramid_trigger_r=2.0, pyramid_add_interval_r=1.0, pyramid_risk_frac=0.5)),
    ("2 adds @1R/0.5 x1.0",    dict(pyramid_max_adds=2, pyramid_trigger_r=1.0, pyramid_add_interval_r=0.5, pyramid_risk_frac=1.0)),
    ("3 adds @1R/0.5 x1.0",    dict(pyramid_max_adds=3, pyramid_trigger_r=1.0, pyramid_add_interval_r=0.5, pyramid_risk_frac=1.0)),
]

print(f"{'config':24s} | {'FULL CAGR':>9} {'DD':>6} {'Calmar':>6} | {'HOLD CAGR':>9} {'DD':>6} {'Calmar':>6}")
print("-"*88)
f_hold, o_hold = filtered(HOLD_START, FULL_END)
rows=[]
for name, pyr in GRID:
    (fc,fd,fcal),_ = run(f_full, o_full, FULL_START, FULL_END, pyr)
    (hc,hd,hcal),_ = run(f_hold, o_hold, HOLD_START, FULL_END, pyr)
    rows.append((name,fc,fd,fcal,hc,hd,hcal))
    print(f"{name:24s} | {fc:8.2f}% {fd:5.1f}% {fcal:6.2f} | {hc:8.2f}% {hd:5.1f}% {hcal:6.2f}")

"""Deepening experiment 2: concentration / deployment. Vary max_positions and max_name_pct
(and risk_per_trade) on the committed config, honest harness. Does deploying harder help, or
is the strategy already optimally concentrated? Also reports avg deployment %."""
import sys
from pathlib import Path
from datetime import timedelta
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import polars as pl
import optimize_joint_wf as J
import scripts.portfolio_diagnostics as diag

s, e = J.FULL_START, J.FULL_END
win_hi = e + timedelta(days=J.BUFFER_DAYS)
sig = J._signals.filter((pl.col("signal_date") >= s) & (pl.col("signal_date") <= e))
ohlcv = J._ohlcv.filter((pl.col("date") >= s) & (pl.col("date") <= win_hi))
filt = diag.filter_signals(sig, settings=J._settings, diagnostics=J._diagnostics, sector_map=J._sector_map,
    rs_rank_min=J._settings.backtest.rs_rank_min, rs_rank_max=J._settings.backtest.rs_rank_max,
    ranking_mode=J._settings.backtest.signal_ranking_mode,
    max_signals_per_day=J._settings.backtest.max_signals_per_day,
    quality_profile=J._settings.backtest.signal_quality_profile, quality_overrides=J._BASE_OVERRIDES)

def metrics(eq):
    eq = eq.sort("date")
    v = eq["equity"].to_list()
    yrs=(eq["date"][-1]-eq["date"][0]).days/365.25
    cagr=(v[-1]/v[0])**(1/yrs)-1
    peak=v[0]; mdd=0.0
    for x in v: peak=max(peak,x); mdd=max(mdd,(peak-x)/peak)
    # avg deployment = 1 - cash/equity, averaged
    depl = (1 - (eq["cash"]/eq["equity"])).mean()
    return cagr*100, mdd*100, (cagr/mdd if mdd>0 else 0), depl*100

def run(maxpos, namepct, risk):
    p = dict(J._portfolio); p.update(max_positions=maxpos, max_name_pct=namepct, risk_per_trade_pct=risk)
    res = diag.simulate_portfolio_python(filt, ohlcv, J._params, p, J._sector_map, exit_config=None,
        entry_gate=J._ENTRY_GATE, entry_size_mult=J._ENTRY_SIZE_MULT,
        breakeven_activation_r=J._BASE_EXIT["breakeven_activation_r"], time_stop_days=J._BASE_EXIT["time_stop_days"],
        partial_profit_r=J._BASE_EXIT["partial_profit_r"], trail_pct_bull=J._BASE_EXIT["trail_pct_bull"],
        trail_pct_bear=J._BASE_EXIT["trail_pct_bear"], trail_widen_r=J._BASE_EXIT["trail_widen_r"])
    return metrics(res["daily_equity"].filter((pl.col("date")>=s)&(pl.col("date")<=e)))

base_risk = J._portfolio["risk_per_trade_pct"]
print(f"committed: max_positions={J._portfolio['max_positions']}  max_name_pct={J._portfolio['max_name_pct']}  risk={base_risk}\n")
GRID = [
    ("BASE (20 / 20% / 2.0%)",   20, 0.20, base_risk),
    ("fewer names 10 / 25%",     10, 0.25, base_risk),
    ("fewer names 8 / 30%",       8, 0.30, base_risk),
    ("higher cap 20 / 33%",      20, 0.33, base_risk),
    ("higher risk 20/20%/3%",    20, 0.20, 0.030),
    ("conc 12/25%/3%",           12, 0.25, 0.030),
    ("more names 30 / 15%",      30, 0.15, base_risk),
]
print(f"{'variant':26s} | {'CAGR':>7} {'DD':>6} {'Calmar':>6} {'deploy%':>8}")
print("-"*60)
for name, mp, np_, rk in GRID:
    c,d,cal,dep = run(mp, np_, rk)
    print(f"{name:26s} | {c:6.2f}% {d:5.1f}% {cal:6.2f} {dep:7.0f}%")

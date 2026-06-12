"""Charts (Pareto + R-histogram -> PNG) and a loss-tail analysis for the committed config.
Key question on losers: does the -0.76R average hide fat-tail losses that blew past the stop?"""
import sys
from pathlib import Path
from datetime import timedelta
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import polars as pl
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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
res = diag.simulate_portfolio_python(filt, ohlcv, J._params, J._portfolio, J._sector_map, exit_config=None,
    entry_gate=J._ENTRY_GATE, entry_size_mult=J._ENTRY_SIZE_MULT,
    breakeven_activation_r=J._BASE_EXIT["breakeven_activation_r"], time_stop_days=J._BASE_EXIT["time_stop_days"],
    partial_profit_r=J._BASE_EXIT["partial_profit_r"], trail_pct_bull=J._BASE_EXIT["trail_pct_bull"],
    trail_pct_bear=J._BASE_EXIT["trail_pct_bear"], trail_widen_r=J._BASE_EXIT["trail_widen_r"])

tr = res["trades"].filter(pl.col("entry_date").is_not_null() & pl.col("r_multiple").is_not_null())
R = sorted(tr["r_multiple"].to_list(), reverse=True)
total = sum(R)

# ---------- CHARTS ----------
fig, ax = plt.subplots(1, 2, figsize=(13, 5))
# Pareto
cum = [sum(R[:i+1])/total*100 for i in range(len(R))]
xpct = [(i+1)/len(R)*100 for i in range(len(R))]
ax[0].plot(xpct, cum, lw=2)
ax[0].axhline(100, color="gray", ls="--", lw=0.8)
ax[0].axvline(10, color="red", ls=":", lw=1); ax[0].axhline(75, color="red", ls=":", lw=1)
ax[0].set_title("Profit concentration (Pareto)")
ax[0].set_xlabel("% of trades (best first)"); ax[0].set_ylabel("cumulative % of net profit")
ax[0].grid(alpha=0.3)
# Histogram
bins = [-3,-2,-1,-0.5,0,0.5,1,2,3,5,8,12,25]
ax[1].hist(tr["r_multiple"].to_list(), bins=bins, edgecolor="black")
ax[1].axvline(0, color="red", lw=1)
ax[1].set_title("R-multiple distribution")
ax[1].set_xlabel("R-multiple"); ax[1].set_ylabel("# trades"); ax[1].grid(alpha=0.3)
out = Path("reports/skew_distribution.png"); out.parent.mkdir(exist_ok=True)
plt.tight_layout(); plt.savefig(out, dpi=110)
print(f"chart -> {out}\n")

# ---------- LOSS-TAIL ANALYSIS ----------
losers = tr.filter(pl.col("r_multiple") <= 0).sort("r_multiple")
nL = losers.height
print(f"=== LOSS TAIL ({nL} losers of {tr.height}) ===")
print(f"  avg loss {losers['r_multiple'].mean():+.2f}R   median {losers['r_multiple'].median():+.2f}R")
# stop is -1R by construction (max_loss 7%). Losses worse than -1R = stop FAILED (gap/slippage).
worse = losers.filter(pl.col("r_multiple") < -1.0)
print(f"  worse than -1R (stop overrun via gap): {worse.height} ({worse.height/nL*100:.0f}% of losers, "
      f"{worse.height/tr.height*100:.0f}% of all trades)")
print(f"  worst single trade: {losers['r_multiple'].min():.2f}R")
print(f"  losses in [-1R,-1.5R): {losers.filter((pl.col('r_multiple')<-1)&(pl.col('r_multiple')>=-1.5)).height}")
print(f"  losses <= -1.5R:        {losers.filter(pl.col('r_multiple')<=-1.5).height}")
print(f"  return% of worst overruns:")
for r in worse.sort("r_multiple").head(10).iter_rows(named=True):
    print(f"    {r['symbol']:12s} {str(r['entry_date'])}  {r['r_multiple']:6.2f}R  ret {(r['return_pct'] or 0):6.1f}%  hold {r['holding_days']}d")
# what fraction of gross loss comes from the overruns
gross_loss = -sum(x for x in tr['r_multiple'].to_list() if x < 0)
overrun_loss = -worse['r_multiple'].sum()
print(f"\n  gross loss total: {gross_loss:.0f}R | from sub--1R overruns: {overrun_loss:.0f}R "
      f"({overrun_loss/gross_loss*100:.0f}%)")

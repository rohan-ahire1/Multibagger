"""Do gap-down stop-overruns (R < -1) cluster in low-liquidity names?
Join each committed-config trade to its signal-time adv50 (50d avg traded value, INR),
tier by liquidity, compare overrun rate + avg loss across tiers."""
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
res = diag.simulate_portfolio_python(filt, ohlcv, J._params, J._portfolio, J._sector_map, exit_config=None,
    entry_gate=J._ENTRY_GATE, entry_size_mult=J._ENTRY_SIZE_MULT,
    breakeven_activation_r=J._BASE_EXIT["breakeven_activation_r"], time_stop_days=J._BASE_EXIT["time_stop_days"],
    partial_profit_r=J._BASE_EXIT["partial_profit_r"], trail_pct_bull=J._BASE_EXIT["trail_pct_bull"],
    trail_pct_bear=J._BASE_EXIT["trail_pct_bear"], trail_widen_r=J._BASE_EXIT["trail_widen_r"])

tr = res["trades"].filter(pl.col("entry_date").is_not_null() & pl.col("r_multiple").is_not_null())
liq = filt.select(["symbol", "signal_date", "adv50"]).unique(["symbol", "signal_date"])
tr = tr.join(liq, on=["symbol", "signal_date"], how="left").filter(pl.col("adv50").is_not_null())
print(f"trades with adv50: {tr.height}/{res['trades'].filter(pl.col('entry_date').is_not_null()).height}")

# liquidity tiers by quintile of adv50 (INR cr)
tr = tr.with_columns((pl.col("adv50")/1e7).alias("adv_cr"))  # INR crore
q = tr["adv_cr"].qcut(5, labels=["Q1 lowest","Q2","Q3","Q4","Q5 highest"], allow_duplicates=True)
tr = tr.with_columns(q.alias("tier"))

print(f"\n{'tier':12s} {'adv_range_cr':>16} {'n':>4} {'overrun%':>9} {'avgLoss_R':>10} {'avgR_all':>9} {'1d_gaps':>8}")
print("-"*72)
for t in ["Q1 lowest","Q2","Q3","Q4","Q5 highest"]:
    g = tr.filter(pl.col("tier")==t)
    if g.height==0: continue
    losers = g.filter(pl.col("r_multiple")<=0)
    overrun = g.filter(pl.col("r_multiple")<-1.0)
    oneday = g.filter((pl.col("r_multiple")<-1.0)&(pl.col("holding_days")<=2))
    lo, hi = g["adv_cr"].min(), g["adv_cr"].max()
    print(f"{t:12s} {f'{lo:.1f}-{hi:.0f}':>16} {g.height:4d} "
          f"{overrun.height/g.height*100:8.0f}% {(losers['r_multiple'].mean() or 0):9.2f} "
          f"{(g['r_multiple'].mean() or 0):8.2f} {oneday.height:8d}")

# Correlation check: overrun trades vs rest, median adv
overrun = tr.filter(pl.col("r_multiple")<-1.0)
rest = tr.filter(pl.col("r_multiple")>=-1.0)
print(f"\noverrun trades (R<-1): median adv {overrun['adv_cr'].median():.1f}cr  (n={overrun.height})")
print(f"all other trades:       median adv {rest['adv_cr'].median():.1f}cr  (n={rest.height})")
print(f"\nuniverse floor min_adv50_inr = {J._settings.universe.min_adv50_inr/1e7:.1f}cr")

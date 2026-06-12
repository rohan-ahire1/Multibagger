"""Year-by-year + trade-distribution stats for the committed config (the 21% strategy),
via the production-parity harness. Replays run_config({}, {}, full history) and dumps the
guts the parity command hides."""
import sys, statistics
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import polars as pl
import optimize_joint_wf as J

# Re-run committed config full history but capture the FULL result (run_config only returns metrics)
import scripts.portfolio_diagnostics as diag
from datetime import timedelta

start, end = J.FULL_START, J.FULL_END
win_hi = end + timedelta(days=J.BUFFER_DAYS)
sig = J._signals.filter((pl.col("signal_date") >= start) & (pl.col("signal_date") <= end))
ohlcv = J._ohlcv.filter((pl.col("date") >= start) & (pl.col("date") <= win_hi))
filt = diag.filter_signals(
    sig, settings=J._settings, diagnostics=J._diagnostics, sector_map=J._sector_map,
    rs_rank_min=J._settings.backtest.rs_rank_min, rs_rank_max=J._settings.backtest.rs_rank_max,
    ranking_mode=J._settings.backtest.signal_ranking_mode,
    max_signals_per_day=J._settings.backtest.max_signals_per_day,
    quality_profile=J._settings.backtest.signal_quality_profile,
    quality_overrides=J._BASE_OVERRIDES,
)
res = diag.simulate_portfolio_python(
    filt, ohlcv, J._params, J._portfolio, J._sector_map,
    exit_config=None, entry_gate=J._ENTRY_GATE, entry_size_mult=J._ENTRY_SIZE_MULT,
    breakeven_activation_r=J._BASE_EXIT["breakeven_activation_r"],
    time_stop_days=J._BASE_EXIT["time_stop_days"],
    partial_profit_r=J._BASE_EXIT["partial_profit_r"],
    trail_pct_bull=J._BASE_EXIT["trail_pct_bull"],
    trail_pct_bear=J._BASE_EXIT["trail_pct_bear"],
    trail_widen_r=J._BASE_EXIT["trail_widen_r"],
)
eq = res["daily_equity"].filter((pl.col("date") >= start) & (pl.col("date") <= end)).sort("date")
tr = res["trades"]

# ---- Year-by-year strategy return vs benchmark ----
print("=== YEAR-BY-YEAR (strategy equity vs NIFTYBEES) ===")
bench = (J._ohlcv.filter(pl.col("symbol") == J._settings.market.benchmark_symbol)
         .select(["date", "close"]).sort("date"))
eqy = eq.with_columns(pl.col("date").dt.year().alias("y"))
by = eqy.group_by("y").agg(pl.col("equity").first().alias("o"), pl.col("equity").last().alias("c")).sort("y")
bch = bench.with_columns(pl.col("date").dt.year().alias("y")).group_by("y").agg(
    pl.col("close").first().alias("bo"), pl.col("close").last().alias("bc")).sort("y")
m = by.join(bch, on="y", how="left")
for r in m.iter_rows(named=True):
    sret = (r["c"]/r["o"]-1)*100
    bret = (r["bc"]/r["bo"]-1)*100 if r["bo"] else float("nan")
    print(f"  {r['y']}  strat {sret:+7.1f}%   nifty {bret:+7.1f}%   diff {sret-bret:+7.1f}")

# ---- Trade distribution ----
cl = tr.filter(pl.col("status").is_in(["stopped","time_stop"]) | pl.col("exit_date").is_not_null())
rmults = [float(x) for x in tr["r_multiple"].drop_nulls().to_list()]
rets = [float(x) for x in tr["return_pct"].drop_nulls().to_list()]
wins = [x for x in rmults if x > 0]; losses = [x for x in rmults if x <= 0]
print("\n=== TRADE DISTRIBUTION ===")
print(f"  closed trades:   {len(rmults)}")
print(f"  win rate:        {len(wins)/len(rmults)*100:.1f}%")
print(f"  avg win:         {statistics.mean(wins):+.2f}R   avg loss: {statistics.mean(losses):+.2f}R")
print(f"  expectancy:      {statistics.mean(rmults):+.3f}R/trade")
print(f"  best / worst:    {max(rmults):+.1f}R / {min(rmults):+.1f}R")
rmults_sorted = sorted(rmults, reverse=True)
top10 = sum(rmults_sorted[:max(1,len(rmults)//10)])
print(f"  top-10% trades contribute {top10/sum(rmults)*100:.0f}% of total R  ({sum(rmults):.0f}R total)")
hold = [int(x) for x in tr["holding_days"].drop_nulls().to_list()]
print(f"  median hold:     {statistics.median(hold):.0f} days   avg: {statistics.mean(hold):.0f}")
# exit reason breakdown
if "exit_reason" in tr.columns:
    er = tr.filter(pl.col("exit_reason").is_not_null()).group_by("exit_reason").len().sort("len", descending=True)
    print("  exit reasons:", {r["exit_reason"]: r["len"] for r in er.iter_rows(named=True)})

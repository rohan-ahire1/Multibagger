"""Why do a few trades make most of the profit? Dissect the committed config's trade
distribution: Pareto curve, top winners, hold-time vs R, and the missing-top-N counterfactual."""
import sys, statistics
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
tr = tr.with_columns(pl.col("entry_date").dt.year().alias("yr"))
n = tr.height
rmult = sorted(tr["r_multiple"].to_list(), reverse=True)
total_r = sum(rmult)
pos_r = sum(x for x in rmult if x > 0)

print(f"=== {n} trades, total {total_r:.0f}R (gross winners {pos_r:.0f}R) ===\n")

print("--- PARETO: cumulative profit by trade rank ---")
cum = 0.0
for pct in [1, 2, 5, 10, 20, 30, 50, 100]:
    k = max(1, round(n * pct / 100))
    contrib = sum(rmult[:k])
    print(f"  top {pct:3d}% ({k:3d} trades): {contrib/total_r*100:5.1f}% of net profit")

print("\n--- TOP 15 WINNERS ---")
top = tr.sort("r_multiple", descending=True).head(15)
print(f"  {'symbol':14s} {'entry':>10} {'hold_d':>6} {'R':>7} {'ret%':>8} {'sector':>10}")
for r in top.iter_rows(named=True):
    print(f"  {r['symbol']:14s} {str(r['entry_date']):>10} {r['holding_days']:6d} "
          f"{r['r_multiple']:7.1f} {(r['return_pct'] or 0):8.1f} {str(r.get('sector',''))[:10]:>10}")

print("\n--- HOLD TIME vs OUTCOME (let-winners-run check) ---")
wins = tr.filter(pl.col("r_multiple") > 0); losses = tr.filter(pl.col("r_multiple") <= 0)
big = tr.filter(pl.col("r_multiple") >= 5)
print(f"  winners  median hold {wins['holding_days'].median():.0f}d   avg R {wins['r_multiple'].mean():+.2f}")
print(f"  losers   median hold {losses['holding_days'].median():.0f}d   avg R {losses['r_multiple'].mean():+.2f}")
print(f"  big wins (>=5R, {big.height} trades) median hold {big['holding_days'].median():.0f}d")

print("\n--- COUNTERFACTUAL: drop the top-N winners (R-space) ---")
for k in [0, 3, 5, 10, 20]:
    remaining = rmult[k:]
    exp = sum(remaining)/len(remaining)
    print(f"  remove top {k:2d}: total {sum(remaining):6.0f}R | expectancy {exp:+.3f}R/trade | "
          f"{'EDGE GONE' if exp <= 0 else 'still +EV'}")

print("\n--- big winners (>=5R) by entry year ---")
by = big.group_by("yr").len().sort("yr")
print("  ", {r["yr"]: r["len"] for r in by.iter_rows(named=True)})

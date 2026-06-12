"""Survivorship audit: (1) point-in-time universe vs price panel coverage,
(2) dead/delisted names in the panel, (3) did the 348 committed trades ever touch a name
that 'died'? If not, the tail is measured on survivors only."""
import sys, yaml, zipfile, io
from pathlib import Path
from datetime import timedelta
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import polars as pl

PANEL = "data/yf_adjusted/ohlcv_all.parquet"
panel = pl.scan_parquet(PANEL)
last = panel.group_by("symbol").agg(pl.col("date").max().alias("last"),
                                    pl.col("date").min().alias("first")).collect()
panel_max = last["last"].max()
panel_syms = set(last["symbol"].to_list())
print(f"PANEL: {len(panel_syms)} symbols, max date {panel_max}")

# (1) Point-in-time universe: distinct EQ symbols across all bhavcopy snapshots
snap_dir = Path("data/raw/universe_snapshots")
uni = set()
zips = sorted(snap_dir.glob("*.zip"))
for z in zips:
    try:
        with zipfile.ZipFile(z) as zf:
            name = zf.namelist()[0]
            df = pl.read_csv(io.BytesIO(zf.read(name)), infer_schema_length=0)
            # bhavcopy: SYMBOL + SERIES columns (legacy/new)
            cols = {c.upper(): c for c in df.columns}
            sc = cols.get("SYMBOL"); se = cols.get("SERIES") or cols.get(" SERIES")
            if sc is None: continue
            d = df
            if se is not None:
                d = d.filter(pl.col(se).str.strip_chars() == "EQ")
            uni.update(d[sc].str.strip_chars().to_list())
    except Exception:
        continue
print(f"POINT-IN-TIME UNIVERSE (bhavcopy EQ, {len(zips)} snapshots): {len(uni)} distinct symbols")

covered = uni & panel_syms
missing = uni - panel_syms
print(f"  covered by price panel: {len(covered)} ({len(covered)/len(uni)*100:.0f}%)")
print(f"  MISSING from panel:     {len(missing)} ({len(missing)/len(uni)*100:.0f}%)  <- survivorship hole")

# (2) Dead names in panel: last trade > 180d before panel max
DEAD_CUT = panel_max - timedelta(days=180)
dead = last.filter(pl.col("last") < DEAD_CUT)
print(f"\nPANEL dead/stopped (last trade < {DEAD_CUT}): {dead.height} of {len(panel_syms)} "
      f"({dead.height/len(panel_syms)*100:.0f}%)")

# (3) The 348 committed trades — did any touch a name that later died?
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
tr = res["trades"].filter(pl.col("entry_date").is_not_null())
traded = set(tr["symbol"].to_list())
dead_set = set(dead["symbol"].to_list())
traded_dead = traded & dead_set
print(f"\nCOMMITTED config traded names: {len(traded)}")
print(f"  ...that later died (delisted/stopped): {len(traded_dead)} ({len(traded_dead)/max(1,len(traded))*100:.0f}%)")
print(f"  names: {sorted(traded_dead)[:30]}")
# also: never-signalled missing names = invisible to strategy entirely
sig_syms = set(J._signals['symbol'].unique().to_list())
print(f"\nNames that EVER produced a breakout signal: {len(sig_syms)}")
print(f"  of the {len(missing)} universe names missing price data, none could ever signal.")

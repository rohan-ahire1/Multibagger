"""Identical coarse weekly momentum strategy, run two ways on the bhavcopy weekly panel:
  FULL universe (incl. delisted)  vs  SURVIVOR-only (names still trading at panel end).
The performance gap = survivorship inflation. Delisted holdings exit at last available price."""
import polars as pl
from datetime import timedelta

P = pl.read_parquet("data/tmp/weekly_bhav_panel.parquet").sort(["symbol", "date"])
panel_max = P["date"].max()
SURVIVOR_CUT = panel_max - timedelta(days=180)

# Per-symbol weekly indicators
P = P.with_columns([
    pl.col("close").rolling_mean(30).over("symbol").alias("sma30"),
    pl.col("close").ewm_mean(span=10).over("symbol").alias("ema10"),
    pl.col("close").rolling_max(50).over("symbol").alias("high50"),
    (pl.col("close") / pl.col("close").shift(26).over("symbol") - 1).alias("ret26"),
    pl.col("close").rolling_mean(30).over("symbol").shift(4).over("symbol").alias("sma30_prev"),
    pl.col("turnover").rolling_mean(10).over("symbol").alias("adt"),
])
# Cross-sectional RS rank per date on 26w return
P = P.with_columns((pl.col("ret26").rank().over("date") /
                    pl.col("ret26").count().over("date")).alias("rs"))
# Signal: new 50w-high breakout, above rising 30w SMA, RS top 30%, liquid, priced
P = P.with_columns((
    (pl.col("close") >= pl.col("high50") * 0.999) &
    (pl.col("close") > pl.col("sma30")) &
    (pl.col("sma30") > pl.col("sma30_prev")) &
    (pl.col("rs") >= 0.70) &
    (pl.col("adt") > 2_000_000) &
    (pl.col("close") > 10)
).fill_null(False).alias("signal"))

dates = P["date"].unique().sort().to_list()
last_date = {s: d for s, d in P.group_by("symbol").agg(pl.col("date").max())
             .iter_rows()}

def run(universe_syms: set[str], label: str):
    pan = P.filter(pl.col("symbol").is_in(universe_syms))
    by_date = {d: g for d, g in pan.group_by("date")}
    # quick lookup: (symbol)->{date: row}
    rows_by_sym = {}
    for s, g in pan.group_by("symbol"):
        sym = s[0] if isinstance(s, tuple) else s
        rows_by_sym[sym] = {r["date"]: r for r in g.sort("date").iter_rows(named=True)}

    cash = 1_000_000.0
    positions = {}  # symbol -> (entry_price, shares)
    MAXPOS = 20
    equity_curve = []
    for d in dates:
        # mark + manage exits
        for sym in list(positions.keys()):
            entry, sh = positions[sym]
            row = rows_by_sym.get(sym, {}).get(d)
            if row is None:
                # maybe delisted: if past this symbol's last date, force exit at last close
                if d > last_date.get(sym, panel_max):
                    last_row = rows_by_sym[sym][last_date[sym]]
                    cash += sh * last_row["close"] * 0.995  # haircut on forced exit
                    del positions[sym]
                continue
            px = row["close"]
            if (px < row["ema10"]) or (px < entry * 0.92):
                cash += sh * px * 0.999
                del positions[sym]
        # equity
        inv = 0.0
        for sym, (entry, sh) in positions.items():
            row = rows_by_sym.get(sym, {}).get(d)
            inv += sh * (row["close"] if row else entry)
        equity = cash + inv
        equity_curve.append((d, equity))
        # entries
        if len(positions) < MAXPOS:
            g = by_date.get((d,))
            if g is not None:
                sigs = g.filter(pl.col("signal") & ~pl.col("symbol").is_in(list(positions.keys())))
                sigs = sigs.sort("rs", descending=True)
                for r in sigs.iter_rows(named=True):
                    if len(positions) >= MAXPOS: break
                    budget = equity / MAXPOS
                    if budget > cash: budget = cash
                    if budget < 1000: break
                    px = r["close"] * 1.001
                    sh = budget / px
                    cash -= sh * px
                    positions[r["symbol"]] = (px, sh)
    ec = pl.DataFrame(equity_curve, schema=["date", "equity"], orient="row").sort("date")
    e = ec["equity"].to_list()
    yrs = (ec["date"][-1] - ec["date"][0]).days / 365.25
    cagr = (e[-1]/e[0])**(1/yrs)-1
    peak=e[0]; mdd=0.0
    for v in e:
        peak=max(peak,v); mdd=max(mdd,(peak-v)/peak)
    print(f"  {label:22s} CAGR {cagr*100:6.2f}%  MaxDD {mdd*100:5.1f}%  final {e[-1]/e[0]:5.1f}x  syms {len(universe_syms)}")
    return cagr*100, mdd*100

all_syms = set(P["symbol"].unique().to_list())
survivor_syms = {s for s, d in last_date.items() if d >= SURVIVOR_CUT}
print(f"FULL universe: {len(all_syms)}   SURVIVOR-only: {len(survivor_syms)} "
      f"(dead excluded: {len(all_syms)-len(survivor_syms)})\n")
print("=== identical weekly momentum strategy ===")
run(all_syms, "FULL (incl delisted)")
run(survivor_syms, "SURVIVOR-only")

"""Analyze forward returns after breakout signals to assess signal quality."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb
import polars as pl

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "db" / "sepa.duckdb"
PARQUET_ROOT = DATA_DIR / "yf_adjusted"
FORWARD_DAYS = 5


def main() -> None:
    conn = duckdb.connect(str(DB_PATH), read_only=True)

    signals = conn.execute("""
        SELECT symbol, signal_date, entry_ref, stop, "pivot"
        FROM signals
        WHERE signal_type = 'BREAKOUT'
        ORDER BY signal_date, symbol
    """).pl()

    print(f"Total breakout signals: {len(signals)}")

    symbols = signals["symbol"].unique().to_list()
    min_date = signals["signal_date"].min()
    max_date = signals["signal_date"].max() + timedelta(days=30)

    ohlcv = (
        pl.scan_parquet(str(PARQUET_ROOT / "**/*.parquet"), hive_partitioning=True)
        .filter(
            (pl.col("symbol").is_in(symbols))
            & (pl.col("date") >= pl.lit(min_date))
            & (pl.col("date") <= pl.lit(max_date))
        )
        .select(["date", "symbol", "open", "high", "low", "close", "volume"])
        .collect()
        .sort(["symbol", "date"])
    )

    prices_by_sym: dict[str, pl.DataFrame] = {}
    for sym in symbols:
        prices_by_sym[sym] = ohlcv.filter(pl.col("symbol") == sym)

    results: list[dict] = []

    for row in signals.iter_rows(named=True):
        sym = row["symbol"]
        sig_date = row["signal_date"]
        entry_ref = float(row["entry_ref"])
        stop = float(row["stop"])

        sym_prices = prices_by_sym.get(sym)
        if sym_prices is None or sym_prices.is_empty():
            continue

        future = sym_prices.filter(pl.col("date") > pl.lit(sig_date)).head(FORWARD_DAYS)
        if future.is_empty():
            continue

        sig_day = sym_prices.filter(pl.col("date") == pl.lit(sig_date))
        sig_close = float(sig_day["close"][0]) if not sig_day.is_empty() else entry_ref

        max_high = float(future["high"].max())
        min_low = float(future["low"].min())

        day_closes = future["close"].to_list()
        day_highs = future["high"].to_list()
        day_dates = future["date"].to_list()

        reached_entry_ref = max_high >= entry_ref
        day_reached = None
        for i, h in enumerate(day_highs):
            if float(h) >= entry_ref:
                day_reached = i + 1
                break

        fwd_returns = [(float(c) - sig_close) / sig_close * 100 for c in day_closes]
        fwd_return_from_ref = [(float(c) - entry_ref) / entry_ref * 100 for c in day_closes]

        max_return_from_close = (max_high - sig_close) / sig_close * 100
        max_drawdown_from_close = (min_low - sig_close) / sig_close * 100

        last_close = float(day_closes[-1]) if day_closes else sig_close
        day5_return = (last_close - sig_close) / sig_close * 100

        profitable_at_day5 = last_close > sig_close

        results.append({
            "symbol": sym,
            "signal_date": sig_date,
            "entry_ref": entry_ref,
            "sig_close": sig_close,
            "reached_entry_ref": reached_entry_ref,
            "day_reached_ref": day_reached,
            "max_return_pct": max_return_from_close,
            "max_dd_pct": max_drawdown_from_close,
            "day5_return_pct": day5_return,
            "profitable_day5": profitable_at_day5,
            "fwd_returns": fwd_returns,
        })

    df = pl.DataFrame([
        {k: v for k, v in r.items() if k != "fwd_returns"}
        for r in results
    ])

    total = len(results)
    reached = sum(1 for r in results if r["reached_entry_ref"])
    profitable = sum(1 for r in results if r["profitable_day5"])

    print(f"\n{'='*60}")
    print(f"SIGNAL QUALITY ANALYSIS ({total} signals with price data)")
    print(f"{'='*60}")

    print(f"\n--- Breakout Pivot Reached (within {FORWARD_DAYS} days) ---")
    print(f"  Reached entry_ref:  {reached}/{total} ({reached/total*100:.1f}%)")
    print(f"  NOT reached:        {total-reached}/{total} ({(total-reached)/total*100:.1f}%)")

    if reached > 0:
        day_dist = {}
        for r in results:
            if r["day_reached_ref"] is not None:
                d = r["day_reached_ref"]
                day_dist[d] = day_dist.get(d, 0) + 1
        print(f"\n  Day reached breakdown:")
        for d in sorted(day_dist):
            print(f"    Day {d}: {day_dist[d]} signals ({day_dist[d]/reached*100:.1f}% of reached)")

    print(f"\n--- Forward Returns (from signal-day close, {FORWARD_DAYS}d) ---")
    print(f"  Profitable at day {FORWARD_DAYS}:  {profitable}/{total} ({profitable/total*100:.1f}%)")
    print(f"  Unprofitable:        {total-profitable}/{total} ({(total-profitable)/total*100:.1f}%)")

    returns = [r["day5_return_pct"] for r in results]
    max_rets = [r["max_return_pct"] for r in results]
    max_dds = [r["max_dd_pct"] for r in results]

    print(f"\n  Avg day-{FORWARD_DAYS} return:    {sum(returns)/len(returns):+.2f}%")
    print(f"  Median day-{FORWARD_DAYS} return: {sorted(returns)[len(returns)//2]:+.2f}%")
    print(f"  Avg max intraday gain: {sum(max_rets)/len(max_rets):+.2f}%")
    print(f"  Avg max intraday loss: {sum(max_dds)/len(max_dds):+.2f}%")

    winners_5pct = sum(1 for r in returns if r >= 5.0)
    losers_5pct = sum(1 for r in returns if r <= -5.0)
    print(f"\n  Gained >=5% in {FORWARD_DAYS}d: {winners_5pct} ({winners_5pct/total*100:.1f}%)")
    print(f"  Lost   <=-5% in {FORWARD_DAYS}d: {losers_5pct} ({losers_5pct/total*100:.1f}%)")

    print(f"\n--- Per-Day Forward Return (avg from signal close) ---")
    for day_idx in range(FORWARD_DAYS):
        day_rets = [r["fwd_returns"][day_idx] for r in results if len(r["fwd_returns"]) > day_idx]
        if day_rets:
            pos = sum(1 for x in day_rets if x > 0)
            print(f"  Day {day_idx+1}: avg {sum(day_rets)/len(day_rets):+.2f}%  |  positive: {pos}/{len(day_rets)} ({pos/len(day_rets)*100:.1f}%)")

    gap_pcts = []
    for r in results:
        sym_prices = prices_by_sym.get(r["symbol"])
        if sym_prices is None:
            continue
        future = sym_prices.filter(pl.col("date") > pl.lit(r["signal_date"])).head(1)
        if not future.is_empty():
            next_open = float(future["open"][0])
            gap = (next_open - r["sig_close"]) / r["sig_close"] * 100
            gap_pcts.append(gap)

    if gap_pcts:
        gap_up = sum(1 for g in gap_pcts if g > 0)
        avg_gap = sum(gap_pcts) / len(gap_pcts)
        big_gap_up = sum(1 for g in gap_pcts if g >= 2.0)
        big_gap_down = sum(1 for g in gap_pcts if g <= -2.0)
        print(f"\n--- Next-Day Open Gap ---")
        print(f"  Gap up:    {gap_up}/{len(gap_pcts)} ({gap_up/len(gap_pcts)*100:.1f}%)")
        print(f"  Avg gap:   {avg_gap:+.2f}%")
        print(f"  Big gap up (>=2%):   {big_gap_up}")
        print(f"  Big gap down (<=-2%): {big_gap_down}")

    not_reached = [r for r in results if not r["reached_entry_ref"]]
    if not_reached:
        nr_max_rets = [r["max_return_pct"] for r in not_reached]
        nr_day5 = [r["day5_return_pct"] for r in not_reached]
        print(f"\n--- Signals That NEVER Reached Pivot ({len(not_reached)}) ---")
        print(f"  Avg max gain from close:  {sum(nr_max_rets)/len(nr_max_rets):+.2f}%")
        print(f"  Avg day-5 return:         {sum(nr_day5)/len(nr_day5):+.2f}%")
        profitable_nr = sum(1 for r in nr_day5 if r > 0)
        print(f"  Still profitable at day 5: {profitable_nr}/{len(not_reached)} ({profitable_nr/len(not_reached)*100:.1f}%)")

    conn.close()


if __name__ == "__main__":
    main()

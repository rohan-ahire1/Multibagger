"""Run the all-signal performance analysis and print results to stdout."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import polars as pl

from sepa_scanner.main import resolve_settings
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore
from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.metrics import summarize_backtest
from sepa_scanner.backtest.models import BacktestParams, ExitMode
from sepa_scanner.utils.io import ensure_directory


def main() -> None:
    parser = argparse.ArgumentParser(description="BREAKOUT signal performance analysis")
    parser.add_argument("--pending", type=int, default=5, help="Pending entry window (days)")
    parser.add_argument("--hold", type=int, default=20, help="Max hold days (default 20 per STRATEGY.md)")
    parser.add_argument(
        "--exit-mode",
        choices=[m.value for m in ExitMode],
        default=ExitMode.V1_FIXED.value,
        help="Exit mode",
    )
    args = parser.parse_args()

    settings = resolve_settings(None)
    data_dir = ensure_directory(settings.runtime.data_dir)
    parquet_store = ParquetOhlcvStore(root=ensure_directory(data_dir / "yf_adjusted"))
    import duckdb
    import shutil
    db_path = ensure_directory(data_dir / "db") / "sepa.duckdb"
    tmp_path = Path("/tmp/sepa_perf_analysis.duckdb")
    shutil.copy2(db_path, tmp_path)
    wal_path = db_path.with_suffix(".duckdb.wal")
    if wal_path.exists():
        shutil.copy2(wal_path, tmp_path.with_suffix(".duckdb.wal"))
    conn = duckdb.connect(str(tmp_path), read_only=True)

    pending_days = args.pending
    max_hold_days = args.hold
    exit_mode = ExitMode(args.exit_mode)

    all_signals = conn.execute(
        "SELECT * FROM signals WHERE signal_type = 'BREAKOUT' ORDER BY signal_date ASC, symbol ASC"
    ).pl()

    if all_signals.is_empty():
        print("No BREAKOUT signals found.")
        return

    symbols = all_signals["symbol"].unique().to_list()
    min_date = all_signals["signal_date"].min()
    max_date = all_signals["signal_date"].max()
    lookahead = timedelta(days=(pending_days + max_hold_days) * 2)

    ohlcv = (
        parquet_store.read_frame(
            start_date=min_date,
            end_date=max_date + lookahead,
            columns=["date", "symbol", "open", "high", "low", "close", "volume"],
        )
        .filter(pl.col("symbol").is_in(symbols))
        .sort(["symbol", "date"])
    )

    params = BacktestParams(
        pending_days=pending_days,
        max_hold_days=max_hold_days,
        target_r=None,
        exit_mode=exit_mode,
    )
    result = simulate_breakout_backtest(all_signals, ohlcv, params)
    summary = summarize_backtest(result.trades, exit_mode=exit_mode)
    trades_df = (
        pl.DataFrame([asdict(t) for t in result.trades])
        if result.trades
        else pl.DataFrame()
    )

    closed = trades_df.filter(pl.col("return_pct").is_not_null())
    winners = closed.filter(pl.col("return_pct") > 0).sort("r_multiple", descending=True)
    losers = closed.filter(pl.col("return_pct") < 0)

    print("=" * 60)
    print("BREAKOUT SIGNAL PERFORMANCE ANALYSIS")
    print("=" * 60)
    print(f"Signal period:    {min_date} to {max_date}")
    print(f"Params:           pending={pending_days}d, max_hold={max_hold_days}d, exit_mode={exit_mode.value}")
    print()
    print(f"Total signals:    {summary.total_signals}")
    print(f"Filled:           {summary.filled_trades}")
    print(f"Not filled:       {summary.not_filled_signals}")
    print(f"Win rate:         {summary.win_rate:.1%}")
    print(f"Avg return:       {summary.average_return_pct:+.2f}%" if summary.average_return_pct else "Avg return:       N/A")
    print(f"Expectancy:       {summary.expectancy:+.3f}R" if summary.expectancy else "Expectancy:       N/A")
    print(f"Cumulative R:     {summary.cumulative_r:+.2f}R")
    print(f"Payoff ratio:     {summary.payoff_ratio:.2f}" if summary.payoff_ratio else "Payoff ratio:     N/A")
    print(f"Max drawdown:     {summary.max_drawdown:.2f}R")

    if summary.avg_mfe_r is not None:
        print(f"Avg MFE:          {summary.avg_mfe_r:+.2f}R")
    if summary.avg_mae_r is not None:
        print(f"Avg MAE:          {summary.avg_mae_r:+.2f}R")
    if summary.avg_giveback_pct is not None:
        print(f"Avg giveback:     {summary.avg_giveback_pct:.0%}")
    print()

    n_winners = winners.height
    n_losers = losers.height
    avg_win_r = winners["r_multiple"].mean() if n_winners else 0
    avg_loss_r = losers["r_multiple"].mean() if n_losers else 0
    print(f"Winners:          {n_winners}")
    print(f"Losers:           {n_losers}")
    print(f"Avg winner R:     {avg_win_r:+.2f}R")
    print(f"Avg loser R:      {avg_loss_r:+.2f}R")
    print()

    cum_r = summary.cumulative_r
    if n_winners > 0 and cum_r:
        r_vals = winners["r_multiple"].to_list()
        top3 = sum(r_vals[:3])
        top5 = sum(r_vals[:5])
        top10 = sum(r_vals[:min(10, len(r_vals))])

        print("--- WINNER CONCENTRATION ---")
        print(f"Top 3 winners:    {top3:+.1f}R  ({top3 / cum_r:.0%} of cumulative)")
        print(f"Top 5 winners:    {top5:+.1f}R  ({top5 / cum_r:.0%} of cumulative)")
        print(f"Top 10 winners:   {top10:+.1f}R  ({top10 / cum_r:.0%} of cumulative)")
        print()
        print("Top 10 winners:")
        for row in winners.head(10).to_dicts():
            print(f"  {row['symbol']:<16} {row['r_multiple']:+.2f}R   {row['return_pct']:+.1f}%   {row['signal_date']}  held {row.get('holding_days', '?')}d")

    print()
    print("=" * 60)


if __name__ == "__main__":
    main()

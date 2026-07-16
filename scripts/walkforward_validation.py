from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import polars as pl

import portfolio_diagnostics as diag
from sepa_scanner.strategy.ranking import load_sector_map


WINDOWS = [
    (f"window_{idx:02d}", date(year, 1, 1), date(year + 1, 12, 31))
    for idx, year in enumerate(range(2011, 2025), start=1)
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run rolling walk-forward validation windows.")
    parser.add_argument("--reference-dir", type=Path, default=Path("data/reference"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/walkforward"))
    return parser.parse_args()


def pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100.0:.1f}%"


def num(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def scalar_stats(values: list[float]) -> dict[str, float | None]:
    clean = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    if not clean:
        return {"median": None, "mean": None, "min": None, "max": None}
    return {
        "median": float(np.median(clean)),
        "mean": float(np.mean(clean)),
        "min": float(np.min(clean)),
        "max": float(np.max(clean)),
    }


def trade_stats(trades: pl.DataFrame) -> dict[str, float | int | None]:
    closed = trades.filter(pl.col("entry_date").is_not_null()).drop_nulls(["pnl"])
    fills = closed.height
    if fills == 0:
        return {
            "fills": 0,
            "win_rate": None,
            "avg_r": None,
            "payoff_ratio": None,
            "total_pnl": 0.0,
        }
    wins = closed.filter(pl.col("pnl") > 0)
    losses = closed.filter(pl.col("pnl") < 0)
    avg_win = float(wins["pnl"].mean()) if wins.height else None
    avg_loss = abs(float(losses["pnl"].mean())) if losses.height else None
    return {
        "fills": fills,
        "win_rate": float((closed["pnl"] > 0).mean()),
        "avg_r": float(closed["r_multiple"].mean()) if "r_multiple" in closed.columns else None,
        "payoff_ratio": (avg_win / avg_loss) if avg_win is not None and avg_loss not in (None, 0.0) else None,
        "total_pnl": float(closed["pnl"].sum()),
    }


def monthly_returns(curve: pl.DataFrame, window_id: str) -> pl.DataFrame:
    if curve.is_empty():
        return pl.DataFrame({"month": [], window_id: []})
    return (
        curve.sort("date")
        .with_columns(pl.col("date").dt.strftime("%Y-%m").alias("month"))
        .group_by("month")
        .agg((((1.0 + pl.col("daily_return")).product()) - 1.0).alias(window_id))
        .sort("month")
    )


def t_test_1samp(values: list[float]) -> tuple[float | None, float | None]:
    clean = np.array([float(v) for v in values if v is not None and not math.isnan(float(v))], dtype=float)
    n = len(clean)
    if n < 2:
        return None, None
    std = float(clean.std(ddof=1))
    if std == 0.0:
        t_stat = math.inf if float(clean.mean()) > 0.0 else (-math.inf if float(clean.mean()) < 0.0 else 0.0)
        p_value = 0.0 if math.isinf(t_stat) else 1.0
        return t_stat, p_value
    t_stat = float(clean.mean()) / (std / math.sqrt(n))
    try:
        from scipy import stats  # type: ignore

        _, p_value = stats.ttest_1samp(clean, 0.0)
        return t_stat, float(p_value)
    except Exception:
        p_value = 2.0 * (1.0 - NormalDist().cdf(abs(t_stat)))
        return t_stat, float(p_value)


def histogram(values: list[int]) -> dict[str, int]:
    buckets = {
        "0-19": 0,
        "20-39": 0,
        "40-59": 0,
        "60-79": 0,
        "80-99": 0,
        "100+": 0,
    }
    for value in values:
        if value < 20:
            buckets["0-19"] += 1
        elif value < 40:
            buckets["20-39"] += 1
        elif value < 60:
            buckets["40-59"] += 1
        elif value < 80:
            buckets["60-79"] += 1
        elif value < 100:
            buckets["80-99"] += 1
        else:
            buckets["100+"] += 1
    return buckets


def load_sector_map_for_run(reference_dir: Path) -> dict[str, str]:
    full_map_path = reference_dir / "full_sector_map.csv"
    fallback_path = reference_dir / "nifty500_constituents.csv"
    return load_sector_map(full_map_path if full_map_path.exists() else fallback_path)


def run_window(
    window_id: str,
    start_date: date,
    end_date: date,
    sector_map: dict[str, str],
    output_dir: Path,
) -> dict[str, Any]:
    bundle, signals, ohlcv = diag.load_inputs(start_date, end_date)
    settings = bundle["settings"]
    benchmark_curve = (
        ohlcv.filter(pl.col("symbol") == settings.market.benchmark_symbol)
        .select(["date", "close"])
        .rename({"close": "tri"})
        .filter((pl.col("date") >= start_date) & (pl.col("date") <= end_date))
        .sort("date")
    )
    if benchmark_curve.is_empty():
        local_ref = Path("data/reference") / "nifty500_tri_2010-01-04_2026-03-17.csv"
        benchmark_curve = diag.load_benchmark_curve(local_ref, start_date, end_date)

    filtered_signals = diag.filter_signals(
        signals,
        settings=settings,
        diagnostics=bundle["diagnostics"],
        sector_map=sector_map,
        rs_rank_min=settings.backtest.rs_rank_min,
        rs_rank_max=settings.backtest.rs_rank_max,
        ranking_mode=settings.backtest.signal_ranking_mode,
        max_signals_per_day=settings.backtest.max_signals_per_day,
        quality_profile=settings.backtest.signal_quality_profile,
        quality_overrides=diag.resolve_signal_quality_overrides(settings),
    )
    regime_inputs = diag.compute_regime_inputs(settings, start_date, end_date, benchmark_curve)
    entry_gate: dict[date, bool] = {}
    entry_size_mult: dict[date, float] = {}
    if settings.backtest.use_regime_entry_controls:
        entry_gate, entry_size_mult = diag.build_entry_controls(settings, regime_inputs)

    result = diag.simulate_portfolio_python(
        filtered_signals,
        ohlcv,
        bundle["params"],
        bundle["portfolio"],
        sector_map,
        exit_config=bundle["exit_config"] if settings.backtest.use_extended_exit_model else None,
        entry_gate=entry_gate,
        entry_size_mult=entry_size_mult,
        breakeven_activation_r=settings.exit_strategy.breakeven_activation_r,
        time_stop_days=settings.exit_strategy.time_stop_days,
        partial_profit_r=settings.exit_strategy.partial_profit_r,
        trail_pct_bull=settings.exit_strategy.trail_pct_bull,
        trail_pct_bear=settings.exit_strategy.trail_pct_bear,
    )

    curve = result["daily_equity"].sort("date")
    trades = result["trades"]
    closed = trades.filter(pl.col("entry_date").is_not_null()).drop_nulls(["pnl"])
    first_entry = closed["entry_date"].min() if closed.height else start_date
    stats = diag.compute_performance_stats(curve.select("date", "equity"), "equity", first_trade_date=first_entry)
    t_stat, p_value = t_test_1samp(closed["r_multiple"].drop_nulls().to_list() if closed.height else [])
    tstats = {
        "window_id": window_id,
        "period_start": start_date.isoformat(),
        "period_end": end_date.isoformat(),
        "n": closed.height,
        "t_stat": t_stat,
        "p_value": p_value,
    }
    row = {
        "window_id": window_id,
        "period_start": start_date.isoformat(),
        "period_end": end_date.isoformat(),
        **trade_stats(trades),
        "cagr": stats["cagr"],
        "max_drawdown": stats["max_drawdown_pct"],
        "calmar": stats["calmar"],
        "total_signals": filtered_signals.height,
        "final_equity": float(result["final_equity"]),
        "t_stat": t_stat,
        "p_value": p_value,
    }

    detail_dir = output_dir / window_id
    detail_dir.mkdir(parents=True, exist_ok=True)
    closed.sort(["exit_date", "symbol"]).write_csv(detail_dir / "trade_pnl.csv")
    (detail_dir / "summary.json").write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
    return {
        "row": row,
        "curve": curve.with_columns(pl.lit(window_id).alias("window_id")).select("date", "window_id", "equity"),
        "monthly": monthly_returns(curve, window_id),
        "r_multiples": (
            closed.select("symbol", "signal_date", "entry_date", "exit_date", "r_multiple")
            .with_columns(pl.lit(window_id).alias("window_id"))
            if closed.height
            else pl.DataFrame(
                schema={
                    "symbol": pl.String,
                    "signal_date": pl.Date,
                    "entry_date": pl.Date,
                    "exit_date": pl.Date,
                    "r_multiple": pl.Float64,
                    "window_id": pl.String,
                }
            )
        ),
        "tstats": tstats,
    }


def print_table(rows: list[dict[str, Any]]) -> None:
    print("Window     Period             Fills  CAGR     MaxDD    Calmar  WR      AvgR")
    for row in rows:
        period = f"{row['period_start'][:7]} -> {row['period_end'][:7]}"
        print(
            f"{row['window_id']:<10} {period:<18} "
            f"{int(row['fills']):>5}  {pct(row['cagr']):>7}  {pct(row['max_drawdown']):>7}  "
            f"{num(row['calmar']):>6}  {pct(row['win_rate']):>6}  {num(row['avg_r']):>5}R"
        )
    if not rows:
        return
    metrics = ["fills", "cagr", "max_drawdown", "calmar", "win_rate", "avg_r"]
    aggregates = {
        "MEDIAN": {metric: float(np.median([row[metric] for row in rows if row[metric] is not None])) for metric in metrics},
        "WORST": {
            "fills": min(int(row["fills"]) for row in rows),
            "cagr": min(float(row["cagr"]) for row in rows),
            "max_drawdown": min(float(row["max_drawdown"]) for row in rows),
            "calmar": min(float(row["calmar"]) for row in rows),
            "win_rate": min(float(row["win_rate"]) for row in rows if row["win_rate"] is not None),
            "avg_r": min(float(row["avg_r"]) for row in rows if row["avg_r"] is not None),
        },
        "BEST": {
            "fills": max(int(row["fills"]) for row in rows),
            "cagr": max(float(row["cagr"]) for row in rows),
            "max_drawdown": max(float(row["max_drawdown"]) for row in rows),
            "calmar": max(float(row["calmar"]) for row in rows),
            "win_rate": max(float(row["win_rate"]) for row in rows if row["win_rate"] is not None),
            "avg_r": max(float(row["avg_r"]) for row in rows if row["avg_r"] is not None),
        },
    }
    print("-" * 78)
    for label, row in aggregates.items():
        print(
            f"{label:<10} {'':<18} "
            f"{int(row['fills']):>5}  {pct(row['cagr']):>7}  {pct(row['max_drawdown']):>7}  "
            f"{num(row['calmar']):>6}  {pct(row['win_rate']):>6}  {num(row['avg_r']):>5}R"
        )


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sector_map = load_sector_map_for_run(args.reference_dir)

    results = [run_window(window_id, start, end, sector_map, args.output_dir) for window_id, start, end in WINDOWS]
    rows = [item["row"] for item in results]
    table = pl.DataFrame(rows)
    table.write_csv(args.output_dir / "walkforward_table.csv")

    pl.concat([item["curve"] for item in results], how="vertical").write_csv(args.output_dir / "equity_curves.csv")
    pl.concat([item["r_multiples"] for item in results], how="vertical").write_csv(args.output_dir / "r_multiples_all.csv")
    pl.DataFrame([item["tstats"] for item in results]).write_csv(args.output_dir / "statistical_tests.csv")

    monthly_frames = [item["monthly"] for item in results]
    monthly = monthly_frames[0]
    for frame in monthly_frames[1:]:
        monthly = monthly.join(frame, on="month", how="full", coalesce=True)
    monthly.write_csv(args.output_dir / "monthly_returns.csv")

    monthly_numeric_cols = [column for column in monthly.columns if column != "month"]
    corr = monthly.select(monthly_numeric_cols).corr() if monthly_numeric_cols else pl.DataFrame()
    corr.write_csv(args.output_dir / "monthly_return_correlation.csv")

    fills = [int(row["fills"]) for row in rows]
    cagr_values = [float(row["cagr"]) for row in rows]
    calmar_values = [float(row["calmar"]) for row in rows]
    summary = {
        "windows": rows,
        "percent_positive_cagr": sum(value > 0.0 for value in cagr_values) / len(cagr_values) if cagr_values else None,
        "percent_calmar_gt_1": sum(value > 1.0 for value in calmar_values) / len(calmar_values) if calmar_values else None,
        "percent_calmar_gt_2": sum(value > 2.0 for value in calmar_values) / len(calmar_values) if calmar_values else None,
        "metrics": {
            "cagr": scalar_stats(cagr_values),
            "max_drawdown": scalar_stats([float(row["max_drawdown"]) for row in rows]),
            "calmar": scalar_stats(calmar_values),
            "win_rate": scalar_stats([float(row["win_rate"]) for row in rows if row["win_rate"] is not None]),
            "avg_r": scalar_stats([float(row["avg_r"]) for row in rows if row["avg_r"] is not None]),
            "fills": scalar_stats([float(value) for value in fills]),
        },
        "fills_std_dev": float(np.std(fills, ddof=1)) if len(fills) > 1 else 0.0,
        "max_of_window_max_drawdowns": min(float(row["max_drawdown"]) for row in rows) if rows else None,
        "trade_count_histogram": histogram(fills),
        "statistical_tests": [item["tstats"] for item in results],
        "output_files": {
            "walkforward_table": str(args.output_dir / "walkforward_table.csv"),
            "equity_curves": str(args.output_dir / "equity_curves.csv"),
            "monthly_returns": str(args.output_dir / "monthly_returns.csv"),
            "r_multiples_all": str(args.output_dir / "r_multiples_all.csv"),
            "statistical_tests": str(args.output_dir / "statistical_tests.csv"),
            "monthly_return_correlation": str(args.output_dir / "monthly_return_correlation.csv"),
        },
    }
    (args.output_dir / "walkforward_summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    print_table(rows)
    print(json.dumps({key: value for key, value in summary.items() if key != "windows"}, indent=2, default=str))


if __name__ == "__main__":
    main()

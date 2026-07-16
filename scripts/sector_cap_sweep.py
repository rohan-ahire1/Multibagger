from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

import portfolio_diagnostics as diag
from sepa_scanner.strategy.ranking import load_sector_map


VARIANTS = [
    ("baseline", 1.00),
    ("sector_cap_30", 0.30),
    ("sector_cap_25", 0.25),
    ("sector_cap_20", 0.20),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run sector-cap portfolio diagnostics sweep.")
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2010, 1, 4))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 3, 17))
    parser.add_argument("--reference-dir", type=Path, default=Path("data/reference"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/sector_cap_sweep"))
    return parser.parse_args()


def scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def load_sector_map_for_run(reference_dir: Path) -> dict[str, str]:
    full_map_path = reference_dir / "full_sector_map.csv"
    fallback_path = reference_dir / "nifty500_constituents.csv"
    return load_sector_map(full_map_path if full_map_path.exists() else fallback_path)


def trade_metrics(trades: pl.DataFrame, final_equity: float, initial_capital: float, curve: pl.DataFrame) -> dict[str, Any]:
    closed = trades.filter(pl.col("entry_date").is_not_null()).drop_nulls(["pnl"])
    stats = diag.compute_performance_stats(curve.select("date", "equity"), "equity")
    wins = closed.filter(pl.col("pnl") > 0)
    losses = closed.filter(pl.col("pnl") < 0)
    avg_win = float(wins["pnl"].mean()) if wins.height else None
    avg_loss = abs(float(losses["pnl"].mean())) if losses.height else None
    return {
        "fills": closed.height,
        "cagr": stats["cagr"],
        "max_drawdown": stats["max_drawdown_pct"],
        "calmar": stats["calmar"],
        "win_rate": float((closed["pnl"] > 0).mean()) if closed.height else None,
        "avg_r": float(closed["r_multiple"].mean()) if closed.height else None,
        "payoff_ratio": (avg_win / avg_loss) if avg_win is not None and avg_loss not in (None, 0.0) else None,
        "total_pnl": final_equity - initial_capital,
        "final_equity": final_equity,
    }


def yearly_returns(curve: pl.DataFrame, variant: str) -> pl.DataFrame:
    return (
        curve.drop_nulls(["daily_return"])
        .with_columns(pl.col("date").dt.year().alias("year"))
        .group_by("year")
        .agg((((1.0 + pl.col("daily_return")).product()) - 1.0).alias(variant))
        .sort("year")
    )


def monthly_returns(curve: pl.DataFrame, variant: str) -> pl.DataFrame:
    return (
        curve.drop_nulls(["daily_return"])
        .with_columns(pl.col("date").dt.strftime("%Y-%m").alias("month"))
        .group_by("month")
        .agg((((1.0 + pl.col("daily_return")).product()) - 1.0).alias(variant))
        .sort("month")
    )


def sector_distribution(trades: pl.DataFrame, variant: str) -> pl.DataFrame:
    closed = trades.filter(pl.col("entry_date").is_not_null()).drop_nulls(["pnl"])
    if closed.is_empty():
        return pl.DataFrame()
    return (
        closed.group_by("sector")
        .agg(
            pl.len().alias("fill_count"),
            pl.col("r_multiple").mean().alias("avg_r"),
            (pl.col("pnl") > 0).mean().alias("win_rate"),
            pl.col("pnl").sum().alias("pnl"),
        )
        .with_columns(pl.lit(variant).alias("variant"))
        .sort(["variant", "fill_count"], descending=[False, True])
    )


def blocked_entries(trades: pl.DataFrame, variant: str) -> pl.DataFrame:
    blocked = trades.filter(pl.col("status") == "skipped_sector_pct")
    if blocked.is_empty():
        return pl.DataFrame()
    return (
        blocked.with_columns(pl.col("signal_date").dt.year().alias("year"))
        .group_by(["year", "sector"])
        .agg(pl.len().alias("blocked_entries"))
        .with_columns(pl.lit(variant).alias("variant"))
        .sort(["variant", "year", "sector"])
    )


def sector_pnl(trades: pl.DataFrame, variant: str) -> pl.DataFrame:
    closed = trades.filter(pl.col("entry_date").is_not_null()).drop_nulls(["pnl"])
    if closed.is_empty():
        return pl.DataFrame()
    return (
        closed.group_by("sector")
        .agg(pl.col("pnl").sum().alias("pnl"), pl.len().alias("fills"))
        .with_columns(pl.lit(variant).alias("variant"))
        .sort(["variant", "pnl"], descending=[False, True])
    )


def concentration_report(trades: pl.DataFrame, curve: pl.DataFrame, ohlcv: pl.DataFrame, variant: str) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    closed = trades.filter(pl.col("entry_date").is_not_null()).drop_nulls(["pnl"])
    if closed.is_empty():
        empty = pl.DataFrame()
        return empty, empty, empty

    prices = {
        (row["symbol"], row["date"]): float(row["close"])
        for row in ohlcv.select("date", "symbol", "close").iter_rows(named=True)
    }
    equity_by_date = {row["date"]: float(row["equity"]) for row in curve.select("date", "equity").iter_rows(named=True)}
    daily_rows: list[dict[str, Any]] = []
    for today in sorted(equity_by_date):
        sector_values: dict[str, float] = {}
        sector_open_pnl: dict[str, float] = {}
        for trade in closed.iter_rows(named=True):
            entry_date = trade["entry_date"]
            exit_date = trade["exit_date"]
            if entry_date is None or exit_date is None or today < entry_date or today > exit_date:
                continue
            price = prices.get((trade["symbol"], today))
            if price is None:
                continue
            shares = float(trade["shares"] or 0.0)
            sector = str(trade["sector"])
            sector_values[sector] = sector_values.get(sector, 0.0) + shares * price
            sector_open_pnl[sector] = sector_open_pnl.get(sector, 0.0) + shares * (price - float(trade["entry_price"]))
        equity = equity_by_date[today]
        for sector, value in sector_values.items():
            daily_rows.append(
                {
                    "variant": variant,
                    "date": today,
                    "sector": sector,
                    "sector_value": value,
                    "sector_capital_pct": value / equity if equity else None,
                    "sector_open_pnl": sector_open_pnl.get(sector, 0.0),
                }
            )
    daily = pl.DataFrame(daily_rows)
    if daily.is_empty():
        empty = pl.DataFrame()
        return empty, empty, empty

    yearly = (
        daily.with_columns(pl.col("date").dt.year().alias("year"))
        .group_by(["variant", "year"])
        .agg(
            pl.col("sector_capital_pct").max().alias("top_sector_capital_pct_max"),
            pl.col("sector_capital_pct").mean().alias("sector_capital_pct_mean_all_sector_days"),
        )
        .sort(["variant", "year"])
    )
    monthly = (
        daily.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("month"))
        .filter(pl.col("sector_capital_pct") > 0.50)
        .group_by(["variant", "month", "sector"])
        .agg(
            pl.len().alias("days_above_50_pct_capital"),
            pl.col("sector_capital_pct").max().alias("max_sector_capital_pct"),
        )
        .sort(["variant", "month", "sector"])
    )

    dd_rows = []
    for group in daily.sort("date").partition_by("sector", maintain_order=True):
        sector = str(group["sector"][0])
        peak = -math.inf
        max_dd = 0.0
        for value in group["sector_open_pnl"].to_list():
            value = float(value)
            peak = max(peak, value)
            max_dd = min(max_dd, value - peak)
        dd_rows.append({"variant": variant, "sector": sector, "max_concurrent_same_sector_drawdown_pnl": max_dd})
    return yearly, monthly, pl.DataFrame(dd_rows).sort(["variant", "max_concurrent_same_sector_drawdown_pnl"])


def run_variant(
    name: str,
    max_sector_pct: float,
    bundle: dict[str, Any],
    signals: pl.DataFrame,
    ohlcv: pl.DataFrame,
    sector_map: dict[str, str],
) -> dict[str, Any]:
    settings = bundle["settings"]
    filtered = diag.filter_signals(
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
    benchmark_curve = (
        ohlcv.filter(pl.col("symbol") == settings.market.benchmark_symbol)
        .select(["date", "close"])
        .rename({"close": "tri"})
        .sort("date")
    )
    regime_inputs = diag.compute_regime_inputs(settings, signals["signal_date"].min(), signals["signal_date"].max(), benchmark_curve)
    entry_gate: dict[date, bool] = {}
    entry_size_mult: dict[date, float] = {}
    if settings.backtest.use_regime_entry_controls:
        entry_gate, entry_size_mult = diag.build_entry_controls(settings, regime_inputs)

    portfolio = dict(bundle["portfolio"])
    portfolio["max_sector_pct"] = max_sector_pct
    result = diag.simulate_portfolio_python(
        filtered,
        ohlcv,
        bundle["params"],
        portfolio,
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
    return {"filtered": filtered, "result": result, "portfolio": portfolio}


def concat_or_empty(frames: list[pl.DataFrame]) -> pl.DataFrame:
    frames = [frame for frame in frames if not frame.is_empty()]
    return pl.concat(frames, how="vertical") if frames else pl.DataFrame()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sector_map = load_sector_map_for_run(args.reference_dir)
    bundle, signals, ohlcv = diag.load_inputs(args.start_date, args.end_date)
    initial_capital = float(bundle["portfolio"]["initial_capital"])

    summaries = []
    yearly_frames = []
    monthly_frames = []
    distribution_frames = []
    blocked_frames = []
    pnl_frames = []
    concentration_yearly = []
    concentration_monthly = []
    concentration_dd = []

    for name, max_sector_pct in VARIANTS:
        print(f"Running {name} max_sector_pct={max_sector_pct}")
        variant_dir = args.output_dir / name
        variant_dir.mkdir(parents=True, exist_ok=True)
        run = run_variant(name, max_sector_pct, bundle, signals, ohlcv, sector_map)
        result = run["result"]
        curve = result["daily_equity"].sort("date")
        trades = result["trades"]
        closed = trades.filter(pl.col("entry_date").is_not_null()).drop_nulls(["pnl"])
        closed.sort(["exit_date", "symbol"]).write_csv(variant_dir / "trade_pnl.csv")
        curve.write_csv(variant_dir / "strategy_daily_equity.csv")
        metrics = trade_metrics(trades, float(result["final_equity"]), initial_capital, curve)
        summary = {"variant": name, "max_sector_pct": max_sector_pct, **metrics}
        summaries.append(summary)
        (variant_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

        yearly_frames.append(yearly_returns(curve, name))
        monthly_frames.append(monthly_returns(curve, name))
        distribution_frames.append(sector_distribution(trades, name))
        blocked_frames.append(blocked_entries(trades, name))
        pnl_frames.append(sector_pnl(trades, name))
        yearly_conc, monthly_conc, dd_conc = concentration_report(trades, curve, ohlcv, name)
        concentration_yearly.append(yearly_conc)
        concentration_monthly.append(monthly_conc)
        concentration_dd.append(dd_conc)

    pl.DataFrame(summaries).write_csv(args.output_dir / "variant_summary.csv")
    yearly = yearly_frames[0]
    for frame in yearly_frames[1:]:
        yearly = yearly.join(frame, on="year", how="full", coalesce=True)
    yearly.write_csv(args.output_dir / "yearly_cagr_by_variant.csv")
    monthly = monthly_frames[0]
    for frame in monthly_frames[1:]:
        monthly = monthly.join(frame, on="month", how="full", coalesce=True)
    monthly.write_csv(args.output_dir / "monthly_returns.csv")
    concat_or_empty(distribution_frames).write_csv(args.output_dir / "sector_distribution.csv")
    concat_or_empty(blocked_frames).write_csv(args.output_dir / "blocked_entries_by_sector_year.csv")
    concat_or_empty(pnl_frames).write_csv(args.output_dir / "sector_pnl_contribution.csv")
    concat_or_empty(concentration_yearly).write_csv(args.output_dir / "sector_concentration_by_year.csv")
    concat_or_empty(concentration_monthly).write_csv(args.output_dir / "months_above_50pct_sector_capital.csv")
    concat_or_empty(concentration_dd).write_csv(args.output_dir / "same_sector_drawdown.csv")

    report = {
        "date_range": {"start": args.start_date.isoformat(), "end": args.end_date.isoformat()},
        "variants": [{key: scalar(value) for key, value in row.items()} for row in summaries],
        "output_files": {
            "variant_summary": str(args.output_dir / "variant_summary.csv"),
            "yearly_cagr_by_variant": str(args.output_dir / "yearly_cagr_by_variant.csv"),
            "monthly_returns": str(args.output_dir / "monthly_returns.csv"),
            "sector_distribution": str(args.output_dir / "sector_distribution.csv"),
            "blocked_entries": str(args.output_dir / "blocked_entries_by_sector_year.csv"),
            "sector_pnl_contribution": str(args.output_dir / "sector_pnl_contribution.csv"),
            "sector_concentration_by_year": str(args.output_dir / "sector_concentration_by_year.csv"),
            "months_above_50pct_sector_capital": str(args.output_dir / "months_above_50pct_sector_capital.csv"),
            "same_sector_drawdown": str(args.output_dir / "same_sector_drawdown.csv"),
        },
    }
    (args.output_dir / "sector_cap_sweep_report.json").write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sepa_scanner.strategy.ranking import load_sector_map
from strategies.mean_reversion.diagnostics import compute_metrics, load_mr_config, load_ohlcv, write_outputs
from strategies.mean_reversion.exit_engine import MRExitConfig
from strategies.mean_reversion.signal_engine import scan_mean_reversion
from strategies.mean_reversion.simulation import build_benchmark_regime, simulate_mr_portfolio
from strategies.mean_reversion.universe import (
    filter_signals_to_historical_membership,
    historical_liquidity_membership,
)


@dataclass(frozen=True, slots=True)
class ValidationScenario:
    name: str
    start_date: date
    end_date: date
    risk_per_trade_pct: float
    round_trip_cost_pct: float
    slippage_bps: float
    exit_execution: str
    regime: str = "all"
    write_detail: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate mean-reversion backtest realism.")
    parser.add_argument("--start", default="2010-01-04")
    parser.add_argument("--end", default="2026-05-29")
    parser.add_argument("--output-dir", default="reports/mean_reversion/validation")
    parser.add_argument("--config", default="strategies/mean_reversion/config.yaml")
    return parser.parse_args()


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _round(value: Any, digits: int = 4) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    return value


def _cap_end_date(ohlcv: pl.DataFrame, requested_end: date) -> date:
    if ohlcv.is_empty():
        return requested_end
    max_date = ohlcv["date"].max()
    return min(requested_end, max_date)


def _signals_for_regime(signals: pl.DataFrame, ohlcv: pl.DataFrame, regime: str) -> pl.DataFrame:
    if regime == "all" or signals.is_empty():
        return signals
    regime_map = build_benchmark_regime(ohlcv)
    allowed = {d for d, bullish in regime_map.items() if bullish} if regime == "bull" else {d for d, bullish in regime_map.items() if not bullish}
    return signals.filter(pl.col("signal_date").is_in(sorted(allowed)))


def _capacity_stats(trades: pl.DataFrame, signals: pl.DataFrame) -> dict[str, Any]:
    if trades.is_empty() or signals.is_empty() or "adv50" not in signals.columns:
        return {"avg_entry_notional_to_adv50": None, "p95_entry_notional_to_adv50": None, "max_entry_notional_to_adv50": None}
    joined = trades.join(
        signals.select(["signal_date", "symbol", "adv50"]),
        on=["signal_date", "symbol"],
        how="left",
    ).with_columns(((pl.col("shares") * pl.col("entry_price")) / pl.col("adv50")).alias("entry_notional_to_adv50"))
    ratios = joined["entry_notional_to_adv50"].drop_nulls().to_list()
    if not ratios:
        return {"avg_entry_notional_to_adv50": None, "p95_entry_notional_to_adv50": None, "max_entry_notional_to_adv50": None}
    ratios = sorted(float(value) for value in ratios)
    p95_idx = min(len(ratios) - 1, int(len(ratios) * 0.95))
    return {
        "avg_entry_notional_to_adv50": sum(ratios) / len(ratios),
        "p95_entry_notional_to_adv50": ratios[p95_idx],
        "max_entry_notional_to_adv50": max(ratios),
    }


def _concentration_stats(trades: pl.DataFrame) -> dict[str, Any]:
    if trades.is_empty():
        return {"top_symbol_trade_pct": None, "top_symbol_pnl_pct": None}
    by_symbol = trades.group_by("symbol").agg(pl.len().alias("fills"), pl.col("pnl").sum().alias("pnl"))
    top_fills = int(by_symbol["fills"].max())
    gross_abs_pnl = float(trades["pnl"].abs().sum())
    top_abs_pnl = float(by_symbol.with_columns(pl.col("pnl").abs().alias("abs_pnl"))["abs_pnl"].max())
    return {
        "top_symbol_trade_pct": top_fills / trades.height,
        "top_symbol_pnl_pct": top_abs_pnl / gross_abs_pnl if gross_abs_pnl > 0 else None,
    }


def run_scenario(
    *,
    scenario: ValidationScenario,
    config: dict[str, Any],
    ohlcv: pl.DataFrame,
    base_signals: pl.DataFrame,
    sector_map: dict[str, str],
    output_dir: Path,
) -> dict[str, Any]:
    signals = base_signals.filter(
        (pl.col("signal_date") >= scenario.start_date) & (pl.col("signal_date") <= scenario.end_date)
    )
    signals = _signals_for_regime(signals, ohlcv, scenario.regime)
    exit_config = MRExitConfig(
        exit_mode=str(config["exit"]["mode"]),
        time_stop_days=int(config["exit"]["time_stop_days"]),
        stop_atr_mult=float(config["entry"]["stop_atr_mult"]),
        max_hold_days=int(config["exit"].get("max_hold_days", 20)),
    )
    result = simulate_mr_portfolio(
        signals,
        ohlcv.filter(pl.col("date") <= scenario.end_date),
        start_date=scenario.start_date,
        end_date=scenario.end_date,
        max_positions=int(config["portfolio"]["max_positions"]),
        max_sector_positions=int(config["portfolio"]["max_sector_positions"]),
        initial_capital=float(config["portfolio"]["initial_capital"]),
        risk_per_trade_pct=scenario.risk_per_trade_pct,
        exit_config=exit_config,
        sector_map=sector_map,
        round_trip_cost_pct=scenario.round_trip_cost_pct,
        exit_execution=scenario.exit_execution,
        slippage_bps=scenario.slippage_bps,
    )
    metrics = compute_metrics(result["daily_equity"], result["trades"])
    daily_equity: pl.DataFrame = result["daily_equity"]
    row = {
        "scenario": scenario.name,
        "period_start": scenario.start_date.isoformat(),
        "period_end": scenario.end_date.isoformat(),
        "regime": scenario.regime,
        "risk_per_trade_pct": scenario.risk_per_trade_pct,
        "round_trip_cost_pct": scenario.round_trip_cost_pct,
        "slippage_bps": scenario.slippage_bps,
        "exit_execution": scenario.exit_execution,
        "signals": signals.height,
        "filled_signals": result["filled_signals"],
        "fill_rate": result["filled_signals"] / signals.height if signals.height else 0.0,
        "expired_signals": result["expired_signals"],
        "skipped_signals": result["skipped_signals"],
        "min_cash": float(daily_equity["cash"].min()) if not daily_equity.is_empty() else None,
        **metrics,
        **_capacity_stats(result["trades"], signals),
        **_concentration_stats(result["trades"]),
    }
    if scenario.write_detail:
        detail = {**result, "signals": signals, "metrics": row}
        write_outputs(output_dir / scenario.name, detail)
    return {key: _round(value) for key, value in row.items()}


def build_scenarios(start_date: date, end_date: date) -> list[ValidationScenario]:
    scenarios = [
        ValidationScenario("baseline_cost_0_2_same_close_risk_5", start_date, end_date, 0.05, 0.002, 0.0, "same_close", write_detail=True),
        ValidationScenario("realistic_cost_0_5_next_open_risk_1", start_date, end_date, 0.01, 0.005, 10.0, "next_open", write_detail=True),
        ValidationScenario("realistic_cost_0_5_next_open_risk_2", start_date, end_date, 0.02, 0.005, 10.0, "next_open"),
    ]
    for cost in (0.002, 0.003, 0.005, 0.0075, 0.01):
        scenarios.append(
            ValidationScenario(f"cost_stress_{cost * 100:.2f}pct_risk_1", start_date, end_date, 0.01, cost, 10.0, "next_open")
        )
    for risk in (0.005, 0.01, 0.02, 0.03):
        scenarios.append(
            ValidationScenario(f"risk_sweep_{risk * 100:.1f}pct_cost_0_5", start_date, end_date, risk, 0.005, 10.0, "next_open")
        )
    for name, split_start, split_end in (
        ("insample_2010_2018", date(2010, 1, 4), date(2018, 12, 31)),
        ("validation_2019_2022", date(2019, 1, 1), date(2022, 12, 31)),
        ("oos_2023_latest", date(2023, 1, 1), end_date),
    ):
        scenarios.append(ValidationScenario(name, max(start_date, split_start), min(end_date, split_end), 0.01, 0.005, 10.0, "next_open"))
    for regime in ("bull", "bear"):
        scenarios.append(ValidationScenario(f"regime_{regime}_risk_1_cost_0_5", start_date, end_date, 0.01, 0.005, 10.0, "next_open", regime=regime))
    return [scenario for scenario in scenarios if scenario.start_date <= scenario.end_date]


def write_verdict(output_dir: Path, rows: list[dict[str, Any]], requested_end: date, actual_end: date) -> None:
    by_name = {str(row["scenario"]): row for row in rows}
    baseline = by_name.get("baseline_cost_0_2_same_close_risk_5", {})
    realistic = by_name.get("realistic_cost_0_5_next_open_risk_1", {})
    oos = by_name.get("oos_2023_latest", {})
    checks = {
        "no_negative_cash_realistic": (realistic.get("min_cash") is not None and float(realistic["min_cash"]) >= -0.01),
        "realistic_cagr_positive": (realistic.get("cagr_pct") is not None and float(realistic["cagr_pct"]) > 0.0),
        "realistic_drawdown_under_35pct": (realistic.get("max_drawdown_pct") is not None and float(realistic["max_drawdown_pct"]) > -35.0),
        "oos_cagr_positive": (oos.get("cagr_pct") is not None and float(oos["cagr_pct"]) > 0.0),
        "realistic_has_enough_fills": (realistic.get("fills") is not None and int(realistic["fills"]) > 100),
    }
    verdict = {
        "requested_end_date": requested_end.isoformat(),
        "actual_data_end_date": actual_end.isoformat(),
        "baseline_cagr_pct": baseline.get("cagr_pct"),
        "baseline_max_drawdown_pct": baseline.get("max_drawdown_pct"),
        "realistic_cagr_pct": realistic.get("cagr_pct"),
        "realistic_max_drawdown_pct": realistic.get("max_drawdown_pct"),
        "oos_2023_latest_cagr_pct": oos.get("cagr_pct"),
        "checks": checks,
        "passed": all(checks.values()),
        "note": "This validates mechanics and robustness, not a live trading promise. Market does not sign affidavits, sadly.",
    }
    (output_dir / "validation_verdict.json").write_text(json.dumps(verdict, indent=2, default=str), encoding="utf-8")

    lines = [
        "# Mean Reversion Realism Validation",
        "",
        f"- Requested end date: `{requested_end.isoformat()}`",
        f"- Actual data end date: `{actual_end.isoformat()}`",
        f"- Baseline CAGR / max DD: `{baseline.get('cagr_pct')}` / `{baseline.get('max_drawdown_pct')}`",
        f"- Realistic CAGR / max DD: `{realistic.get('cagr_pct')}` / `{realistic.get('max_drawdown_pct')}`",
        f"- OOS 2023-latest CAGR: `{oos.get('cagr_pct')}`",
        "",
        "## Checks",
        "",
    ]
    for name, passed in checks.items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} `{name}`")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Use `validation_summary.csv` as the source of truth. The realistic row uses 0.5% round-trip cost, 10 bps slippage each side, 1% risk, no negative cash, and next-open indicator exits.",
        ]
    )
    (output_dir / "validation_verdict.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    start_date = parse_date(args.start)
    requested_end = parse_date(args.end)
    config = load_mr_config(Path(args.config))
    sector_map = load_sector_map(Path(config["data"]["sector_map_path"]))
    ohlcv = load_ohlcv(config, start_date, requested_end)
    actual_end = _cap_end_date(ohlcv, requested_end)
    ohlcv = ohlcv.filter(pl.col("date") <= actual_end)

    membership = historical_liquidity_membership(
        ohlcv,
        size=100,
        lookback_sessions=50,
        min_history_sessions=200,
        reconstitute="semiannual",
    )
    base_signals = filter_signals_to_historical_membership(scan_mean_reversion(
        ohlcv,
        rsi_period=int(config["entry"]["rsi_period"]),
        rsi_threshold=float(config["entry"]["rsi_threshold"]),
        sma_trend_period=int(config["entry"]["sma_trend_period"]),
        min_adv_inr=float(config["entry"]["min_adv_inr"]),
        min_price=float(config["entry"]["min_price_inr"]),
        stop_atr_mult=float(config["entry"]["stop_atr_mult"]),
    ), membership).filter((pl.col("signal_date") >= start_date) & (pl.col("signal_date") <= actual_end))

    output_dir = Path(args.output_dir) / f"{start_date.isoformat()}_{actual_end.isoformat()}"
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        run_scenario(
            scenario=scenario,
            config=config,
            ohlcv=ohlcv,
            base_signals=base_signals,
            sector_map=sector_map,
            output_dir=output_dir,
        )
        for scenario in build_scenarios(start_date, actual_end)
    ]
    pl.DataFrame(rows).write_csv(output_dir / "validation_summary.csv")
    (output_dir / "validation_summary.json").write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    write_verdict(output_dir, rows, requested_end, actual_end)

    print(f"Loaded {ohlcv.height} OHLCV rows, {base_signals.height} base signals.")
    print(f"Wrote validation outputs to {output_dir}")
    print(pl.DataFrame(rows).select(["scenario", "fills", "cagr_pct", "max_drawdown_pct", "win_rate", "min_cash"]))


if __name__ == "__main__":
    main()

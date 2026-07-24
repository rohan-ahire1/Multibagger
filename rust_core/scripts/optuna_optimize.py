from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import optuna
import polars as pl

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.portfolio_diagnostics import (  # noqa: E402
    build_entry_controls,
    compute_ex_2023_cagr,
    compute_regime_inputs,
    compute_performance_stats,
    compute_rolling_cagr,
    compute_rolling_summary,
    ensure_reference_data,
    filter_signals,
    join_strategy_and_benchmark,
    load_benchmark_curve,
    load_sector_map as load_analysis_sector_map,
    resolve_exit_config,
    resolve_signal_quality_overrides,
    simulate_portfolio_python,
)
from sepa_scanner.backtest.models import BacktestParams, ExitMode  # noqa: E402
from sepa_scanner.backtest.queries import (  # noqa: E402
    get_backtest_diagnostics,
    get_backtest_ohlcv,
    get_backtest_signals,
)
from sepa_scanner.settings import load_settings  # noqa: E402
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore  # noqa: E402


@dataclass(slots=True)
class ObjectiveThresholds:
    min_rolling_beat_rate: float
    require_ex_2023_above_benchmark: bool


def _load_optional_sector_map(constituent_csv_path: Path | None = None) -> dict[str, str]:
    if constituent_csv_path is not None and constituent_csv_path.exists():
        return load_analysis_sector_map(constituent_csv_path)

    candidates: list[Path] = []
    env_path = os.getenv("SEPA_SECTOR_MAP_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(
        [
            PROJECT_ROOT / "config" / "sector_map.csv",
            PROJECT_ROOT / "data" / "sector_map.csv",
            PROJECT_ROOT / "data" / "cache" / "sector_map.csv",
        ]
    )

    for path in candidates:
        if path.exists():
            return load_analysis_sector_map(path)
    return {}


def _decode_max_signals_per_day(value: Any) -> int | None:
    return None if value in (None, "off") else int(value)


def _resolve_optimization_window(ohlcv_df: pl.DataFrame) -> tuple[date, date]:
    env_start = os.getenv("SEPA_OPTUNA_START")
    env_end = os.getenv("SEPA_OPTUNA_END")

    start_date = date.fromisoformat(env_start) if env_start else ohlcv_df["date"].min()
    end_date = date.fromisoformat(env_end) if env_end else ohlcv_df["date"].max()
    return start_date, end_date


def _resolve_trial_count() -> int:
    env_trials = os.getenv("SEPA_OPTUNA_TRIALS")
    return int(env_trials) if env_trials else 500


def _resolve_thresholds() -> ObjectiveThresholds:
    min_beat_rate = float(os.getenv("SEPA_OPTUNA_MIN_ROLLING_BEAT_RATE", "0.50"))
    require_ex_2023 = os.getenv("SEPA_OPTUNA_REQUIRE_EX2023", "1") != "0"
    return ObjectiveThresholds(
        min_rolling_beat_rate=min_beat_rate,
        require_ex_2023_above_benchmark=require_ex_2023,
    )


def _resolve_study_storage() -> str:
    storage = os.getenv("SEPA_OPTUNA_STORAGE")
    if storage:
        return storage
    optuna_dir = PROJECT_ROOT / "data" / "optuna"
    optuna_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(optuna_dir / 'robust_studies.db').as_posix()}"


def _resolve_study_name(start_date: date, end_date: date, thresholds: ObjectiveThresholds) -> str:
    env_name = os.getenv("SEPA_OPTUNA_STUDY_NAME")
    if env_name:
        return env_name
    beat_label = int(round(thresholds.min_rolling_beat_rate * 100))
    return f"robust_br{beat_label}_{start_date.isoformat()}_{end_date.isoformat()}"


def _decode_soft_floor(value: Any) -> float | None:
    return None if value in (None, "off") else float(value)


def _build_trial_params(
    settings: Any,
    trial: optuna.Trial,
) -> tuple[BacktestParams, dict[str, Any], dict[str, Any] | None, dict[str, Any], dict[str, Any]]:
    breadth_soft_floor_choice = trial.suggest_categorical(
        "breadth_soft_floor_pct", ["off", "0.45", "0.50", "0.55", "0.60"]
    )
    # breadth_soft_floor_pct = _decode_soft_floor(breadth_soft_floor_choice)
    if breadth_soft_floor_choice == "off":
        breadth_soft_floor_pct = None
    else:
        breadth_soft_floor_pct = float(breadth_soft_floor_choice)
    breadth_soft_size_mult = 1.0
    if breadth_soft_floor_pct is not None:
        breadth_soft_size_mult = trial.suggest_float("breadth_soft_size_mult", 0.25, 0.75, step=0.25)
    use_extended_exit_model = trial.suggest_categorical("use_extended_exit_model", [True]) # Always use advanced for sniper
    use_regime_entry_controls = trial.suggest_categorical("use_regime_entry_controls", [False, True])
    use_rs_filter = trial.suggest_categorical("use_rs_filter", [False, True])
    signal_ranking_mode = trial.suggest_categorical("signal_ranking_mode", ["default", "championship", "offense_v2"])
    max_signals_per_day_choice = trial.suggest_categorical(
        "max_signals_per_day",
        ["off", "1", "2", "3", "4", "5"],
    )
    signal_quality_profile = trial.suggest_categorical(
        "signal_quality_profile",
        ["default", "championship", "championship_loose"],
    )
    params = BacktestParams(
        pending_days=trial.suggest_int("pending_days", 3, 12),
        max_hold_days=trial.suggest_int("max_hold_days", 60, 250),
        target_r=settings.risk.target_r,
        exit_mode=ExitMode(trial.suggest_categorical("exit_mode", ["v3_trail_pct", "advanced_sepa"])),
        trail_activation_r=trial.suggest_float("trail_activation_r", 1.5, 4.5, step=0.5),
        trail_pct=trial.suggest_float("trail_pct", 0.05, 0.25, step=0.05),
        dead_money_days=trial.suggest_int("dead_money_days", 15, 45),
    )
    portfolio = {
        "initial_capital": settings.portfolio.initial_capital,
        "risk_per_trade_pct": trial.suggest_float("risk_per_trade_pct", 0.02, 0.08, step=0.01),
        "max_positions": trial.suggest_int("max_positions", 3, 15),
        "max_sector_positions": settings.portfolio.max_sector_positions,
        "max_sector_pct": settings.portfolio.max_sector_pct,
        "max_heat_pct": trial.suggest_float("max_heat_pct", 0.20, 1.0, step=0.1),
        "drawdown_half_pct": trial.suggest_float("drawdown_half_pct", 0.10, 0.25, step=0.05),
        "drawdown_stop_pct": trial.suggest_float("drawdown_stop_pct", 0.20, 0.35, step=0.05),
        "drawdown_close_pct": trial.suggest_float("drawdown_close_pct", 0.30, 0.45, step=0.05),
        "regime_gate_enabled": settings.portfolio.regime_gate_enabled,
        "regime_high_volatility_threshold": trial.suggest_float("regime_high_volatility_threshold", 0.18, 0.30, step=0.02),
        "regime_cooldown_days": trial.suggest_int("regime_cooldown_days", 0, 10),
        "breadth_soft_floor_pct": breadth_soft_floor_pct,
        "breadth_soft_size_mult": breadth_soft_size_mult,
        "pyramid_max_adds": trial.suggest_int("pyramid_max_adds", 0, 2),
        "pyramid_trigger_r": trial.suggest_float("pyramid_trigger_r", 1.0, 3.0, step=0.5),
        "pyramid_add_interval_r": trial.suggest_float("pyramid_add_interval_r", 0.5, 2.0, step=0.5),
        "pyramid_risk_frac": trial.suggest_float("pyramid_risk_frac", 0.25, 0.75, step=0.25),
    }
    exit_config = None
    if use_extended_exit_model:
        exit_config = {
            "breakeven_activation_r": trial.suggest_float("breakeven_activation_r", 0.8, 3.0, step=0.1),
            "partial_profit_r": trial.suggest_float("partial_profit_r", 1.5, 5.0, step=0.5),
            "partial_profit_pct": trial.suggest_float("partial_profit_pct", 0.25, 0.75, step=0.05),
            "trail_activation_r": params.trail_activation_r,
            "trail_pct": params.trail_pct,
            "trail_pct_bull": params.trail_pct,
            "trail_pct_bear": min(params.trail_pct, 0.15),
            "dead_money_days": params.dead_money_days,
            "time_stop_days": trial.suggest_int("time_stop_days", 8, 25),
            "max_hold_before_tighten": trial.suggest_int("max_hold_before_tighten", 20, 80, step=5),
        }
    signal_filters = {
        "rs_rank_min": trial.suggest_int("rs_rank_min", 65, 85, step=5) if use_rs_filter else None,
        "rs_rank_max": settings.backtest.rs_rank_max,
        "ranking_mode": signal_ranking_mode,
        "max_signals_per_day": _decode_max_signals_per_day(max_signals_per_day_choice),
        "quality_profile": signal_quality_profile,
        "quality_overrides": None,
    }
    runtime_options = {
        "use_extended_exit_model": use_extended_exit_model,
        "use_regime_entry_controls": use_regime_entry_controls,
        "use_rs_filter": use_rs_filter,
    }
    return params, portfolio, exit_config, signal_filters, runtime_options


def _evaluate_trial_result(
    *,
    result: dict[str, Any],
    benchmark_curve: pl.DataFrame,
    benchmark_roll: pl.DataFrame,
    thresholds: ObjectiveThresholds,
    initial_capital: float,
) -> dict[str, Any]:
    strategy_curve = result["daily_equity"]
    trades = result["trades"]
    first_entry = trades.filter(pl.col("entry_date").is_not_null())["entry_date"].min()
    
    if first_entry is None:
        return {
            "score": -1_000_000.0,
            "constraints_met": False,
            "calmar": 0.0,
            "cagr": 0.0,
            "max_drawdown_pct": 0.0,
            "final_equity": initial_capital,
            "benchmark_cagr": 0.0,
            "ex_2023_cagr": 0.0,
            "rolling_beat_rate": 0.0,
            "rolling_latest_cagr": 0.0,
            "rolling_median_cagr": 0.0,
            "filled_trades": 0,
            "ex_2023_ok": False,
            "beat_rate_ok": False,
        }

    joined_curve = join_strategy_and_benchmark(strategy_curve, benchmark_curve)
    strategy_stats = compute_performance_stats(strategy_curve.select("date", "equity"), "equity", first_trade_date=first_entry)
    strategy_roll = compute_rolling_cagr(joined_curve.select("date", "equity"), "equity")
    rolling_summary = compute_rolling_summary(strategy_roll, benchmark_roll)
    ex_2023 = compute_ex_2023_cagr(strategy_curve, initial_capital)

    active_benchmark_stats = compute_performance_stats(benchmark_curve.select("date", "tri"), "tri", first_trade_date=first_entry)
    benchmark_cagr = float(active_benchmark_stats["cagr"])

    calmar = float(strategy_stats["calmar"])
    cagr = float(strategy_stats["cagr"])
    max_drawdown_pct = float(strategy_stats["max_drawdown_pct"])
    filled_trades = int(trades.filter(pl.col("entry_date").is_not_null()).height)

    ex_2023_spread = float(ex_2023["cagr_ex_2023"] - benchmark_cagr)
    
    # Apex Objective: High CAGR + High Calmar
    score = (100.0 * cagr) + (50.0 * calmar)
    
    if calmar < 1.0:
        score -= 2000.0
    if cagr < 0.20:
        score -= 1000.0
    if filled_trades < 100:
        score -= 1000.0
    if max_drawdown_pct < -0.25:
        score -= 2000.0

    ex_2023_ok = ex_2023["cagr_ex_2023"] > benchmark_cagr if thresholds.require_ex_2023_above_benchmark else True
    beat_rate_ok = rolling_summary["strategy_beat_rate"] >= thresholds.min_rolling_beat_rate
    constraints_met = calmar >= 0.25 and filled_trades >= 60 and max_drawdown_pct >= -0.40

    return {
        "score": score,
        "constraints_met": constraints_met,
        "calmar": calmar,
        "cagr": cagr,
        "max_drawdown_pct": max_drawdown_pct,
        "final_equity": float(strategy_stats["end_value"]),
        "benchmark_cagr": benchmark_cagr,
        "ex_2023_cagr": float(ex_2023["cagr_ex_2023"]),
        "rolling_beat_rate": float(rolling_summary["strategy_beat_rate"]),
        "rolling_latest_cagr": float(rolling_summary["strategy_latest"]),
        "rolling_median_cagr": float(rolling_summary["strategy_median"]),
        "filled_trades": filled_trades,
        "ex_2023_ok": ex_2023_ok,
        "beat_rate_ok": beat_rate_ok,
    }


def _evaluate_current_defaults(
    *,
    settings: Any,
    signals_df: pl.DataFrame,
    diagnostics_df: pl.DataFrame,
    ohlcv_df: pl.DataFrame,
    benchmark_curve: pl.DataFrame,
    benchmark_roll: pl.DataFrame,
    benchmark_cagr: float,
    sector_map: dict[str, str],
    thresholds: ObjectiveThresholds,
    regime_inputs: pl.DataFrame,
) -> dict[str, Any]:
    params = BacktestParams(
        pending_days=settings.backtest.pending_days,
        max_hold_days=settings.backtest.max_hold_days,
        target_r=settings.risk.target_r,
        exit_mode=ExitMode(settings.backtest.exit_mode),
        baseline_tighten_r=settings.backtest.baseline_tighten_r,
        trail_activation_r=settings.exit_strategy.trail_activation_r,
        trail_pct=settings.exit_strategy.trail_pct,
        dead_money_days=settings.exit_strategy.dead_money_days,
    )
    portfolio = {
        "initial_capital": settings.portfolio.initial_capital,
        "risk_per_trade_pct": settings.portfolio.risk_per_trade_pct,
        "max_positions": settings.portfolio.max_positions,
        "max_sector_positions": settings.portfolio.max_sector_positions,
        "max_sector_pct": settings.portfolio.max_sector_pct,
        "max_heat_pct": settings.portfolio.max_heat_pct,
        "drawdown_half_pct": settings.portfolio.drawdown_half_pct,
        "drawdown_stop_pct": settings.portfolio.drawdown_stop_pct,
        "drawdown_close_pct": settings.portfolio.drawdown_close_pct,
        "regime_gate_enabled": settings.portfolio.regime_gate_enabled,
        "regime_high_volatility_threshold": settings.portfolio.regime_high_volatility_threshold,
        "regime_cooldown_days": settings.portfolio.regime_cooldown_days,
        "breadth_soft_floor_pct": settings.portfolio.breadth_soft_floor_pct,
        "breadth_soft_size_mult": settings.portfolio.breadth_soft_size_mult,
    }
    exit_config = resolve_exit_config(settings)
    signal_filters = {
        "rs_rank_min": settings.backtest.rs_rank_min,
        "rs_rank_max": settings.backtest.rs_rank_max,
        "ranking_mode": settings.backtest.signal_ranking_mode,
        "max_signals_per_day": settings.backtest.max_signals_per_day,
        "quality_profile": settings.backtest.signal_quality_profile,
        "quality_overrides": resolve_signal_quality_overrides(settings),
    }
    filtered_signals = filter_signals(signals_df, diagnostics=diagnostics_df, **signal_filters)
    entry_gate: dict[date, bool] = {}
    entry_size_mult: dict[date, float] = {}
    if settings.backtest.use_regime_entry_controls:
        entry_gate, entry_size_mult = build_entry_controls(
            settings,
            regime_inputs,
            portfolio_overrides=portfolio,
        )
    result = simulate_portfolio_python(
        filtered_signals,
        ohlcv_df,
        params,
        portfolio,
        sector_map,
        exit_config=exit_config if settings.backtest.use_extended_exit_model else None,
        entry_gate=entry_gate,
        entry_size_mult=entry_size_mult,
    )
    filled_trades = int(result["trades"].filter(pl.col("entry_date").is_not_null()).height)
    metrics = _evaluate_trial_result(
        result=result,
        benchmark_curve=benchmark_curve,
        benchmark_roll=benchmark_roll,
        thresholds=thresholds,
        initial_capital=settings.portfolio.initial_capital,
    )
    param_dump: dict[str, Any] = {
        "pending_days": params.pending_days,
        "max_hold_days": params.max_hold_days,
        "trail_activation_r": params.trail_activation_r,
        "trail_pct": params.trail_pct,
        "dead_money_days": params.dead_money_days,
        "risk_per_trade_pct": portfolio["risk_per_trade_pct"],
        "exit_mode": params.exit_mode.value,
        "max_positions": portfolio["max_positions"],
        "max_heat_pct": portfolio["max_heat_pct"],
        "drawdown_half_pct": portfolio["drawdown_half_pct"],
        "drawdown_stop_pct": portfolio["drawdown_stop_pct"],
        "drawdown_close_pct": portfolio["drawdown_close_pct"],
        "regime_high_volatility_threshold": portfolio["regime_high_volatility_threshold"],
        "regime_cooldown_days": portfolio["regime_cooldown_days"],
        "use_extended_exit_model": settings.backtest.use_extended_exit_model,
        "use_regime_entry_controls": settings.backtest.use_regime_entry_controls,
        "use_rs_filter": signal_filters["rs_rank_min"] is not None,
        "signal_ranking_mode": signal_filters["ranking_mode"],
        "max_signals_per_day": signal_filters["max_signals_per_day"] or "off",
        "signal_quality_profile": signal_filters["quality_profile"],
    }
    if portfolio["breadth_soft_floor_pct"] is None:
        param_dump["breadth_soft_floor_pct"] = "off"
    else:
        param_dump["breadth_soft_floor_pct"] = portfolio["breadth_soft_floor_pct"]
        param_dump["breadth_soft_size_mult"] = portfolio["breadth_soft_size_mult"]
    if settings.backtest.use_extended_exit_model:
        param_dump.update(
            {
                "breakeven_activation_r": exit_config["breakeven_activation_r"],
                "partial_profit_r": exit_config["partial_profit_r"],
                "partial_profit_pct": exit_config["partial_profit_pct"],
                "time_stop_days": exit_config["time_stop_days"],
                "max_hold_before_tighten": exit_config["max_hold_before_tighten"],
            }
        )
    if signal_filters["rs_rank_min"] is not None:
        param_dump["rs_rank_min"] = signal_filters["rs_rank_min"]
    return {"params": param_dump, "metrics": metrics}


def run_optuna_optimization() -> None:
    settings = load_settings()
    db_path = PROJECT_ROOT / "data" / "db" / "sepa.duckdb"
    ohlcv_path = PROJECT_ROOT / "data" / "yf_adjusted" / "ohlcv_all.parquet"
    reference_dir = PROJECT_ROOT / "data" / "reference"
    trial_count = _resolve_trial_count()
    thresholds = _resolve_thresholds()

    print("Loading data for robust Optuna...")
    full_ohlcv_df = pl.read_parquet(ohlcv_path)
    start_date, end_date = _resolve_optimization_window(full_ohlcv_df)
    study_name = f"{_resolve_study_name(start_date, end_date, thresholds)}_alpha"
    storage = _resolve_study_storage()
    print(f"Optimization window: {start_date} -> {end_date}")
    print(f"Study storage: {storage}")
    print(f"Study name: {study_name}")
    print(f"Minimum rolling 3Y beat rate: {thresholds.min_rolling_beat_rate:.0%}")

    # tri_csv_path, constituent_csv_path = ensure_reference_data(reference_dir, start_date, end_date)
    # benchmark_curve = load_benchmark_curve(tri_csv_path, start_date, end_date)
    benchmark_curve = (
        full_ohlcv_df.filter(pl.col("symbol") == settings.market.benchmark_symbol)
        .select(["date", "close"])
        .rename({"close": "tri"})
        .filter((pl.col("date") >= pl.lit(start_date)) & (pl.col("date") <= pl.lit(end_date)))
        .sort("date")
    )
    constituent_csv_path = None
    benchmark_stats = compute_performance_stats(benchmark_curve.select("date", "tri"), "tri")
    benchmark_roll = compute_rolling_cagr(benchmark_curve.select("date", "tri"), "tri")
    regime_inputs = compute_regime_inputs(settings, start_date, end_date, benchmark_curve)

    parquet_store = ParquetOhlcvStore(root=PROJECT_ROOT / "data" / "yf_adjusted")
    conn = duckdb.connect(str(db_path), read_only=True)
    signals_df = get_backtest_signals(conn, start_date=start_date, end_date=end_date)
    if signals_df.is_empty():
        conn.close()
        raise RuntimeError(f"No BREAKOUT signals found for optimization window {start_date} -> {end_date}")
    diagnostics_df = get_backtest_diagnostics(
        conn,
        start_date=start_date,
        end_date=end_date,
        symbols=signals_df["symbol"].unique().to_list(),
    )
    conn.close()

    symbols = signals_df["symbol"].unique().to_list()
    ohlcv_end_date = end_date + timedelta(
        days=(settings.backtest.pending_days + settings.backtest.max_hold_days) * 2
    )
    ohlcv_df = get_backtest_ohlcv(
        parquet_store,
        symbols=symbols,
        start_date=start_date,
        end_date=ohlcv_end_date,
    ).sort(["symbol", "date"])
    sector_map = _load_optional_sector_map(constituent_csv_path)

    print(f"Loaded {signals_df.height} signals across {len(symbols)} symbols")
    print(f"Loaded {ohlcv_df.height} OHLCV rows across {ohlcv_df['symbol'].n_unique()} symbols")
    if sector_map:
        print(f"Loaded sector map for {len(sector_map)} symbols")
    else:
        print("No sector map configured; optimisation remains aligned to current CLI path.")

    baseline = _evaluate_current_defaults(
        settings=settings,
        signals_df=signals_df,
        diagnostics_df=diagnostics_df,
        ohlcv_df=ohlcv_df,
        benchmark_curve=benchmark_curve,
        benchmark_roll=benchmark_roll,
        benchmark_cagr=float(benchmark_stats["cagr"]),
        sector_map=sector_map,
        thresholds=thresholds,
        regime_inputs=regime_inputs,
    )
    print("\nBaseline current defaults:")
    print(json.dumps(baseline, indent=2, default=str))

    def objective(trial: optuna.Trial) -> float:
        params, portfolio, exit_config, signal_filters, runtime_options = _build_trial_params(settings, trial)
        if not (
            portfolio["drawdown_half_pct"] < portfolio["drawdown_stop_pct"] < portfolio["drawdown_close_pct"]
        ):
            trial.set_user_attr("constraints_met", False)
            return -1_000_000.0

        filtered_signals = filter_signals(signals_df, diagnostics=diagnostics_df, **signal_filters)
        if filtered_signals.is_empty():
            trial.set_user_attr("constraints_met", False)
            return -1_000_000.0

        entry_gate: dict[date, bool] = {}
        entry_size_mult: dict[date, float] = {}
        if runtime_options["use_regime_entry_controls"]:
            entry_gate, entry_size_mult = build_entry_controls(
                settings,
                regime_inputs,
                portfolio_overrides=portfolio,
            )
        started = time.perf_counter()
        result = simulate_portfolio_python(
            filtered_signals,
            ohlcv_df,
            params,
            portfolio,
            sector_map,
            exit_config=exit_config if runtime_options["use_extended_exit_model"] else None,
            entry_gate=entry_gate,
            entry_size_mult=entry_size_mult,
        )
        filled_trades = int(result["trades"].filter(pl.col("entry_date").is_not_null()).height)
        metrics = _evaluate_trial_result(
            result=result,
            benchmark_curve=benchmark_curve,
            benchmark_roll=benchmark_roll,
            thresholds=thresholds,
            initial_capital=settings.portfolio.initial_capital,
        )
        elapsed = time.perf_counter() - started

        trial.set_user_attr("score", metrics["score"])
        trial.set_user_attr("final_equity", metrics["final_equity"])
        trial.set_user_attr("cagr", metrics["cagr"])
        trial.set_user_attr("calmar", metrics["calmar"])
        trial.set_user_attr("max_drawdown_pct", metrics["max_drawdown_pct"])
        trial.set_user_attr("ex_2023_cagr", metrics["ex_2023_cagr"])
        trial.set_user_attr("rolling_beat_rate", metrics["rolling_beat_rate"])
        trial.set_user_attr("constraints_met", metrics["constraints_met"])
        trial.set_user_attr("ex_2023_ok", metrics["ex_2023_ok"])
        trial.set_user_attr("beat_rate_ok", metrics["beat_rate_ok"])
        trial.set_user_attr("filled_trades", metrics["filled_trades"])
        trial.set_user_attr("use_extended_exit_model", runtime_options["use_extended_exit_model"])
        trial.set_user_attr("use_regime_entry_controls", runtime_options["use_regime_entry_controls"])
        trial.set_user_attr("use_rs_filter", runtime_options["use_rs_filter"])
        trial.set_user_attr("signal_ranking_mode", signal_filters["ranking_mode"])
        trial.set_user_attr("max_signals_per_day", signal_filters["max_signals_per_day"])
        trial.set_user_attr("signal_quality_profile", signal_filters["quality_profile"])
        trial.set_user_attr("elapsed_sec", elapsed)

        return float(metrics["score"])

    sampler = optuna.samplers.TPESampler(seed=42)
    study = optuna.create_study(
        direction="maximize",
        study_name=study_name,
        storage=storage,
        load_if_exists=True,
        sampler=sampler,
    )
    # study.enqueue_trial(baseline["params"], skip_if_exists=True)

    print(f"\n🚀 Starting robust AI optimization ({trial_count} trials)...")
    study.optimize(objective, n_trials=trial_count)

    completed_trials = [trial for trial in study.trials if trial.value is not None]
    feasible_trials = [trial for trial in completed_trials if trial.user_attrs.get("constraints_met")]
    best_trial = max(feasible_trials or completed_trials, key=lambda trial: float(trial.value))

    best_params = best_trial.params
    use_extended_exit_model = bool(best_params.get("use_extended_exit_model", False))
    use_regime_entry_controls = bool(best_params.get("use_regime_entry_controls", False))
    best_backtest_params = BacktestParams(
        pending_days=int(best_params["pending_days"]),
        max_hold_days=int(best_params["max_hold_days"]),
        target_r=settings.risk.target_r,
        exit_mode=ExitMode(str(best_params["exit_mode"])),
        trail_activation_r=float(best_params["trail_activation_r"]),
        trail_pct=float(best_params["trail_pct"]),
        dead_money_days=int(best_params["dead_money_days"]),
    )
    best_portfolio = {
        "initial_capital": settings.portfolio.initial_capital,
        "risk_per_trade_pct": float(best_params["risk_per_trade_pct"]),
        "max_positions": int(best_params["max_positions"]),
        "max_sector_positions": settings.portfolio.max_sector_positions,
        "max_sector_pct": settings.portfolio.max_sector_pct,
        "max_heat_pct": float(best_params["max_heat_pct"]),
        "drawdown_half_pct": float(best_params["drawdown_half_pct"]),
        "drawdown_stop_pct": float(best_params["drawdown_stop_pct"]),
        "drawdown_close_pct": float(best_params["drawdown_close_pct"]),
        "regime_gate_enabled": settings.portfolio.regime_gate_enabled,
        "regime_high_volatility_threshold": float(best_params["regime_high_volatility_threshold"]),
        "regime_cooldown_days": int(best_params["regime_cooldown_days"]),
        "breadth_soft_floor_pct": _decode_soft_floor(best_params.get("breadth_soft_floor_pct")),
        "breadth_soft_size_mult": (
            float(best_params["breadth_soft_size_mult"])
            if _decode_soft_floor(best_params.get("breadth_soft_floor_pct")) is not None
            else 1.0
        ),
    }
    best_exit_config = None
    if use_extended_exit_model:
        best_exit_config = {
            "breakeven_activation_r": float(best_params["breakeven_activation_r"]),
            "partial_profit_r": float(best_params["partial_profit_r"]),
            "partial_profit_pct": float(best_params["partial_profit_pct"]),
            "trail_activation_r": best_backtest_params.trail_activation_r,
            "trail_pct": best_backtest_params.trail_pct,
            "trail_pct_bull": best_backtest_params.trail_pct,
            "trail_pct_bear": min(best_backtest_params.trail_pct, 0.15),
            "dead_money_days": best_backtest_params.dead_money_days,
            "time_stop_days": int(best_params["time_stop_days"]),
            "max_hold_before_tighten": int(best_params["max_hold_before_tighten"]),
        }
    best_signal_filters = {
        "rs_rank_min": int(best_params["rs_rank_min"]) if best_params.get("use_rs_filter") else None,
        "rs_rank_max": settings.backtest.rs_rank_max,
        "ranking_mode": str(best_params.get("signal_ranking_mode", settings.backtest.signal_ranking_mode)),
        "max_signals_per_day": _decode_max_signals_per_day(best_params.get("max_signals_per_day")),
        "quality_profile": str(best_params.get("signal_quality_profile", settings.backtest.signal_quality_profile)),
        "quality_overrides": None,
    }
    best_signals = filter_signals(signals_df, diagnostics=diagnostics_df, **best_signal_filters)
    best_entry_gate: dict[date, bool] = {}
    best_entry_size_mult: dict[date, float] = {}
    if use_regime_entry_controls:
        best_entry_gate, best_entry_size_mult = build_entry_controls(
            settings,
            regime_inputs,
            portfolio_overrides=best_portfolio,
        )
    best_result = simulate_portfolio_python(
        best_signals,
        ohlcv_df,
        best_backtest_params,
        best_portfolio,
        sector_map,
        exit_config=best_exit_config,
        entry_gate=best_entry_gate,
        entry_size_mult=best_entry_size_mult,
    )
    filled_trades = int(best_result["trades"].filter(pl.col("entry_date").is_not_null()).height)
    best_metrics = _evaluate_trial_result(
        result=best_result,
        benchmark_curve=benchmark_curve,
        benchmark_roll=benchmark_roll,
        thresholds=thresholds,
        initial_capital=settings.portfolio.initial_capital,
    )

    result_payload = {
        "study_name": study_name,
        "storage": storage,
        "window": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "baseline": baseline,
        "best_params": best_params,
        "runtime_options": {
            "use_extended_exit_model": use_extended_exit_model,
            "use_regime_entry_controls": use_regime_entry_controls,
            "use_rs_filter": bool(best_params.get("use_rs_filter", False)),
        },
        "best_exit_config": best_exit_config,
        "best_signal_filters": best_signal_filters,
        "best_metrics": best_metrics,
        "filled_trades": filled_trades,
        "completed_trials": len(completed_trials),
        "feasible_trials": len(feasible_trials),
    }

    output_dir = PROJECT_ROOT / "data" / "optuna"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{study_name}_best.json"
    output_path.write_text(json.dumps(result_payload, indent=2, default=str), encoding="utf-8")

    print("\n" + "=" * 60)
    print("🏆 BEST ROBUSTNESS-OPTIMIZED PARAMETERS")
    print("=" * 60)
    print(f"Robust score: {best_metrics['score']:.4f}")
    print(f"Final equity: ₹{best_metrics['final_equity']:,.2f}")
    print(f"CAGR: {best_metrics['cagr'] * 100:.2f}%")
    print(f"Max drawdown: {best_metrics['max_drawdown_pct'] * 100:.2f}%")
    print(f"Calmar: {best_metrics['calmar']:.3f}")
    print(f"Ex-2023 CAGR: {best_metrics['ex_2023_cagr'] * 100:.2f}%")
    print(f"Benchmark CAGR: {best_metrics['benchmark_cagr'] * 100:.2f}%")
    print(f"Rolling 3Y beat rate: {best_metrics['rolling_beat_rate'] * 100:.2f}%")
    print(f"Constraints met: {best_metrics['constraints_met']}")
    print(f"Filled trades: {filled_trades}")
    print(f"Feasible trials: {len(feasible_trials)} / {len(completed_trials)}")
    print(f"Parameters: {best_params}")
    print(f"Saved report: {output_path}")
    print("=" * 60)


if __name__ == "__main__":
    run_optuna_optimization()

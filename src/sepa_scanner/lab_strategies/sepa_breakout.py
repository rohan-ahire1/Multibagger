"""SEPA breakout — the committed strategy, expressed as a lab strategy.

Signals are sourced from the persisted ``signals`` table and put through the
canonical quality/ranking filter, then handed to the shared signal engine. That
is exactly the pipeline ``optimize_joint_wf.py --parity`` runs, which is what
lets ``tests/lab/test_sepa_parity.py`` assert the committed baseline of
12.48% CAGR / 24.28% MaxDD / 281 trades through the lab.

Known deviation from the lab design: the plan calls for signals to be computed
on demand from the OHLCV panel so a parameter change is immediately
backtestable without re-running the nightly scan. This strategy still reads
precomputed signals, because reproducing the scan in-process is not guaranteed
to yield byte-identical signals and parity is the harder constraint. Moving to
on-demand generation is a follow-up, gated on the same parity test.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import polars as pl

from sepa_scanner.lab.contracts import (
    DataRequirement,
    ParamSpec,
    StrategyContext,
    StrategyKind,
)
from sepa_scanner.lab.engines.portfolio_sim import (
    build_entry_controls,
    compute_regime_inputs,
    filter_signals,
    resolve_signal_quality_overrides,
)
from sepa_scanner.lab.registry import register_strategy

_EXIT_MODES = (
    "baseline_ema_hybrid",
    "baseline_ema21",
    "baseline_ema10",
    "baseline_hard",
    "minervini_phased",
    "v3_trail_pct",
)

_STOP_TRIGGER_MODES = ("intraday_hard", "intraday_active", "close_only")
_EXIT_EXECUTIONS = ("next_open", "same_close")


class SepaBreakout:
    id = "sepa_breakout"
    name = "SEPA breakout"
    kind = StrategyKind.SIGNAL
    data_requirement = DataRequirement.SIGNALS
    description = (
        "Minervini/SEPA trend-template breakouts from VCP bases, sized by risk "
        "with sector and heat caps, exited on a trailing EMA with a stagnation "
        "recycle. This is the committed strategy."
    )

    @property
    def param_specs(self) -> list[ParamSpec]:
        return [
            # Entry selection
            ParamSpec("rs_rank_min", "RS rank floor", "float", 70.0, min=0.0, max=100.0,
                      step=1.0, group="Entry",
                      help="Reject breakouts weaker than this relative-strength percentile."),
            ParamSpec("rs_rank_max", "RS rank ceiling", "float", 100.0, min=0.0, max=100.0,
                      step=1.0, group="Entry", advanced=True),
            ParamSpec("max_signals_per_day", "Max signals per day", "int", 20, min=1, max=50,
                      group="Entry",
                      help="Truncate each day's ranked candidates to this many."),
            ParamSpec("contraction_max_count", "Max contractions", "int", 2, min=1, max=6,
                      group="Entry",
                      help="Reject breakout-day bases with more contractions than this."),
            # Execution
            ParamSpec("stop_trigger_mode", "Stop trigger", "enum", "intraday_hard",
                      choices=_STOP_TRIGGER_MODES, group="Execution",
                      help="intraday_hard uses the session low for hard stops (honest)."),
            ParamSpec("signal_exit_execution", "Exit execution", "enum", "next_open",
                      choices=_EXIT_EXECUTIONS, group="Execution",
                      help="next_open fills close-confirmed exits the following session."),
            ParamSpec("slippage_bps", "Slippage (bps)", "float", 10.0, min=0.0, max=200.0,
                      step=1.0, group="Execution"),
            # Exits
            ParamSpec("exit_mode", "Exit mode", "enum", "baseline_ema_hybrid",
                      choices=_EXIT_MODES, group="Exit"),
            ParamSpec("baseline_tighten_r", "Tighten at (R)", "float", 5.0, min=1.0, max=15.0,
                      step=0.5, group="Exit",
                      help="Peak close at which the trail tightens from EMA21 to EMA10."),
            ParamSpec("max_hold_days", "Max hold (days)", "int", 150, min=10, max=500,
                      group="Exit", advanced=True),
            ParamSpec("pending_days", "Pending window (bars)", "int", 8, min=1, max=30,
                      group="Exit", advanced=True,
                      help="Symbol trading bars a trigger stays live. Entry is never same-day."),
            ParamSpec("stagnation_exit_bars", "Stagnation bars", "int", 15, min=0, max=60,
                      group="Exit",
                      help="Recycle a trade after this many flat sessions. 0 disables."),
            ParamSpec("stagnation_max_mfe_r", "Stagnation peak (R)", "float", 0.40,
                      min=0.0, max=3.0, step=0.05, group="Exit", advanced=True),
            # Sizing
            ParamSpec("risk_per_trade_pct", "Risk per trade", "float", 0.020,
                      min=0.001, max=0.10, step=0.001, group="Sizing"),
            ParamSpec("max_positions", "Max positions", "int", 20, min=1, max=60,
                      group="Sizing"),
            ParamSpec("max_sector_positions", "Max per sector", "int", 4, min=1, max=20,
                      group="Sizing"),
            ParamSpec("cash_yield_apy", "Idle cash yield", "float", 0.0,
                      min=0.0, max=0.15, step=0.01, group="Sizing", advanced=True,
                      help="Reference only. Never counted as SEPA alpha; keep at 0 "
                           "for the committed headline."),
        ]

    def defaults(self, settings: Any) -> dict[str, Any]:
        """Every default reads from config.yaml — the single source of truth."""
        backtest = settings.backtest
        exits = settings.exit_strategy
        portfolio = settings.portfolio
        return {
            "rs_rank_min": backtest.rs_rank_min,
            "rs_rank_max": backtest.rs_rank_max,
            "max_signals_per_day": backtest.max_signals_per_day,
            "contraction_max_count": settings.setup_vcp.contraction_max_count,
            "stop_trigger_mode": backtest.stop_trigger_mode,
            "signal_exit_execution": backtest.signal_exit_execution,
            "slippage_bps": backtest.slippage_bps,
            "exit_mode": backtest.exit_mode,
            "baseline_tighten_r": backtest.baseline_tighten_r,
            "max_hold_days": backtest.max_hold_days,
            "pending_days": backtest.pending_days,
            "stagnation_exit_bars": exits.stagnation_exit_bars or 0,
            "stagnation_max_mfe_r": exits.stagnation_max_mfe_r,
            "risk_per_trade_pct": portfolio.risk_per_trade_pct,
            "max_positions": portfolio.max_positions,
            "max_sector_positions": portfolio.max_sector_positions,
            "cash_yield_apy": portfolio.cash_yield_apy,
        }

    def generate(self, ctx: StrategyContext, params: dict[str, Any]) -> pl.DataFrame:
        settings = ctx.settings
        raw = ctx.extras["signals"]
        diagnostics = ctx.extras.get("diagnostics")

        overrides = resolve_signal_quality_overrides(settings)
        if params.get("contraction_max_count") is not None:
            overrides["contraction_count_max"] = params["contraction_max_count"]

        return filter_signals(
            raw.filter(
                (pl.col("signal_date") >= ctx.start_date)
                & (pl.col("signal_date") <= ctx.end_date)
            ),
            settings=settings,
            diagnostics=diagnostics,
            sector_map=ctx.sector_map,
            rs_rank_min=params["rs_rank_min"],
            rs_rank_max=params["rs_rank_max"],
            ranking_mode=settings.backtest.signal_ranking_mode,
            max_signals_per_day=params["max_signals_per_day"],
            quality_profile=settings.backtest.signal_quality_profile,
            quality_overrides=overrides,
        )

    def engine_config(self, ctx: StrategyContext, params: dict[str, Any]) -> dict[str, Any]:
        settings = ctx.settings
        exits = settings.exit_strategy

        base_params = ctx.extras["params"]
        sim_params = replace(
            base_params,
            pending_days=params["pending_days"],
            max_hold_days=max(params["max_hold_days"], 90),
            exit_mode=type(base_params.exit_mode)(params["exit_mode"]),
            baseline_tighten_r=params["baseline_tighten_r"],
            stop_trigger_mode=params["stop_trigger_mode"],
            signal_exit_execution=params["signal_exit_execution"],
            slippage_bps=params["slippage_bps"],
            stagnation_exit_bars=params["stagnation_exit_bars"] or None,
            stagnation_max_mfe_r=params["stagnation_max_mfe_r"],
        )

        portfolio = dict(ctx.extras["portfolio"])
        portfolio.update(
            risk_per_trade_pct=params["risk_per_trade_pct"],
            max_positions=params["max_positions"],
            max_sector_positions=params["max_sector_positions"],
            cash_yield_apy=params["cash_yield_apy"],
        )

        entry_gate: dict = {}
        entry_size_mult: dict = {}
        if settings.backtest.use_regime_entry_controls:
            regime_inputs = compute_regime_inputs(
                settings, ctx.start_date, ctx.end_date, ctx.benchmark
            )
            entry_gate, entry_size_mult = build_entry_controls(settings, regime_inputs)

        exit_kwargs = {
            "breakeven_activation_r": exits.breakeven_activation_r,
            "partial_profit_r": exits.partial_profit_r,
            "time_stop_days": exits.time_stop_days,
            "trail_pct_bull": exits.trail_pct_bull,
            "trail_pct_bear": exits.trail_pct_bear,
            "trail_widen_r": getattr(exits, "trail_widen_r", 3.0),
        }

        return {
            "params": sim_params,
            "portfolio": portfolio,
            "exit_kwargs": exit_kwargs,
            # The phased ExitConfig is inert under baseline_* exit modes; passing
            # it anyway is how a past report silently routed EMA21 through the
            # phased engine and had to be thrown out.
            "exit_config": (
                exit_kwargs if settings.backtest.use_extended_exit_model else None
            ),
            "entry_gate": entry_gate,
            "entry_size_mult": entry_size_mult,
        }


register_strategy(SepaBreakout())

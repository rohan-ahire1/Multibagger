"""Mean reversion — buy short-term oversold names inside a long-term uptrend.

Signal detection comes from ``strategies/mean_reversion/signal_engine.py``
(`scan_mean_reversion` for the Connors RSI-2 rule, `scan_mr_v2` for the
research-driven variant with extra filters), selected by the ``variant``
parameter. Execution is the shared signal engine.

**Long only.** The module also has `scan_short_mean_reversion`, and the original
`simulate_mr_portfolio` could run a long/short book. The shared engine has no
short side, and adding one means changing the canonical simulator that
`optimize_joint_wf.py --parity` depends on. That is not worth doing on the back
of a strategy whose long leg has not yet been shown to survive honest execution
here, so the short leg stays unmigrated and its own simulator remains in place
for it. See the migration note in that directory's README.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import polars as pl

from sepa_scanner.lab.contracts import (
    DataRequirement,
    ParamSpec,
    StrategyContext,
    StrategyKind,
)
from sepa_scanner.lab.registry import register_strategy

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

VARIANTS = ("connors_rsi2", "v2_research")
ENTRY_INDICATORS = ("crsi", "rsi2", "ibs")


def _mr_signals():
    from strategies.mean_reversion import signal_engine

    return signal_engine


class MeanReversion:
    id = "mean_reversion"
    name = "Mean reversion (long)"
    kind = StrategyKind.REVERSION
    data_requirement = DataRequirement.PANEL
    description = (
        "Buys short-term oversold names that are still above their long-term "
        "moving average, exiting on reversion rather than trend. Two variants: "
        "the Connors RSI-2 rule, and a research-driven version adding "
        "days-down, IBS, ADX, and volatility-rank filters. Long only."
    )

    @property
    def param_specs(self) -> list[ParamSpec]:
        return [
            ParamSpec("variant", "Variant", "enum", "connors_rsi2", choices=VARIANTS,
                      group="Entry",
                      help="connors_rsi2 is the classic rule; v2_research adds filters."),
            ParamSpec("entry_threshold", "Entry threshold", "float", 10.0, min=1.0,
                      max=50.0, step=1.0, group="Entry",
                      help="Oversold level on the entry indicator."),
            ParamSpec("entry_indicator", "Entry indicator", "enum", "crsi",
                      choices=ENTRY_INDICATORS, group="Entry",
                      help="v2_research only."),
            ParamSpec("sma_trend_period", "Trend filter (days)", "int", 200, min=20,
                      max=400, group="Entry",
                      help="Only buy above this moving average."),
            ParamSpec("stop_atr_mult", "Stop (ATR multiple)", "float", 2.0, min=0.5,
                      max=8.0, step=0.25, group="Exit",
                      help="Initial stop distance, and therefore position size."),
            ParamSpec("max_signals_per_day", "Max signals per day", "int", 10, min=1,
                      max=50, group="Entry"),
            ParamSpec("cooldown_days", "Cooldown (days)", "int", 0, min=0, max=30,
                      group="Entry", advanced=True,
                      help="Bars to wait before re-signalling the same symbol."),
            ParamSpec("min_adv_inr", "Min 50-day turnover", "float", 1e8, min=0.0,
                      max=1e11, step=1e7, group="Liquidity", advanced=True),
            ParamSpec("min_price", "Min price", "float", 50.0, min=0.0, max=5000.0,
                      step=10.0, group="Liquidity", advanced=True),
            ParamSpec("min_days_down", "Min days down", "int", 0, min=0, max=10,
                      group="Entry", advanced=True,
                      help="v2_research only. 0 disables."),
            ParamSpec("max_adx", "Max ADX", "float", 0.0, min=0.0, max=100.0, step=1.0,
                      group="Entry", advanced=True,
                      help="v2_research only. 0 disables."),
            ParamSpec("pending_days", "Pending window (bars)", "int", 2, min=1, max=15,
                      group="Exit", advanced=True),
            ParamSpec("max_hold_days", "Max hold (days)", "int", 10, min=1, max=200,
                      group="Exit",
                      help="Mean reversion is a short-horizon trade; hold time is the exit."),
            ParamSpec("exit_mode", "Exit rule", "enum", "sma5",
                      choices=("sma5", "rsi_70", "rsi_50", "cum_rsi_130", "cum_rsi_150"),
                      group="Exit",
                      help="When the reversion is judged complete. sma5 exits on a "
                           "close back above the 5-day mean."),
            ParamSpec("exit_execution", "Exit execution", "enum", "next_open",
                      choices=("next_open", "same_close"), group="Exit",
                      help="next_open is honest: a close-confirmed signal cannot "
                           "fill at that same close."),
            ParamSpec("slippage_bps", "Slippage (bps)", "float", 10.0, min=0.0,
                      max=200.0, step=1.0, group="Exit"),
            ParamSpec("round_trip_cost_pct", "Round-trip cost", "float", 0.005,
                      min=0.0, max=0.05, step=0.001, group="Exit",
                      help="Charged per round trip on top of slippage. The repo's own "
                           "realism validation treats 0.5% as the truthful setting; "
                           "0 reproduces the flattering baseline."),
            ParamSpec("max_sector_positions", "Max per sector", "int", 3, min=1, max=20,
                      group="Sizing"),
            ParamSpec("risk_per_trade_pct", "Risk per trade", "float", 0.02, min=0.001,
                      max=0.10, step=0.001, group="Sizing"),
            ParamSpec("max_positions", "Max positions", "int", 10, min=1, max=60,
                      group="Sizing"),
        ]

    def defaults(self, settings: Any) -> dict[str, Any]:
        return {
            "variant": "connors_rsi2",
            "entry_threshold": 10.0,
            "entry_indicator": "crsi",
            "sma_trend_period": 200,
            "stop_atr_mult": 2.0,
            "max_signals_per_day": 10,
            "cooldown_days": 0,
            "min_adv_inr": 1e8,
            "min_price": 50.0,
            "min_days_down": 0,
            "max_adx": 0.0,
            "pending_days": 2,
            "max_hold_days": 10,
            "exit_mode": "sma5",
            "exit_execution": "next_open",
            "slippage_bps": settings.backtest.slippage_bps,
            "round_trip_cost_pct": 0.005,
            "max_sector_positions": 3,
            "risk_per_trade_pct": settings.portfolio.risk_per_trade_pct,
            "max_positions": 10,
        }

    def generate(self, ctx: StrategyContext, params: dict[str, Any]) -> pl.DataFrame:
        scans = _mr_signals()

        if params["variant"] == "connors_rsi2":
            signals = scans.scan_mean_reversion(
                ctx.panel,
                rsi_threshold=params["entry_threshold"],
                sma_trend_period=params["sma_trend_period"],
                min_adv_inr=params["min_adv_inr"],
                min_price=params["min_price"],
                stop_atr_mult=params["stop_atr_mult"],
            )
        else:
            signals = scans.scan_mr_v2(
                ctx.panel,
                entry_indicator=params["entry_indicator"],
                entry_threshold=params["entry_threshold"],
                sma_trend_period=params["sma_trend_period"],
                # 0 reads as "off" in the UI; the scan wants None.
                min_days_down=params["min_days_down"] or None,
                max_adx=params["max_adx"] or None,
                min_adv_inr=params["min_adv_inr"],
                min_price=params["min_price"],
                stop_atr_mult=params["stop_atr_mult"],
                max_signals_per_day=params["max_signals_per_day"],
                cooldown_days=params["cooldown_days"],
            )

        if signals.is_empty():
            return signals

        signals = signals.filter(
            (pl.col("signal_date") >= ctx.start_date)
            & (pl.col("signal_date") <= ctx.end_date)
        )
        if signals.is_empty():
            return signals

        # Deeper oversold ranks first, so the per-day cap keeps the strongest
        # signals rather than an arbitrary slice.
        score_col = "rsi2" if "rsi2" in signals.columns else None
        signals = signals.with_columns(
            pl.lit("long").alias("direction"),
            pl.lit(None, dtype=pl.Float64).alias("pivot"),
            (-pl.col(score_col)).alias("rank_score")
            if score_col
            else pl.lit(0.0).alias("rank_score"),
        )

        return (
            signals.sort(["signal_date", "rank_score"], descending=[False, True])
            .group_by("signal_date", maintain_order=True)
            .head(params["max_signals_per_day"])
            .unique(subset=["symbol", "signal_date"], keep="first")
            .sort(["signal_date", "symbol"])
        )

    def engine_config(self, ctx: StrategyContext, params: dict[str, Any]) -> dict[str, Any]:
        """Config for the reversion executor.

        Not the shared portfolio simulator: its exits are trend-based and cannot
        express "sell when the reversion completes", which is the rule that
        defines this strategy. See lab/engines/reversion_engine.py.
        """
        return {
            "start_date": ctx.start_date,
            "end_date": ctx.end_date,
            "max_positions": params["max_positions"],
            "max_sector_positions": params["max_sector_positions"],
            "initial_capital": ctx.extras["portfolio"]["initial_capital"],
            "risk_per_trade_pct": params["risk_per_trade_pct"],
            "exit_mode": params["exit_mode"],
            "exit_overrides": {
                "stop_atr_mult": params["stop_atr_mult"],
                "max_hold_days": params["max_hold_days"],
                "time_stop_days": params["max_hold_days"],
            },
            "sector_map": ctx.sector_map,
            "cooldown_days": params["cooldown_days"],
            "exit_execution": params["exit_execution"],
            "slippage_bps": params["slippage_bps"],
            "round_trip_cost_pct": params["round_trip_cost_pct"],
        }


register_strategy(MeanReversion())

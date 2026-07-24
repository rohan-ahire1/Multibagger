"""Episodic pivots — buy the day a dull stock re-rates on a volume shock.

Signal detection reuses ``strategies/episodic_pivots/ep_engine.py`` (its
``build_features`` and ``make_signals``) rather than copying the logic, so the
research script and the lab cannot drift apart. Execution, sizing, exits,
friction, and metrics come from the shared engine, which is what makes this
comparable to SEPA on the same leaderboard.

Its own simulator in ep_engine remains for reference. Its numbers are not
comparable to lab numbers: it sized positions as a fixed percentage of equity
with a percentage stop, while the shared engine sizes on risk-per-trade against
the stop distance.
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


def _ep_engine():
    """Imported lazily: strategies/ lives outside the installed package."""
    from strategies.episodic_pivots import ep_engine

    return ep_engine


class EpisodicPivots:
    id = "episodic_pivots"
    name = "Episodic pivots"
    kind = StrategyKind.SIGNAL
    data_requirement = DataRequirement.PANEL
    description = (
        "Buys the session a quiet stock re-rates: a large gap or day return on a "
        "volume multiple of its 20-day average, closing near the high, above the "
        "200-day mean. Entries are ranked per day by event strength."
    )

    @property
    def param_specs(self) -> list[ParamSpec]:
        return [
            ParamSpec("min_gap_pct", "Min gap %", "float", 5.0, min=0.0, max=30.0,
                      step=0.5, group="Event",
                      help="Either this gap or the day-return floor must be met."),
            ParamSpec("min_day_ret_pct", "Min day return %", "float", 8.0, min=0.0,
                      max=40.0, step=0.5, group="Event"),
            ParamSpec("min_vol_ratio", "Min volume multiple", "float", 3.0, min=1.0,
                      max=20.0, step=0.5, group="Event",
                      help="Session volume over its 20-day average."),
            ParamSpec("min_close_pos", "Min close position", "float", 0.6, min=0.0,
                      max=1.0, step=0.05, group="Event",
                      help="Where the close sits in the day's range; 1.0 is the high."),
            ParamSpec("require_above_sma200", "Require above 200-day", "bool", True,
                      group="Event"),
            ParamSpec("min_price", "Min price", "float", 20.0, min=0.0, max=1000.0,
                      step=5.0, group="Liquidity", advanced=True),
            ParamSpec("min_adv20", "Min 20-day turnover", "float", 50_000_000.0,
                      min=0.0, max=1e10, step=1e7, group="Liquidity", advanced=True),
            ParamSpec("max_signals_per_day", "Max signals per day", "int", 5, min=1,
                      max=50, group="Entry",
                      help="Keeps the strongest events when many fire at once."),
            ParamSpec("initial_stop_pct", "Initial stop %", "float", 0.08, min=0.01,
                      max=0.50, step=0.01, group="Exit",
                      help="Stop distance below entry; also sets position size."),
            ParamSpec("max_hold_days", "Max hold (days)", "int", 60, min=5, max=400,
                      group="Exit"),
            ParamSpec("pending_days", "Pending window (bars)", "int", 1, min=1, max=15,
                      group="Exit", advanced=True),
            ParamSpec("exit_mode", "Exit mode", "enum", "baseline_ema21",
                      choices=("baseline_ema21", "baseline_ema10",
                               "baseline_ema_hybrid", "baseline_hard"),
                      group="Exit"),
            ParamSpec("risk_per_trade_pct", "Risk per trade", "float", 0.02,
                      min=0.001, max=0.10, step=0.001, group="Sizing"),
            ParamSpec("max_positions", "Max positions", "int", 10, min=1, max=60,
                      group="Sizing"),
        ]

    def defaults(self, settings: Any) -> dict[str, Any]:
        return {
            "min_gap_pct": 5.0,
            "min_day_ret_pct": 8.0,
            "min_vol_ratio": 3.0,
            "min_close_pos": 0.6,
            "require_above_sma200": True,
            "min_price": 20.0,
            "min_adv20": 50_000_000.0,
            "max_signals_per_day": 5,
            "initial_stop_pct": 0.08,
            "max_hold_days": 60,
            "pending_days": 1,
            "exit_mode": "baseline_ema21",
            "risk_per_trade_pct": settings.portfolio.risk_per_trade_pct,
            "max_positions": 10,
        }

    def generate(self, ctx: StrategyContext, params: dict[str, Any]) -> pl.DataFrame:
        ep = _ep_engine()

        cfg = ep.EPConfig(
            name="lab",
            min_gap_pct=params["min_gap_pct"],
            min_day_ret_pct=params["min_day_ret_pct"],
            min_vol_ratio=params["min_vol_ratio"],
            min_close_pos=params["min_close_pos"],
            min_adv20=params["min_adv20"],
            min_price=params["min_price"],
            max_positions=params["max_positions"],
            position_pct=0.0,      # sizing belongs to the shared engine
            max_adv_pct=0.0,       # ditto for participation caps
            initial_stop_pct=params["initial_stop_pct"],
            trail_pct=0.0,
            max_hold_days=params["max_hold_days"],
            slippage_bps=0.0,
            require_above_sma200=params["require_above_sma200"],
        )

        features = ep.build_features(ctx.panel)
        events = ep.make_signals(features, cfg, ctx.start_date, ctx.end_date)
        if events.is_empty():
            return events

        # The event bar's close is the reference; the engine enters no earlier
        # than the next session, so this is never a same-bar fill.
        closes = ctx.panel.select(["date", "symbol", "close"])
        signals = (
            events.join(closes, on=["date", "symbol"], how="inner")
            .rename({"date": "signal_date", "close": "entry_ref"})
            .with_columns(
                (pl.col("entry_ref") * (1.0 - params["initial_stop_pct"])).alias("stop"),
                pl.lit(None, dtype=pl.Float64).alias("pivot"),
                # Rank ascending in the source, so invert into a score where
                # higher is better, matching the signal-frame contract.
                (-pl.col("event_rank")).alias("rank_score"),
                pl.lit("long").alias("direction"),
            )
        )

        return (
            signals.sort(["signal_date", "event_rank"])
            .group_by("signal_date", maintain_order=True)
            .head(params["max_signals_per_day"])
            .select(
                "symbol", "signal_date", "direction", "entry_ref", "stop", "pivot",
                "rank_score", "gap_pct", "day_ret_pct", "vol_ratio",
            )
        )

    def engine_config(self, ctx: StrategyContext, params: dict[str, Any]) -> dict[str, Any]:
        base = ctx.extras["params"]
        sim_params = replace(
            base,
            pending_days=params["pending_days"],
            max_hold_days=max(params["max_hold_days"], 90),
            exit_mode=type(base.exit_mode)(params["exit_mode"]),
        )

        portfolio = dict(ctx.extras["portfolio"])
        portfolio.update(
            risk_per_trade_pct=params["risk_per_trade_pct"],
            max_positions=params["max_positions"],
        )

        exits = ctx.settings.exit_strategy
        return {
            "params": sim_params,
            "portfolio": portfolio,
            "exit_config": None,
            "exit_kwargs": {
                "breakeven_activation_r": exits.breakeven_activation_r,
                "partial_profit_r": exits.partial_profit_r,
                "time_stop_days": exits.time_stop_days,
                "trail_pct_bull": exits.trail_pct_bull,
                "trail_pct_bear": exits.trail_pct_bear,
                "trail_widen_r": getattr(exits, "trail_widen_r", 3.0),
            },
            "entry_gate": {},
            "entry_size_mult": {},
        }


register_strategy(EpisodicPivots())

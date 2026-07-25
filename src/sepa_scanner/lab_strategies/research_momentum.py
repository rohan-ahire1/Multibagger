"""Research momentum — the configuration the autonomous search settled on.

`strategies/autonomous_research/` is a search process, not a strategy: it sweeps
families and configurations and reports what survived. Its verdict was
`mom_126_hold84_r150` — 126-day momentum skipping the most recent month, held
while price stays above the 100-day mean, entered only in a bull tape with
breadth above 40%.

That configuration is registered here so the finding stays runnable and
comparable. Feature construction and entry selection are imported from the
research engine; execution is the shared signal engine.
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

ENTRY_RULES = (
    "momentum_trend",
    "momentum_trend_63",
    "momentum_trend_252",
    "momentum_leaders",
    "breakout_126",
)


def _research_engine():
    from strategies.autonomous_research import research_engine

    return research_engine


class ResearchMomentum:
    id = "research_momentum"
    name = "Research momentum"
    kind = StrategyKind.SIGNAL
    data_requirement = DataRequirement.PANEL
    description = (
        "The configuration the autonomous research sweep selected: 126-day "
        "momentum skipping the last month, above the 200-day mean with a rising "
        "50/200 stack, not extended from the 52-week high, entered only when the "
        "market is bullish and breadth holds up."
    )

    @property
    def param_specs(self) -> list[ParamSpec]:
        return [
            ParamSpec("entry_rule", "Entry rule", "enum", "momentum_trend",
                      choices=ENTRY_RULES, group="Entry",
                      help="momentum_trend is the selected rule; the others are the "
                           "families the sweep compared it against."),
            ParamSpec("rank_max", "Rank cutoff", "int", 150, min=10, max=500,
                      group="Entry",
                      help="Only consider the top N by momentum rank each day."),
            ParamSpec("max_positions", "Max positions", "int", 12, min=1, max=60,
                      group="Sizing"),
            ParamSpec("require_market_bull", "Require bull market", "bool", True,
                      group="Regime",
                      help="Selected configuration entered only in a bull tape."),
            ParamSpec("min_breadth200", "Min breadth", "float", 0.40, min=0.0, max=1.0,
                      step=0.05, group="Regime",
                      help="Fraction of the universe above its 200-day mean."),
            ParamSpec("hold_days", "Hold (days)", "int", 84, min=5, max=400,
                      group="Exit"),
            ParamSpec("stop_atr", "Stop (ATR multiple)", "float", 3.0, min=0.5, max=10.0,
                      step=0.25, group="Exit"),
            ParamSpec("min_adv20", "Min 20-day turnover", "float", 20_000_000.0,
                      min=0.0, max=1e10, step=1e7, group="Liquidity", advanced=True),
            ParamSpec("min_price", "Min price", "float", 20.0, min=0.0, max=5000.0,
                      step=5.0, group="Liquidity", advanced=True),
            ParamSpec("slippage_bps", "Slippage (bps)", "float", 15.0, min=0.0, max=200.0,
                      step=1.0, group="Execution",
                      help="The sweep used 15 bps, higher than SEPA's 10."),
            ParamSpec("max_signals_per_day", "Max signals per day", "int", 5, min=1,
                      max=50, group="Entry"),
            ParamSpec("exit_mode", "Exit mode", "enum", "baseline_ema21",
                      choices=("baseline_ema21", "baseline_ema10",
                               "baseline_ema_hybrid", "baseline_hard"),
                      group="Exit",
                      help="The research engine exited below the 100-day mean; the "
                           "shared engine's nearest equivalent is an EMA trail."),
            ParamSpec("risk_per_trade_pct", "Risk per trade", "float", 0.02,
                      min=0.001, max=0.10, step=0.001, group="Sizing"),
        ]

    def defaults(self, settings: Any) -> dict[str, Any]:
        return {
            "entry_rule": "momentum_trend",
            "rank_max": 150,
            "max_positions": 12,
            "require_market_bull": True,
            "min_breadth200": 0.40,
            "hold_days": 84,
            "stop_atr": 3.0,
            "min_adv20": 20_000_000.0,
            "min_price": 20.0,
            "slippage_bps": 15.0,
            "max_signals_per_day": 5,
            "exit_mode": "baseline_ema21",
            "risk_per_trade_pct": settings.portfolio.risk_per_trade_pct,
        }

    def generate(self, ctx: StrategyContext, params: dict[str, Any]) -> pl.DataFrame:
        research = _research_engine()

        cfg = replace(
            research.StrategyConfig(
                name="lab",
                family="momentum",
                rank_col="rank_mom126",
                entry_rule=params["entry_rule"],
                hold_days=params["hold_days"],
                max_positions=params["max_positions"],
                position_pct=0.10,
                stop_atr=params["stop_atr"],
                trail_atr=None,
                exit_rule="below_sma100",
                slippage_bps=params["slippage_bps"],
                min_adv20=params["min_adv20"],
                max_adv_pct=0.02,
                rank_max=params["rank_max"],
            ),
            min_price=params["min_price"],
            require_market_bull=params["require_market_bull"],
            min_breadth200=params["min_breadth200"],
        )

        features = research.build_features(ctx.panel)
        signals = research.signal_frame(features, cfg, ctx.start_date, ctx.end_date)
        if signals.is_empty():
            return signals

        # The research engine sized by fixed percentage and stopped at an ATR
        # multiple; the shared engine sizes from the stop distance, so the ATR
        # stop becomes the position-sizing input.
        if "atr14" not in signals.columns:
            raise ValueError(
                "research signals are missing atr14; cannot derive a stop price"
            )

        # signal_frame emits the ranking column as `rank_value`, not under the
        # config's rank_col name.
        signals = signals.with_columns(
            pl.col("close").alias("entry_ref"),
            (pl.col("close") - pl.col("atr14") * params["stop_atr"]).alias("stop"),
            pl.lit(None, dtype=pl.Float64).alias("pivot"),
            pl.lit("long").alias("direction"),
            (-pl.col("rank_value")).alias("rank_score"),
        ).rename({"date": "signal_date"})

        return (
            signals.filter(pl.col("stop") > 0)
            .sort(["signal_date", "rank_score"], descending=[False, True])
            .group_by("signal_date", maintain_order=True)
            .head(params["max_signals_per_day"])
            .unique(subset=["symbol", "signal_date"], keep="first")
            .select("symbol", "signal_date", "direction", "entry_ref", "stop",
                    "pivot", "rank_score")
            .sort(["signal_date", "symbol"])
        )

    def engine_config(self, ctx: StrategyContext, params: dict[str, Any]) -> dict[str, Any]:
        base = ctx.extras["params"]
        sim_params = replace(
            base,
            pending_days=1,
            max_hold_days=params["hold_days"],
            exit_mode=type(base.exit_mode)(params["exit_mode"]),
            slippage_bps=params["slippage_bps"],
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
                "time_stop_days": params["hold_days"],
                "trail_pct_bull": exits.trail_pct_bull,
                "trail_pct_bear": exits.trail_pct_bear,
                "trail_widen_r": getattr(exits, "trail_widen_r", 3.0),
            },
            "entry_gate": {},
            "entry_size_mult": {},
        }


register_strategy(ResearchMomentum())

"""Daily factors — monthly-rebalanced factor portfolios.

The first REBALANCE-kind strategy: it expresses intent as target weights on
month-end signal dates, not as dated entries with stops. Feature construction,
point-in-time index membership, and target selection all come from
``strategies/daily_factors/engine.py``; execution is the shared rebalance
engine, which wraps that module's own simulator so the numbers stay a pure
extraction rather than a re-derivation.

The ``factor`` parameter selects between the presets that module already
defines, so the five recorded variants remain reachable by name.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date
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

FACTORS = (
    "momentum_12_1_top20",
    "high_52w_top20",
    "alpha_low_vol_top30",
    "high_52w_alpha_low_vol_top30",
    "equal_weight_universe",
)


def _factors_engine():
    from strategies.daily_factors import engine

    return engine


class DailyFactors:
    id = "daily_factors"
    name = "Daily factors"
    kind = StrategyKind.REBALANCE
    data_requirement = DataRequirement.PANEL
    description = (
        "Monthly-rebalanced equal-weight factor portfolios over a liquidity-screened "
        "universe with point-in-time index membership: 12-1 momentum, proximity to "
        "the 52-week high, low-volatility alpha, a combined sleeve, or the whole "
        "universe as a reference."
    )

    @property
    def param_specs(self) -> list[ParamSpec]:
        return [
            ParamSpec("factor", "Factor", "enum", "momentum_12_1_top20",
                      choices=FACTORS, group="Selection",
                      help="Which sleeve to run. equal_weight_universe is the reference."),
            ParamSpec("universe_size", "Universe size", "int", 200, min=20, max=1000,
                      group="Selection",
                      help="Most liquid N names by 63-day turnover, per rebalance."),
            ParamSpec("momentum_holdings", "Momentum holdings", "int", 20, min=5, max=100,
                      group="Selection", advanced=True),
            ParamSpec("high_52w_holdings", "52-week-high holdings", "int", 20, min=5,
                      max=100, group="Selection", advanced=True),
            ParamSpec("alpha_holdings", "Alpha holdings", "int", 30, min=5, max=100,
                      group="Selection", advanced=True),
            ParamSpec("combined_holdings", "Combined holdings", "int", 30, min=5, max=100,
                      group="Selection", advanced=True),
            ParamSpec("alpha_universe_size", "Alpha universe", "int", 150, min=20, max=1000,
                      group="Selection", advanced=True),
            ParamSpec("alpha_weight_cap", "Weight cap", "float", 0.05, min=0.005, max=1.0,
                      step=0.005, group="Sizing",
                      help="Maximum weight in any one name."),
            ParamSpec("liquidity_lookback", "Liquidity lookback", "int", 63, min=5, max=252,
                      group="Selection", advanced=True),
            ParamSpec("initial_capital", "Initial capital", "float", 1_000_000.0,
                      min=10_000.0, max=1e10, step=100_000.0, group="Sizing"),
            ParamSpec("slippage_bps", "Slippage (bps)", "float", 10.0, min=0.0, max=200.0,
                      step=1.0, group="Execution"),
        ]

    def defaults(self, settings: Any) -> dict[str, Any]:
        engine = _factors_engine()
        base = engine.ResearchConfig()
        return {
            "factor": "momentum_12_1_top20",
            "universe_size": base.universe_size,
            "momentum_holdings": base.momentum_holdings,
            "high_52w_holdings": base.high_52w_holdings,
            "alpha_holdings": base.alpha_holdings,
            "combined_holdings": base.combined_holdings,
            "alpha_universe_size": base.alpha_universe_size,
            "alpha_weight_cap": base.alpha_weight_cap,
            "liquidity_lookback": base.liquidity_lookback,
            "initial_capital": base.initial_capital,
            "slippage_bps": base.slippage_bps,
        }

    def _config(self, ctx: StrategyContext, params: dict[str, Any]):
        engine = _factors_engine()
        return replace(
            engine.ResearchConfig(),
            start=ctx.start_date,
            end=ctx.end_date,
            universe_size=params["universe_size"],
            momentum_holdings=params["momentum_holdings"],
            high_52w_holdings=params["high_52w_holdings"],
            alpha_holdings=params["alpha_holdings"],
            combined_holdings=params["combined_holdings"],
            alpha_universe_size=params["alpha_universe_size"],
            alpha_weight_cap=params["alpha_weight_cap"],
            liquidity_lookback=params["liquidity_lookback"],
            initial_capital=params["initial_capital"],
            slippage_bps=params["slippage_bps"],
        )

    def generate(self, ctx: StrategyContext, params: dict[str, Any]) -> pl.DataFrame:
        engine = _factors_engine()
        config = self._config(ctx, params)

        # This module builds its own feature panel: it needs turnover history
        # well before `start` for the 63-day liquidity screen and a full year
        # for 12-1 momentum, and it reads point-in-time index membership from
        # bhavcopy snapshots rather than from the price panel.
        #
        # It is pointed at the *configured* panel, not its own legacy default.
        # Features and execution must come from one panel: mixing them leaves
        # targeted symbols with no price at fill time, which silently drops
        # orders. This is why lab numbers differ from output/summary.json — see
        # the migration note in strategies/daily_factors/README.md.
        panel_path = (
            ctx.settings.runtime.data_dir
            / ctx.settings.data.panel_subdir
            / "ohlcv_all.parquet"
        )
        features, calendar, _ = engine.load_and_build_features(config, panel_path)
        membership, _ = engine.load_point_in_time_membership(
            features["date"].unique().sort().to_list()
        )
        targets = engine.build_targets(features, membership, config)

        factor = params["factor"]
        selected = targets.get(factor, {})
        ctx.extras["factors_calendar"] = calendar
        ctx.extras["factors_config"] = config

        rows: list[dict[str, Any]] = []
        for rebalance_date, weights in selected.items():
            if not (ctx.start_date <= rebalance_date <= ctx.end_date):
                continue
            for symbol, weight in weights.items():
                rows.append(
                    {
                        "rebalance_date": rebalance_date,
                        "symbol": symbol,
                        "weight": float(weight),
                    }
                )

        if not rows:
            return pl.DataFrame(
                schema={"rebalance_date": pl.Date, "symbol": pl.Utf8, "weight": pl.Float64}
            )
        return pl.DataFrame(rows).sort(["rebalance_date", "symbol"])

    def engine_config(self, ctx: StrategyContext, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "config": ctx.extras.get("factors_config") or self._config(ctx, params),
            "calendar": ctx.extras.get("factors_calendar"),
            "initial_capital": params["initial_capital"],
            "name": params["factor"],
        }


register_strategy(DailyFactors())

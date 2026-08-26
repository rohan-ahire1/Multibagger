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
            ParamSpec("universe_size", "Universe size", "int", 500, min=20, max=1000,
                      group="Selection",
                      help="Most liquid N names by 63-day turnover, per rebalance."),
            ParamSpec("momentum_holdings", "Momentum holdings", "int", 100, min=5, max=100,
                      group="Selection", advanced=True),
            ParamSpec("momentum_selection_pct", "Momentum selection", "float", 0.20,
                      min=0.05, max=1.0, step=0.05, group="Selection", advanced=True,
                      help="Fraction of the point-in-time liquidity universe selected by 12-1 momentum."),
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
            ParamSpec("liquidity_lookback", "Liquidity lookback", "int", 60, min=5, max=252,
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
            "universe_size": 500,
            "momentum_holdings": 100,
            "momentum_selection_pct": base.momentum_selection_pct,
            "high_52w_holdings": base.high_52w_holdings,
            "alpha_holdings": base.alpha_holdings,
            "combined_holdings": base.combined_holdings,
            "alpha_universe_size": base.alpha_universe_size,
            "alpha_weight_cap": base.alpha_weight_cap,
            "liquidity_lookback": 60,
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
            momentum_selection_pct=params["momentum_selection_pct"],
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

        factor = params["factor"]
        panel_path = (
            ctx.settings.runtime.data_dir
            / ctx.settings.data.panel_subdir
            / "ohlcv_all.parquet"
        )
        # The baseline momentum and equal-weight controls use a strict
        # observation-only liquidity universe.  The retrospective adjusted
        # panel is loaded only for execution/marking; it never contributes to
        # eligibility, ranking, or signal values.
        if factor in {"momentum_12_1_top20", "equal_weight_universe"}:
            pit_panel = (
                pl.scan_parquet(panel_path)
                .select("date", "symbol", "close", "volume", "turnover")
                .filter(pl.col("date") <= ctx.end_date)
                .collect()
            )
            selection_pct = params["momentum_selection_pct"] if factor.startswith("momentum") else 1.0
            pit_targets, calendar, audit = engine.build_pit_momentum_targets(
                pit_panel,
                start=ctx.start_date,
                end=ctx.end_date,
                universe_size=params["universe_size"],
                selection_pct=selection_pct,
                liquidity_lookback=params["liquidity_lookback"],
                exclude_symbols={config.benchmark_symbol},
            )
            selected = pit_targets
            ctx.extras["factors_calendar"] = calendar
            ctx.extras["factors_pit_audit"] = audit
            trade_symbols = sorted({symbol for weights in selected.values() for symbol in weights})
            adjusted_path = ctx.data_dir / "adjusted" / "total_return" / "ohlcv_all.parquet"
            if not adjusted_path.exists():
                adjusted_path = panel_path
            ctx.extras["factors_execution_panel"] = (
                pl.scan_parquet(adjusted_path)
                .select("date", "symbol", "open", "close")
                .filter((pl.col("date") >= ctx.start_date) & (pl.col("date") <= ctx.end_date))
                .filter(pl.col("symbol").is_in(trade_symbols))
                .collect()
                .sort(["date", "symbol"])
            )
            ctx.extras["factors_config"] = config
        else:
            # Legacy factor sleeves retain their existing feature definitions;
            # they are not used by the baseline validation request.
            # This module builds its own feature panel: it needs turnover history
            # well before `start` for the 63-day liquidity screen and a full year
            # for 12-1 momentum, and it reads point-in-time index membership from
            # bhavcopy snapshots rather than from the price panel.
            features, calendar, _ = engine.load_and_build_features(config, panel_path)
            membership, _ = engine.load_point_in_time_membership(
                features["date"].unique().sort().to_list()
            )
            targets = engine.build_targets(features, membership, config)
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
            "execution_panel": ctx.extras.get("factors_execution_panel"),
            "initial_capital": params["initial_capital"],
            "name": params["factor"],
        }


register_strategy(DailyFactors())

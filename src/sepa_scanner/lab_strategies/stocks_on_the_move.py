"""Clenow Stocks on the Move, implemented as an order-level lab strategy."""

from __future__ import annotations

from datetime import date, timedelta
from functools import partial
from typing import Any

import numpy as np
import polars as pl

from sepa_scanner.lab.contracts import DataRequirement, ParamSpec, StrategyContext, StrategyKind
from sepa_scanner.lab.costs import indian_delivery_cost
from sepa_scanner.lab.engines.portfolio_order_engine import PortfolioOrderConfig
from sepa_scanner.lab.indicators import (
    atr_simple, atr_wilder, gap_close_to_close, gap_open, max_gap, momentum_score, sma,
)
from sepa_scanner.lab.panel import nifty500_price_index, nifty500_tri
from sepa_scanner.lab.registry import register_strategy
from sepa_scanner.lab.universe import liquidity_universe


def _weekly_sessions(sessions: list[date], weekday: str) -> list[date]:
    target = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4}[weekday]
    cursor = sessions[0] + timedelta(days=(target - sessions[0].weekday()) % 7)
    selected: list[date] = []
    while cursor <= sessions[-1]:
        chosen = next((item for item in sessions if item >= cursor), None)
        if chosen is None:
            break
        if not selected or chosen != selected[-1]:
            selected.append(chosen)
        cursor += timedelta(days=7)
    return selected


def _members_by_date(membership: pl.DataFrame, sessions: list[date]) -> dict[date, set[str]]:
    result: dict[date, set[str]] = {}
    current: set[str] = set()
    grouped = {
        effective[0]: set(rows["symbol"].to_list())
        for effective, rows in membership.group_by("effective_date", maintain_order=True)
    } if not membership.is_empty() else {}
    for session in sessions:
        if session in grouped:
            current = grouped[session]
        result[session] = set(current)
    return result


class StocksOnTheMove:
    id = "stocks_on_the_move"
    name = "Stocks on the Move"
    kind = StrategyKind.PORTFOLIO
    data_requirement = DataRequirement.PANEL
    description = (
        "Weekly long-only cross-sectional momentum portfolio with ATR-risk-balanced "
        "integer-share orders. Liquidity-proxy universe, not Nifty 500 membership."
    )

    @property
    def param_specs(self) -> list[ParamSpec]:
        return [
            ParamSpec("momentum_window", "Momentum window", "int", 90, min=2, group="Momentum"),
            ParamSpec("annualisation_sessions", "Annualisation sessions", "int", 250, min=1, group="Momentum"),
            ParamSpec("stock_sma_sessions", "Stock SMA sessions", "int", 100, min=2, group="Filters"),
            ParamSpec("market_sma_sessions", "Market SMA sessions", "int", 200, min=2, group="Filters"),
            ParamSpec("regime_source", "Regime series", "enum", "price_index",
                      choices=("price_index", "nifty500_tri"), group="Regime",
                      help="The spec's regime series is the Nifty 500 price index. "
                           "'nifty500_tri' substitutes the total-return series we hold "
                           "locally: a documented deviation, since TRI drifts above price "
                           "by reinvested dividends."),
            ParamSpec("gap_window_sessions", "Gap window", "int", 90, min=2, group="Filters"),
            ParamSpec("gap_threshold", "Gap threshold", "float", 0.15, min=0.0, group="Filters"),
            ParamSpec("gap_definition", "Gap definition", "enum", "close_to_close", choices=("close_to_close", "open_gap"), group="Filters"),
            ParamSpec("rank_exit_pct", "Rank percentile", "float", 0.20, min=0.001, max=1.0, group="Filters"),
            ParamSpec("atr_sessions", "ATR sessions", "int", 20, min=2, group="Risk"),
            ParamSpec("atr_smoothing", "ATR smoothing", "enum", "simple", choices=("simple", "wilder"), group="Risk"),
            ParamSpec("risk_factor", "Risk factor", "float", 0.001, min=0.0, group="Risk"),
            ParamSpec("signal_weekday", "Signal weekday", "enum", "WED", choices=("MON", "TUE", "WED", "THU", "FRI"), group="Schedule"),
            ParamSpec("position_rebalance_every_n_weeks", "Position rebalance every N weeks", "int", 2, min=1, group="Schedule"),
            ParamSpec("rebalance_tolerance", "Rebalance tolerance", "float", 0.05, min=0.0, max=1.0, group="Schedule"),
            ParamSpec("require_full_target", "Require full target position", "bool", True, group="Orders"),
            ParamSpec("participation_limit_adv", "Participation limit ADV", "float", 0.02, min=0.0, max=1.0, group="Execution"),
            ParamSpec("slippage_bps", "Slippage (bps)", "float", 0.0, min=0.0, group="Execution"),
            ParamSpec("cost_stress_multiplier", "Cost stress multiplier", "float", 1.0, min=0.0, group="Execution"),
            ParamSpec("universe_size", "Universe size", "int", 500, min=1, group="Universe"),
            ParamSpec("universe_reconstitute", "Universe reconstitution", "enum", "semiannual", choices=("semiannual", "quarterly", "annual"), group="Universe"),
        ]

    def defaults(self, settings: Any) -> dict[str, Any]:
        return {spec.name: spec.default for spec in self.param_specs}

    def generate(self, ctx: StrategyContext, params: dict[str, Any]) -> pl.DataFrame:
        panel = ctx.panel
        if panel is None or panel.is_empty():
            return pl.DataFrame()
        sessions = panel["date"].unique().sort().to_list()
        membership = liquidity_universe(
            panel, size=params["universe_size"], min_history_sessions=200,
            reconstitute=params["universe_reconstitute"],
        )
        members = _members_by_date(membership, sessions)
        decisions = {
            session for session in _weekly_sessions(sessions, params["signal_weekday"])
            if ctx.start_date <= session <= ctx.end_date
        }
        rows: list[dict[str, Any]] = []
        for symbol, group in panel.sort(["symbol", "date"]).group_by("symbol", maintain_order=True):
            ticker = str(symbol[0])
            dates = group["date"].to_list()
            close = group["close"].to_numpy().astype(float)
            opening = group["open"].to_numpy().astype(float)
            atr = (atr_simple if params["atr_smoothing"] == "simple" else atr_wilder)(group, params["atr_sessions"])
            trend = close > sma(close, params["stock_sma_sessions"])
            gaps = gap_close_to_close(close) if params["gap_definition"] == "close_to_close" else gap_open(opening, np.r_[np.nan, close[:-1]])
            largest_gap = max_gap(gaps, params["gap_window_sessions"])
            for index, today in enumerate(dates):
                if today not in decisions or ticker not in members.get(today, set()):
                    continue
                score = momentum_score(close[: index + 1], params["momentum_window"], params["annualisation_sessions"])
                if score is None or not np.isfinite(atr[index]) or not np.isfinite(largest_gap[index]):
                    continue
                rows.append({"decision_date": today, "symbol": ticker, "score": score, "atr20": float(atr[index]),
                             "trend_ok": bool(trend[index]), "gap_ok": bool(largest_gap[index] <= params["gap_threshold"]),
                             "is_member": True, "close": float(close[index])})
        unranked = pl.DataFrame(rows)
        if unranked.is_empty():
            return pl.DataFrame(schema={"decision_date": pl.Date, "symbol": pl.String, "score": pl.Float64, "rank": pl.Int64, "rank_pct": pl.Float64, "atr20": pl.Float64, "trend_ok": pl.Boolean, "gap_ok": pl.Boolean, "is_member": pl.Boolean, "close": pl.Float64})
        ranked: list[dict[str, Any]] = []
        for today, frame in unranked.group_by("decision_date", maintain_order=True):
            decision_date = today[0]
            universe_count = len(members.get(decision_date, set()))
            for rank, row in enumerate(sorted(frame.to_dicts(), key=lambda item: (-float(item["score"]), str(item["symbol"]))), 1):
                row["rank"] = rank
                row["rank_pct"] = rank / universe_count
                ranked.append(row)
        return pl.DataFrame(ranked).select("decision_date", "symbol", "score", "rank", "rank_pct", "atr20", "trend_ok", "gap_ok", "is_member", "close").sort(["decision_date", "rank", "symbol"])

    def engine_config(self, ctx: StrategyContext, params: dict[str, Any]) -> dict[str, Any]:
        panel_start = ctx.panel["date"].min() if ctx.panel is not None and not ctx.panel.is_empty() else ctx.start_date
        # A missing regime series is not "no buys allowed" — that silently turns
        # the whole strategy into a no-op that still reports a tidy 0.00% run.
        # Refuse instead, and make the substitute an explicit choice.
        source = params["regime_source"]
        if source == "price_index":
            series = nifty500_price_index(ctx.data_dir, panel_start, ctx.end_date)
            column = "price"
        else:
            series = nifty500_tri(ctx.data_dir, panel_start, ctx.end_date)
            column = "tri"
        if series.is_empty():
            raise ValueError(
                f"regime_source={source!r} resolves to no local data, so no buy could "
                "ever fire and the run would report a meaningless flat curve. Fetch the "
                "Nifty 500 price index (scripts/fetch_nifty500_price_index.py), or set "
                "regime_source='nifty500_tri' to use the total-return series already in "
                "data/reference as a documented deviation."
            )
        values = series[column].to_numpy().astype(float)
        regime = series.with_columns(
            pl.Series("allows_new_buys", values > sma(values, params["market_sma_sessions"]))
        ).select("date", "allows_new_buys")
        return {"config": PortfolioOrderConfig(
            initial_capital=float(ctx.extras["portfolio"]["initial_capital"]), risk_factor=params["risk_factor"],
            market_regime=regime, rebalance_every_n_weeks=params["position_rebalance_every_n_weeks"],
            rebalance_tolerance=params["rebalance_tolerance"], require_full_target=params["require_full_target"],
            participation_limit_adv=params["participation_limit_adv"], slippage_bps=params["slippage_bps"],
            cost_model=partial(indian_delivery_cost, stress_multiplier=params["cost_stress_multiplier"]),
            pending_order_expiry_sessions=1, cash_yield_pct=0.0, rank_exit_pct=params["rank_exit_pct"],
        ), "regime_source": source}


register_strategy(StocksOnTheMove())

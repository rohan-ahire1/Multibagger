"""Reversion engine: exits when the move has reverted, not when a trend breaks.

Wraps ``strategies/mean_reversion/simulation.simulate_mr_portfolio``.

This exists because the shared signal engine cannot express a mean-reversion
exit. Its exits are trailing-EMA, hard-stop, and holding-limit; mean reversion
sells when the reversion *completes* — close back above the 5-day mean, or RSI-2
back above 70. Routing mean reversion through the shared engine replaces the
rule that defines it, producing a different strategy that merely shares its
entries. Measured that way it returned about -12% CAGR, which says nothing about
mean reversion.

The alternative was to teach the canonical simulator a reversion exit. That
simulator is what ``optimize_joint_wf.py --parity`` depends on, and the change
would be a whole extra exit engine inside it. Keeping mean reversion on its own
executor behind the same contract is the cheaper and safer trade, and it is the
fallback the plan specified for exactly this case.

Metrics still come from the shared equity-curve summariser, which is where the
comparability actually lives.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import polars as pl

from sepa_scanner.lab.result import TradeRecord

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _mr_simulation():
    from strategies.mean_reversion import simulation

    return simulation


def _mr_exit_config():
    from strategies.mean_reversion.exit_engine import MRExitConfig

    return MRExitConfig


def run_reversion_engine(
    signals: pl.DataFrame,
    ohlcv: pl.DataFrame,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Execute mean-reversion signals on their own executor."""
    simulation = _mr_simulation()
    exit_config_cls = _mr_exit_config()

    exit_config = exit_config_cls(
        exit_mode=config.get("exit_mode", "sma5"),
        **{
            k: v
            for k, v in config.get("exit_overrides", {}).items()
            if hasattr(exit_config_cls(), k)
        },
    )

    result = simulation.simulate_mr_portfolio(
        signals,
        ohlcv,
        start_date=config["start_date"],
        end_date=config["end_date"],
        max_positions=config["max_positions"],
        max_sector_positions=config["max_sector_positions"],
        initial_capital=config["initial_capital"],
        risk_per_trade_pct=config["risk_per_trade_pct"],
        exit_config=exit_config,
        sector_map=config.get("sector_map") or {},
        cooldown_days=config.get("cooldown_days", 0),
        exit_execution=config.get("exit_execution", "next_open"),
        slippage_bps=config.get("slippage_bps", 10.0),
        round_trip_cost_pct=config.get("round_trip_cost_pct", 0.0),
    )
    return result


def _first_frame(result: dict[str, Any], *names: str) -> pl.DataFrame | None:
    for name in names:
        value = result.get(name)
        if isinstance(value, pl.DataFrame) and not value.is_empty():
            return value
    return None


def reversion_daily_equity(result: dict[str, Any]) -> pl.DataFrame:
    """Normalise the executor's curve to the (date, equity) the metrics want."""
    curve = _first_frame(result, "daily_equity", "equity_curve", "daily")
    if curve is None:
        return pl.DataFrame(schema={"date": pl.Date, "equity": pl.Float64})
    if "equity" not in curve.columns:
        for candidate in ("total_equity", "portfolio_value", "value"):
            if candidate in curve.columns:
                curve = curve.rename({candidate: "equity"})
                break
    return curve.select(["date", "equity"]).sort("date")


def reversion_trade_records(result: dict[str, Any]) -> list[TradeRecord]:
    trades = _first_frame(result, "trades", "closed_trades")
    if trades is None:
        return []

    rows = trades.to_dicts()
    records: list[TradeRecord] = []
    for row in rows:
        hold = row.get("holding_days") or row.get("hold_days")
        records.append(
            TradeRecord(
                symbol=row["symbol"],
                direction=row.get("direction") or "long",
                signal_date=row.get("signal_date"),
                entry_date=row.get("entry_date"),
                exit_date=row.get("exit_date"),
                entry_price=row.get("entry_price"),
                exit_price=row.get("exit_price"),
                shares=row.get("shares"),
                stop=row.get("stop"),
                r_multiple=row.get("r_multiple"),
                pnl=row.get("pnl"),
                pnl_pct=row.get("return_pct") or row.get("pnl_pct"),
                hold_days=int(hold) if hold is not None else None,
                exit_reason=row.get("exit_reason") or row.get("status"),
                status="closed" if row.get("exit_date") is not None else "open",
                sector=row.get("sector"),
                meta={
                    k: row[k]
                    for k in ("rsi2_at_signal", "atr14", "mae_price", "mfe_price")
                    if row.get(k) is not None
                },
            )
        )
    return records

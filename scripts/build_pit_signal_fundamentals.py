"""Attach honest point-in-time NSE growth features to canonical SEPA signals."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl


ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "data/backtest/signal_quality_research/signal_feature_panel.parquet"
DETAIL_PATH = ROOT / "data/reference/nse_pit_financial_details.parquet"
OUTPUT_PATH = ROOT / "data/backtest/signal_quality_research/pit_signal_fundamentals.parquet"


def _growth(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior <= 0.0:
        return None
    return current / prior - 1.0


def _prior_year_row(rows: list[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any] | None:
    target = current["period_end"] - timedelta(days=365)
    candidates = [
        row
        for row in rows
        if 330 <= (current["period_end"] - row["period_end"]).days <= 400
    ]
    return min(candidates, key=lambda row: abs((row["period_end"] - target).days)) if candidates else None


def _basis_snapshot(
    rows: list[dict[str, Any]],
    signal_date: date,
    basis: str,
) -> dict[str, Any] | None:
    known = [
        row
        for row in rows
        if row.get("consolidated") == basis
        and row.get("available_date") is not None
        and row["available_date"] <= signal_date
        and row.get("period_end") is not None
        and any(row.get(name) is not None for name in ("eps", "net_profit", "revenue"))
    ]
    if not known:
        return None

    # A later exchange filing supersedes an earlier filing for the same period.
    latest_by_period: dict[date, dict[str, Any]] = {}
    for row in sorted(known, key=lambda item: item["filing_timestamp"]):
        latest_by_period[row["period_end"]] = row
    periods = sorted(latest_by_period, reverse=True)
    current = latest_by_period[periods[0]]
    if (signal_date - current["available_date"]).days > 200:
        return None
    prior = _prior_year_row(list(latest_by_period.values()), current)
    if prior is None:
        return None

    eps_growth = _growth(current.get("eps"), prior.get("eps"))
    profit_growth = _growth(current.get("net_profit"), prior.get("net_profit"))
    revenue_growth = _growth(current.get("revenue"), prior.get("revenue"))
    earnings_growth = eps_growth if eps_growth is not None else profit_growth
    turnaround = bool(
        (
            current.get("eps") is not None
            and prior.get("eps") is not None
            and current["eps"] > 0.0 >= prior["eps"]
        )
        or (
            current.get("net_profit") is not None
            and prior.get("net_profit") is not None
            and current["net_profit"] > 0.0 >= prior["net_profit"]
        )
    )
    return {
        "reporting_basis": basis,
        "fundamental_period_end": current["period_end"],
        "fundamental_available_date": current["available_date"],
        "fundamental_age_days": (signal_date - current["available_date"]).days,
        "eps_growth_yoy": eps_growth,
        "profit_growth_yoy": profit_growth,
        "earnings_growth_yoy": earnings_growth,
        "revenue_growth_yoy": revenue_growth,
        "earnings_turnaround": turnaround,
        "fundamental_seq_number": current.get("seq_number"),
        "prior_year_seq_number": prior.get("seq_number"),
    }


def main() -> None:
    signals = (
        pl.read_parquet(PANEL_PATH)
        .filter(pl.col("canonical_selected"))
        .select("symbol", "signal_date")
        .unique(maintain_order=True)
        .sort(["signal_date", "symbol"])
    )
    details = pl.read_parquet(DETAIL_PATH)
    by_symbol = {
        str(group["symbol"][0]): group.to_dicts()
        for group in details.partition_by("symbol", maintain_order=True)
    }

    output: list[dict[str, Any]] = []
    for signal in signals.iter_rows(named=True):
        symbol = str(signal["symbol"])
        signal_date = signal["signal_date"]
        rows = by_symbol.get(symbol, [])
        consolidated = _basis_snapshot(rows, signal_date, "Consolidated")
        standalone = _basis_snapshot(rows, signal_date, "Non-Consolidated")
        # Prefer consolidated only when it provides an actual growth comparison.
        snapshot = consolidated or standalone
        output.append({**signal, **(snapshot or {})})

    result = pl.from_dicts(output, infer_schema_length=None).sort(["signal_date", "symbol"])
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.write_parquet(OUTPUT_PATH)
    result.write_csv(OUTPUT_PATH.with_suffix(".csv"))

    covered = result.filter(pl.col("fundamental_period_end").is_not_null())
    earnings = result.filter(pl.col("earnings_growth_yoy").is_not_null())
    revenue = result.filter(pl.col("revenue_growth_yoy").is_not_null())
    print(f"Signals: {result.height}")
    print(f"Point-in-time period coverage: {covered.height} ({covered.height / result.height:.1%})")
    print(f"Usable earnings growth: {earnings.height} ({earnings.height / result.height:.1%})")
    print(f"Usable revenue growth: {revenue.height} ({revenue.height / result.height:.1%})")
    print(
        result.with_columns(pl.col("signal_date").dt.year().alias("year"))
        .group_by("year")
        .agg(
            pl.len().alias("signals"),
            pl.col("fundamental_period_end").is_not_null().sum().alias("covered"),
            pl.col("earnings_growth_yoy").is_not_null().sum().alias("earnings"),
            pl.col("revenue_growth_yoy").is_not_null().sum().alias("revenue"),
        )
        .sort("year")
    )


if __name__ == "__main__":
    main()

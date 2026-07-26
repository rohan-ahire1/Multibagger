"""Strictly backward-looking liquidity-proxy universe construction."""

from __future__ import annotations

from datetime import date
from typing import Literal

import polars as pl


def _effective_dates(sessions: list[date], cadence: str) -> list[date]:
    periods: set[tuple[int, int]] = set()
    effective: list[date] = []
    for session in sessions:
        if cadence == "semiannual":
            key = (session.year, 1 if session.month <= 6 else 7)
        elif cadence == "quarterly":
            key = (session.year, ((session.month - 1) // 3) * 3 + 1)
        else:
            key = (session.year, 1)
        if key not in periods:
            periods.add(key)
            effective.append(session)
    return effective


def liquidity_universe(
    panel: pl.DataFrame,
    size: int = 500,
    lookback_sessions: int = 60,
    min_history_sessions: int = 200,
    reconstitute: Literal["semiannual", "quarterly", "annual"] = "semiannual",
) -> pl.DataFrame:
    """Return auditable membership rows based only on prior panel observations."""
    if reconstitute not in {"semiannual", "quarterly", "annual"}:
        raise ValueError("reconstitute must be semiannual, quarterly, or annual")
    if panel.is_empty():
        return pl.DataFrame(schema={"effective_date": pl.Date, "symbol": pl.String, "rank_by_turnover": pl.Int64})
    required = {"date", "symbol", "turnover"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"panel missing universe columns: {sorted(missing)}")
    clean = panel.select("date", "symbol", "turnover").sort(["date", "symbol"])
    sessions = clean["date"].unique().sort().to_list()
    rows: list[dict[str, object]] = []
    for effective in _effective_dates(sessions, reconstitute):
        prior = clean.filter(pl.col("date") < effective)
        ranked: list[tuple[str, float]] = []
        for symbol, group in prior.group_by("symbol", maintain_order=True):
            history = group.sort("date")
            if history.height < min_history_sessions:
                continue
            sample = history.tail(lookback_sessions)
            if sample.height < lookback_sessions:
                continue
            positive = sample.filter(pl.col("turnover") > 0).height
            if positive < 0.8 * lookback_sessions:
                continue
            ranked.append((str(symbol[0]), float(sample["turnover"].median())))
        for rank, (symbol, _) in enumerate(sorted(ranked, key=lambda item: (-item[1], item[0]))[:size], 1):
            rows.append({"effective_date": effective, "symbol": symbol, "rank_by_turnover": rank})
    return pl.DataFrame(rows, schema={"effective_date": pl.Date, "symbol": pl.String, "rank_by_turnover": pl.Int64})

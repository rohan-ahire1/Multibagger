from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from sepa_scanner.lab.universe import liquidity_universe


def _panel(days: int = 600) -> pl.DataFrame:
    rows = []
    for i in range(days):
        day = date(2019, 1, 1) + timedelta(days=i)
        rows.extend([
            {"date": day, "symbol": "OLD", "turnover": 1000.0},
            {"date": day, "symbol": "NEW", "turnover": 2000.0} if i >= 250 else {"date": day, "symbol": "OLD2", "turnover": 500.0},
        ])
    return pl.DataFrame(rows).unique(["date", "symbol"])


def test_listing_requires_history_and_membership_is_backward_looking():
    panel = _panel()
    actual = liquidity_universe(panel, size=2, lookback_sessions=20, min_history_sessions=200)
    assert "NEW" not in actual.filter(pl.col("effective_date") <= date(2020, 1, 1))["symbol"].to_list()
    cutoff = date(2020, 1, 1)
    before = liquidity_universe(panel.filter(pl.col("date") <= cutoff), size=2, lookback_sessions=20, min_history_sessions=200)
    after = actual.filter(pl.col("effective_date") <= cutoff)
    assert before.equals(after)


def test_reconstitution_uses_real_sessions_and_retired_member_remains_until_next_date():
    panel = _panel().filter(~((pl.col("symbol") == "OLD") & (pl.col("date") > date(2020, 10, 1))))
    result = liquidity_universe(panel, size=2, lookback_sessions=20, min_history_sessions=200)
    assert set(result["effective_date"]).issubset(set(panel["date"]))
    july = result.filter(pl.col("effective_date") == date(2020, 7, 1))
    assert "OLD" in july["symbol"].to_list()

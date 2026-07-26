from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from sepa_scanner.lab.engines.portfolio_order_engine import PortfolioOrderConfig, run_portfolio_order_engine


def _panel() -> pl.DataFrame:
    rows = []
    for i in range(30):
        for symbol, price in (("A", 10.0), ("B", 20.0)):
            rows.append({"date": date(2020, 1, 1) + timedelta(days=i), "symbol": symbol,
                         "open": price, "high": price + 1, "low": price - 1, "close": price,
                         "volume": 10_000, "turnover": 1_000_000.0})
    return pl.DataFrame(rows)


def _decision(day: int, symbol: str = "A", *, rank_pct: float = 0.1) -> dict:
    return {"decision_date": date(2020, 1, 1) + timedelta(days=day), "symbol": symbol,
            "score": 1.0 if symbol == "A" else 0.5, "rank": 1 if symbol == "A" else 2,
            "rank_pct": rank_pct, "atr20": 1.0, "trend_ok": True, "gap_ok": True,
            "is_member": True, "close": 10.0 if symbol == "A" else 20.0}


def _config(regime: bool = True, full: bool = True) -> PortfolioOrderConfig:
    return PortfolioOrderConfig(10_000.0, 0.01, pl.DataFrame({"date": [date(2020, 1, 21)], "allows_new_buys": [regime]}), 2, 0.05, full, 1.0, 0.0)


def test_signal_fills_t_plus_one_and_market_filter_does_not_liquidate():
    result = run_portfolio_order_engine(pl.DataFrame([_decision(20)]), _panel(), _config())
    order = result["orders"].row(0, named=True)
    assert order["date"] == date(2020, 1, 22)
    assert order["decision_date"] == date(2020, 1, 21)
    blocked = run_portfolio_order_engine(pl.DataFrame([_decision(20)]), _panel(), _config(regime=False))
    assert blocked["orders"].is_empty()


def test_full_target_skips_underfunded_and_partial_variant_is_deterministic():
    decisions = pl.DataFrame([_decision(20, "A"), _decision(20, "B")])
    full = run_portfolio_order_engine(decisions, _panel(), _config(full=True))
    partial_one = run_portfolio_order_engine(decisions, _panel(), _config(full=False))
    partial_two = run_portfolio_order_engine(decisions, _panel(), _config(full=False))
    assert full["daily_equity"]["equity"].min() >= 0
    assert partial_one["orders"].equals(partial_two["orders"])
    assert all(float(value).is_integer() for value in partial_one["orders"]["shares"].to_list())


def test_zero_volume_prevents_fill_and_expires():
    panel = _panel().with_columns(pl.when((pl.col("symbol") == "A") & pl.col("date").is_in([date(2020, 1, 22), date(2020, 1, 23)])).then(0).otherwise(pl.col("volume")).alias("volume"))
    result = run_portfolio_order_engine(pl.DataFrame([_decision(20)]), panel, _config())
    assert result["orders"].is_empty()
    assert result["rejections"].filter(pl.col("reason") == "missing_or_zero_volume_expired").height == 1


def test_exhausted_lots_do_not_emit_phantom_trades():
    """A fully consumed lot must not match again on a later sell.

    Without a remaining>0 guard, every later sell re-walked all historical lots,
    matched zero shares against the spent ones, and still emitted a trade dated
    from their original entry -- which inflated the trade count and pushed the
    average hold to values longer than the backtest itself.
    """
    import polars as pl

    from sepa_scanner.lab.engines.portfolio_order_engine import portfolio_trade_records

    orders = pl.DataFrame([
        {"date": date(2020, 1, 1), "decision_date": date(2019, 12, 31), "symbol": "AAA",
         "side": "buy", "shares": 10, "fill_price": 100.0, "cost": 1.0, "reason": "entry", "status": "filled"},
        {"date": date(2020, 2, 1), "decision_date": date(2020, 1, 31), "symbol": "AAA",
         "side": "sell", "shares": 10, "fill_price": 110.0, "cost": 1.0, "reason": "exit", "status": "filled"},
        {"date": date(2020, 3, 1), "decision_date": date(2020, 2, 28), "symbol": "AAA",
         "side": "buy", "shares": 5, "fill_price": 120.0, "cost": 1.0, "reason": "entry", "status": "filled"},
        {"date": date(2020, 4, 1), "decision_date": date(2020, 3, 31), "symbol": "AAA",
         "side": "sell", "shares": 5, "fill_price": 130.0, "cost": 1.0, "reason": "exit", "status": "filled"},
    ])

    records = portfolio_trade_records(orders)

    assert len(records) == 2, f"expected two round trips, got {len(records)}"
    assert all(record.shares and record.shares > 0 for record in records)
    # The second round trip must date from the March buy, not the January one.
    assert records[1].entry_date == date(2020, 3, 1)
    assert records[1].hold_days == 31

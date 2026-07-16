from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from strategies.mean_reversion.exit_engine import MRExitConfig
from strategies.mean_reversion.simulation import simulate_mr_portfolio


def _ohlcv(closes: list[float], opens: list[float] | None = None) -> pl.DataFrame:
    start = date(2026, 1, 1)
    opens = opens or closes
    return pl.DataFrame(
        [
            {
                "date": start + timedelta(days=idx),
                "symbol": "TEST",
                "open": float(opens[idx]),
                "high": float(max(opens[idx], close) + 2.0),
                "low": float(min(opens[idx], close) - 2.0),
                "close": float(close),
                "volume": 1_000_000.0,
            }
            for idx, close in enumerate(closes)
        ]
    ).with_columns(pl.col("date").cast(pl.Date))


def _signals(signal_date: date, stop: float = 90.0) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "signal_date": signal_date,
                "symbol": "TEST",
                "close": 100.0,
                "rsi2": 5.0,
                "sma200": 80.0,
                "sma5": 99.0,
                "entry_ref": 100.0,
                "stop": stop,
                "atr14": 3.0,
                "adv50": 100_000_000.0,
            }
        ]
    ).with_columns(pl.col("signal_date").cast(pl.Date))


def test_entry_sizing_reserves_transaction_cost_cash() -> None:
    frame = _ohlcv([100.0, 100.0, 100.0, 100.0, 101.0], opens=[100.0, 100.0, 100.0, 100.0, 101.0])
    result = simulate_mr_portfolio(
        _signals(date(2026, 1, 3), stop=99.0),
        frame,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 5),
        initial_capital=1_000.0,
        risk_per_trade_pct=1.0,
        round_trip_cost_pct=0.002,
        exit_config=MRExitConfig(exit_mode="time_stop", time_stop_days=10),
    )

    min_cash = float(result["daily_equity"]["cash"].min())
    assert min_cash >= -0.01


def test_next_open_exit_defers_indicator_exit() -> None:
    frame = _ohlcv(
        [100.0, 100.0, 100.0, 100.0, 110.0, 120.0],
        opens=[100.0, 100.0, 100.0, 101.0, 105.0, 115.0],
    )
    same_close = simulate_mr_portfolio(
        _signals(date(2026, 1, 3)),
        frame,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 6),
        exit_config=MRExitConfig(exit_mode="sma3"),
        exit_execution="same_close",
    )
    next_open = simulate_mr_portfolio(
        _signals(date(2026, 1, 3)),
        frame,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 6),
        exit_config=MRExitConfig(exit_mode="sma3"),
        exit_execution="next_open",
    )

    same_trade = same_close["trades"].row(0, named=True)
    next_trade = next_open["trades"].row(0, named=True)
    assert same_trade["exit_date"] == date(2026, 1, 5)
    assert same_trade["exit_price"] == 110.0
    assert next_trade["exit_date"] == date(2026, 1, 6)
    assert next_trade["exit_price"] == 115.0
    assert next_trade["exit_reason"] == "mean_revert_sma3_next_open"

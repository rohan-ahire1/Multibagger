"""Integration tests for simulation using the phased exit engine."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from sepa_scanner.backtest.simulation import build_symbol_data, simulate_portfolio_python
from sepa_scanner.backtest.models import BacktestParams, ExitMode


def _make_ohlcv(n_days: int = 250, start: date = date(2024, 1, 1)) -> pl.DataFrame:
    """Generate synthetic OHLCV data for one symbol with an uptrend."""
    dates = [start + timedelta(days=i) for i in range(n_days)]
    base = 100.0
    rows = []
    for i, d in enumerate(dates):
        c = base + i * 0.5  # steady uptrend
        rows.append({
            "date": d,
            "symbol": "TEST",
            "open": c - 0.5,
            "high": c + 1.0,
            "low": c - 1.5,
            "close": c,
            "volume": 1_000_000 + i * 1000,
        })
    return pl.DataFrame(rows).cast({"date": pl.Date, "volume": pl.Int64})


def test_build_symbol_data_has_all_indicator_keys():
    ohlcv = _make_ohlcv(250)
    symbol_data, unique_dates = build_symbol_data(ohlcv)
    sd = symbol_data["TEST"]

    required_keys = [
        "dates", "opens", "highs", "lows", "closes", "volumes",
        "date_to_idx", "sma_50", "sma_200", "ema_21", "ema_50",
        "low_10d", "prev_close", "avg_range_20", "avg_volume_50",
    ]
    for key in required_keys:
        assert key in sd, f"Missing key: {key}"
    assert len(sd["sma_50"]) == len(sd["closes"])
    assert len(sd["ema_21"]) == len(sd["closes"])


def test_build_symbol_data_sma_50_correct_at_index_60():
    """SMA-50 at index 60 should be the mean of closes[11:61]."""
    ohlcv = _make_ohlcv(250)
    symbol_data, _ = build_symbol_data(ohlcv)
    sd = symbol_data["TEST"]
    # Manual calculation: closes are 100.0, 100.5, 101.0, ... so close[i] = 100 + i*0.5
    # sma_50 at index 60 = mean of closes[11..60] = mean(105.5 .. 130.0)
    expected = sum(100.0 + i * 0.5 for i in range(11, 61)) / 50
    assert abs(sd["sma_50"][60] - expected) < 0.01


def test_build_symbol_data_prev_close():
    ohlcv = _make_ohlcv(100)
    symbol_data, _ = build_symbol_data(ohlcv)
    sd = symbol_data["TEST"]
    # prev_close[0] should be 0 (no previous), prev_close[1] = closes[0]
    assert sd["prev_close"][0] == 0.0
    assert sd["prev_close"][1] == sd["closes"][0]


def test_build_symbol_data_low_10d():
    ohlcv = _make_ohlcv(100)
    symbol_data, _ = build_symbol_data(ohlcv)
    sd = symbol_data["TEST"]
    # At index 15, low_10d should be the min of lows[6:16]
    expected = min(sd["lows"][6:16])
    assert abs(sd["low_10d"][15] - expected) < 0.01


def _make_trending_ohlcv(
    symbol: str = "WINNER",
    n_days: int = 120,
    start: date = date(2024, 1, 1),
    daily_gain: float = 0.5,
) -> pl.DataFrame:
    """Generate OHLCV for a stock in a steady uptrend."""
    dates = [start + timedelta(days=i) for i in range(n_days)]
    rows = []
    base = 100.0
    for i, d in enumerate(dates):
        c = base + i * daily_gain
        rows.append({
            "date": d,
            "symbol": symbol,
            "open": c - 0.3,
            "high": c + 1.5,
            "low": c - 1.0,
            "close": c,
            "volume": 1_000_000,
        })
    return pl.DataFrame(rows).cast({"date": pl.Date, "volume": pl.Int64})


def test_simulation_uses_phased_exit_engine():
    """A steadily rising stock should be held longer than 15 days with phased exits."""
    ohlcv = _make_trending_ohlcv(n_days=120, daily_gain=0.8)
    # Signal on day 10: entry_ref slightly above close, stop below
    entry_day = date(2024, 1, 11)
    entry_price = 108.0  # close on day 10 = 100 + 10*0.8 = 108
    stop_price = 102.0
    signals = pl.DataFrame({
        "signal_date": [entry_day],
        "symbol": ["WINNER"],
        "entry_ref": [entry_price + 0.5],
        "stop": [stop_price],
        "pivot": [entry_price],
        "rs_rank": [85.0],
        "signal_size_mult": [1.0],
    }).cast({"signal_date": pl.Date})

    params = BacktestParams(pending_days=5, max_hold_days=90)
    portfolio = {
        "initial_capital": 1_000_000,
        "risk_per_trade_pct": 0.01,
        "max_positions": 10,
        "drawdown_half_pct": 0.10,
        "drawdown_stop_pct": 0.15,
        "drawdown_close_pct": 0.20,
        "pyramid_trigger_r": 0.5,
    }

    result = simulate_portfolio_python(
        signals=signals,
        ohlcv=ohlcv,
        params=params,
        portfolio=portfolio,
        analysis_sector_map={"WINNER": "TestSector"},
    )
    trades = result["trades"]
    assert len(trades) >= 1
    filled = [t for t in trades if t.entry_date is not None]
    assert len(filled) == 1
    trade = filled[0]
    # With a steadily rising stock at 0.8/day and risk of ~6,
    # the trade should be held well past 15 days
    assert trade.holding_days > 15, f"Trade held only {trade.holding_days} days — exit engine not wired"
    assert trade.r_multiple > 1.0, f"R-multiple is {trade.r_multiple} — should be >1R on a rising stock"
    # The phased exit engine should have set exit_reason (the inline logic doesn't)
    assert trade.exit_reason is not None, "exit_reason not set — phased exit engine not wired"
    assert trade.status != "time_stop" or trade.holding_days == params.max_hold_days, f"Trade was time-stopped early at {trade.holding_days} days — exit engine should trail instead"


def test_simulation_exposes_open_and_pending_snapshots_without_counting_them_as_closed():
    ohlcv = _make_trending_ohlcv(n_days=5, daily_gain=1.0)
    signals = pl.DataFrame({
        "signal_date": [date(2024, 1, 1), date(2024, 1, 5)],
        "symbol": ["WINNER", "WINNER"],
        "entry_ref": [100.5, 1_000.0],
        "stop": [95.0, 990.0],
        "pivot": [100.0, 999.0],
        "rs_rank": [90.0, 90.0],
        "signal_size_mult": [1.0, 1.0],
    }).cast({"signal_date": pl.Date})
    params = BacktestParams(pending_days=8, max_hold_days=90)
    portfolio = {
        "initial_capital": 1_000_000,
        "risk_per_trade_pct": 0.01,
        "max_positions": 10,
        "drawdown_half_pct": 0.10,
        "drawdown_stop_pct": 0.15,
        "drawdown_close_pct": 0.20,
    }

    result = simulate_portfolio_python(
        signals=signals,
        ohlcv=ohlcv,
        params=params,
        portfolio=portfolio,
        analysis_sector_map={"WINNER": "TestSector"},
    )

    assert result["trades"] == []
    assert len(result["open_trades"]) == 1
    assert result["open_trades"][0].status == "open"
    assert result["open_trades"][0].entry_date == date(2024, 1, 2)
    assert result["open_trades"][0].exit_date is None
    assert result["open_trades"][0].return_pct is not None
    assert result["open_trades"][0].return_pct > 0

    assert len(result["pending_trades"]) == 1
    assert result["pending_trades"][0].status == "pending_entry"
    assert result["pending_trades"][0].entry_date is None
    assert result["pending_trades"][0].entry_ref == 1_000.0


def test_pending_entry_starts_on_d_plus_one_and_expires_after_symbol_bars():
    """The EOD signal bar cannot fill and calendar gaps do not consume a bar."""
    signal_date = date(2024, 1, 1)
    ohlcv = pl.DataFrame({
        "date": [signal_date, date(2024, 1, 2), date(2024, 1, 5)],
        "symbol": ["TEST", "TEST", "TEST"],
        # The signal bar clears entry_ref, but both D+1 onward bars do not.
        "open": [100.0, 99.0, 99.0],
        "high": [110.0, 104.0, 104.0],
        "low": [99.0, 98.0, 98.0],
        "close": [105.0, 100.0, 100.0],
        "volume": [1_000_000, 1_000_000, 1_000_000],
    }).cast({"date": pl.Date, "volume": pl.Int64})
    signals = pl.DataFrame({
        "signal_date": [signal_date],
        "symbol": ["TEST"],
        "entry_ref": [105.0],
        "stop": [95.0],
        "pivot": [104.0],
        "rs_rank": [90.0],
        "signal_size_mult": [1.0],
    }).cast({"signal_date": pl.Date})
    params = BacktestParams(pending_days=2, max_hold_days=90)
    portfolio = {
        "initial_capital": 1_000_000,
        "risk_per_trade_pct": 0.01,
        "max_positions": 10,
        "drawdown_half_pct": 0.10,
        "drawdown_stop_pct": 0.15,
        "drawdown_close_pct": 0.20,
    }

    result = simulate_portfolio_python(
        signals=signals,
        ohlcv=ohlcv,
        params=params,
        portfolio=portfolio,
        analysis_sector_map={"TEST": "TestSector"},
    )

    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade.status == "not_filled"
    assert trade.entry_date is None


def test_production_simulator_executes_hard_stop_on_intraday_touch() -> None:
    dates = [date(2026, 3, day) for day in range(1, 5)]
    ohlcv = pl.DataFrame({
        "date": dates,
        "symbol": ["REVERSAL"] * 4,
        "open": [100.0] * 4,
        "high": [101.0] * 4,
        "low": [99.0, 99.0, 94.0, 99.0],
        "close": [100.0] * 4,
        "volume": [1_000_000] * 4,
    }).cast({"date": pl.Date, "volume": pl.Int64})
    signals = pl.DataFrame({
        "signal_date": [dates[0]],
        "symbol": ["REVERSAL"],
        "entry_ref": [100.0],
        "stop": [95.0],
        "pivot": [100.0],
        "rs_rank": [90.0],
    }).cast({"signal_date": pl.Date})
    params = BacktestParams(
        pending_days=1,
        max_hold_days=20,
        exit_mode=ExitMode.BASELINE_HARD,
        stop_trigger_mode="intraday_hard",
    )
    portfolio = {
        "initial_capital": 100_000.0,
        "risk_per_trade_pct": 0.01,
        "max_positions": 5,
        "drawdown_half_pct": 0.50,
        "drawdown_stop_pct": 0.75,
        "drawdown_close_pct": 0.90,
    }

    result = simulate_portfolio_python(signals, ohlcv, params, portfolio, {})

    assert len(result["trades"]) == 1
    assert result["trades"][0].status == "intraday_stop"
    assert result["trades"][0].exit_date == dates[2]
    assert result["trades"][0].exit_price == 95.0


def test_production_simulator_executes_ema_close_signal_at_next_open() -> None:
    dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(24)]
    ohlcv = pl.DataFrame({
        "date": dates,
        "symbol": ["TRAIL"] * 24,
        "open": [100.0] * 21 + [115.0, 102.0, 90.0],
        "high": [101.0] * 21 + [116.0, 103.0, 91.0],
        "low": [99.0] * 21 + [114.0, 100.25, 89.0],
        "close": [100.0] * 21 + [115.0, 100.5, 90.0],
        "volume": [1_000_000] * 24,
    }).cast({"date": pl.Date, "volume": pl.Int64})
    signals = pl.DataFrame({
        "signal_date": [dates[0]],
        "symbol": ["TRAIL"],
        "entry_ref": [100.0],
        "stop": [95.0],
        "pivot": [100.0],
        "rs_rank": [90.0],
    }).cast({"signal_date": pl.Date})
    params = BacktestParams(
        pending_days=1,
        max_hold_days=150,
        exit_mode=ExitMode.BASELINE_EMA21,
        stop_trigger_mode="intraday_hard",
        signal_exit_execution="next_open",
    )
    portfolio = {
        "initial_capital": 100_000.0,
        "risk_per_trade_pct": 0.01,
        "max_positions": 5,
        "drawdown_half_pct": 0.50,
        "drawdown_stop_pct": 0.75,
        "drawdown_close_pct": 0.90,
    }

    result = simulate_portfolio_python(signals, ohlcv, params, portfolio, {})

    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade.status.endswith("_next_open")
    assert trade.exit_date == dates[23]
    assert trade.exit_price == 90.0


def test_production_simulator_executes_stagnation_signal_at_next_open() -> None:
    dates = [date(2026, 3, day) for day in range(1, 20)]
    ohlcv = pl.DataFrame({
        "date": dates,
        "symbol": ["STALL"] * len(dates),
        "open": [100.0] * 17 + [98.0, 98.0],
        "high": [103.9] * len(dates),
        "low": [98.0] * len(dates),
        "close": [99.0] * len(dates),
        "volume": [1_000_000] * len(dates),
    }).cast({"date": pl.Date, "volume": pl.Int64})
    signals = pl.DataFrame({
        "signal_date": [dates[0]],
        "symbol": ["STALL"],
        "entry_ref": [100.0],
        "stop": [90.0],
        "pivot": [100.0],
        "rs_rank": [90.0],
    }).cast({"signal_date": pl.Date})
    params = BacktestParams(
        pending_days=1,
        max_hold_days=150,
        exit_mode=ExitMode.BASELINE_HARD,
        signal_exit_execution="next_open",
        stagnation_exit_bars=15,
        stagnation_max_mfe_r=0.40,
        stagnation_max_open_r=0.0,
    )
    portfolio = {
        "initial_capital": 100_000.0,
        "risk_per_trade_pct": 0.01,
        "max_positions": 5,
        "drawdown_half_pct": 0.50,
        "drawdown_stop_pct": 0.75,
        "drawdown_close_pct": 0.90,
    }

    result = simulate_portfolio_python(signals, ohlcv, params, portfolio, {})

    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade.status == "stagnation_next_open"
    assert trade.entry_date == dates[1]
    assert trade.exit_date == dates[17]
    assert trade.exit_price == 98.0

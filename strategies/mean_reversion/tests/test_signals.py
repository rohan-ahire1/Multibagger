from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from strategies.mean_reversion.signal_engine import scan_mean_reversion, scan_mr_v2, scan_short_mean_reversion


def _frame(symbol: str = "TEST", *, final_close: float = 295.0, volume: float = 1_000_000.0) -> pl.DataFrame:
    closes = [100.0 + idx for idx in range(210)] + [300.0, final_close, final_close + 4.0]
    rows = []
    start = date(2025, 1, 1)
    for idx, close in enumerate(closes):
        rows.append(
            {
                "date": start + timedelta(days=idx),
                "symbol": symbol,
                "open": close + 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": volume,
            }
        )
    return pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Date))


def test_signal_generated_when_all_entry_rules_pass() -> None:
    frame = _frame()
    signals = scan_mean_reversion(frame, rsi_threshold=10, min_adv_inr=1, min_price=50)

    assert signals.height == 1
    row = signals.row(0, named=True)
    assert row["symbol"] == "TEST"
    assert row["rsi2"] < 10
    assert row["close"] > row["sma200"]
    assert row["adv50"] > 1
    assert row["stop"] == row["close"] - (row["atr14"] * 2.0)


def test_no_signal_when_rsi_above_threshold() -> None:
    signals = scan_mean_reversion(_frame(), rsi_threshold=5, min_adv_inr=1, min_price=50)
    assert signals.is_empty()


def test_no_signal_when_close_below_sma200() -> None:
    signals = scan_mean_reversion(_frame(final_close=50.0), rsi_threshold=10, min_adv_inr=1, min_price=1)
    assert signals.is_empty()


def test_no_signal_when_adv_below_threshold() -> None:
    signals = scan_mean_reversion(_frame(volume=1.0), rsi_threshold=10, min_adv_inr=1_000_000_000, min_price=50)
    assert signals.is_empty()


def test_multiple_signals_same_day_sorted_by_most_oversold() -> None:
    frame = pl.concat([_frame("AAA", final_close=295.0), _frame("BBB", final_close=280.0)])
    signals = scan_mean_reversion(frame, rsi_threshold=10, min_adv_inr=1, min_price=50)

    assert signals.height == 2
    assert signals["rsi2"].to_list() == sorted(signals["rsi2"].to_list())


def test_v2_signal_emits_research_indicator_columns() -> None:
    signals = scan_mr_v2(_frame(), entry_indicator="rsi2", entry_threshold=10, min_adv_inr=1, min_price=50)

    assert signals.height == 1
    row = signals.row(0, named=True)
    assert row["rsi2"] < 10
    assert row["crsi"] >= 0
    assert row["cum_rsi"] >= 0
    assert 0 <= row["ibs"] <= 1
    assert row["sma3"] > 0


def test_v2_daily_ranking_keeps_most_oversold() -> None:
    frame = pl.concat([_frame("AAA", final_close=295.0), _frame("BBB", final_close=280.0)])
    signals = scan_mr_v2(
        frame,
        entry_indicator="rsi2",
        entry_threshold=10,
        max_signals_per_day=1,
        min_adv_inr=1,
        min_price=50,
    )

    assert signals.height == 1
    assert signals["symbol"].to_list() == ["BBB"]


def test_short_signal_generated_when_mirror_rules_pass() -> None:
    closes = [300.0 - idx for idx in range(210)] + [95.0, 100.0, 105.0]
    rows = []
    start = date(2025, 1, 1)
    for idx, close in enumerate(closes):
        rows.append(
            {
                "date": start + timedelta(days=idx),
                "symbol": "TEST",
                "open": close + 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1_000_000.0,
            }
        )
    frame = pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Date))

    signals = scan_short_mean_reversion(frame, rsi_threshold=65, min_adv_inr=1, min_price=1, stop_atr_mult=3.5)

    assert signals.height == 2
    row = signals.row(0, named=True)
    assert row["rsi2"] > 65
    assert row["close"] < row["sma200"]
    assert row["stop"] == row["close"] + (row["atr14"] * 3.5)

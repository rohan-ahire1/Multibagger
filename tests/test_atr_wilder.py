from __future__ import annotations

import random
from datetime import date, timedelta

import polars as pl
import pytest

from sepa_scanner.indicators.atr import add_atr_columns, wilder_atr


def test_wilder_atr_matches_recursive_formula() -> None:
    result = wilder_atr(
        high=[10.0, 12.0, 13.0, 14.0, 15.0],
        low=[9.0, 11.0, 12.0, 13.0, 13.0],
        close=[9.5, 11.5, 12.5, 13.5, 14.0],
        period=3,
    )

    assert result[:2] == [None, None]
    assert result[2] == pytest.approx((1.0 + 2.5 + 1.5) / 3.0)
    assert result[3] == pytest.approx((((1.0 + 2.5 + 1.5) / 3.0) * 2.0 + 1.5) / 3.0)
    assert result[4] == pytest.approx((result[3] * 2.0 + 2.0) / 3.0)


def _synthetic_ohlcv(symbol: str, n_bars: int, seed: int = 42) -> pl.DataFrame:
    rng = random.Random(seed)
    rows: list[dict] = []
    price = 100.0
    start = date(2023, 1, 1)
    for i in range(n_bars):
        change = rng.gauss(0, 2)
        close = max(price + change, 10.0)
        high = close + abs(rng.gauss(0, 1))
        low = close - abs(rng.gauss(0, 1))
        rows.append({
            "date": start + timedelta(days=i),
            "symbol": symbol,
            "open": price,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.randint(100_000, 1_000_000),
        })
        price = close
    return pl.DataFrame(rows)


def test_polars_atr_converges_with_wilder_atr() -> None:
    """Polars-native ATR diverges < 0.01% from Python Wilder ATR after warmup.

    EWM-seeded vs SMA-seeded difference decays as (1 - 1/period)^n;
    with period=14 this needs ~130 bars to drop below 0.01%.
    Our scanner requires 320+ bars so this is safe in practice.
    """
    period = 14
    warmup = 200
    n_bars = 400

    frames = [_synthetic_ohlcv("SYM_A", n_bars, seed=1), _synthetic_ohlcv("SYM_B", n_bars, seed=2)]
    frame = pl.concat(frames).sort(["symbol", "date"])

    polars_result = add_atr_columns(frame, period=period)

    for sym in ["SYM_A", "SYM_B"]:
        sym_df = frame.filter(pl.col("symbol") == sym).sort("date")
        python_atr = wilder_atr(
            high=sym_df["high"].to_list(),
            low=sym_df["low"].to_list(),
            close=sym_df["close"].to_list(),
            period=period,
        )

        polars_atr = (
            polars_result.filter(pl.col("symbol") == sym)
            .sort("date")["atr"]
            .to_list()
        )

        for i in range(warmup, n_bars):
            py_val = python_atr[i]
            pl_val = polars_atr[i]
            assert py_val is not None and pl_val is not None, f"null at bar {i} for {sym}"
            rel_err = abs(py_val - pl_val) / py_val
            assert rel_err < 1e-4, (
                f"{sym} bar {i}: python={py_val:.6f} polars={pl_val:.6f} err={rel_err:.6f}"
            )


def test_add_atr_columns_nulls_before_period() -> None:
    """First period-1 bars per symbol should be null."""
    period = 5
    frame = _synthetic_ohlcv("TEST", 20).sort(["symbol", "date"])
    result = add_atr_columns(frame, period=period)
    atr_values = result.sort("date")["atr"].to_list()
    assert all(v is None for v in atr_values[: period - 1])
    assert atr_values[period - 1] is not None

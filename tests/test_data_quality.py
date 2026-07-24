"""Read-only data-quality guards for the NSE bhavcopy adjustment pipeline.

These fail loudly rather than silently cleaning data. The adjustment-math tests always
run; the panel-invariant tests skip if the built adjusted panel is not present.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from sepa_scanner.data_sources.adjustment import apply_adjustment, event_price_multiplier

ADJUSTED_PANEL = Path("data/nse_adjusted/ohlcv_all.parquet")


def _flat_raw(sym: str = "X") -> pl.DataFrame:
    dates = [date(2020, 5, 1), date(2020, 5, 15), date(2020, 6, 1), date(2020, 6, 15)]
    return pl.DataFrame(
        {
            "date": dates,
            "symbol": [sym] * 4,
            "open": [100.0] * 4,
            "high": [100.0] * 4,
            "low": [100.0] * 4,
            "close": [100.0] * 4,
            "volume": [1000, 1000, 1000, 1000],
            "turnover": [1e5] * 4,
        }
    )


def test_split_back_adjustment_scales_pre_ex_prices_and_inverts_volume() -> None:
    ca = pl.DataFrame(
        {
            "symbol": ["X"],
            "ex_date": [date(2020, 6, 1)],
            "action_type": ["split"],
            "ratio_num": [None],
            "ratio_den": [None],
            "old_face_value": [10.0],
            "new_face_value": [5.0],
        }
    )
    adj = apply_adjustment(_flat_raw(), ca).sort("date")
    assert adj["close"].to_list() == [50.0, 50.0, 100.0, 100.0]
    assert adj["volume"].to_list() == [2000, 2000, 1000, 1000]


def test_bonus_multiplier_and_dividend_multiplier() -> None:
    ca = pl.DataFrame(
        {
            "symbol": ["X"],
            "ex_date": [date(2020, 6, 1)],
            "action_type": ["bonus"],
            "ratio_num": [1],
            "ratio_den": [1],
            "old_face_value": [None],
            "new_face_value": [None],
        }
    )
    adj = apply_adjustment(_flat_raw(), ca).sort("date")
    assert adj["close"].to_list() == [50.0, 50.0, 100.0, 100.0]
    assert event_price_multiplier(
        {"action_type": "dividend", "dividend_amount": 5, "prev_close": 100}
    ) == pytest.approx(0.95)


def test_no_events_leaves_prices_unchanged() -> None:
    empty = pl.DataFrame(
        schema={
            "symbol": pl.Utf8,
            "ex_date": pl.Date,
            "action_type": pl.Utf8,
            "old_face_value": pl.Float64,
            "new_face_value": pl.Float64,
            "ratio_num": pl.Int64,
            "ratio_den": pl.Int64,
        }
    )
    adj = apply_adjustment(_flat_raw(), empty)
    assert adj["close"].to_list() == [100.0] * 4


# --- panel invariants (skip if the panel has not been built yet) ---

requires_panel = pytest.mark.skipif(
    not ADJUSTED_PANEL.exists(), reason="adjusted panel not built"
)


@requires_panel
def test_panel_primary_key_unique() -> None:
    df = pl.read_parquet(ADJUSTED_PANEL, columns=["date", "symbol"])
    assert df.height == df.unique(subset=["date", "symbol"]).height


@requires_panel
def test_panel_prices_positive() -> None:
    df = pl.read_parquet(ADJUSTED_PANEL, columns=["open", "high", "low", "close"])
    assert df.filter(
        (pl.col("open") <= 0)
        | (pl.col("high") <= 0)
        | (pl.col("low") <= 0)
        | (pl.col("close") <= 0)
    ).height == 0


@requires_panel
def test_panel_survivorship_canary_present() -> None:
    """Known delisted names absent from the yfinance panel must exist here."""
    syms = set(
        pl.read_parquet(ADJUSTED_PANEL, columns=["symbol"])["symbol"].unique().to_list()
    )
    for name in ["DHFL", "SINTEX", "ALBK", "ANDHRABANK"]:
        assert name in syms, f"survivorship canary missing: {name}"

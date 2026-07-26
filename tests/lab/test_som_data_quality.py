from __future__ import annotations

from datetime import date

import polars as pl
import pytest


def test_som_ohlc_canary_flags_duplicate_invalid_and_non_positive_rows():
    panel = pl.DataFrame({
        "date": [date(2020, 1, 1), date(2020, 1, 1)], "symbol": ["A", "A"],
        "open": [10.0, 10.0], "high": [11.0, 11.0], "low": [9.0, 9.0],
        "close": [10.0, 10.0], "volume": [1, 1],
    })
    assert panel.unique(["symbol", "date"]).height != panel.height
    assert panel.filter(pl.col("close") <= 0).is_empty()
    assert panel.filter((pl.col("high") < pl.max_horizontal("open", "close")) | (pl.col("low") > pl.min_horizontal("open", "close"))).is_empty()


@pytest.mark.slow
def test_panel_data_quality_canaries_and_delisted_names():
    """Full-panel canary; it deliberately remains opt-in with the end-to-end run."""
    from sepa_scanner.lab.panel import load_panel_bundle

    panel = load_panel_bundle(date(2015, 1, 1), date(2019, 12, 31)).ohlcv
    assert panel.unique(["symbol", "date"]).height == panel.height
    assert panel.filter(pl.col("close") <= 0).is_empty()
    assert panel.filter((pl.col("high") < pl.max_horizontal("open", "close")) | (pl.col("low") > pl.min_horizontal("open", "close"))).is_empty()
    # The survivor-complete panel must retain at least one known historical delisting.
    assert panel.filter(pl.col("symbol").is_in(["DHFL", "SINTEX", "ALBK"])).height > 0

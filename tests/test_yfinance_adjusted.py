from __future__ import annotations

from datetime import datetime

import pandas as pd

from sepa_scanner.data_sources.yfinance_adjusted import normalize_history_frame


def test_normalize_history_frame_adjusts_ohlc_from_adj_close_ratio() -> None:
    raw = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [110.0],
            "Low": [95.0],
            "Close": [100.0],
            "Adj Close": [80.0],
            "Volume": [1_000],
            "Dividends": [20.0],
            "Stock Splits": [0.0],
        },
        index=pd.to_datetime([datetime(2024, 1, 1)]),
    )

    frame = normalize_history_frame(raw, symbol="TEST")
    row = frame.to_dicts()[0]

    assert row["symbol"] == "TEST"
    assert row["open"] == 80.0
    assert row["high"] == 88.0
    assert row["low"] == 76.0
    assert row["close"] == 80.0
    assert row["volume"] == 1_000
    assert row["turnover"] == 100_000.0


def test_normalize_history_frame_split_adjusts_historical_volume_not_split_day() -> None:
    raw = pd.DataFrame(
        {
            "Open": [100.0, 55.0],
            "High": [110.0, 60.0],
            "Low": [90.0, 50.0],
            "Close": [100.0, 55.0],
            "Adj Close": [50.0, 55.0],
            "Volume": [100, 400],
            "Dividends": [0.0, 0.0],
            "Stock Splits": [0.0, 2.0],
        },
        index=pd.to_datetime([datetime(2024, 1, 1), datetime(2024, 1, 2)]),
    )

    frame = normalize_history_frame(raw, symbol="TEST")
    rows = frame.sort("date").to_dicts()

    assert rows[0]["close"] == 50.0
    assert rows[0]["volume"] == 200
    assert rows[0]["turnover"] == 10_000.0

    assert rows[1]["close"] == 55.0
    assert rows[1]["volume"] == 400
    assert rows[1]["turnover"] == 22_000.0

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from sepa_scanner.settings import PullbackSettings
from sepa_scanner.strategy.pullback_setup import add_pullback_setup_columns
from sepa_scanner.strategy.ranking import select_top_signals_by_day


def _prepared_leader_frame() -> pl.DataFrame:
    dates = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(30)]
    closes = [100.0 + (offset * 0.5) for offset in range(30)]
    lows = [close - 0.4 for close in closes]
    highs = [close + 0.2 for close in closes]
    return pl.DataFrame(
        {
            "date": dates,
            "symbol": ["LEADER"] * len(dates),
            "open": [close - 0.1 for close in closes],
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [200_000] * len(dates),
            "sma50": [98.0 + (offset * 0.4) for offset in range(30)],
            "ema21": [99.0 + (offset * 0.45) for offset in range(30)],
            "voldry": [0.80] * len(dates),
            "trend_template_pass": [True] * len(dates),
        }
    )


def test_pullback_detector_accepts_established_leader_reversal() -> None:
    result = add_pullback_setup_columns(
        _prepared_leader_frame(),
        PullbackSettings(leader_min_days=20),
    )

    row = result.tail(1).to_dicts()[0]
    assert row["pullback_established_leader"] is True
    assert row["pullback_anchor_rising"] is True
    assert row["pullback_touched_support"] is True
    assert row["pullback_held_structure"] is True
    assert row["pullback_reversal"] is True
    assert row["pullback_setup_pass"] is True
    assert row["pullback_stop"] < row["close"]


def test_pullback_detector_rejects_distribution_volume() -> None:
    frame = _prepared_leader_frame().with_columns(
        pl.when(pl.col("date") == pl.col("date").max())
        .then(pl.lit(1.15))
        .otherwise(pl.col("voldry"))
        .alias("voldry")
    )
    result = add_pullback_setup_columns(
        frame,
        PullbackSettings(pullback_voldry_max=0.90),
    )

    assert result.tail(1)["pullback_setup_pass"][0] is False


def test_pullback_settings_reject_invalid_anchor() -> None:
    with pytest.raises(ValueError, match="anchor_ma"):
        PullbackSettings(anchor_ma="sma20")


def test_breakout_wins_daily_ranking_tie() -> None:
    signals = pl.DataFrame(
        {
            "signal_date": [date(2026, 3, 2), date(2026, 3, 2)],
            "symbol": ["PULL", "BREAK"],
            "signal_type": ["PULLBACK", "BREAKOUT"],
            "rs_rank": [90.0, 90.0],
            "vol_mult": [1.5, 1.5],
            "range10": [0.05, 0.05],
        }
    )

    ranked = select_top_signals_by_day(signals, max_signals_per_day=1)

    assert ranked["signal_type"].to_list() == ["BREAKOUT"]

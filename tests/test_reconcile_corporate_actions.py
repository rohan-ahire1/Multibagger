from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.reconcile_corporate_actions import (
    classify,
    merge_inferred_overrides,
    purpose_events,
)


def _panel(second_date: date, second_close: float) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["TEST", "TEST"],
            "date": [date(2020, 1, 1), second_date],
            "close": [100.0, second_close],
        }
    )


def _raw(second_date: date, second_close: float) -> pl.DataFrame:
    return _panel(second_date, second_close).with_columns(
        pl.lit("INE000A01000").alias("isin")
    )


def _ca(second_date: date, purpose: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["TEST"],
            "ex_date": [second_date],
            "action_type": ["unparsed"],
            "raw_purpose": [purpose],
        }
    )


def _master() -> pl.DataFrame:
    return pl.DataFrame({"symbol": ["TEST"], "instrument_class": ["equity"]})


def test_nse_purpose_factor_allows_normal_ex_date_price_noise() -> None:
    """A parsed factor is corroborated within 25%, not mistaken for the return."""
    second_date = date(2020, 1, 2)
    result = classify(
        _panel(second_date, 4.8),
        _raw(second_date, 4.8),
        _ca(second_date, "Fv Spl-Rs10tore1/Bon-3:2"),
        _master(),
        window=3,
        tolerance=0.03,
        purpose_tolerance=0.25,
        max_gap_days=10,
        max_jump=4.0,
    )

    assert result["parsed_mult"].item() == pytest.approx(0.04)
    assert result["bucket"].item() == "purpose_parsed"


def test_purpose_factor_outside_tolerance_stays_manual() -> None:
    second_date = date(2020, 1, 2)
    result = classify(
        _panel(second_date, 6.0),
        _raw(second_date, 6.0),
        _ca(second_date, "Fv Spl-Rs10tore1/Bon-3:2"),
        _master(),
        window=3,
        tolerance=0.03,
        purpose_tolerance=0.25,
        max_gap_days=10,
        max_jump=4.0,
    )

    assert result["bucket"].item() == "manual"


def test_long_hiatus_is_never_promoted_to_a_corporate_action() -> None:
    second_date = date(2021, 1, 2)
    result = classify(
        _panel(second_date, 10.0),
        _raw(second_date, 10.0),
        _ca(second_date, "Fv Split Rs.10/- To Re.1/"),
        _master(),
        window=3,
        tolerance=0.03,
        purpose_tolerance=0.25,
        max_gap_days=10,
        max_jump=4.0,
    )

    assert result["gap_days"].item() == 367
    assert result["bucket"].item() == "long_hiatus"


def test_demerger_is_recorded_but_never_inferred_as_a_split() -> None:
    second_date = date(2020, 1, 2)
    result = classify(
        _panel(second_date, 5.0),
        _raw(second_date, 5.0),
        _ca(second_date, "Demerger"),
        _master(),
        window=3,
        tolerance=0.03,
        purpose_tolerance=0.25,
        max_gap_days=10,
        max_jump=4.0,
    )

    assert result["bucket"].item() == "complex_action"


def test_separate_bonus_and_split_rows_form_one_replacement_factor() -> None:
    ca = pl.DataFrame(
        {
            "symbol": ["TEST", "TEST"],
            "ex_date": [date(2023, 2, 24), date(2023, 2, 24)],
            "action_type": ["bonus", "unparsed"],
            "ratio_num": [13, None],
            "ratio_den": [10, None],
            "old_face_value": [None, None],
            "new_face_value": [None, None],
            "dividend_amount": [None, None],
            "adjustment_factor": [None, None],
            "raw_purpose": [
                "Bonus 13:10",
                "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share",
            ],
        }
    )

    event = purpose_events(ca).row(0, named=True)
    assert event["parsed_mult"] == pytest.approx(0.1 * (10 / 23))
    # The bonus is already applied by the feed; only the split remains visible.
    assert event["parsed_residual"] == pytest.approx(0.1)
    assert event["parsed_kind"] == "bonus_split"


def test_inferred_write_upserts_matching_event_and_preserves_history() -> None:
    old = pl.DataFrame(
        {
            "symbol": ["OLD", "TEST"],
            "ex_date": [date(2010, 1, 1), date(2020, 1, 2)],
            "action_type": ["split", "bonus"],
            "adjustment_factor": [0.5, 0.5],
            "source": ["purpose_parsed", "isin_corroborated"],
        }
    )
    inferred = pl.DataFrame(
        {
            "symbol": ["TEST"],
            "ex_date": [date(2020, 1, 2)],
            "action_type": ["bonus_split"],
            "adjustment_factor": [0.1],
            "source": ["purpose_parsed"],
        }
    )

    merged = merge_inferred_overrides(old, inferred)
    assert set(merged["symbol"]) == {"OLD", "TEST"}
    test_row = merged.filter(pl.col("symbol") == "TEST").row(0, named=True)
    assert test_row["action_type"] == "bonus_split"
    assert test_row["adjustment_factor"] == pytest.approx(0.1)

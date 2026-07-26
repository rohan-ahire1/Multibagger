"""Ticker-rename collapsing. Merging the wrong two securities splices unrelated
companies into one price series, so the refusals matter as much as the merges.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from sepa_scanner.data_sources.security_identity import (
    apply_canonical_symbols,
    build_rename_map,
    overlapping_renames,
    remap_corporate_actions,
    reused_tickers,
)


def _master(rows: list[tuple[str, str, date, date]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"isin": i, "symbol": s, "first_seen": f, "last_seen": l, "sessions": 10}
            for i, s, f, l in rows
        ]
    )


def test_maps_superseded_ticker_to_the_latest_one():
    master = _master([
        ("INE001A01001", "OLDNAME", date(2010, 1, 1), date(2015, 6, 30)),
        ("INE001A01001", "NEWNAME", date(2015, 7, 1), date(2026, 1, 1)),
    ])
    assert build_rename_map(master) == {"OLDNAME": "NEWNAME"}


def test_resolves_chains_transitively():
    """A -> B -> C must land on C; stopping at B leaves the series still split."""
    master = _master([
        ("INE002A01001", "A", date(2005, 1, 1), date(2010, 1, 1)),
        ("INE002A01001", "B", date(2010, 1, 2), date(2015, 1, 1)),
        ("INE002A01001", "C", date(2015, 1, 2), date(2026, 1, 1)),
    ])
    mapping = build_rename_map(master)
    assert mapping == {"A": "C", "B": "C"}
    assert not set(mapping.values()) & set(mapping.keys())


def test_single_ticker_securities_are_left_alone():
    master = _master([("INE003A01001", "SOLO", date(2010, 1, 1), date(2026, 1, 1))])
    assert build_rename_map(master) == {}


def test_apply_rewrites_only_mapped_symbols():
    frame = pl.DataFrame({"symbol": ["OLDNAME", "UNTOUCHED"], "close": [10.0, 20.0]})
    out = apply_canonical_symbols(frame, {"OLDNAME": "NEWNAME"})
    assert out["symbol"].to_list() == ["NEWNAME", "UNTOUCHED"]
    assert out["close"].to_list() == [10.0, 20.0]


def test_apply_is_a_noop_without_a_mapping():
    frame = pl.DataFrame({"symbol": ["A"], "close": [1.0]})
    assert apply_canonical_symbols(frame, {}).equals(frame)


def test_overlapping_eras_are_reported():
    """Two tickers live at once means the rename story is wrong -- do not merge."""
    master = _master([
        ("INE004A01001", "FIRST", date(2010, 1, 1), date(2015, 1, 1)),
        ("INE004A01001", "SECOND", date(2014, 1, 1), date(2020, 1, 1)),
    ])
    assert overlapping_renames(master).height == 1


def test_reused_tickers_report_the_gap_between_eras():
    """A long gap suggests genuine reuse; a short one, a face-value reissue."""
    master = _master([
        ("INE005A01001", "SHARED", date(2005, 1, 1), date(2008, 1, 1)),
        ("INE006B01001", "SHARED", date(2015, 1, 1), date(2020, 1, 1)),
    ])
    report = reused_tickers(master)
    assert report.height == 1
    assert report.row(0, named=True)["gap_days"] == (date(2015, 1, 1) - date(2008, 1, 1)).days


def test_canonical_action_override_replaces_feed_event_under_new_ticker():
    actions = pl.DataFrame(
        [
            {
                "symbol": "NEWNAME",
                "ex_date": date(2020, 1, 2),
                "action_type": "split",
                "adjustment_factor": 0.2,
                "source": "nse",
            },
            {
                "symbol": "OLDNAME",
                "ex_date": date(2020, 1, 2),
                "action_type": "split",
                "adjustment_factor": 0.21,
                "source": "isin_corroborated",
            },
        ]
    )

    out = remap_corporate_actions(
        actions,
        {"OLDNAME": "NEWNAME"},
        override_sources=("isin_corroborated",),
    )

    assert out.to_dicts() == [
        {
            "symbol": "NEWNAME",
            "ex_date": date(2020, 1, 2),
            "action_type": "split",
            "adjustment_factor": 0.21,
            "source": "isin_corroborated",
        }
    ]


def test_canonical_action_remap_preserves_legitimate_stacked_feed_events():
    actions = pl.DataFrame(
        [
            {
                "symbol": "NEWNAME",
                "ex_date": date(2020, 1, 2),
                "action_type": "split",
                "source": "nse",
            },
            {
                "symbol": "NEWNAME",
                "ex_date": date(2020, 1, 2),
                "action_type": "bonus",
                "source": "nse",
            },
        ]
    )

    out = remap_corporate_actions(
        actions,
        {"OLDNAME": "NEWNAME"},
        override_sources=("isin_corroborated",),
    )

    assert out.height == 2
    assert set(out["action_type"]) == {"split", "bonus"}

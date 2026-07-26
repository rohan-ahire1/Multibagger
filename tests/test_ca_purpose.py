"""The purpose parser decides 247 corporate-action corrections, so it has to be
exact about what it recognises -- and, more importantly, about what it refuses.
"""

from __future__ import annotations

import pytest

from sepa_scanner.data_sources.ca_purpose import parse_purpose


@pytest.mark.parametrize(
    "purpose, multiplier, kind",
    [
        # Face-value splits, in every spelling the feed actually uses.
        ("Fv Split Rs.10/- To Re.1/", 0.1, "split"),
        ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share", 0.1, "split"),
        ("Fv Spl-Rs10tors2", 0.2, "split"),
        ("Fv Split Rs.10/- To Rs.5/-", 0.5, "split"),
        # Bonus: `new` extra shares per `held` held.
        ("Bonus 1:1", 0.5, "bonus"),
        ("Bonus Issue 3:2", 0.4, "bonus"),
        ("Bon-12:1", 1 / 13, "bonus"),
    ],
)
def test_recognised_actions(purpose: str, multiplier: float, kind: str) -> None:
    parsed = parse_purpose(purpose)
    assert parsed is not None
    assert parsed.multiplier == pytest.approx(multiplier, rel=1e-9)
    assert parsed.kind == kind


def test_combined_split_and_bonus_multiplies_both_legs() -> None:
    """A single row can carry both; missing one leaves a residual cliff."""
    parsed = parse_purpose("Fv Spl-Rs10tors2/Bon-12:1")
    assert parsed is not None
    assert parsed.kind == "bonus_split"
    # FV 10->2 is 0.2; bonus 12:1 is 1/13.
    assert parsed.multiplier == pytest.approx(0.2 * (1 / 13), rel=1e-9)


@pytest.mark.parametrize(
    "purpose",
    [
        "Demerger",
        " Demerger",
        "Interest Payment",
        "Annual General Meeting",
        "Scheme Of Arrangement",
        "Buyback Of Shares",
        "",
        None,
    ],
)
def test_refuses_anything_that_is_not_a_split_or_bonus(purpose: str | None) -> None:
    """Guessing here would adjust prices for an event that never moved them."""
    assert parse_purpose(purpose) is None


def test_refuses_degenerate_face_values() -> None:
    """Equal or zero face values are a parse artefact, not a corporate action."""
    assert parse_purpose("Fv Split Rs.10/- To Rs.10/-") is None
    assert parse_purpose("Fv Split Rs.0/- To Rs.1/-") is None


def test_bare_ratio_without_bonus_keyword_is_not_a_bonus() -> None:
    """`10:1` alone could be anything; only an explicit bonus counts."""
    assert parse_purpose("Ratio 10:1") is None

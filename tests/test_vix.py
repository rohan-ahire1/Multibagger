from __future__ import annotations

from sepa_scanner.data_sources.vix import vix_position_multiplier


def test_vix_full_size_below_15() -> None:
    assert vix_position_multiplier(12.0) == 1.0


def test_vix_normal_at_15_to_20() -> None:
    assert vix_position_multiplier(17.0) == 1.0


def test_vix_reduced_at_20_to_25() -> None:
    assert vix_position_multiplier(22.0) == 0.5


def test_vix_no_entries_above_25() -> None:
    assert vix_position_multiplier(30.0) == 0.0


def test_vix_none_returns_full_size() -> None:
    assert vix_position_multiplier(None) == 1.0

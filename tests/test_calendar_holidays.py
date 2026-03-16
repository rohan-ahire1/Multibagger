from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

from sepa_scanner.calendar import (
    is_cm_trading_day,
    load_cm_holidays,
    previous_cm_trading_day,
    _parse_holiday_date,
    _read_cache,
    _write_cache,
)


def test_is_cm_trading_day_weekday_not_holiday() -> None:
    holidays: set[date] = {date(2026, 1, 26)}
    assert is_cm_trading_day(date(2026, 1, 27), holidays) is True


def test_is_cm_trading_day_weekend() -> None:
    holidays: set[date] = set()
    assert is_cm_trading_day(date(2026, 3, 7), holidays) is False  # Saturday


def test_is_cm_trading_day_holiday() -> None:
    holidays: set[date] = {date(2026, 3, 6)}
    assert is_cm_trading_day(date(2026, 3, 6), holidays) is False  # Friday holiday


def test_previous_cm_trading_day_skips_weekend() -> None:
    holidays: set[date] = set()
    assert previous_cm_trading_day(date(2026, 3, 9), holidays) == date(2026, 3, 6)


def test_previous_cm_trading_day_skips_holiday_and_weekend() -> None:
    holidays: set[date] = {date(2026, 3, 6)}
    assert previous_cm_trading_day(date(2026, 3, 9), holidays) == date(2026, 3, 5)


def test_parse_holiday_date_formats() -> None:
    assert _parse_holiday_date("27-Jan-2026") == date(2026, 1, 27)
    assert _parse_holiday_date("2026-01-27") == date(2026, 1, 27)
    assert _parse_holiday_date("27-01-2026") == date(2026, 1, 27)


def test_cache_roundtrip(tmp_path: Path) -> None:
    cache_path = tmp_path / "holidays.json"
    holidays = {date(2026, 1, 26), date(2026, 8, 15), date(2026, 10, 2)}
    _write_cache(cache_path, holidays)
    loaded = _read_cache(cache_path)
    assert loaded == holidays

    raw = json.loads(cache_path.read_text())
    assert raw == sorted(d.isoformat() for d in holidays)


def test_load_cm_holidays_uses_fresh_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "holidays.json"
    holidays = {date(2026, 1, 26)}
    _write_cache(cache_path, holidays)

    session = MagicMock()
    result = load_cm_holidays(session, cache_path, max_age_hours=9999)
    assert result == holidays
    session.get.assert_not_called()


def test_load_cm_holidays_falls_back_to_cache_on_failure(tmp_path: Path) -> None:
    cache_path = tmp_path / "holidays.json"
    holidays = {date(2026, 3, 10)}
    _write_cache(cache_path, holidays)

    import os
    os.utime(cache_path, (0, 0))

    session = MagicMock()
    session.get.side_effect = Exception("network error")
    result = load_cm_holidays(session, cache_path, max_age_hours=0)
    assert result == holidays


def test_load_cm_holidays_returns_empty_when_no_cache_and_failure(tmp_path: Path) -> None:
    cache_path = tmp_path / "holidays.json"
    session = MagicMock()
    session.get.side_effect = Exception("network error")
    result = load_cm_holidays(session, cache_path, max_age_hours=0)
    assert result == set()

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

import requests

LOGGER = logging.getLogger(__name__)

HOLIDAY_API_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
DEFAULT_CACHE_PATH = Path("data/cache/nse_holidays_trading.json")
_DEFAULT_MAX_AGE_HOURS = 24


def is_trading_day(value: date) -> bool:
    return value.weekday() < 5


def previous_trading_days(end_date: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = end_date
    while len(days) < count:
        if is_trading_day(cursor):
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def trading_days_between(start_date: date, end_date: date) -> list[date]:
    if end_date < start_date:
        return []
    days: list[date] = []
    cursor = start_date
    while cursor <= end_date:
        if is_trading_day(cursor):
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def load_cm_holidays(
    session: requests.Session,
    cache_path: Path = DEFAULT_CACHE_PATH,
    max_age_hours: int = _DEFAULT_MAX_AGE_HOURS,
) -> set[date]:
    """Fetch NSE CM trading holidays, caching to disk.

    Falls back to cached data on network failure.  Returns an empty set
    only when both the API and cache are unavailable.
    """
    if _cache_is_fresh(cache_path, max_age_hours):
        return _read_cache(cache_path)

    try:
        holidays = _fetch_cm_holidays(session)
        _write_cache(cache_path, holidays)
        return holidays
    except Exception:
        LOGGER.warning("Failed to fetch NSE holidays; falling back to cache")
        if cache_path.exists():
            return _read_cache(cache_path)
        return set()


def is_cm_trading_day(d: date, cm_holidays: set[date]) -> bool:
    return d.weekday() < 5 and d not in cm_holidays


def previous_cm_trading_day(d: date, cm_holidays: set[date]) -> date:
    cursor = d - timedelta(days=1)
    while not is_cm_trading_day(cursor, cm_holidays):
        cursor -= timedelta(days=1)
    return cursor


def _fetch_cm_holidays(session: requests.Session) -> set[date]:
    resp = session.get(HOLIDAY_API_URL, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    holidays: set[date] = set()
    for entry in payload.get("CM", []):
        raw = entry.get("tradingDate", "")
        try:
            holidays.add(_parse_holiday_date(raw))
        except ValueError:
            LOGGER.debug("Skipping unparseable holiday date: %s", raw)
    return holidays


def _parse_holiday_date(raw: str) -> date:
    """Parse dates like '27-Jan-2026' from the NSE holiday API."""
    from datetime import datetime as _dt

    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return _dt.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(raw)


def _cache_is_fresh(cache_path: Path, max_age_hours: int) -> bool:
    if not cache_path.exists():
        return False
    age_seconds = (date.today() - date.fromtimestamp(cache_path.stat().st_mtime)).total_seconds()
    return age_seconds < max_age_hours * 3600


def _read_cache(cache_path: Path) -> set[date]:
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    return {date.fromisoformat(d) for d in data}


def _write_cache(cache_path: Path, holidays: set[date]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(sorted(d.isoformat() for d in holidays), indent=2),
        encoding="utf-8",
    )

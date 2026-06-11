"""Pre-flight data guards for the live / paper daily runner.

These abort the daily run before any trade sheet is emitted when the input
data is stale (older than the most recent completed trading day) or when a
signal's price looks implausible (a large gap vs the prior close, usually an
unadjusted-split artefact or a bad tick). Emitting a ticket off bad data is
worse than emitting nothing, so the runner treats a tripped guard as fatal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sepa_scanner.calendar import is_cm_trading_day


def most_recent_trading_day(d: date, cm_holidays: set[date]) -> date:
    """Most recent CM trading day on or before ``d``."""
    cursor = d
    while not is_cm_trading_day(cursor, cm_holidays):
        cursor -= timedelta(days=1)
    return cursor


def _trading_day_gap(start: date, end: date, cm_holidays: set[date]) -> int:
    """Number of trading days strictly after ``start`` up to and including ``end``."""
    if end <= start:
        return 0
    gap = 0
    cursor = start + timedelta(days=1)
    while cursor <= end:
        if is_cm_trading_day(cursor, cm_holidays):
            gap += 1
        cursor += timedelta(days=1)
    return gap


@dataclass
class StalenessResult:
    ok: bool
    latest_data_date: date
    expected_date: date
    lag_trading_days: int
    reason: str = ""


def check_data_staleness(
    latest_data_date: date,
    today: date,
    cm_holidays: set[date] | None = None,
    max_lag_trading_days: int = 1,
) -> StalenessResult:
    """Trip if the freshest OHLCV date lags the last completed trading day.

    ``max_lag_trading_days`` grace (default 1) lets a pre-close run on a
    trading day, where the data is still yesterday's, pass; a multi-day gap
    (missed updates, holiday confusion, broken feed) trips the guard.
    """
    holidays = cm_holidays or set()
    expected = most_recent_trading_day(today, holidays)
    lag = _trading_day_gap(latest_data_date, expected, holidays)
    ok = lag <= max_lag_trading_days
    reason = (
        ""
        if ok
        else (
            f"OHLCV is stale: latest data {latest_data_date.isoformat()} is "
            f"{lag} trading day(s) behind the expected {expected.isoformat()} "
            f"(max allowed lag {max_lag_trading_days}). Run --update or check the feed."
        )
    )
    return StalenessResult(ok, latest_data_date, expected, lag, reason)


@dataclass
class PriceSanityResult:
    ok: bool
    flagged: list[tuple[str, float]] = field(default_factory=list)
    reason: str = ""


def check_price_sanity(
    rows: list[tuple[str, float, float]],
    max_gap_pct: float = 0.50,
) -> PriceSanityResult:
    """Flag any (symbol, close, prior_close) whose move exceeds ``max_gap_pct``.

    Used on the symbols backing today's entry signals; an implausible gap
    usually means an unadjusted corporate action or a bad print, not a real
    breakout, so the run aborts rather than sizing off it.
    """
    flagged: list[tuple[str, float]] = []
    for symbol, close, prior_close in rows:
        if prior_close and prior_close > 0 and close > 0:
            gap = abs(close / prior_close - 1.0)
            if gap > max_gap_pct:
                flagged.append((symbol, gap))
    ok = not flagged
    reason = (
        ""
        if ok
        else (
            "Implausible price gap(s) vs prior close: "
            + ", ".join(f"{s} ({g * 100:.0f}%)" for s, g in flagged)
            + ". Likely an unadjusted action or bad tick; aborting."
        )
    )
    return PriceSanityResult(ok, flagged, reason)

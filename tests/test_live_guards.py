from datetime import date

from sepa_scanner.live.guards import (
    check_data_staleness,
    check_price_sanity,
    most_recent_trading_day,
)


def test_most_recent_trading_day_skips_weekend():
    # 2026-06-13 is a Saturday -> most recent trading day is Friday 2026-06-12
    assert most_recent_trading_day(date(2026, 6, 13), set()) == date(2026, 6, 12)


def test_most_recent_trading_day_skips_holiday():
    holidays = {date(2026, 6, 12)}
    # Friday is a holiday -> fall back to Thursday 2026-06-11
    assert most_recent_trading_day(date(2026, 6, 12), holidays) == date(2026, 6, 11)


def test_staleness_ok_when_data_is_current():
    # today Wed 2026-06-10, data also Wed -> lag 0, ok
    res = check_data_staleness(date(2026, 6, 10), date(2026, 6, 10), set())
    assert res.ok and res.lag_trading_days == 0


def test_staleness_ok_within_one_day_grace():
    # today Wed, data Tue (pre-close run) -> lag 1, within default grace
    res = check_data_staleness(date(2026, 6, 9), date(2026, 6, 10), set())
    assert res.ok and res.lag_trading_days == 1


def test_staleness_trips_on_multi_day_gap():
    # today Wed 2026-06-10, data last Thu 2026-06-04 -> several trading days behind
    res = check_data_staleness(date(2026, 6, 4), date(2026, 6, 10), set())
    assert not res.ok
    assert res.lag_trading_days >= 2
    assert "stale" in res.reason.lower()


def test_staleness_ignores_weekend_gap():
    # today Mon 2026-06-15, data Fri 2026-06-12 -> weekend only, lag 1, ok
    res = check_data_staleness(date(2026, 6, 12), date(2026, 6, 15), set())
    assert res.ok and res.lag_trading_days == 1


def test_price_sanity_passes_normal_moves():
    res = check_price_sanity([("AAA", 105.0, 100.0), ("BBB", 90.0, 100.0)])
    assert res.ok and not res.flagged


def test_price_sanity_flags_large_gap():
    res = check_price_sanity([("AAA", 200.0, 100.0)])  # +100% gap
    assert not res.ok
    assert res.flagged[0][0] == "AAA"
    assert "AAA" in res.reason


def test_price_sanity_ignores_missing_prior():
    res = check_price_sanity([("AAA", 200.0, 0.0)])  # no prior -> can't judge
    assert res.ok

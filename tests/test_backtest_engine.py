from __future__ import annotations

from datetime import date

import polars as pl

from sepa_scanner.backtest.engine import simulate_breakout_backtest
from sepa_scanner.backtest.models import BacktestParams, ExitMode


def _signal(
    *,
    symbol: str = "ABC",
    signal_date: date = date(2026, 3, 2),
    entry_ref: float = 101.0,
    stop: float = 95.0,
    pivot: float = 100.0,
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "signal_date": [signal_date],
            "symbol": [symbol],
            "signal_type": ["BREAKOUT"],
            "pivot": [pivot],
            "entry_ref": [entry_ref],
            "stop": [stop],
        }
    )


def _ohlcv(rows: list[dict[str, object]]) -> pl.DataFrame:
    return pl.DataFrame(rows).sort(["symbol", "date"])


def test_backtest_fills_gap_up_at_next_open() -> None:
    signals = _signal()
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 102.5,
                "high": 103.0,
                "low": 100.5,
                "close": 102.0,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 102.0,
                "high": 103.0,
                "low": 101.0,
                "close": 102.5,
            },
        ]
    )

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=2))

    trade = result.trades[0]
    assert trade.entry_date == date(2026, 3, 3)
    assert trade.entry_price == 102.5


def test_backtest_fills_intraday_at_entry_reference() -> None:
    signals = _signal()
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 100.0,
                "high": 101.2,
                "low": 99.8,
                "close": 100.8,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 101.0,
                "high": 102.0,
                "low": 100.5,
                "close": 101.8,
            },
        ]
    )

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=2))

    trade = result.trades[0]
    assert trade.entry_date == date(2026, 3, 3)
    assert trade.entry_price == 101.0


def test_backtest_marks_signal_not_filled_when_trigger_never_hits() -> None:
    signals = _signal()
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 99.0,
                "high": 100.8,
                "low": 98.5,
                "close": 100.0,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 99.5,
                "high": 100.6,
                "low": 98.8,
                "close": 100.1,
            },
        ]
    )

    result = simulate_breakout_backtest(signals, prices, BacktestParams(pending_days=2))

    trade = result.trades[0]
    assert trade.status == "not_filled"
    assert trade.entry_date is None
    assert trade.exit_date is None


def test_backtest_exits_at_stop_when_low_breaks_after_entry() -> None:
    signals = _signal()
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 100.0,
                "high": 101.2,
                "low": 99.8,
                "close": 100.8,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 100.5,
                "high": 100.7,
                "low": 94.5,
                "close": 95.2,
            },
        ]
    )

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=5))

    trade = result.trades[0]
    assert trade.status == "stopped"
    assert trade.exit_date == date(2026, 3, 4)
    assert trade.exit_price == 95.0


def test_backtest_exits_gap_below_stop_at_open() -> None:
    signals = _signal()
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 100.0,
                "high": 101.5,
                "low": 99.8,
                "close": 101.1,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 93.5,
                "high": 94.2,
                "low": 92.8,
                "close": 93.0,
            },
        ]
    )

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=5))

    trade = result.trades[0]
    assert trade.status == "stopped"
    assert trade.exit_price == 93.5


def test_backtest_gap_stop_counts_open_but_not_post_exit_intraday_range_for_excursions() -> None:
    signals = _signal()
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 100.0,
                "high": 101.5,
                "low": 99.9,
                "close": 101.0,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 94.0,
                "high": 110.0,
                "low": 90.0,
                "close": 109.0,
            },
        ]
    )

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=5))

    trade = result.trades[0]
    assert trade.status == "stopped"
    assert trade.exit_price == 94.0
    assert trade.mae_price == 94.0
    assert trade.mfe_price == 101.5
    assert trade.mae_r == (101.0 - 94.0) / 6.0


def test_backtest_gap_target_counts_open_but_not_post_exit_intraday_range_for_excursions() -> None:
    signals = _signal()
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 100.0,
                "high": 101.5,
                "low": 99.9,
                "close": 101.0,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 108.0,
                "high": 120.0,
                "low": 80.0,
                "close": 82.0,
            },
        ]
    )

    result = simulate_breakout_backtest(
        signals,
        prices,
        BacktestParams(target_r=1.0, max_hold_days=5, exit_mode=ExitMode.V1_FIXED),
    )

    trade = result.trades[0]
    assert trade.status == "target"
    assert trade.exit_price == 108.0
    assert trade.mae_price == 99.9
    assert trade.mfe_price == 108.0
    assert trade.mfe_r == (108.0 - 101.0) / 6.0


def test_backtest_uses_worse_outcome_when_stop_and_target_hit_same_day() -> None:
    signals = _signal(stop=96.0)
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 100.0,
                "high": 101.4,
                "low": 99.7,
                "close": 101.0,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 100.8,
                "high": 111.0,
                "low": 95.5,
                "close": 106.0,
            },
        ]
    )

    result = simulate_breakout_backtest(signals, prices, BacktestParams(target_r=2.0, max_hold_days=5, exit_mode=ExitMode.V1_FIXED))

    trade = result.trades[0]
    assert trade.status == "stopped"
    assert trade.exit_price == 96.0


def test_backtest_uses_time_stop_when_no_other_exit_occurs() -> None:
    signals = _signal()
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 100.0,
                "high": 101.4,
                "low": 99.9,
                "close": 101.1,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 101.0,
                "high": 101.3,
                "low": 100.1,
                "close": 100.8,
            },
            {
                "date": date(2026, 3, 5),
                "symbol": "ABC",
                "open": 100.7,
                "high": 101.2,
                "low": 100.4,
                "close": 100.6,
            },
        ]
    )

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=3))

    trade = result.trades[0]
    assert trade.status == "time_stop"
    assert trade.exit_date == date(2026, 3, 5)
    assert trade.exit_price == 100.6


def test_backtest_skips_later_signal_when_same_symbol_trade_is_still_open() -> None:
    signals = pl.concat(
        [
            _signal(signal_date=date(2026, 3, 2)),
            _signal(signal_date=date(2026, 3, 4)),
        ],
        how="vertical",
    )
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 100.0,
                "high": 101.3,
                "low": 99.8,
                "close": 101.0,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 101.0,
                "high": 101.4,
                "low": 100.5,
                "close": 101.2,
            },
            {
                "date": date(2026, 3, 5),
                "symbol": "ABC",
                "open": 101.1,
                "high": 101.6,
                "low": 100.7,
                "close": 101.4,
            },
            {
                "date": date(2026, 3, 6),
                "symbol": "ABC",
                "open": 101.3,
                "high": 101.7,
                "low": 100.9,
                "close": 101.5,
            },
        ]
    )

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=4))

    assert len(result.trades) == 2
    assert result.trades[0].status == "time_stop"
    assert result.trades[1].status == "skipped_overlap"


def test_backtest_allows_same_symbol_signal_on_prior_trade_exit_date() -> None:
    signals = pl.concat(
        [
            _signal(signal_date=date(2026, 3, 2)),
            _signal(signal_date=date(2026, 3, 4)),
        ],
        how="vertical",
    )
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 100.0,
                "high": 101.5,
                "low": 99.8,
                "close": 101.0,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 94.0,
                "high": 101.0,
                "low": 93.0,
                "close": 100.8,
            },
            {
                "date": date(2026, 3, 5),
                "symbol": "ABC",
                "open": 100.0,
                "high": 101.5,
                "low": 99.8,
                "close": 101.2,
            },
            {
                "date": date(2026, 3, 6),
                "symbol": "ABC",
                "open": 101.1,
                "high": 101.4,
                "low": 100.5,
                "close": 100.9,
            },
        ]
    )

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=2))

    assert len(result.trades) == 2
    assert result.trades[0].status == "stopped"
    assert result.trades[0].exit_date == date(2026, 3, 4)
    assert result.trades[1].status == "time_stop"
    assert result.trades[1].entry_date == date(2026, 3, 5)


def test_backtest_force_closes_entered_trade_at_last_close_when_history_ends() -> None:
    signals = _signal()
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 100.0,
                "high": 101.2,
                "low": 99.8,
                "close": 101.0,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 101.5,
                "high": 102.0,
                "low": 100.0,
                "close": 100.5,
            },
        ]
    )

    result = simulate_breakout_backtest(signals, prices, BacktestParams(max_hold_days=10))

    trade = result.trades[0]
    assert trade.status == "incomplete_data"
    assert trade.entry_date == date(2026, 3, 3)
    assert trade.exit_date == date(2026, 3, 4)
    assert trade.exit_price == 100.5
    assert trade.holding_days == 2
    assert trade.r_multiple == (100.5 - 101.0) / 6.0


def test_backtest_trail_10d_uses_rolling_low_after_activation() -> None:
    signals = _signal()
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 100.0,
                "high": 101.2,
                "low": 99.8,
                "close": 101.0,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 101.0,
                "high": 108.0,
                "low": 100.5,
                "close": 107.0,
            },
            {
                "date": date(2026, 3, 5),
                "symbol": "ABC",
                "open": 106.5,
                "high": 106.8,
                "low": 101.0,
                "close": 102.0,
            },
            {
                "date": date(2026, 3, 6),
                "symbol": "ABC",
                "open": 101.5,
                "high": 102.0,
                "low": 99.0,
                "close": 99.5,
            },
        ]
    )

    result = simulate_breakout_backtest(
        signals,
        prices,
        BacktestParams(max_hold_days=4, exit_mode=ExitMode.V2_TRAIL_10D, trail_activation_r=1.0),
    )

    trade = result.trades[0]
    assert trade.status == "stopped"
    assert trade.exit_date == date(2026, 3, 5)
    assert trade.exit_price == 101.0


def test_backtest_trail_21ema_uses_ema_stop_after_activation() -> None:
    signals = _signal()
    prices = _ohlcv(
        [
            {
                "date": date(2026, 3, 3),
                "symbol": "ABC",
                "open": 100.0,
                "high": 101.2,
                "low": 99.8,
                "close": 101.0,
            },
            {
                "date": date(2026, 3, 4),
                "symbol": "ABC",
                "open": 101.0,
                "high": 108.0,
                "low": 100.5,
                "close": 107.0,
            },
            {
                "date": date(2026, 3, 5),
                "symbol": "ABC",
                "open": 106.5,
                "high": 106.8,
                "low": 101.0,
                "close": 102.0,
            },
            {
                "date": date(2026, 3, 6),
                "symbol": "ABC",
                "open": 101.5,
                "high": 102.0,
                "low": 99.0,
                "close": 99.5,
            },
        ]
    )

    result = simulate_breakout_backtest(
        signals,
        prices,
        BacktestParams(max_hold_days=4, exit_mode=ExitMode.V2_TRAIL_21EMA, trail_activation_r=1.0),
    )

    trade = result.trades[0]
    assert trade.status == "stopped"
    assert trade.exit_date == date(2026, 3, 5)
    assert trade.exit_price == 101.54545454545455


def test_backtest_v3_trail_pct_exits_on_percentage_retracement() -> None:
    """V3_TRAIL_PCT: after +2R activation, exit when price drops 20% from highest close."""
    signals = _signal(entry_ref=101.0, stop=95.0)  # risk = 6.0, +2R = 113.0
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC",
             "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0},
            # Day 2: price surges past +2R (113.0)
            {"date": date(2026, 3, 4), "symbol": "ABC",
             "open": 101.0, "high": 115.0, "low": 100.5, "close": 114.0},
            # Day 3: continues higher
            {"date": date(2026, 3, 5), "symbol": "ABC",
             "open": 114.0, "high": 120.0, "low": 113.0, "close": 118.0},
            # Day 4: continues higher — highest_close = 124.0
            {"date": date(2026, 3, 6), "symbol": "ABC",
             "open": 117.0, "high": 125.0, "low": 116.0, "close": 124.0},
            # Day 5: price drops below breakeven stop (101.0)
            # trail = 124 * 0.80 = 99.2, but active_stop = max(101.0, 99.2) = 101.0
            {"date": date(2026, 3, 7), "symbol": "ABC",
             "open": 123.0, "high": 123.5, "low": 100.0, "close": 100.5},
        ]
    )

    result = simulate_breakout_backtest(
        signals, prices,
        BacktestParams(
            max_hold_days=10,
            exit_mode=ExitMode.V3_TRAIL_PCT,
            trail_activation_r=2.0,
            trail_pct=0.20,
            dead_money_days=None,
        ),
    )

    trade = result.trades[0]
    assert trade.status == "stopped"
    # Trail stop = max(breakeven=101.0, 124.0 * 0.80=99.2) = 101.0
    assert trade.exit_price == 101.0


def test_backtest_v3_trail_pct_uses_highest_close_for_trail() -> None:
    """V3_TRAIL_PCT: trail stop ratchets up with highest close, never down."""
    signals = _signal(entry_ref=101.0, stop=95.0)  # risk = 6.0
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC",
             "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0},
            # Day 2: surges past +2R (113.0)
            {"date": date(2026, 3, 4), "symbol": "ABC",
             "open": 101.0, "high": 115.0, "low": 100.5, "close": 114.0},
            # Day 3: highest_close = 150
            {"date": date(2026, 3, 5), "symbol": "ABC",
             "open": 140.0, "high": 155.0, "low": 139.0, "close": 150.0},
            # Day 4: drops — trail = 150 * 0.80 = 120.0
            # low = 119.0 <= 120.0 → stopped at 120.0
            {"date": date(2026, 3, 6), "symbol": "ABC",
             "open": 145.0, "high": 146.0, "low": 119.0, "close": 120.0},
        ]
    )

    result = simulate_breakout_backtest(
        signals, prices,
        BacktestParams(
            max_hold_days=10,
            exit_mode=ExitMode.V3_TRAIL_PCT,
            trail_activation_r=2.0,
            trail_pct=0.20,
            dead_money_days=None,
        ),
    )

    trade = result.trades[0]
    assert trade.status == "stopped"
    # Trail = 150 * 0.80 = 120.0; on day 4, close=120.0 <= trail after ratchet
    # Actually: day 3 close=150, trail ratchets to 120.0. Day 4 low=119 <= 120 → stopped
    assert trade.exit_price == 120.0


def test_backtest_breakeven_at_1r_without_trail_activation() -> None:
    """At +1R, stop moves to breakeven but trail does NOT activate."""
    signals = _signal(entry_ref=101.0, stop=95.0)  # risk = 6.0
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC",
             "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0},
            # Day 2: reaches +1R (107.0) but not +2R (113.0)
            {"date": date(2026, 3, 4), "symbol": "ABC",
             "open": 101.0, "high": 108.0, "low": 100.5, "close": 107.5},
            # Day 3: drops back — stop should be at breakeven (101.0), not original (95.0)
            {"date": date(2026, 3, 5), "symbol": "ABC",
             "open": 106.0, "high": 106.5, "low": 100.5, "close": 101.0},
        ]
    )

    result = simulate_breakout_backtest(
        signals, prices,
        BacktestParams(
            max_hold_days=3,
            exit_mode=ExitMode.V3_TRAIL_PCT,
            trail_activation_r=2.0,
            trail_pct=0.20,
            dead_money_days=None,
        ),
    )

    trade = result.trades[0]
    # Should be time_stop at day 3 close of 101.0
    # Key: stop is at breakeven (101.0), not original (95.0)
    # Day 3: low=100.5 > 95.0 (would survive original stop) but close=101.0 >= 101.0 (breakeven)
    # The stop is at 101.0 (breakeven), low=100.5 < 101.0, so it DOES hit the stop
    assert trade.status == "stopped"
    assert trade.exit_price == 101.0


def test_backtest_breakeven_not_triggered_below_1r() -> None:
    """If price never reaches +1R, stop stays at original level."""
    signals = _signal(entry_ref=101.0, stop=95.0)  # risk = 6.0, +1R = 107.0
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC",
             "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0},
            # Day 2: high = 106.5, just below +1R (107.0)
            {"date": date(2026, 3, 4), "symbol": "ABC",
             "open": 101.0, "high": 106.5, "low": 100.5, "close": 105.0},
            # Day 3: price drops to 96.0 — above original stop (95.0), so NOT stopped
            {"date": date(2026, 3, 5), "symbol": "ABC",
             "open": 104.0, "high": 104.5, "low": 96.0, "close": 97.0},
        ]
    )

    result = simulate_breakout_backtest(
        signals, prices,
        BacktestParams(
            max_hold_days=3,
            exit_mode=ExitMode.V3_TRAIL_PCT,
            trail_activation_r=2.0,
            trail_pct=0.20,
            dead_money_days=None,
        ),
    )

    trade = result.trades[0]
    # Low = 96 > 95 (original stop), so not stopped. Time stop at day 3.
    assert trade.status == "time_stop"
    assert trade.exit_price == 97.0


def test_backtest_breakeven_applies_to_v2_trail_modes() -> None:
    """Breakeven at +1R also applies to V2 trail modes, not just V3."""
    signals = _signal(entry_ref=101.0, stop=95.0)  # risk = 6.0
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC",
             "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0},
            # Day 2: reaches +1R (107.0) but not +2R (113.0)
            {"date": date(2026, 3, 4), "symbol": "ABC",
             "open": 101.0, "high": 108.0, "low": 100.5, "close": 107.5},
            # Day 3: drops — stop at breakeven (101.0)
            {"date": date(2026, 3, 5), "symbol": "ABC",
             "open": 106.0, "high": 106.5, "low": 100.5, "close": 101.0},
        ]
    )

    result = simulate_breakout_backtest(
        signals, prices,
        BacktestParams(
            max_hold_days=3,
            exit_mode=ExitMode.V2_TRAIL_21EMA,
            trail_activation_r=2.0,
            dead_money_days=None,
        ),
    )

    trade = result.trades[0]
    # Breakeven at 101.0 should trigger since low=100.5 < 101.0
    assert trade.status == "stopped"
    assert trade.exit_price == 101.0


def test_backtest_dead_money_exit_when_no_progress() -> None:
    """Exit at dead_money_days if position hasn't reached +1R."""
    signals = _signal(entry_ref=101.0, stop=95.0)  # risk = 6, +1R = 107.0
    # 4 bars of flat trading — never reaches +1R
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC",
             "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0},
            {"date": date(2026, 3, 4), "symbol": "ABC",
             "open": 101.0, "high": 102.0, "low": 100.5, "close": 101.5},
            {"date": date(2026, 3, 5), "symbol": "ABC",
             "open": 101.5, "high": 102.5, "low": 100.0, "close": 101.0},
            {"date": date(2026, 3, 6), "symbol": "ABC",
             "open": 101.0, "high": 102.0, "low": 99.5, "close": 100.8},
        ]
    )

    result = simulate_breakout_backtest(
        signals, prices,
        BacktestParams(max_hold_days=10, dead_money_days=3),
    )

    trade = result.trades[0]
    assert trade.status == "dead_money"
    assert trade.holding_days == 3
    assert trade.exit_date == date(2026, 3, 5)
    assert trade.exit_price == 101.0


def test_backtest_dead_money_disabled_when_none() -> None:
    """dead_money_days=None disables the feature."""
    signals = _signal(entry_ref=101.0, stop=95.0)
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC",
             "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0},
            {"date": date(2026, 3, 4), "symbol": "ABC",
             "open": 101.0, "high": 102.0, "low": 100.5, "close": 101.5},
            {"date": date(2026, 3, 5), "symbol": "ABC",
             "open": 101.5, "high": 102.5, "low": 100.0, "close": 101.0},
        ]
    )

    result = simulate_breakout_backtest(
        signals, prices,
        BacktestParams(max_hold_days=3, dead_money_days=None),
    )

    trade = result.trades[0]
    # Should reach time_stop, not dead_money
    assert trade.status == "time_stop"
    assert trade.holding_days == 3


def test_backtest_dead_money_skipped_when_breakeven_reached() -> None:
    """If +1R is reached (breakeven activated), dead money does not trigger."""
    signals = _signal(entry_ref=101.0, stop=95.0)  # risk = 6, +1R = 107.0
    prices = _ohlcv(
        [
            {"date": date(2026, 3, 3), "symbol": "ABC",
             "open": 100.0, "high": 101.5, "low": 99.8, "close": 101.0},
            # Day 2: reaches +1R
            {"date": date(2026, 3, 4), "symbol": "ABC",
             "open": 101.0, "high": 108.0, "low": 100.5, "close": 107.5},
            # Day 3: still holding, past dead_money_days=2
            {"date": date(2026, 3, 5), "symbol": "ABC",
             "open": 107.0, "high": 108.0, "low": 105.0, "close": 106.0},
        ]
    )

    result = simulate_breakout_backtest(
        signals, prices,
        BacktestParams(max_hold_days=3, dead_money_days=2),
    )

    trade = result.trades[0]
    # +1R was reached on day 2, so dead_money should NOT trigger on day 3
    assert trade.status != "dead_money"
    # Should be time_stop at day 3
    assert trade.status == "time_stop"

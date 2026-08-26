from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sys

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.daily_factors.engine import build_pit_momentum_targets


def _lifecycle_panel(*, mutate_future: bool = False) -> pl.DataFrame:
    start = date(2020, 1, 1)
    rows: list[dict[str, object]] = []
    for offset in range(430):
        current = start + timedelta(days=offset)
        for symbol in ("A", "B"):
            # B ends historically; its earlier rows must remain eligible.
            if symbol == "B" and offset > 360:
                continue
            close = 100.0 + offset if symbol == "A" else 200.0 - offset * 0.1
            turnover = 10_000.0 if symbol == "A" else 20_000.0
            if mutate_future and offset > 350:
                close *= 100.0
                turnover *= 1_000.0
            rows.append(
                {
                    "date": current,
                    "symbol": symbol,
                    "close": close,
                    "volume": 100,
                    "turnover": turnover,
                }
            )
        # IPO after the first signal date.  It must not be back-filled.
        if offset >= 370:
            rows.append(
                {
                    "date": current,
                    "symbol": "FUTURE_IPO",
                    "close": 50.0 + offset,
                    "volume": 100,
                    "turnover": 1_000_000.0,
                }
            )
    return pl.DataFrame(rows)


def _targets(panel: pl.DataFrame):
    return build_pit_momentum_targets(
        panel,
        start=date(2020, 11, 1),
        end=date(2021, 3, 31),
        universe_size=2,
        selection_pct=1.0,
        liquidity_lookback=60,
        min_history_sessions=200,
        min_market_symbols=1,
    )


def test_future_prices_do_not_change_historical_momentum_targets() -> None:
    baseline, _, _ = _targets(_lifecycle_panel())
    changed, _, _ = _targets(_lifecycle_panel(mutate_future=True))
    signal = date(2020, 11, 30)
    assert baseline[signal] == changed[signal]


def test_dead_security_remains_historically_selectable() -> None:
    targets, _, audit = _targets(_lifecycle_panel())
    signal = date(2020, 11, 30)
    assert "B" in targets[signal]
    assert audit.filter((pl.col("signal_date") == signal) & (pl.col("symbol") == "B")).height == 1


def test_future_ipo_never_appears_before_listing() -> None:
    targets, _, audit = _targets(_lifecycle_panel())
    assert all("FUTURE_IPO" not in symbols for symbols in targets.values())
    assert audit.filter(pl.col("symbol") == "FUTURE_IPO").is_empty()


def test_pit_audit_retains_observation_date_before_signal_date() -> None:
    _, _, audit = _targets(_lifecycle_panel())
    assert audit.filter(pl.col("observation_date") >= pl.col("signal_date")).is_empty()


def test_shifted_rebalance_still_uses_strict_prior_observation() -> None:
    _, _, audit = build_pit_momentum_targets(
        _lifecycle_panel(),
        start=date(2020, 11, 1),
        end=date(2021, 3, 31),
        universe_size=2,
        selection_pct=1.0,
        liquidity_lookback=60,
        min_history_sessions=200,
        rebalance_shift=3,
        min_market_symbols=1,
    )
    assert audit.filter(pl.col("observation_date") >= pl.col("signal_date")).is_empty()

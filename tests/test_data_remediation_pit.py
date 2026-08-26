"""Regression tests for the deterministic research-data remediation.

The fixtures are deliberately synthetic: their only purpose is to prove that
historical outputs are invariant to information first available after the
evaluation date.  They do not infer any market reference data.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from sepa_scanner.data_sources.adjustment import apply_adjustment, validate_share_action_ratios
from sepa_scanner.lab.engines.portfolio_order_engine import (
    PortfolioOrderConfig,
    run_portfolio_order_engine,
)
from sepa_scanner.storage.parquet_store import ParquetOhlcvStore
from strategies.mean_reversion.signal_engine import scan_mean_reversion


def _raw_panel(*, symbol: str = "AAA", days: int = 55, start: date = date(2020, 1, 2)) -> pl.DataFrame:
    dates = [start + timedelta(days=index) for index in range(days)]
    closes = [100.0 + index for index in range(days)]
    return pl.DataFrame(
        {
            "date": dates,
            "symbol": [symbol] * days,
            "open": closes,
            "high": [value + 1.0 for value in closes],
            "low": [value - 1.0 for value in closes],
            "close": closes,
            "volume": [10_000] * days,
            "turnover": [value * 10_000 for value in closes],
        }
    )


def _future_split(ex_date: date = date(2027, 1, 2)) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["AAA"],
            "ex_date": [ex_date],
            "action_type": ["split"],
            "ratio_num": [None],
            "ratio_den": [None],
            "old_face_value": [10.0],
            "new_face_value": [5.0],
        }
    )


def _signal_rows(frame: pl.DataFrame, cutoff: date) -> pl.DataFrame:
    return scan_mean_reversion(
        frame,
        rsi_period=2,
        rsi_threshold=101.0,
        sma_trend_period=2,
        min_adv_inr=0.0,
        min_price=0.0,
    ).filter(pl.col("signal_date") <= cutoff)


def test_post_dataset_action_does_not_modify_canonical_adjusted_build() -> None:
    """An ex-date beyond the final raw bar must be excluded before adjustment."""
    raw = _raw_panel(days=3)
    adjusted = apply_adjustment(raw, _future_split())

    assert adjusted["close"].to_list() == raw["close"].to_list()


def test_builder_excludes_post_dataset_actions_before_writing_any_canonical_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import nse_build_adjusted_panel as builder

    action_path = tmp_path / "corporate_actions.parquet"
    _future_split().write_parquet(action_path)
    monkeypatch.setattr(builder, "CA_PATH", action_path)
    monkeypatch.setattr(builder, "CA_OVERRIDES_PATH", tmp_path / "no-overrides.csv")

    selected = builder._load_ca(False, raw=_raw_panel(days=3), dataset_max_date=date(2020, 1, 4))
    assert selected.is_empty()


def test_future_corporate_action_cannot_change_2020_pit_features_or_signals() -> None:
    from sepa_scanner.data_sources.pit import build_pit_features

    raw = _raw_panel()
    cutoff = date(2020, 2, 20)
    baseline = build_pit_features(raw, corporate_actions=pl.DataFrame())
    with_future_action = build_pit_features(raw, corporate_actions=_future_split())

    assert baseline.filter(pl.col("date") <= cutoff).equals(
        with_future_action.filter(pl.col("date") <= cutoff)
    )
    assert _signal_rows(baseline, cutoff).equals(_signal_rows(with_future_action, cutoff))


def test_future_prices_do_not_change_historical_signals() -> None:
    from sepa_scanner.data_sources.pit import build_pit_features

    raw = _raw_panel()
    cutoff = raw["date"][-1]
    future = _raw_panel(days=5, start=date(2027, 1, 2)).with_columns(
        pl.lit(10_000_000.0).alias("close"),
        pl.lit(10_000_000).alias("volume"),
        pl.lit(10_000_000_000_000.0).alias("turnover"),
    )
    baseline = build_pit_features(raw, corporate_actions=pl.DataFrame())
    extended = build_pit_features(pl.concat([raw, future], how="vertical_relaxed"), corporate_actions=pl.DataFrame())

    assert _signal_rows(baseline, cutoff).equals(_signal_rows(extended, cutoff))


def test_signal_generation_rejects_explicitly_ex_post_columns() -> None:
    frame = _raw_panel().with_columns(pl.lit(0.42).alias("fwd_return_20d"))

    with pytest.raises(ValueError, match="ex-post"):
        _signal_rows(frame, date(2020, 2, 20))


def _lifecycle_panel(*, add_future_history: bool = False) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    # A survives throughout; B stops in 2010; C begins in 2015.
    start = date(2005, 1, 3)
    end = date(2026, 1, 2)
    b_end = date(2010, 12, 31)
    c_start = date(2015, 1, 2)
    current = start
    while current <= end:
        for symbol in ("A", "B") if current <= b_end else ("A",):
            rows.append({"date": current, "symbol": symbol, "close": 100.0, "volume": 1000, "turnover": 100_000.0})
        if current >= c_start:
            rows.append({"date": current, "symbol": "C", "close": 100.0, "volume": 1000, "turnover": 100_000.0})
        current += timedelta(days=1)
    if add_future_history:
        for index in range(30):
            current = date(2027, 1, 2) + timedelta(days=index)
            rows.append({"date": current, "symbol": "A", "close": 1_000_000.0, "volume": 1_000_000, "turnover": 1e12})
    return pl.DataFrame(rows)


def test_historical_liquidity_universe_keeps_dead_security_and_excludes_future_ipo() -> None:
    from strategies.mean_reversion.universe import historical_liquidity_universe

    panel = _lifecycle_panel()
    eligible_2008 = historical_liquidity_universe(
        panel, as_of=date(2008, 1, 1), size=10, lookback_sessions=50, min_history_sessions=200
    )
    eligible_2014 = historical_liquidity_universe(
        panel, as_of=date(2014, 12, 31), size=10, lookback_sessions=50, min_history_sessions=200
    )

    assert "B" in eligible_2008
    assert "C" not in eligible_2008
    assert "C" not in eligible_2014


def test_future_prices_volumes_and_delisting_do_not_change_historical_eligibility() -> None:
    from strategies.mean_reversion.universe import historical_liquidity_universe

    cutoff = date(2008, 1, 1)
    kwargs = {"as_of": cutoff, "size": 10, "lookback_sessions": 50, "min_history_sessions": 200}
    baseline = historical_liquidity_universe(_lifecycle_panel(), **kwargs)
    with_future = historical_liquidity_universe(_lifecycle_panel(add_future_history=True), **kwargs)

    assert baseline == with_future
    assert "B" in baseline  # later disappearance does not make B ineligible at T


def test_pit_identity_preserves_renames_splits_reused_symbols_and_marks_unknowns() -> None:
    from sepa_scanner.data_sources.pit import build_processed_panel

    panel = pl.DataFrame(
        {
            "date": [date(2010, 1, 1), date(2011, 1, 1), date(2012, 1, 1), date(2013, 1, 1)],
            "symbol": ["OLD", "NEW", "REUSED", "REUSED"],
            "isin": ["INE000A01001", "INE000A01001", "INE000B01001", None],
            "series": ["EQ", "EQ", "EQ", "EQ"],
            "open": [10.0] * 4,
            "high": [10.0] * 4,
            "low": [10.0] * 4,
            "close": [10.0] * 4,
            "volume": [1] * 4,
            "turnover": [10.0] * 4,
        }
    )
    processed = build_processed_panel(panel)

    assert processed.filter(pl.col("symbol").is_in(["OLD", "NEW"]))["security_id"].n_unique() == 1
    reused = processed.filter(pl.col("symbol") == "REUSED")
    assert reused["security_id"].n_unique() == 2
    unknown = reused.filter(pl.col("isin").is_null()).row(0, named=True)
    assert unknown["security_id"].startswith("fallback:")
    assert unknown["identity_quality"] == "unverified"


def test_adjustment_uses_isin_across_a_rename_without_merging_reused_ticker() -> None:
    raw = pl.DataFrame(
        {
            "date": [date(2020, 1, 1), date(2021, 1, 2), date(2020, 1, 1), date(2021, 1, 2)],
            "symbol": ["OLD", "NEW", "REUSED", "REUSED"],
            "isin": ["INE000A01001", "INE000A01001", "INE000B01001", "INE000C01001"],
            "open": [100.0] * 4, "high": [100.0] * 4, "low": [100.0] * 4,
            "close": [100.0] * 4, "volume": [100] * 4, "turnover": [10_000.0] * 4,
        }
    )
    actions = pl.DataFrame(
        {
            "symbol": ["NEW", "REUSED"], "isin": ["INE000A01001", "INE000C01001"],
            "ex_date": [date(2021, 1, 2), date(2021, 1, 2)], "action_type": ["split", "split"],
            "ratio_num": [None, None], "ratio_den": [None, None],
            "old_face_value": [10.0, 10.0], "new_face_value": [5.0, 5.0],
        }
    )
    adjusted = apply_adjustment(raw, actions)

    assert adjusted.filter(pl.col("symbol") == "OLD")["close"].to_list() == [50.0]
    # The different ISIN under a reused ticker must not receive the later ISIN's factor.
    assert adjusted.filter((pl.col("symbol") == "REUSED") & (pl.col("isin") == "INE000B01001"))["close"].to_list() == [100.0]


def test_identity_keyed_panel_refuses_symbol_only_upsert(tmp_path: Path) -> None:
    from sepa_scanner.data_sources.pit import build_processed_panel

    store = ParquetOhlcvStore(tmp_path)
    pit = build_processed_panel(_raw_panel(days=2))
    store.upsert(pit)

    with pytest.raises(ValueError, match="identity-keyed"):
        store.upsert(_raw_panel(days=1))


def test_split_bonus_validation_reports_raw_ratio_and_never_repairs_mismatch() -> None:
    raw = _raw_panel(days=2)
    actions = _future_split(ex_date=date(2020, 1, 3))
    result = validate_share_action_ratios(raw, actions, tolerance=0.15)

    row = result.row(0, named=True)
    assert row["security_id"] == "fallback:AAA"
    assert row["expected_ratio"] == 0.5
    assert row["observed_raw_ratio"] == pytest.approx(1.01)
    assert row["validation_status"] == "WARNING"
    assert row["classification"] == "unresolved"


def test_corporate_action_parser_keeps_explicit_metadata_and_leaves_unknown_unparsed() -> None:
    from scripts.nse_corporate_actions_fetch import parse_subject

    split = parse_subject("Face Value Split From Rs.10/- To Re.1/-")
    unknown = parse_subject("Board meeting regarding capital reorganisation")

    assert split["action_type"] == "split"
    assert split["parser_rule"]
    assert split["parser_version"]
    assert split["confidence"] == "explicit"
    assert unknown["action_type"] == "unparsed"
    assert unknown["confidence"] == "unverified"
    assert parse_subject("Dividend Rs./- per share")["action_type"] == "unparsed"


def test_empty_corporate_action_year_is_reported_as_source_gap() -> None:
    from scripts.nse_corporate_actions_fetch import coverage_by_year

    actions = pl.DataFrame({"ex_date": [date(2014, 1, 2), date(2016, 1, 2)]})
    coverage = coverage_by_year(actions, start_year=2014, end_year=2016)
    assert coverage.filter(pl.col("year") == 2015).row(0, named=True)["status"] == "source_gap"


def _order_panel(include_future_bar: bool) -> pl.DataFrame:
    days = [date(2020, 1, 1) + timedelta(days=index) for index in range(4)]
    rows: list[dict[str, object]] = []
    for current in days:
        rows.append({"date": current, "symbol": "MARKET", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 10_000, "turnover": 100_000.0})
    for current in days[:2] + (days[3:] if include_future_bar else []):
        rows.append({"date": current, "symbol": "A", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 10_000, "turnover": 100_000.0})
    return pl.DataFrame(rows)


def _order_decision() -> pl.DataFrame:
    return pl.DataFrame(
        [{"decision_date": date(2020, 1, 1), "symbol": "A", "score": 1.0, "rank": 1,
          "rank_pct": 0.1, "atr20": 1.0, "trend_ok": True, "gap_ok": True,
          "is_member": True, "close": 10.0}]
    )


def _order_config() -> PortfolioOrderConfig:
    return PortfolioOrderConfig(
        initial_capital=10_000.0, risk_factor=0.01,
        market_regime=pl.DataFrame({"date": [date(2020, 1, 1)], "allows_new_buys": [True]}),
        rebalance_every_n_weeks=2, rebalance_tolerance=0.05, require_full_target=True,
        participation_limit_adv=1.0, slippage_bps=0.0,
    )


def test_future_trading_history_cannot_change_prior_terminal_treatment() -> None:
    """A later bar must not decide whether the holding was terminal on 2020-01-03."""
    cutoff = date(2020, 1, 3)
    truncated = run_portfolio_order_engine(_order_decision(), _order_panel(False), _order_config())
    extended = run_portfolio_order_engine(_order_decision(), _order_panel(True), _order_config())

    def orders_through(result: dict[str, object]) -> pl.DataFrame:
        return result["orders"].filter(pl.col("date") <= cutoff)  # type: ignore[union-attr]

    assert orders_through(truncated).equals(orders_through(extended))
    assert not orders_through(truncated).filter(
        pl.col("reason") == "delisted_assumed_terminal"
    ).height


def test_historical_validation_no_longer_imports_static_current_nifty100_universe() -> None:
    source = Path("strategies/mean_reversion/validation.py").read_text(encoding="utf-8")
    assert "load_nifty100_universe" not in source


def test_historical_diagnostics_use_dated_liquidity_membership_not_static_symbols() -> None:
    source = Path("strategies/mean_reversion/diagnostics.py").read_text(encoding="utf-8")
    assert "load_nifty100_universe" not in source
    assert "universe_symbols=universe" not in source

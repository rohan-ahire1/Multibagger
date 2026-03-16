from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl

from sepa_scanner.api.routes.backtest import BacktestRunRequest, run_backtest
from sepa_scanner.backtest.models import BacktestSummary, ExitMode
from sepa_scanner.main import run_backtest_command


def test_run_backtest_route_defaults_to_minervini_phased(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeStore:
        def initialize(self) -> None:
            captured["initialized"] = True

        def _connect(self) -> object:
            return object()

    monkeypatch.setattr(
        "sepa_scanner.api.routes.backtest.get_backtest_signals",
        lambda conn, start_date, end_date: pl.DataFrame(
            {
                "symbol": ["RELIANCE"],
                "signal_date": [date(2026, 1, 2)],
            }
        ),
    )
    monkeypatch.setattr(
        "sepa_scanner.api.routes.backtest.get_backtest_ohlcv",
        lambda parquet, symbols, start_date, end_date: pl.DataFrame(),
    )

    def fake_simulate(signals_df, ohlcv_df, params):
        captured["exit_mode"] = params.exit_mode
        return SimpleNamespace(trades=[])

    monkeypatch.setattr(
        "sepa_scanner.api.routes.backtest.simulate_breakout_backtest",
        fake_simulate,
    )
    monkeypatch.setattr(
        "sepa_scanner.api.routes.backtest.analyze_signal_quality",
        lambda signals_df, ohlcv_df, pending_days: {},
    )
    monkeypatch.setattr(
        "sepa_scanner.api.routes.backtest.analyze_post_entry_returns",
        lambda trades, ohlcv_df, forward_days: [],
    )
    monkeypatch.setattr(
        "sepa_scanner.api.routes.backtest.summarize_backtest",
        lambda trades, exit_mode: BacktestSummary(
            total_signals=0,
            filled_trades=0,
            not_filled_signals=0,
            stopped_trades=0,
            target_exits=0,
            time_stop_exits=0,
            win_rate=0.0,
            average_return_pct=None,
            average_r=None,
            median_r=None,
            expectancy=None,
            payoff_ratio=None,
            cumulative_r=0.0,
            max_drawdown=0.0,
            longest_losing_streak=0,
            exit_mode=exit_mode.value,
            exit_engine="minervini_phased",
        ),
    )
    monkeypatch.setattr(
        "sepa_scanner.api.routes.backtest.build_equity_curve",
        lambda trades: pl.DataFrame(),
    )

    response = run_backtest(
        req=BacktestRunRequest(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        ),
        settings=SimpleNamespace(runtime=SimpleNamespace(data_dir=tmp_path)),
        store=FakeStore(),
        parquet=object(),
    )

    assert captured["initialized"] is True
    assert captured["exit_mode"] == ExitMode.MINERVINI_PHASED
    assert response["params"]["exit_mode"] == ExitMode.MINERVINI_PHASED.value
    assert response["params"]["exit_engine"] == "minervini_phased"


def test_run_backtest_command_empty_state_uses_minervini_phased_defaults(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "sepa_scanner.main.resolve_settings",
        lambda config_dir: SimpleNamespace(runtime=SimpleNamespace(data_dir=tmp_path)),
    )
    monkeypatch.setattr(
        "sepa_scanner.main._stores",
        lambda settings: (object(), SimpleNamespace(initialize=lambda: None, _connect=lambda: object())),
    )
    monkeypatch.setattr(
        "sepa_scanner.main.get_backtest_signals",
        lambda conn, start_date, end_date, symbol=None: pl.DataFrame(),
    )

    report = run_backtest_command(
        config_dir=None,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        pending_days=5,
        max_hold_days=90,
        target_r=None,
        symbol=None,
        export_csv=False,
    )

    assert report.params.exit_mode == ExitMode.MINERVINI_PHASED
    assert report.summary.exit_mode == ExitMode.MINERVINI_PHASED.value
    assert report.summary.exit_engine == "minervini_phased"

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl

from sepa_scanner.api.routes.backtest import BacktestRunRequest, run_backtest
from sepa_scanner.backtest.models import BacktestSummary, ExitMode
from sepa_scanner.main import run_backtest_command


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(data_dir=tmp_path),
        market=SimpleNamespace(benchmark_symbol="NIFTYBEES"),
        market_filter=SimpleNamespace(breadth_weak_pct=0.40),
        backtest=SimpleNamespace(
            exit_mode=ExitMode.MINERVINI_PHASED.value,
            rs_rank_min=None,
            rs_rank_max=None,
            signal_ranking_mode="default",
            max_signals_per_day=None,
            use_regime_entry_controls=False,
            sector_alpha_enabled=False,
        ),
        exit_strategy=SimpleNamespace(
            trail_activation_r=2.0,
            trail_pct=0.20,
            dead_money_days=15,
        ),
        portfolio=SimpleNamespace(
            initial_capital=1_000_000.0,
            risk_per_trade_pct=0.01,
            max_positions=5,
            max_sector_positions=0,
            max_sector_pct=1.0,
            max_heat_pct=1.0,
            drawdown_half_pct=0.50,
            drawdown_stop_pct=0.75,
            drawdown_close_pct=0.90,
            regime_gate_enabled=False,
            regime_high_volatility_threshold=0.25,
            regime_cooldown_days=0,
            breadth_soft_floor_pct=None,
            breadth_soft_size_mult=0.50,
            pyramid_max_adds=0,
            pyramid_trigger_r=1.0,
            pyramid_add_interval_r=0.5,
            pyramid_risk_frac=0.50,
        ),
    )


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
        lambda parquet, symbols, start_date, end_date: pl.DataFrame(
            {
                "date": [date(2026, 1, 2)],
                "symbol": ["RELIANCE"],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1_000_000],
            }
        ),
    )
    monkeypatch.setattr(
        "sepa_scanner.strategy.ranking.load_sector_map",
        lambda path: {},
    )
    monkeypatch.setattr(
        "sepa_scanner.backtest.filter_signals",
        lambda signals_df, **kwargs: signals_df,
    )

    def fake_simulate(signals_df, ohlcv_df, params, portfolio, sector_map, **kwargs):
        captured["exit_mode"] = params.exit_mode
        return {
            "trades": [],
            "daily_equity": pl.DataFrame(
                {
                    "date": [date(2026, 1, 2)],
                    "equity": [1_000_000.0],
                    "cash": [1_000_000.0],
                    "positions": [0],
                    "daily_pnl": [0.0],
                    "daily_return": [0.0],
                }
            ),
        }

    monkeypatch.setattr(
        "sepa_scanner.backtest.simulate_portfolio_python",
        fake_simulate,
    )
    monkeypatch.setattr(
        "sepa_scanner.backtest.compute_regime_inputs",
        lambda settings, start_date, end_date, benchmark_df: pl.DataFrame(),
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
        settings=_settings(tmp_path),
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
        lambda config_dir: _settings(tmp_path),
    )
    monkeypatch.setattr(
        "sepa_scanner.main._stores",
        lambda settings, read_only=False: (
            object(),
            SimpleNamespace(initialize=lambda: None, _connect=lambda: object()),
        ),
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

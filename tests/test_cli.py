from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl
from typer.testing import CliRunner

from sepa_scanner.main import app, run_backfill_command, run_scan_command, run_update_command
from sepa_scanner.strategy.signal_engine import ScanResult


runner = CliRunner()


def test_scan_command_routes_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_scan_command(
        *, config_dir, scan_date, export_parquet, export_csv, no_telegram, force, **kwargs
    ) -> ScanResult:
        captured.update(
            {
                "config_dir": config_dir,
                "scan_date": scan_date,
                "export_parquet": export_parquet,
                "export_csv": export_csv,
                "no_telegram": no_telegram,
            }
        )
        return ScanResult(
            scan_date=scan_date,
            market_filter_on=True,
            num_symbols=3,
            num_eligible=2,
            signals=pl.DataFrame(
                {
                    "signal_type": ["WATCH"],
                    "symbol": ["RELIANCE"],
                }
            ),
        )

    monkeypatch.setattr("sepa_scanner.main.run_scan_command", fake_run_scan_command)

    result = runner.invoke(
        app,
        ["scan", "--date", "2026-03-07", "--export-csv", "--no-telegram"],
    )

    assert result.exit_code == 0
    assert captured["scan_date"] == date(2026, 3, 7)
    assert captured["export_parquet"] is True
    assert captured["export_csv"] is True
    assert captured["no_telegram"] is True


def test_run_command_calls_update_then_scan(monkeypatch) -> None:
    calls: list[str] = []

    def fake_run_update_command(*, config_dir, days, target_date, force):
        calls.append("update")
        assert days == 5
        assert target_date is None
        assert force is False
        return date(2026, 3, 7), None

    def fake_run_scan_command(
        *, config_dir, scan_date, export_parquet, export_csv, no_telegram, **kwargs
    ) -> None:
        calls.append("scan")
        assert scan_date == date(2026, 3, 7)
        assert export_parquet is True
        assert export_csv is False
        assert no_telegram is False

    monkeypatch.setattr("sepa_scanner.main.run_update_command", fake_run_update_command)
    monkeypatch.setattr("sepa_scanner.main.run_scan_command", fake_run_scan_command)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0
    assert calls == ["update", "scan"]


def test_run_update_command_uses_yfinance_universe_and_upserts(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    upserts: list[pl.DataFrame] = []

    class FakeParquetStore:
        def upsert(self, frame: pl.DataFrame) -> None:
            upserts.append(frame)

    fake_settings = SimpleNamespace(
        runtime=SimpleNamespace(data_dir=tmp_path),
        manual_universe=["RELIANCE", "TCS"],
    )

    def fake_download_adjusted_history(symbols, *, start, end):
        captured["symbols"] = symbols
        captured["start"] = start
        captured["end"] = end
        return (
            pl.DataFrame(
                {
                    "date": [date(2026, 3, 7)],
                    "symbol": ["RELIANCE"],
                    "open": [100.0],
                    "high": [105.0],
                    "low": [99.0],
                    "close": [104.0],
                    "volume": [1_000_000],
                    "turnover": [104_000_000.0],
                }
            ),
            [],
        )

    monkeypatch.setattr("sepa_scanner.main.resolve_settings", lambda config_dir: fake_settings)
    monkeypatch.setattr("sepa_scanner.main._stores", lambda settings: (FakeParquetStore(), None))
    monkeypatch.setattr(
        "sepa_scanner.main.download_adjusted_history",
        fake_download_adjusted_history,
        raising=False,
    )

    latest_date, meta = run_update_command(
        config_dir=None,
        days=5,
        target_date=date(2026, 3, 7),
        force=False,
    )

    assert captured == {
        "symbols": ["RELIANCE", "TCS"],
        "start": date(2026, 3, 7),
        "end": date(2026, 3, 8),
    }
    assert len(upserts) == 1
    assert latest_date == date(2026, 3, 7)
    assert meta == []


def test_backfill_command_uses_all_known_store_symbols_by_default(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    upserts: list[pl.DataFrame] = []

    class FakeParquetStore:
        def latest_date(self) -> date:
            return date(2026, 4, 16)

        def read_frame(self, *, start_date=None, end_date=None, columns=None) -> pl.DataFrame:
            if start_date == date(2026, 4, 16) and end_date == date(2026, 4, 16):
                return pl.DataFrame({"symbol": ["CURRENT"]})
            return pl.DataFrame({"symbol": ["CURRENT", "OLD_DELISTED", "OLDER_MERGED"]})

        def upsert(self, frame: pl.DataFrame) -> None:
            upserts.append(frame)

    fake_settings = SimpleNamespace(
        runtime=SimpleNamespace(data_dir=tmp_path),
        manual_universe=[],
    )

    def fake_download_adjusted_history(symbols, *, start, end):
        captured["symbols"] = symbols
        captured["start"] = start
        captured["end"] = end
        return (
            pl.DataFrame(
                {
                    "date": [date(2005, 1, 3)],
                    "symbol": ["OLD_DELISTED"],
                    "open": [100.0],
                    "high": [105.0],
                    "low": [99.0],
                    "close": [104.0],
                    "volume": [1_000_000],
                    "turnover": [104_000_000.0],
                }
            ),
            [],
        )

    monkeypatch.setattr("sepa_scanner.main.resolve_settings", lambda config_dir: fake_settings)
    monkeypatch.setattr("sepa_scanner.main._stores", lambda settings: (FakeParquetStore(), None))
    monkeypatch.setattr(
        "sepa_scanner.main.download_adjusted_history",
        fake_download_adjusted_history,
        raising=False,
    )

    processed = run_backfill_command(
        config_dir=None,
        start=date(2005, 1, 1),
        end=date(2005, 1, 31),
        final_only=True,
        sleep_seconds=0.0,
    )

    assert captured == {
        "symbols": ["CURRENT", "OLDER_MERGED", "OLD_DELISTED"],
        "start": date(2005, 1, 1),
        "end": date(2005, 2, 1),
    }
    assert len(upserts) == 1
    assert processed == [date(2005, 1, 3)]


def test_run_scan_command_bootstraps_plain_nse_session(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeParquetStore:
        def latest_date(self) -> date:
            return date(2026, 3, 6)

        def read_frame(self, *, start_date=None, end_date=None, columns=None) -> pl.DataFrame:
            return pl.DataFrame(
                    {
                    "date": [date(2026, 3, 6)],
                    "symbol": ["RELIANCE"],
                    "open": [100.0],
                    "high": [101.0],
                    "low": [99.0],
                    "close": [100.5],
                    "volume": [1_000_000],
                    "turnover": [100_500_000.0],
                }
            )

    class FakeDuckDbStore:
        def upsert_signals(self, signals) -> None:
            captured["signals"] = signals

        def replace_watchlist_candidates(self, watch_date, candidates) -> None:
            captured["watch_date"] = watch_date
            captured["watchlist"] = candidates

        def replace_daily_diagnostics(self, watch_date, diagnostics) -> None:
            captured["diagnostics"] = diagnostics

        def insert_run(self, run) -> None:
            captured["run"] = run

    fake_settings = SimpleNamespace(
        runtime=SimpleNamespace(data_dir=tmp_path),
        data=SimpleNamespace(min_history_days=50, http_timeout_sec=30),
        trend_template=SimpleNamespace(
            lookback_52w=252,
            sma_slow=200,
            sma200_slope_lookback=66,
        ),
        setup_vcp=SimpleNamespace(base_lookback=65),
        relative_strength=SimpleNamespace(lookbacks=[63, 126, 189, 252]),
        alerts=SimpleNamespace(channels=[]),
        telegram=SimpleNamespace(enabled=False),
    )

    sentinel_raw_session = object()
    sentinel_bootstrapped_session = object()

    monkeypatch.setattr("sepa_scanner.main.resolve_settings", lambda config_dir: fake_settings)
    monkeypatch.setattr(
        "sepa_scanner.main._stores",
        lambda settings: (FakeParquetStore(), FakeDuckDbStore()),
    )
    monkeypatch.setattr(
        "sepa_scanner.main.build_nse_session",
        lambda: sentinel_raw_session,
    )

    def fake_bootstrap_nse_session(session, *, timeout):
        captured["bootstrap_session"] = session
        captured["bootstrap_timeout"] = timeout
        return sentinel_bootstrapped_session

    def fake_load_cm_holidays(session, cache_path):
        captured["holiday_session"] = session
        return set()

    monkeypatch.setattr(
        "sepa_scanner.main.bootstrap_nse_session",
        fake_bootstrap_nse_session,
        raising=False,
    )
    monkeypatch.setattr("sepa_scanner.main.load_cm_holidays", fake_load_cm_holidays)
    monkeypatch.setattr(
        "sepa_scanner.main.scan_for_date",
        lambda *, frame, settings, scan_date: ScanResult(
            scan_date=scan_date,
            market_filter_on=True,
            num_symbols=1,
            num_eligible=1,
            signals=pl.DataFrame({"signal_type": pl.Series([], dtype=pl.Utf8)}),
        ),
    )

    result = run_scan_command(
        config_dir=None,
        scan_date=None,
        export_parquet=False,
        export_csv=False,
        no_telegram=True,
        force=False,
    )

    assert result.scan_date == date(2026, 3, 6)
    assert captured["bootstrap_session"] is sentinel_raw_session
    assert captured["bootstrap_timeout"] == 30
    assert captured["holiday_session"] is sentinel_bootstrapped_session


def test_health_command_prints_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        "sepa_scanner.main.collect_health",
        lambda *, config_dir: {
            "last_run_status": "success",
            "latest_ohlcv_date": date(2026, 3, 7),
            "signals_on_latest_date": 2,
        },
    )

    result = runner.invoke(app, ["health"])

    assert result.exit_code == 0
    assert "last_run_status: success" in result.stdout
    assert "latest_ohlcv_date: 2026-03-07" in result.stdout


def test_ui_command_routes_host_and_port(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_ui_command(*, config_dir, host, port) -> None:
        captured.update(
            {
                "config_dir": config_dir,
                "host": host,
                "port": port,
            }
        )

    monkeypatch.setattr("sepa_scanner.main.run_ui_command", fake_run_ui_command, raising=False)

    result = runner.invoke(app, ["ui", "--host", "127.0.0.1", "--port", "8602"])

    assert result.exit_code == 0
    assert captured == {
        "config_dir": None,
        "host": "127.0.0.1",
        "port": 8602,
    }


def test_backtest_command_routes_options(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_backtest_command(
        *,
        config_dir,
        start_date,
        end_date,
        pending_days,
        max_hold_days,
        target_r,
        symbol,
        export_csv,
        use_rust,
        use_portfolio,
        max_positions,
    ):
        captured.update(
            {
                "config_dir": config_dir,
                "start_date": start_date,
                "end_date": end_date,
                "pending_days": pending_days,
                "max_hold_days": max_hold_days,
                "target_r": target_r,
                "symbol": symbol,
                "export_csv": export_csv,
                "use_rust": use_rust,
                "use_portfolio": use_portfolio,
                "max_positions": max_positions,
            }
        )
        return SimpleNamespace(
            summary=SimpleNamespace(
                total_signals=1,
                filled_trades=1,
                win_rate=1.0,
                cumulative_r=2.0,
                expectancy=2.0,
                max_drawdown=0.0,
                exit_mode="v1_fixed",
            ),
            trades=pl.DataFrame({"symbol": ["RELIANCE"]}),
        )

    monkeypatch.setattr("sepa_scanner.main.run_backtest_command", fake_run_backtest_command, raising=False)
    monkeypatch.setattr(
        "sepa_scanner.main.resolve_settings",
        lambda config_dir: SimpleNamespace(
            risk=SimpleNamespace(target_r=2.0),
            backtest=SimpleNamespace(pending_days=5, max_hold_days=60, exit_mode="minervini_phased"),
            exit_strategy=SimpleNamespace(
                trail_activation_r=2.0,
                trail_pct=0.20,
                dead_money_days=15,
            ),
            portfolio=SimpleNamespace(max_positions=8),
        ),
    )

    result = runner.invoke(
        app,
        [
            "backtest",
            "--start",
            "2026-03-01",
            "--end",
            "2026-03-31",
            "--pending-days",
            "4",
            "--max-hold-days",
            "15",
            "--target-r",
            "2.5",
            "--symbol",
            "RELIANCE",
            "--export-csv",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "config_dir": None,
        "start_date": date(2026, 3, 1),
        "end_date": date(2026, 3, 31),
        "pending_days": 4,
        "max_hold_days": 15,
        "target_r": 2.5,
        "symbol": "RELIANCE",
        "export_csv": True,
        "use_rust": False,
        "use_portfolio": False,
        "max_positions": 8,
    }
    assert "total_signals: 1" in result.stdout


def test_backtest_command_prints_empty_state(monkeypatch) -> None:
    def fake_run_backtest_command(
        *,
        config_dir,
        start_date,
        end_date,
        pending_days,
        max_hold_days,
        target_r,
        symbol,
        export_csv,
        use_rust,
        use_portfolio,
        max_positions,
    ):
        return SimpleNamespace(
            summary=SimpleNamespace(
                total_signals=0,
                filled_trades=0,
                win_rate=0.0,
                cumulative_r=0.0,
            ),
            trades=pl.DataFrame(),
        )

    monkeypatch.setattr("sepa_scanner.main.run_backtest_command", fake_run_backtest_command, raising=False)

    result = runner.invoke(
        app,
        ["backtest", "--start", "2026-03-01", "--end", "2026-03-31"],
    )

    assert result.exit_code == 0
    assert "No eligible BREAKOUT signals" in result.stdout

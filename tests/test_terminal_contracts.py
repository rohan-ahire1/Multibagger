from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_terminal_app_exposes_only_supported_routes() -> None:
    app_source = _read("sepa-terminal/src/App.tsx")

    assert 'path="/"' in app_source
    assert 'path="/scanner"' in app_source
    assert 'path="/symbol/:symbol"' in app_source
    assert 'path="/backtest"' in app_source

    assert "RegimeStrip" not in app_source
    assert "Portfolio" not in app_source


def test_navigation_is_limited_to_supported_views() -> None:
    top_bar = _read("sepa-terminal/src/components/layout/TopBar.tsx")

    assert "to: '/scanner'" in top_bar
    assert "to: '/backtest'" in top_bar

    assert "Portfolio" not in top_bar
    assert "/portfolio" not in top_bar


def test_candle_chart_windows_on_a_selectable_timeframe() -> None:
    """The chart deliberately shows a timeframe window rather than all history.

    Superseded the old "always fitContent()" contract when the terminal was
    rebuilt: a trade is far easier to read centred in a fixed-duration window
    than as a blip in fifteen years of bars. The invariant that survives is
    that the requested duration is preserved when the window hits either edge
    of the available history, so the chart never silently shrinks.
    """
    chart_source = _read("sepa-terminal/src/components/chart/CandleChart.tsx")

    assert "chartVisibleRange" in chart_source
    assert "setVisibleRange(chartVisibleRange(" in chart_source
    assert "TIMEFRAME_MONTHS" in chart_source

    # Edge clamping: running past either end shifts the window instead of
    # truncating it.
    assert "fromTime -= toTimeMs - lastTime" in chart_source
    assert "toTimeMs + (firstTime - fromTime)" in chart_source


def test_scan_date_picker_uses_successful_diagnostic_dates() -> None:
    scanner_routes = _read("src/sepa_scanner/api/routes/scanner.py")

    assert "WHERE r.status = 'success'" in scanner_routes
    assert "INNER JOIN daily_diagnostics" in scanner_routes

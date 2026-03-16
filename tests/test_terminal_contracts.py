from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_terminal_app_exposes_only_scanner_and_backtest() -> None:
    app_source = _read("sepa-terminal/src/App.tsx")

    assert "RegimeStrip" not in app_source
    assert "Analysis" not in app_source
    assert "Portfolio" not in app_source
    assert "{activeWorkspace === 1 && <MarketScanner />}" in app_source
    assert "{activeWorkspace === 2 && <Backtest />}" in app_source
    assert "activeWorkspace === 3" not in app_source
    assert "activeWorkspace === 4" not in app_source


def test_workspace_navigation_is_limited_to_two_supported_views() -> None:
    top_bar = _read("sepa-terminal/src/components/layout/TopBar.tsx")
    shortcuts = _read("sepa-terminal/src/hooks/useWorkspaceSwitch.ts")
    ui_store = _read("sepa-terminal/src/stores/ui.ts")

    assert "Analysis" not in top_bar
    assert "Portfolio" not in top_bar
    assert "label: 'Scanner'" in top_bar
    assert "label: 'Backtest'" in top_bar
    assert "setWorkspace(3)" not in shortcuts
    assert "setWorkspace(4)" not in shortcuts
    assert "activeWorkspace: 1 | 2" in ui_store


def test_price_chart_uses_full_history_instead_of_signal_window() -> None:
    chart_source = _read("sepa-terminal/src/components/chart/PriceChart.tsx")

    assert "setVisibleRange" not in chart_source
    assert "fitContent()" in chart_source

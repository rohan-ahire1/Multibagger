"""The parity gate.

SEPA run through the lab must reproduce the committed baseline in
``docs/BACKTEST_TRUTH.md`` exactly. If this fails, the lab is not executing the
canonical code path and no number it produces can be trusted.

    uv run pytest tests/lab/test_sepa_parity.py -m slow

Reference (NSE bhavcopy panel, signals 2010-01-04 -> 2026-03-17):
    CAGR 12.48%  MaxDD 24.28%  Calmar 0.51  trades 281
"""

from __future__ import annotations

import pytest

from sepa_scanner.lab.runner import run_backtest
from tests.lab.conftest import FULL_END, FULL_START

import sepa_scanner.lab_strategies  # noqa: F401  (registers the strategies)

EXPECTED_CAGR_PCT = 12.48
EXPECTED_MAX_DD_PCT = 24.28
EXPECTED_CALMAR = 0.51
EXPECTED_TRADES = 281

# Rates are asserted to the same 2dp the canonical harness prints. The trade
# count is exact: an off-by-one there means signal selection diverged.
RATE_TOLERANCE = 0.01
CALMAR_TOLERANCE = 0.005


@pytest.mark.slow
def test_sepa_lab_run_reproduces_committed_baseline(full_bundle) -> None:
    result = run_backtest(
        "sepa_breakout", FULL_START, FULL_END, bundle=full_bundle
    )
    m = result.metrics

    assert m.trades == EXPECTED_TRADES, (
        f"trade count {m.trades} != {EXPECTED_TRADES}: signal selection diverged "
        "from the canonical harness"
    )
    assert m.cagr_pct == pytest.approx(EXPECTED_CAGR_PCT, abs=RATE_TOLERANCE)
    assert m.max_drawdown_pct == pytest.approx(EXPECTED_MAX_DD_PCT, abs=RATE_TOLERANCE)
    assert m.calmar == pytest.approx(EXPECTED_CALMAR, abs=CALMAR_TOLERANCE)


@pytest.mark.slow
def test_committed_run_excludes_idle_cash_income(full_bundle) -> None:
    """The committed config excludes idle-cash income from the headline."""
    result = run_backtest("sepa_breakout", FULL_START, FULL_END, bundle=full_bundle)

    assert result.params["cash_yield_apy"] == 0.0
    assert not any("cash_yield_apy" in w for w in result.warnings)


@pytest.mark.slow
def test_broken_benchmark_suppresses_alpha_instead_of_fabricating_it(full_bundle) -> None:
    """NIFTYBEES split 1:10 on 2019-12-19 and was never adjusted for.

    Its raw curve implies a ~90% loss over the window, which would surface as
    roughly +16 points of invented alpha. Publish nothing rather than that.
    """
    result = run_backtest("sepa_breakout", FULL_START, FULL_END, bundle=full_bundle)

    assert result.metrics.benchmark_cagr_pct is None
    assert result.metrics.excess_cagr_pct is None
    assert any("unadjusted price discontinuit" in w for w in result.warnings)
    assert all(p.benchmark_equity is None for p in result.equity_curve)


@pytest.mark.slow
def test_cash_yield_sleeve_matches_reference_and_is_flagged(full_bundle) -> None:
    """The 6% sleeve reproduces the reference row and is marked as not alpha."""
    result = run_backtest(
        "sepa_breakout",
        FULL_START,
        FULL_END,
        params={"cash_yield_apy": 0.06},
        bundle=full_bundle,
    )

    assert result.metrics.cagr_pct == pytest.approx(17.20, abs=RATE_TOLERANCE)
    assert result.metrics.max_drawdown_pct == pytest.approx(15.08, abs=RATE_TOLERANCE)
    assert any("not strategy alpha" in w for w in result.warnings)

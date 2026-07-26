from __future__ import annotations

import pytest

from sepa_scanner.lab.registry import get_strategy

import sepa_scanner.lab_strategies  # noqa: F401


def test_source_ambiguities_are_exposed_as_runnable_parameters():
    specs = {spec.name: spec for spec in get_strategy("stocks_on_the_move").param_specs}
    assert specs["gap_definition"].choices == ("close_to_close", "open_gap")
    assert specs["atr_smoothing"].choices == ("simple", "wilder")
    assert specs["require_full_target"].type == "bool"
    assert specs["rebalance_tolerance"].validate(0.0) == 0.0


@pytest.mark.slow
def test_end_to_end_portfolio_run_is_slow():
    """The complete panel run is intentionally opt-in; it is a compute canary."""
    from datetime import date
    from sepa_scanner.lab.runner import run_backtest

    # The Nifty 500 price index is not in the repo, so the default regime source
    # refuses. Use the documented TRI substitute, which also means this canary
    # exercises a run that actually trades rather than a silent flat curve.
    result = run_backtest(
        "stocks_on_the_move", date(2015, 1, 1), date(2019, 12, 31),
        params={"regime_source": "nifty500_tri"},
    )
    assert result.kind.value == "portfolio"
    assert result.metrics.trades and result.metrics.trades > 0

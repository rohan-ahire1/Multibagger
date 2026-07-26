"""A missing regime series must refuse, not quietly produce a no-op run.

The first real backtest of this strategy returned 0 trades and a tidy 0.00%
table because the Nifty 500 price index is not in the repo and an absent regime
frame read as "no buys allowed" on every decision date. A run that cannot
possibly trade has to say so.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from sepa_scanner.lab.registry import get_strategy
from sepa_scanner.lab_strategies import stocks_on_the_move as som


def _context(tmp_path, monkeypatch, *, price: bool, tri: bool):
    """A context whose data_dir holds only the reference files we ask for."""
    reference = tmp_path / "reference"
    reference.mkdir(parents=True, exist_ok=True)
    sessions = pl.date_range(date(2015, 1, 1), date(2016, 12, 31), eager=True)
    if price:
        pl.DataFrame({"date": sessions, "price": [100.0 + i for i in range(len(sessions))]}).write_csv(
            reference / "nifty500_price_2015-01-01_2016-12-31.csv"
        )
    if tri:
        pl.DataFrame({"date": sessions, "tri": [200.0 + i for i in range(len(sessions))]}).write_csv(
            reference / "nifty500_tri_2015-01-01_2016-12-31.csv"
        )

    class Ctx:
        data_dir = tmp_path
        start_date = date(2015, 1, 1)
        end_date = date(2016, 12, 31)
        panel = pl.DataFrame({"date": sessions})
        extras = {"portfolio": {"initial_capital": 1_000_000.0}}

    return Ctx()


def test_absent_regime_series_refuses_instead_of_blocking_every_buy(tmp_path, monkeypatch):
    strategy = get_strategy("stocks_on_the_move")
    params = strategy.defaults(None)
    ctx = _context(tmp_path, monkeypatch, price=False, tri=False)

    with pytest.raises(ValueError, match="no local data"):
        strategy.engine_config(ctx, params)


def test_tri_fallback_is_explicit_and_builds_a_regime(tmp_path, monkeypatch):
    strategy = get_strategy("stocks_on_the_move")
    ctx = _context(tmp_path, monkeypatch, price=False, tri=True)

    # The default still refuses: substituting TRI must be a deliberate choice,
    # never an automatic fallback that hides the deviation.
    with pytest.raises(ValueError, match="no local data"):
        strategy.engine_config(ctx, strategy.defaults(None))

    params = {**strategy.defaults(None), "regime_source": "nifty500_tri"}
    config = strategy.engine_config(ctx, params)

    regime = config["config"].market_regime
    assert not regime.is_empty()
    assert regime.columns == ["date", "allows_new_buys"]
    # A monotonically rising series is above its own SMA200 once the window fills.
    assert regime["allows_new_buys"].any()
    assert config["regime_source"] == "nifty500_tri"


def test_regime_source_is_an_exposed_variant():
    strategy = get_strategy("stocks_on_the_move")
    spec = next(s for s in strategy.param_specs if s.name == "regime_source")
    assert spec.default == "price_index"
    assert set(spec.choices) == {"price_index", "nifty500_tri"}

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from sepa_scanner.lab.contracts import ParamSpec, StrategyKind
from sepa_scanner.lab.registry import (
    get_strategy,
    list_strategies,
    register_strategy,
    resolve_params,
)
from sepa_scanner.lab.runner import params_hash, validate_intent

import sepa_scanner.lab_strategies  # noqa: F401


class _Stub:
    id = "stub_strategy"
    name = "Stub"
    kind = StrategyKind.SIGNAL
    description = "test double"

    @property
    def param_specs(self):
        return [
            ParamSpec("count", "Count", "int", 5, min=1, max=10),
            ParamSpec("ratio", "Ratio", "float", 0.5, min=0.0, max=1.0),
            ParamSpec("mode", "Mode", "enum", "a", choices=("a", "b")),
            ParamSpec("flag", "Flag", "bool", False),
        ]

    def defaults(self, settings):
        return {"count": 5, "ratio": 0.5, "mode": "a", "flag": False}

    def generate(self, ctx, params):
        return pl.DataFrame()

    def engine_config(self, ctx, params):
        return {}


# --- ParamSpec ------------------------------------------------------------


def test_enum_requires_choices():
    with pytest.raises(ValueError, match="enum requires choices"):
        ParamSpec("x", "X", "enum", "a")


def test_choices_rejected_for_non_enum():
    with pytest.raises(ValueError, match="choices only valid for enum"):
        ParamSpec("x", "X", "int", 1, choices=("a",))


def test_min_greater_than_max_rejected():
    with pytest.raises(ValueError, match="min > max"):
        ParamSpec("x", "X", "int", 1, min=10, max=1)


def test_int_spec_rejects_fractional_value():
    spec = ParamSpec("count", "Count", "int", 5)
    with pytest.raises(ValueError, match="not a valid int"):
        spec.validate(1.5)


def test_int_spec_accepts_integral_float():
    assert ParamSpec("count", "Count", "int", 5).validate(7.0) == 7


def test_bounds_are_enforced_both_ways():
    spec = ParamSpec("count", "Count", "int", 5, min=1, max=10)
    with pytest.raises(ValueError, match="below minimum"):
        spec.validate(0)
    with pytest.raises(ValueError, match="above maximum"):
        spec.validate(11)


def test_enum_rejects_value_outside_choices():
    spec = ParamSpec("mode", "Mode", "enum", "a", choices=("a", "b"))
    with pytest.raises(ValueError, match="not one of"):
        spec.validate("z")


def test_bool_parses_strings():
    spec = ParamSpec("flag", "Flag", "bool", False)
    assert spec.validate("true") is True
    assert spec.validate("no") is False
    with pytest.raises(ValueError):
        spec.validate("maybe")


def test_date_spec_parses_iso():
    spec = ParamSpec("d", "D", "date", date(2020, 1, 1))
    assert spec.validate("2024-03-05") == date(2024, 3, 5)


# --- registry -------------------------------------------------------------


def test_registered_sepa_strategy_is_discoverable():
    strategy = get_strategy("sepa_breakout")
    assert strategy.kind is StrategyKind.SIGNAL
    assert strategy.param_specs


def test_unknown_strategy_lists_known_ids():
    with pytest.raises(KeyError, match="sepa_breakout"):
        get_strategy("does_not_exist")


def test_every_registered_strategy_is_well_formed():
    for strategy in list_strategies():
        assert strategy.id and strategy.id.islower()
        assert strategy.name
        assert strategy.description
        assert strategy.param_specs


def test_bad_id_rejected():
    class Bad(_Stub):
        id = "Not-A-Slug"

    with pytest.raises(ValueError, match="lower_snake_case"):
        register_strategy(Bad())


def test_duplicate_param_rejected():
    class Dupe(_Stub):
        id = "dupe_params"

        @property
        def param_specs(self):
            return [
                ParamSpec("count", "Count", "int", 1),
                ParamSpec("count", "Count again", "int", 2),
            ]

    with pytest.raises(ValueError, match="duplicate param"):
        register_strategy(Dupe())


# --- param resolution -----------------------------------------------------


def test_unknown_override_is_rejected_not_ignored():
    with pytest.raises(ValueError, match="unknown parameter"):
        resolve_params(_Stub(), None, {"nope": 1})


def test_overrides_merge_onto_defaults():
    resolved = resolve_params(_Stub(), None, {"count": 9})
    assert resolved["count"] == 9
    assert resolved["ratio"] == 0.5


def test_override_bounds_enforced():
    with pytest.raises(ValueError, match="above maximum"):
        resolve_params(_Stub(), None, {"count": 99})


# --- intent validation ----------------------------------------------------


def test_signal_frame_missing_column_is_rejected():
    frame = pl.DataFrame({"symbol": ["X"], "signal_date": [date(2020, 1, 1)]})
    with pytest.raises(ValueError, match="missing required column"):
        validate_intent(frame, StrategyKind.SIGNAL, "s")


def test_empty_intent_is_rejected():
    frame = pl.DataFrame(
        schema={"symbol": pl.Utf8, "signal_date": pl.Date, "entry_ref": pl.Float64, "stop": pl.Float64}
    )
    with pytest.raises(ValueError, match="produced no rows"):
        validate_intent(frame, StrategyKind.SIGNAL, "s")


def test_duplicate_signals_are_rejected():
    frame = pl.DataFrame(
        {
            "symbol": ["X", "X"],
            "signal_date": [date(2020, 1, 1), date(2020, 1, 1)],
            "entry_ref": [10.0, 10.0],
            "stop": [9.0, 9.0],
        }
    )
    with pytest.raises(ValueError, match="duplicate"):
        validate_intent(frame, StrategyKind.SIGNAL, "s")


def test_overallocated_weights_are_rejected():
    frame = pl.DataFrame(
        {
            "rebalance_date": [date(2020, 1, 1), date(2020, 1, 1)],
            "symbol": ["A", "B"],
            "weight": [0.7, 0.5],
        }
    )
    with pytest.raises(ValueError, match="more than 100%"):
        validate_intent(frame, StrategyKind.REBALANCE, "s")


def test_weights_summing_to_one_are_accepted():
    frame = pl.DataFrame(
        {
            "rebalance_date": [date(2020, 1, 1), date(2020, 1, 1)],
            "symbol": ["A", "B"],
            "weight": [0.5, 0.5],
        }
    )
    validate_intent(frame, StrategyKind.REBALANCE, "s")


# --- hashing --------------------------------------------------------------


def test_params_hash_is_order_insensitive_but_value_sensitive():
    a = params_hash("s", {"x": 1, "y": 2}, "nse")
    b = params_hash("s", {"y": 2, "x": 1}, "nse")
    c = params_hash("s", {"x": 2, "y": 2}, "nse")
    assert a == b
    assert a != c


def test_params_hash_tracks_data_source():
    assert params_hash("s", {"x": 1}, "nse") != params_hash("s", {"x": 1}, "yfinance")


# --- benchmark integrity --------------------------------------------------


def test_discontinuity_detector_flags_unadjusted_split():
    from sepa_scanner.lab.panel import detect_price_discontinuities

    frame = pl.DataFrame(
        {
            "date": [date(2019, 12, 17), date(2019, 12, 18), date(2019, 12, 19)],
            "tri": [1300.0, 1302.0, 130.2],
        }
    )
    assert detect_price_discontinuities(frame, "tri") == [date(2019, 12, 19)]


def test_discontinuity_detector_ignores_ordinary_moves():
    from sepa_scanner.lab.panel import detect_price_discontinuities

    frame = pl.DataFrame(
        {
            "date": [date(2020, 3, 1), date(2020, 3, 2), date(2020, 3, 3)],
            "tri": [100.0, 92.0, 108.0],
        }
    )
    assert detect_price_discontinuities(frame, "tri") == []


def test_discontinuity_detector_handles_degenerate_input():
    from sepa_scanner.lab.panel import detect_price_discontinuities

    empty = pl.DataFrame(schema={"date": pl.Date, "tri": pl.Float64})
    assert detect_price_discontinuities(empty, "tri") == []
    single = pl.DataFrame({"date": [date(2020, 1, 1)], "tri": [10.0]})
    assert detect_price_discontinuities(single, "tri") == []

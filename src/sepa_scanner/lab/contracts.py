"""The strategy contract every lab strategy implements.

A strategy supplies *what to trade* (signals, or target weights) plus metadata
describing its own parameters. It never simulates: execution, sizing, exits,
friction, and metrics all belong to the shared engines, which is what makes two
strategies comparable on one leaderboard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import polars as pl


class DataRequirement(str, Enum):
    """Which inputs a strategy needs the runner to load."""

    SIGNALS = "signals"
    """Precomputed signals from the DuckDB signals table (SEPA's scan output)."""

    PANEL = "panel"
    """Raw OHLCV only; the strategy computes its own signals."""


class StrategyKind(str, Enum):
    """How a strategy expresses its intent, which selects the engine."""

    SIGNAL = "signal"
    """Discrete dated entries carrying a stop. Trade-driven, R-multiples apply."""

    REBALANCE = "rebalance"
    """Periodic target weights. No per-trade stop, so R-multiples do not apply."""

    REVERSION = "reversion"
    """Dated entries with a stop, but exits on reversion completion rather than a
    trend break. Uses the same signal frame as SIGNAL; only the executor differs,
    because the shared portfolio simulator has no reversion exit."""


ParamType = Literal["int", "float", "bool", "enum", "date", "str"]


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """One tunable parameter, rich enough for the UI to render a control."""

    name: str
    label: str
    type: ParamType
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple[str, ...] | None = None
    group: str = "General"
    help: str = ""
    advanced: bool = False

    def __post_init__(self) -> None:
        if self.type == "enum" and not self.choices:
            raise ValueError(f"param {self.name!r}: enum requires choices")
        if self.choices and self.type != "enum":
            raise ValueError(f"param {self.name!r}: choices only valid for enum")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"param {self.name!r}: min > max")

    def coerce(self, value: Any) -> Any:
        """Convert a JSON-ish value to this param's type, or raise ValueError."""
        if value is None:
            return None
        try:
            if self.type == "int":
                # Reject 1.5 rather than silently truncating to 1.
                coerced = int(value)
                if isinstance(value, float) and value != coerced:
                    raise ValueError
                return coerced
            if self.type == "float":
                return float(value)
            if self.type == "bool":
                if isinstance(value, str):
                    lowered = value.strip().lower()
                    if lowered in {"true", "1", "yes"}:
                        return True
                    if lowered in {"false", "0", "no"}:
                        return False
                    raise ValueError
                return bool(value)
            if self.type == "date":
                return value if isinstance(value, date) else date.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"param {self.name!r}: {value!r} is not a valid {self.type}"
            ) from exc
        return str(value)

    def validate(self, value: Any) -> Any:
        """Coerce then bounds-check. Returns the clean value."""
        coerced = self.coerce(value)
        if coerced is None:
            return None
        if self.type in {"int", "float"}:
            if self.min is not None and coerced < self.min:
                raise ValueError(f"param {self.name!r}: {coerced} below minimum {self.min}")
            if self.max is not None and coerced > self.max:
                raise ValueError(f"param {self.name!r}: {coerced} above maximum {self.max}")
        if self.type == "enum" and self.choices and coerced not in self.choices:
            raise ValueError(
                f"param {self.name!r}: {coerced!r} not one of {list(self.choices)}"
            )
        return coerced


@dataclass(slots=True)
class StrategyContext:
    """Everything a strategy may read while producing its intent.

    Deliberately read-only in spirit: a strategy that mutates these frames
    corrupts every later run in the same process.
    """

    settings: Any
    start_date: date
    end_date: date
    data_dir: Path
    sector_map: dict[str, str] = field(default_factory=dict)
    panel: pl.DataFrame | None = None
    benchmark: pl.DataFrame | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# Required columns for each intent frame, checked by the runner before the
# engine ever sees the data. Failing here produces a clear strategy-authoring
# error instead of an opaque IndexError 800 lines into the simulator.
SIGNAL_REQUIRED_COLUMNS: tuple[str, ...] = ("symbol", "signal_date", "entry_ref", "stop")
WEIGHT_REQUIRED_COLUMNS: tuple[str, ...] = ("rebalance_date", "symbol", "weight")


@runtime_checkable
class Strategy(Protocol):
    """Structural contract. Registered strategies are instances, not classes."""

    id: str
    name: str
    kind: StrategyKind
    description: str
    data_requirement: DataRequirement

    @property
    def param_specs(self) -> list[ParamSpec]: ...

    def defaults(self, settings: Any) -> dict[str, Any]: ...

    def generate(self, ctx: StrategyContext, params: dict[str, Any]) -> pl.DataFrame:
        """Return signals (SIGNAL kind) or target weights (REBALANCE kind)."""
        ...

    def engine_config(self, ctx: StrategyContext, params: dict[str, Any]) -> dict[str, Any]:
        """Engine-level overrides (sizing, exits, friction) derived from params."""
        ...

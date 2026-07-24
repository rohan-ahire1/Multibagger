"""Strategy registry.

Discovery is by explicit import in ``sepa_scanner.lab_strategies``, not
filesystem scanning: a strategy that fails to import should break loudly at
startup rather than silently vanish from the UI.
"""

from __future__ import annotations

import re
from typing import Any, TypeVar

from sepa_scanner.lab.contracts import ParamSpec, Strategy

_REGISTRY: dict[str, Strategy] = {}

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

S = TypeVar("S", bound=Strategy)


def register_strategy(strategy: S) -> S:
    """Register a strategy instance. Returns it, so it doubles as a decorator."""
    sid = getattr(strategy, "id", None)
    if not sid or not _ID_RE.match(sid):
        raise ValueError(
            f"strategy id {sid!r} must be a lower_snake_case slug: it is persisted "
            "with every run and renaming it orphans that history"
        )
    if sid in _REGISTRY and _REGISTRY[sid] is not strategy:
        raise ValueError(f"duplicate strategy id {sid!r}")
    if not getattr(strategy, "name", None):
        raise ValueError(f"strategy {sid!r} must define a human-readable name")
    if not getattr(strategy, "description", None):
        raise ValueError(f"strategy {sid!r} must define a description")

    seen: set[str] = set()
    for spec in strategy.param_specs:
        if spec.name in seen:
            raise ValueError(f"strategy {sid!r}: duplicate param {spec.name!r}")
        seen.add(spec.name)

    _REGISTRY[sid] = strategy
    return strategy


def get_strategy(strategy_id: str) -> Strategy:
    try:
        return _REGISTRY[strategy_id]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "<none registered>"
        raise KeyError(f"unknown strategy {strategy_id!r}; registered: {known}") from None


def list_strategies() -> list[Strategy]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def clear_registry() -> None:
    """Test-only hook."""
    _REGISTRY.clear()


def resolve_params(
    strategy: Strategy, settings: Any, overrides: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge user overrides onto strategy defaults, validating every value.

    Unknown keys are rejected rather than ignored: a silently dropped parameter
    produces a run whose recorded params do not describe what actually ran.
    """
    specs: dict[str, ParamSpec] = {s.name: s for s in strategy.param_specs}
    resolved = dict(strategy.defaults(settings))

    for key, value in (overrides or {}).items():
        if key not in specs:
            known = ", ".join(sorted(specs)) or "<none>"
            raise ValueError(f"unknown parameter {key!r} for {strategy.id!r}; known: {known}")
        resolved[key] = specs[key].validate(value)

    # Defaults are validated too, so a bad config.yaml is caught at run time
    # rather than producing a quietly wrong backtest.
    for name, spec in specs.items():
        if name in resolved:
            resolved[name] = spec.validate(resolved[name])

    return resolved

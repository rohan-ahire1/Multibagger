"""Registered strategies.

Discovery is these explicit imports. A strategy that fails to import must break
loudly here rather than quietly disappear from the registry and the UI.
"""

from sepa_scanner.lab_strategies import sepa_breakout  # noqa: F401

__all__ = ["sepa_breakout"]

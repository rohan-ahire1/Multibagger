"""Registered strategies.

Discovery is these explicit imports. A strategy that fails to import must break
loudly here rather than quietly disappear from the registry and the UI.
"""

from sepa_scanner.lab_strategies import daily_factors  # noqa: F401
from sepa_scanner.lab_strategies import episodic_pivots  # noqa: F401
from sepa_scanner.lab_strategies import sepa_breakout  # noqa: F401

__all__ = ["daily_factors", "episodic_pivots", "sepa_breakout"]

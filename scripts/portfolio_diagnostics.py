"""Compatibility shim for the canonical portfolio simulator.

The engine moved to ``sepa_scanner.lab.engines.portfolio_sim`` so that library
code can import it. ``scripts/`` is not a package, so anything under ``src/``
could only have reached it through a cwd-dependent ``sys.path`` entry.

Nothing here changes behaviour: this module re-exports the moved engine so the
existing entry points (``optimize_joint_wf.py``, ``diagnose_entry_exit.py``,
``rust_core/scripts/optuna_optimize.py``, the sibling scripts, and the tests)
keep working unchanged, including ``python scripts/portfolio_diagnostics.py``.

Prefer importing from ``sepa_scanner.lab.engines.portfolio_sim`` in new code.
"""

from __future__ import annotations

from sepa_scanner.lab.engines.portfolio_sim import *  # noqa: F401,F403
from sepa_scanner.lab.engines.portfolio_sim import (  # noqa: F401
    build_entry_controls,
    compute_regime_inputs,
    filter_signals,
    load_inputs,
    main,
    resolve_signal_quality_overrides,
    simulate_portfolio_python,
)

if __name__ == "__main__":
    main()

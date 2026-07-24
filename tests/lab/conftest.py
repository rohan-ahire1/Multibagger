from __future__ import annotations

from datetime import date

import pytest

FULL_START = date(2010, 1, 4)
FULL_END = date(2026, 3, 17)


@pytest.fixture(scope="session")
def full_bundle():
    """The full-history data bundle, loaded once for the slow parity tests."""
    from sepa_scanner.lab.panel import load_bundle

    return load_bundle(FULL_START, FULL_END)
